"""扩展桥的机械冒烟（不碰真网站、不碰你的浏览器配置）：

1. 在进程里起一个临时 studio 后端（临时数据目录，端口 18799）；
2. 用 Playwright 自带的 Chromium、一个临时配置目录加载 browser-extension（--load-extension），
   把扩展的 studio 地址改成 ws://127.0.0.1:18799/api/browser/ext；
3. 把 https://chatgpt.com/ 拦成一个假的 ChatGPT 页面（输入框、发送/停止按钮、隐藏的 file input、
   8 秒后在助手消息里出一张 1024×1536 的图），并给 chatgpt.com 加一个假的登录 cookie；
4. 后端用 gpt_image 出一张 9:16 的图（带一张参考图），检查：扩展连上、报了状态、参考图传进了
   file input、提示词输进了输入框、原图取回来并被居中裁成 864×1536。
5. 生成器页（v0.5 起）：把 https://jimeng.jianying.com/ai-tool/generate 拦成一个假的即梦生成页（Arco 风格的
   三个下拉、画幅 / 时长弹层、两个隐藏的上传框、tiptap 输入框、生成按钮、结果区 6 秒后出 <video>），后端用
   jimeng_video 出一段（首帧 + 尾帧、8 秒 → 选 10 秒），检查选项都选对了、两帧传进了对应的上传框、mp4 取回来。
   本机没有 ffmpeg 时跳过这一段（结果视频要 ffprobe 读得出来）。

用法（仓库根目录）：uv run --no-sync python browser-extension/dev/smoke.py
"""
from __future__ import annotations

import io
import shutil
import subprocess
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
from backend.engines.base import ImageRequest, VideoRequest

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


# 假的即梦生成页：结构照 2026-10-12 实测的类名（lv-select / lv-select-option / lv-btn / lv-popover / ProseMirror /
# credit-amount-text / actual-credits），没实测的部分（上传位、生成按钮、结果卡片）照 sites/jimeng.js 里猜的选择器
FAKE_JIMENG = """<!doctype html><html><head><meta charset="utf-8"><title>即梦</title>
<style>.lv-select,.lv-btn{display:inline-block;margin:4px;padding:4px;border:1px solid #ccc}</style></head>
<body>
  <span class="credit-amount-text">100</span>
  <div id="bar">
    <div role="combobox" class="lv-select">Agent 模式</div>
    <div role="combobox" class="lv-select">Seedance 2.0 mini</div>
    <div role="combobox" class="lv-select">全能参考</div>
    <button class="lv-btn" id="ratio">16:9 720P 1</button>
    <button class="lv-btn" id="dur">5s</button>
  </div>
  <div class="upload-slot" id="slots">
    <input type="file" id="f0" style="display:none"><input type="file" id="f1" style="display:none">
  </div>
  <div role="textbox" class="ProseMirror" contenteditable="true" style="min-height:40px;border:1px solid #ccc"></div>
  <span class="actual-credits">30</span>
  <button class="submit-button" id="go">生成</button>
  <div id="records"></div>
<script>
  window.__got = {picked: [], files: [], prompt: ''};
  const MENUS = [
    ['Agent 模式', '图片生成', '视频生成', '数字人'],
    ['Seedance 2.0 mini', 'Seedance 2.0 Fast', 'Seedance 2.0'],
    ['全能参考', '首尾帧', '智能多帧'],
  ];
  const close = () => document.querySelectorAll('.lv-select-popup, .lv-popover').forEach((e) => e.remove());
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  document.querySelectorAll('[role=combobox]').forEach((box, i) => box.addEventListener('click', () => {
    close();
    const pop = document.createElement('div');
    pop.className = 'lv-select-popup';
    for (const t of MENUS[i]) {
      const o = document.createElement('div');
      o.className = 'lv-select-option';
      o.textContent = t;
      o.addEventListener('click', () => { box.textContent = t; window.__got.picked.push(t); close(); });
      pop.appendChild(o);
    }
    document.body.appendChild(pop);
  }));
  document.getElementById('ratio').addEventListener('click', () => {
    close();
    const pop = document.createElement('div');
    pop.className = 'lv-popover';
    for (const t of ['16:9', '9:16', '1:1']) {
      const b = document.createElement('button');
      b.textContent = t;
      b.addEventListener('click', () => {
        document.getElementById('ratio').textContent = t + ' 720P 1'; window.__got.picked.push(t); close();
      });
      pop.appendChild(b);
    }
    document.body.appendChild(pop);
  });
  document.getElementById('dur').addEventListener('click', () => {
    close();
    const pop = document.createElement('div');
    pop.className = 'lv-popover';
    pop.innerHTML = '<div class="lv-input-number"><input value="5"></div>';
    pop.querySelector('input').addEventListener('change', (e) => {
      document.getElementById('dur').textContent = e.target.value + 's'; window.__got.picked.push(e.target.value + 's');
    });
    document.body.appendChild(pop);
  });
  for (const id of ['f0', 'f1']) document.getElementById(id).addEventListener('change', (e) => {
    for (const f of e.target.files) {
      window.__got.files.push(id + ':' + f.name);
      const img = document.createElement('img');
      img.src = URL.createObjectURL(f); img.width = 48; img.height = 48;
      document.getElementById('slots').appendChild(img);
    }
  });
  document.getElementById('go').addEventListener('click', () => {
    window.__got.prompt = document.querySelector('.ProseMirror').innerText;
    const card = document.createElement('div');
    card.className = 'video-record';
    card.textContent = '生成中';
    document.getElementById('records').appendChild(card);
    setTimeout(() => { card.innerHTML = '<p>已完成</p><video src="/fake/video.mp4"></video>'; }, 6000);
  });
</script></body></html>"""


def mp4(path: Path) -> bytes | None:
    ff = shutil.which("ffmpeg")
    if ff is None:
        return None
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=72x128:d=1:r=24",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest", "-c:v", "mpeg4",
                    "-c:a", "aac", str(path)], check=True)
    return path.read_bytes()


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

            # 生成器页：假的即梦，首帧 + 尾帧、8 秒（选 10 秒档）
            video = mp4(tmp / "fake.mp4")
            if video is None:
                print("没有 ffmpeg，跳过生成器页（即梦）这一段")
            else:
                # 不碰真网站：即梦域名下别的请求（favicon 之类）一律拦掉（后注册的路由先匹配）
                ctx.route("https://*.jianying.com/**", lambda r: r.abort())
                ctx.route("https://jimeng.jianying.com/fake/video.mp4",
                          lambda r: r.fulfill(status=200, content_type="video/mp4", body=video))
                ctx.route("https://jimeng.jianying.com/ai-tool/generate*",
                          lambda r: r.fulfill(status=200, content_type="text/html", body=FAKE_JIMENG))
                ctx.add_cookies([{"name": "sessionid", "value": "fake", "domain": ".jianying.com", "path": "/",
                                  "secure": True, "httpOnly": True}])
                end = tmp / "end.png"
                end.write_bytes(png(256, 256, (40, 200, 80)))
                vcall = app.state.services.engines.video("jimeng_video").build(
                    VideoRequest(prompt="灯笼 smoke test", first_frame=ref, end_frame=end, seconds=8.0))
                vfiles = run_job(vcall, tmp / "run3")
                print("即梦结果：", vfiles[0], vfiles[0].stat().st_size)
                assert vfiles[0].name == "jimeng_cn_00.mp4" and vfiles[0].read_bytes() == video
                jp = next(p for p in ctx.pages if "jimeng.jianying.com" in p.url)
                got = jp.evaluate("window.__got")
                print("假即梦页面收到：", got)
                assert got["picked"] == ["视频生成", "首尾帧", "9:16", "10s"], got["picked"]
                assert got["files"] == ["f0:chen.png", "f1:end.png"], got["files"]
                assert "灯笼 smoke test" in got["prompt"]
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
