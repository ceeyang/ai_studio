"""配置加载：config.yaml + .env。

相对路径一律相对配置文件所在目录解析；没写的路径用仓库内默认值。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]


class ServerCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8700
    data_dir: Path = ROOT / "projects"


class ComfyCfg(BaseModel):
    url: str = "http://127.0.0.1:8188"
    models_dir: Path | None = None  # 只给 doctor 检查模型文件用
    timeout: float = 30.0
    max_retries: int = 6
    # 队列空闲超过这么多秒（自上一个 GPU 任务结束算起）就自动释放 ComfyUI 的显存/内存；0 关闭。
    idle_free_seconds: float = 120.0


class ProviderCfg(BaseModel):
    id: str
    kind: Literal["openai", "claude", "manual"]
    base_url: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    # 跑在本机显卡上（LM Studio）：调用前要让 GPU 任务让路，调用后卸载模型
    local_gpu: bool = False
    unload_cmd: list[str] | None = None
    use_sdk: bool = True  # 仅 claude：先试 Agent SDK，失败退回 claude -p
    timeout: float = 1800.0

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None


class LLMCfg(BaseModel):
    default: str | None = None
    providers: list[ProviderCfg] = Field(default_factory=list)
    routes: dict[str, str] = Field(default_factory=dict)

    def provider(self, provider_id: str) -> ProviderCfg:
        for p in self.providers:
            if p.id == provider_id:
                return p
        raise KeyError(f"未配置 LLM provider {provider_id!r}，已有：{[p.id for p in self.providers]}")

    def provider_for(self, task: str) -> ProviderCfg:
        pid = self.routes.get(task) or self.default
        if not pid:
            raise KeyError(f"任务 {task!r} 没有配置 llm.routes，也没有 llm.default")
        return self.provider(pid)


class EngineCfg(BaseModel):
    enabled: bool = True
    params: dict = Field(default_factory=dict)


class FfmpegCfg(BaseModel):
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    font: str | None = None


def _windows_fonts() -> Path:
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


class SubtitlesCfg(BaseModel):
    """字幕和片尾卡的字体。libass 按字体族名找字；fonts_dir 里的 font_files 会拷进产物工作目录
    （滤镜串里的盘符冒号没法可靠转义，只能用相对路径的 fontsdir）。"""
    zh_font: str = "Microsoft YaHei"
    km_font: str = "Khmer UI"          # 也可以用 "Leelawadee UI"
    latin_font: str = "Arial"
    fonts_dir: Path = Field(default_factory=_windows_fonts)
    font_files: list[str] = Field(default_factory=lambda: [
        "msyh.ttc", "msyhbd.ttc", "KhmerUI.ttf", "KhmerUIb.ttf", "LeelawUI.ttf", "LeelaUIb.ttf",
        "arial.ttf", "arialbd.ttf"])


class DubCfg(BaseModel):
    """外挂配音（Edge TTS）。原音轨在台词窗口外压到 bed_db 当环境底，窗口内再压到 duck_db。"""
    bed_db: float = -14.0
    duck_db: float = -30.0
    max_rate: int = 35                 # 台词塞不进窗口时最多提速 +35%
    gap: float = 0.1                   # 每句至少比下一句（或镜头结尾）早结束这么多秒
    loudness: float | None = -14.0     # 配音版整体响度（LUFS，loudnorm）；null 不处理
    # dialogue.fit_khmer：按高棉语时长改写中文时，用哪个普通话音色实测（按角色的高棉语音色对应）
    zh_voices: dict[str, str] = Field(default_factory=lambda: {
        "km-KH-PisethNeural": "zh-CN-YunxiNeural", "km-KH-SreymomNeural": "zh-CN-XiaoxiaoNeural"})
    zh_voice_default: str = "zh-CN-YunxiNeural"
    fit_tolerance: float = 0.10        # 中文实测时长和高棉语相差在 ±10% 以内算合格
    fit_rounds: int = 3                # 最多改写几轮
    # align=detected：在 H3 音轨里找说话段（带通后按帧找浊音：能量 ≥ speech_min_db 且有周期性）
    speech_min_db: float = -45.0
    speech_periodicity: float = 0.5
    align_max_shift: float = 1.5       # 只在台词计划起点前后这么多秒内找说话段


class BrowserCfg(BaseModel):
    """网页图像引擎（gpt_image / gemini_image）用哪个浏览器出图。

    transport=extension（默认）：在你平时用的 Chrome/Edge 里装 browser-extension（开发者模式加载已解压的
    扩展程序），扩展用 WebSocket 连 /api/browser/ext，出图任务在那个浏览器里已登录的 ChatGPT / Gemini
    标签页里跑。studio 不启动浏览器、不读也不拷 cookie。

    transport=cdp（备用）：studio 自己管一个独立的 Chrome/Edge 配置目录，带远程调试端口启动，Playwright
    走 CDP 连上去。Chrome 136 起默认配置目录不许开远程调试，所以必须用单独的 user_data_dir；在那个窗口里
    登录一次 ChatGPT / Gemini，登录态就留在这个目录里。下面 executable 到 launch_timeout 只给 cdp 用。"""
    transport: Literal["extension", "cdp"] = "extension"
    # extension：只接受这个 id 的扩展连接（chrome://extensions 里看）；不写就接受任何 chrome-extension:// 来源
    extension_id: str | None = None
    reuse_conversation: bool = False   # true：在标签页当前的对话里接着发，不开新对话（会混进你的对话）
    reuse_user_tabs: bool = True       # 可以借用你已经开着、没在用的 ChatGPT / Gemini 标签页（会切到新对话）
    activate_tab: bool = False         # 出图时把标签页切到前台（后台标签页被浏览器冻结时打开）

    executable: Path | None = None     # 不写就先找 Chrome，再找 Edge
    user_data_dir: Path = ROOT / "browser-profile"   # 不进 git
    cdp_port: int = 9333
    headless: bool = False             # 要能看到窗口、手动登录，默认有界面
    launch_timeout: float = 30.0       # 启动后等调试端口响应的秒数


class Config(BaseModel):
    server: ServerCfg = Field(default_factory=ServerCfg)
    comfyui: ComfyCfg = Field(default_factory=ComfyCfg)
    llm: LLMCfg = Field(default_factory=LLMCfg)
    engines: dict[str, EngineCfg] = Field(default_factory=dict)
    ffmpeg: FfmpegCfg = Field(default_factory=FfmpegCfg)
    subtitles: SubtitlesCfg = Field(default_factory=SubtitlesCfg)
    dub: DubCfg = Field(default_factory=DubCfg)
    browser: BrowserCfg = Field(default_factory=BrowserCfg)
    workflows_dir: Path = ROOT / "workflows"
    log_dir: Path = ROOT / "logs"
    # 生产态由后端托管的前端构建目录（cd frontend; npm run build 生成）；不存在就只提供 API
    frontend_dist: Path | None = ROOT / "frontend" / "dist"

    def engine(self, name: str) -> EngineCfg:
        return self.engines.get(name) or EngineCfg()

    @property
    def data_dir(self) -> Path:
        return self.server.data_dir


def _absolutize(cfg: Config, base: Path) -> Config:
    def fix(p: Path | None) -> Path | None:
        if p is None:
            return None
        return p if p.is_absolute() else (base / p).resolve()

    cfg.server.data_dir = fix(cfg.server.data_dir)
    cfg.comfyui.models_dir = fix(cfg.comfyui.models_dir)
    cfg.workflows_dir = fix(cfg.workflows_dir)
    cfg.log_dir = fix(cfg.log_dir)
    cfg.frontend_dist = fix(cfg.frontend_dist)
    cfg.subtitles.fonts_dir = fix(cfg.subtitles.fonts_dir)
    cfg.browser.executable = fix(cfg.browser.executable)
    cfg.browser.user_data_dir = fix(cfg.browser.user_data_dir)
    return cfg


def load_config(path: Path | None = None) -> Config:
    """读配置。不给路径时依次找 $STUDIO_CONFIG、config.yaml、config.example.yaml。"""
    load_dotenv(ROOT / ".env")
    if path is None:
        env = os.environ.get("STUDIO_CONFIG")
        path = Path(env) if env else ROOT / "config.yaml"
        if not path.exists():
            path = ROOT / "config.example.yaml"
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return _absolutize(Config.model_validate(data), Path(path).resolve().parent)
