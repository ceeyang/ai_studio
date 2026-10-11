"""网页文本模型：在用户自己浏览器里已登录的 ChatGPT / Gemini / Claude / Muse 网页上问（免费额度，不用 API Key）。

走浏览器扩展（browser-extension，v0.2 起有 rpc）：在一个这个网站的标签页里开一个新对话（v0.4.1 起和出图共用标签页：
先用 studio 用过的，再借你开着、闲着的，都没有才新开），
归属到某集某一步的调用（剧本、分镜每段、推荐角色）重新生成时续接上次那条对话（backend/engines/browser/
conversations.py；超过 20 轮、对话打不开、设置里关掉时新开）。把 system 和对话拼成一条提示词发出去，
等回复写完、文字稳定，读这次新增的那条回复的全文；json_mode 时要求「只输出 JSON，放在 ```json 代码块里」，解析交给网关（extract_json，不合格按网关的重试）。页面流程见
backend/engines/browser/chat.py。

同一个网站同时只跑一件事：和网页出图共用 BrowserRunner 的网站锁（出图在跑时排队等）。
适合写梗概、推荐角色这类短文本；剧本、分镜输出长，网页版可能截断或超时，优先用 API 或本机 Claude。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from backend.config import ProviderCfg
from backend.engines.browser.errors import BrowserError, excerpt
from backend.llm.base import LLMError, LLMResult
from backend.queue.trace import annotate_last

logger = logging.getLogger(__name__)

# 文本模型 provider id → 网站（backend/engines/browser/sites 的 key）
# Muse（muse.ai）在 side chat 里问（muse.ai/thread/new），不碰用户的主对话；2026-10-11 实测：一轮回复是一个气泡，
# 12 个分镜约 2600 字的 JSON 31 秒写完、能直接解析，没掺进用户的个人上下文
WEB_PROVIDERS: dict[str, str] = {"web_chatgpt": "chatgpt", "web_gemini": "gemini", "web_claude": "claude",
                                 "web_muse": "muse"}
WEB_TIMEOUT = 300.0          # 等回复写完的最长秒数（长输出够用；剧本、分镜更长的不建议用网页版）
LOCK_WAIT = 900.0            # 网站被出图任务占着时最多排队等多久
JSON_RULE = ("【输出格式】只输出一个 JSON 对象，放在 ```json 代码块里；代码块外面不要写任何文字，不要解释。"
             "直接回答，不要联网搜索，不要画图，不要调用任何工具。")
WEB_HINT = ("网页版适合写梗概、推荐角色这类短文本；剧本、分镜输出长，网页版可能截断或超时，"
            "优先用 API 或本机 Claude")


def ensure_web_providers(llm_cfg) -> None:
    """config.yaml 里没写的话，把三个网页文本模型加进 llm.providers（设置页下拉、按步骤设置都能选）。"""
    have = {p.id for p in llm_cfg.providers}
    have_sites = {p.site for p in llm_cfg.providers if p.kind == "web"}
    for pid, site in WEB_PROVIDERS.items():
        if pid in have or site in have_sites:
            continue
        llm_cfg.providers.append(ProviderCfg(id=pid, kind="web", site=site, timeout=WEB_TIMEOUT))


def web_prompt(system: str, messages: list[dict], *, json_mode: bool = True) -> str:
    """system 放前面，接着是对话（只有一条就是那条；重试时带上上一次的输出和要改的地方），最后是输出格式要求。"""
    if len(messages) == 1:
        convo = str(messages[0]["content"])
    else:
        label = {"user": "【用户】", "assistant": "【你上一次的回答】"}
        convo = "\n\n".join(f"{label.get(m['role'], m['role'])}\n{m['content']}" for m in messages)
    parts = [s for s in ((system or "").strip(), "---", convo.strip()) if s]
    if json_mode:
        parts += ["---", JSON_RULE]
    return "\n\n".join(parts)


def _label(site: str, runner=None) -> str:
    spec = (getattr(runner, "sites", None) or {}).get(site)
    return f"{spec.label if spec else site} 网页"


def web_status(runner, site: str) -> tuple[bool, str | None, str | None]:
    """设置页下拉用：(能不能用, 原因, 下拉里括号里的短说明)。"""
    label = _label(site, runner)
    if runner is None:
        return False, "studio 没有初始化网页浏览器", "现在不可用"
    if getattr(runner, "transport", "extension") != "extension":
        return False, "网页文本模型只支持浏览器扩展方式（config.yaml 的 browser.transport 要是 extension）", "要用浏览器扩展"
    hub = runner.hub
    problem = hub.text_problem(site, label)
    if problem:
        short = "扩展没连上" if not hub.connected else "扩展需要重新加载"
        return False, problem, short
    st = hub.site_status().get(site)
    if st is not None and st.get("logged_in") is False:
        spec = runner.sites.get(site)
        host = spec.host if spec else site
        return False, f"没登录 {host}：在装了扩展的浏览器里打开 {host} 登录（只需一次）", "没登录"
    return True, None, None


class WebChatProvider:
    """LLMProvider：在网页上问。browser 是 BrowserRunner（或返回它的函数）；没有时调用就报错。"""

    accepts_log = True
    accepts_scope = True      # 网关把这次 LLM 调用的 key 传进来（scope_key），按集、按步骤续接网页会话

    def __init__(self, cfg: ProviderCfg, *, browser: Any = None,
                 clock: Callable[[], float] = time.monotonic, chat: Callable[..., str] | None = None,
                 driver_factory: Callable[[Any, int], Any] | None = None):
        if cfg.site not in WEB_PROVIDERS.values():
            raise ValueError(f"网页文本模型 {cfg.id} 的 site 要是 {'/'.join(WEB_PROVIDERS.values())}，现在是 {cfg.site!r}")
        self.id = cfg.id
        self.cfg = cfg
        self.site = cfg.site
        self._browser = browser
        self._clock = clock
        self._chat = chat
        self._driver_factory = driver_factory
        self.supports_images = cfg.vision is not False

    def runner(self):
        b = self._browser
        return b() if callable(b) and not hasattr(b, "hub") else b

    def complete(self, system: str, messages: list[dict], *, json_mode: bool = True, images=None,
                 log: Callable[[str], None] | None = None, scope_key: str | None = None) -> LLMResult:
        from backend.engines.browser import chat as chat_mod
        from backend.engines.browser import conversations as conv

        runner = self.runner()
        label = _label(self.site, runner)

        def say(m: str) -> None:
            logger.info("[%s] %s", self.id, m)
            if log is not None:
                log(m)

        # 扩展报的「没登录」可能已经过时：不在这里拦，页面上再判断
        if runner is None:
            raise LLMError(f"{label}用不了：网页文本模型只能在 studio 服务里用（要连着浏览器扩展）")
        if getattr(runner, "transport", "extension") != "extension":
            raise LLMError(f"{label}用不了：网页文本模型只支持浏览器扩展方式（config.yaml 的 browser.transport 要是 extension）")
        problem = runner.hub.text_problem(self.site, label)
        if problem:
            raise LLMError(f"{label}用不了：{problem}")
        spec = runner.sites[self.site]
        prompt = web_prompt(system, messages, json_mode=json_mode)
        timeout = float(self.cfg.timeout or WEB_TIMEOUT)
        ask = self._chat or chat_mod.ask
        make_driver = self._driver_factory or (lambda hub, tab: chat_mod.ExtTabDriver(hub, tab))
        t0 = self._clock()
        try:
            with runner.site_lock(self.site, label, say, wait_timeout=LOCK_WAIT):
                info = runner.hub.rpc("acquire_tab", {"site": self.site}) or {}
                tab = info.get("tab_id")
                if tab is None:
                    raise LLMError(f"{label}：浏览器扩展没给出标签页")
                say(f"{label}：{info.get('how') or '用一个标签页'}")
                # 网页会话续接：只有归属到某集某一步的调用（剧本、分镜、推荐角色）才续接，写梗概等每次新对话
                scope = conv.current_scope()
                scope = scope.child(scope_key or "") if scope is not None and scope.for_text else None
                book = getattr(runner, "conversations", None)
                plan = book.plan(self.site, scope) if (book is not None and scope is not None) else None
                extra: dict = {}
                page: dict = {}
                if plan is not None:
                    extra = {"conversation_url": plan.url, "info": page,
                             "continue_prefix": conv.CONTINUE_TEXT_PREFIX}
                text = ask(make_driver(runner.hub, tab), spec, prompt, list(images or ()), timeout=timeout,
                           log=say, fresh=bool(info.get("fresh")), **extra)
                if plan is not None:
                    got = book.record(plan, spec, page.get("url"), continued=page.get("continued"),
                                      note=page.get("note") or "")
                    say(conv.describe(got, label))
                    annotate_last("llm", {"conversation": got})
        except BrowserError as e:
            raise LLMError(str(e)) from None
        if json_mode and "{" not in text:
            if any(p.search(text) for p in spec.quota_patterns):
                raise LLMError(f"{label}用量到上限或被限流：{excerpt(text)}。等一阵再试，或换别的文本模型")
            if any(p.search(text) for p in spec.refusal_patterns):
                raise LLMError(f"{label}拒绝回答：{excerpt(text)}。换个文本模型再试")
        return LLMResult(text=text, model=label, seconds=self._clock() - t0)
