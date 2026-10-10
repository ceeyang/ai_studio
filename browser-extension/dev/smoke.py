"""扩展桥的机械冒烟（不碰真网站、不碰你的浏览器配置）：

1. 在进程里起一个临时 studio 后端（临时数据目录，端口 18799）；
2. 用 Playwright 自带的 Chromium、一个临时配置目录加载 browser-extension（--load-extension），
   把扩展的 studio 地址改成 ws://127.0.0.1:18799/api/browser/ext；
3. 把 https://chatgpt.com/ 拦成一个假的 ChatGPT 页面（输入框、发送/停止按钮、隐藏的 file input、
   8 秒后在助手消息里出一张 1024×1536 的图），并给 chatgpt.com 加一个假的登录 cookie；
4. 后端用 gpt_image 出一张 9:16 的图（带一张参考图），检查：扩展连上、报了状态、参考图传进了
   file input、提示词输进了输入框、原图取回来并被居中裁成 864×1536。

用法（仓库根目录）：uv run --no-sync python browser-extension/dev/smoke.py
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from backend.app.main import create_app
from backend.config import ComfyCfg, Config, LLMCfg, ProviderCfg, ServerCfg
from backend.engines.base import ImageRequest

PORT = 18799
EXT = REPO / "browser-extension"

FAKE_CHATGPT = """<!doctype html><html><head><meta charset="utf-8"><title>ChatGPT</title></head>
<body><main>
  <div id="thread"></div>
  <form id="f" onsubmit="return false">
    <div id="thumbs"></div>
    <input type="file" accept="image/*" multiple style="display:none" id="files">
    <div id="prompt-textarea" contenteditable="true" style="min-height:40px;border:1px solid #ccc"></div>
    <button type="button" data-testid="send-button" id="send">发送</button>
  </form>
</main>
<script>
  window.__got = {files: [], prompt: ''};
  document.getElementById('files').addEventListener('change', (e) => {
    for (const f of e.target.files) {
      window.__got.files.push(f.name + ':' + f.size);
      const img = document.createElement('img');
      img.src = URL.createObjectURL(f); img.width = 48; img.height = 48;
      document.getElementById('thumbs').appendChild(img);
    }
  });
  document.getElementById('send').addEventListener('click', () => {
    window.__got.prompt = document.getElementById('prompt-textarea').innerText;
    document.title = 'PROMPT:' + window.__got.prompt.slice(0, 40) + ' FILES:' + window.__got.files.join(',');
    const stop = document.createElement('button');
    stop.setAttribute('data-testid', 'stop-button'); stop.textContent = '停止';
    document.getElementById('f').appendChild(stop);
    setTimeout(() => {
      stop.remove();
      const art = document.createElement('article');
      art.setAttribute('data-turn', 'assistant');
      art.innerHTML = '<p>Here is your image.</p><img alt="Generated image" src="/backend-api/estuary/content?id=file_final">';
      document.getElementById('thread').appendChild(art);
    }, 8000);
  });
</script></body></html>"""


def png(w, h, color=(30, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def main() -> int:
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp(prefix="studio-ext-smoke-"))
    cfg = Config(server=ServerCfg(port=PORT, data_dir=tmp / "data"), log_dir=tmp / "logs",
                 comfyui=ComfyCfg(url="http://127.0.0.1:9", max_retries=1),
                 llm=LLMCfg(default="manual", providers=[ProviderCfg(id="manual", kind="manual")]),
                 frontend_dist=None)
    app = create_app(cfg, start_workers=False, configure_logging=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.1)
    ok = False
    try:
        result_png = png(1024, 1536)
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                str(tmp / "profile"), channel="chromium", headless=True,
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"])
            ctx.route("https://chatgpt.com/backend-api/estuary/content*",
                      lambda r: r.fulfill(status=200, content_type="image/png", body=result_png))
            ctx.route("https://chatgpt.com/", lambda r: r.fulfill(status=200, content_type="text/html",
                                                                body=FAKE_CHATGPT))
            ctx.add_cookies([{"name": "__Secure-next-auth.session-token", "value": "fake", "domain": "chatgpt.com",
                              "path": "/", "secure": True, "httpOnly": True}])
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
            print("扩展 service worker：", sw.url)
            sw.evaluate(f"chrome.storage.local.set({{serverUrl: 'ws://127.0.0.1:{PORT}/api/browser/ext'}})")
            hub = app.state.services.browser.hub
            end = time.monotonic() + 20
            while not hub.connected and time.monotonic() < end:
                time.sleep(0.2)
            assert hub.connected, "扩展 20 秒内没连上"
            print("已连上：", hub.info())

            ref = tmp / "chen.png"
            ref.write_bytes(png(256, 256, (200, 80, 40)))
            call = app.state.services.engines.image("gpt_image").build(
                ImageRequest(prompt="陈师傅 smoke test", aspect="9:16", refs=[ref]))
            logs: list[str] = []
            pump = ctx.pages[0]

            def run_job(c, run_dir):
                """路由（假页面）只在主线程待在 Playwright 调用里时才处理：出图放到线程里，主线程一直泵事件。"""
                box: dict = {}

                def go():
                    try:
                        box["files"] = app.state.services.browser.run(
                            c, run_dir=run_dir, cancel=threading.Event(),
                            log=lambda m: (logs.append(m), print("  [任务]", m)))
                    except BaseException as e:  # noqa: BLE001
                        box["error"] = e

                job = threading.Thread(target=go, daemon=True)
                job.start()
                while job.is_alive():
                    pump.wait_for_timeout(200)
                if "error" in box:
                    raise box["error"]
                return box["files"]

            files = run_job(call, tmp / "run")
            im = Image.open(files[0])
            print("结果：", files[0], im.size)
            assert im.size == (864, 1536), im.size
            page = next(p for p in ctx.pages if "chatgpt.com" in p.url)
            got = page.evaluate("window.__got")
            print("假页面收到：", {"files": got["files"], "prompt": got["prompt"][:80] + "…"})
            assert got["files"] and got["files"][0].startswith("chen.png:"), got
            assert "陈师傅 smoke test" in got["prompt"] and got["prompt"].startswith("Generate one image now")
            # 第二张：复用上次出图的标签页，切到新对话，不带参考图，1:1
            call2 = app.state.services.engines.image("gpt_image").build(ImageRequest(prompt="second", aspect="1:1"))
            files2 = run_job(call2, tmp / "run2")
            assert any("用上次出图的那个标签页" in m for m in logs), logs
            assert Image.open(files2[0]).size == (1024, 1024), Image.open(files2[0]).size
            print("第二张：", files2[0], Image.open(files2[0]).size)
            hub.request_status()
            pump.wait_for_timeout(2000)
            st = app.state.services.browser.status()
            print("状态：", st["sites"])
            assert st["sites"]["chatgpt"]["tabs"] >= 1 and st["sites"]["chatgpt"]["logged_in"] is True
            ctx.close()
        ok = True
        print("扩展桥冒烟通过")
    finally:
        server.should_exit = True
        th.join(10)
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
