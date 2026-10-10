"""网页出图流程（flow.generate）：用假的页面跑新对话、上传、提交、等图、下载和各种出错。"""
import threading

import pytest

from backend.engines.browser import flow
from backend.engines.browser.errors import BrowserError
from backend.engines.browser.sites import CHATGPT, GEMINI
from backend.queue.errors import JobCanceled
from backend.tests.fakes.fake_browser import FakePage, png

GPT_PARTIAL = "https://chatgpt.com/backend-api/estuary/content?id=file_partial"
GPT_FINAL = "https://chatgpt.com/backend-api/estuary/content?id=file_final"
GEM_FINAL = "https://lh3.googleusercontent.com/gg/final=s1024"


def run(page, spec=CHATGPT, refs=(), timeout=300, cancel=None):
    logs, prog = [], []
    out = flow.generate(page, spec, "PROMPT", list(refs), timeout=timeout, cancel=cancel,
                        log=logs.append, progress=lambda v, m: prog.append((v, m)),
                        clock=page.clock, poll=1.0)
    return out, logs, prog


def gpt_success_script(spec=CHATGPT):
    return [(1, lambda p: p.show(spec.stop_button)),
            (20, lambda p: p.add_result(GPT_PARTIAL, 1024, 1536)),
            (60, lambda p: p.finish(GPT_FINAL, 1024, 1536))]


def test_chatgpt_generates_and_fetches_full_image():
    page = FakePage(CHATGPT).after_submit(gpt_success_script())
    out, logs, prog = run(page)
    assert len(out) == 1 and out[0].src == GPT_FINAL and out[0].via == "fetch"
    assert (out[0].width, out[0].height, out[0].format) == (1024, 1536, "PNG")
    assert ("goto", CHATGPT.new_chat_url) in page.calls and ("type", "PROMPT") in page.calls
    # 半成品不算：停止按钮消失、图片地址稳定 settle_seconds 后才算出完
    assert ("fetch", GPT_PARTIAL) not in page.calls
    assert page.t >= 60 + CHATGPT.settle_seconds
    assert prog[0] == (0, 100) and prog[-1] == (100, 100)
    assert any(0 < v < 100 for v, _ in prog)
    assert any("已提交" in m for m in logs) and any("1024×1536" in m for m in logs)


def test_uploads_refs_via_file_input_and_excludes_them_from_results(tmp_path):
    refs = [tmp_path / "chen.png", tmp_path / "yu.png"]
    for r in refs:
        r.write_bytes(png(64, 64))
    page = FakePage(CHATGPT)

    def echo_refs(p):  # 网站把用户上传的参考图也显示在对话里，地址和结果图一样的格式
        p.mine.append(flow.ImageInfo(GPT_PARTIAL, 1024, 1024, True))
        p.add_result(GPT_PARTIAL, 1024, 1024)

    page.after_submit([(1, lambda p: p.show(CHATGPT.stop_button)), (2, echo_refs),
                       (30, lambda p: (p.hide(CHATGPT.stop_button), p.add_result(GPT_FINAL)))])
    out, logs, _ = run(page, refs=refs)
    assert ("set_files", ["chen.png", "yu.png"]) in page.calls
    assert [o.src for o in out] == [GPT_FINAL]
    assert any("上传 2 张参考图" in m for m in logs)


def test_refs_echoed_right_after_submit_are_not_results(tmp_path):
    ref = tmp_path / "chen.png"
    ref.write_bytes(png(64, 64))
    echo = "https://chatgpt.com/backend-api/estuary/content?id=file_upload"
    page = FakePage(CHATGPT)
    page.after_submit([(1, lambda p: (p.show(CHATGPT.stop_button), p.add_result(echo, 1024, 1024))),
                       (40, lambda p: (p.hide(CHATGPT.stop_button), p.add_result(GPT_FINAL)))])
    out, _, _ = run(page, refs=[ref])
    assert [o.src for o in out] == [GPT_FINAL]


def test_upload_falls_back_to_file_chooser(tmp_path):
    ref = tmp_path / "chen.png"
    ref.write_bytes(png(64, 64))
    page = FakePage(GEMINI, has_file_input=False)
    page.downloads[GEM_FINAL] = png(2048, 2048)
    page.after_submit([(1, lambda p: p.show(GEMINI.stop_button)),
                       (15, lambda p: p.finish(GEM_FINAL, 1024, 1024))])
    out, _, _ = run(page, GEMINI, refs=[ref])
    assert ("choose_files", ["chen.png"]) in page.calls
    # Gemini 优先点「下载原尺寸图片」：拿到的是 2048 的原图，不是页面里 1024 的缩小图
    assert out[0].via == "download" and out[0].width == 2048


def test_upload_without_any_entry_is_a_clear_error(tmp_path):
    ref = tmp_path / "chen.png"
    ref.write_bytes(png(64, 64))
    page = FakePage(GEMINI, has_file_input=False, has_upload_menu=False)
    with pytest.raises(BrowserError) as e:
        run(page, GEMINI, refs=[ref])
    assert e.value.kind == "page" and "sites/gemini.py" in str(e.value)


def test_gemini_download_button_missing_falls_back_to_src():
    page = FakePage(GEMINI).after_submit([(1, lambda p: p.show(GEMINI.stop_button)),
                                          (10, lambda p: p.finish(GEM_FINAL, 1024, 1024))])
    out, _, _ = run(page, GEMINI)
    assert out[0].via == "fetch" and ("download", GEM_FINAL) in page.calls


def test_not_logged_in():
    page = FakePage(CHATGPT, logged_in=False)
    with pytest.raises(BrowserError) as e:
        run(page)
    assert e.value.kind == "not_logged_in"
    assert "没有登录" in str(e.value) and "打开浏览器登录" in str(e.value)
    assert not any(c[0] == "type" for c in page.calls)


def test_missing_session_cookie_counts_as_not_logged_in():
    page = FakePage(GEMINI)
    page.cookies = set()
    with pytest.raises(BrowserError) as e:
        run(page, GEMINI)
    assert e.value.kind == "not_logged_in" and "gemini.google.com" in str(e.value)


def test_prompt_box_never_appears_points_to_selectors():
    page = FakePage(CHATGPT, prompt_box=False)
    with pytest.raises(BrowserError) as e:
        run(page)
    assert e.value.kind == "page" and "prompt_box" in str(e.value) and "sites/chatgpt.py" in str(e.value)


def test_quota_message_in_reply():
    page = FakePage(CHATGPT).after_submit([
        (1, lambda p: p.show(CHATGPT.stop_button)),
        (3, lambda p: (p.hide(CHATGPT.stop_button),
                       setattr(p, "reply", "You've hit the plan limit for image generations requests. "
                                           "You can create more images when the limit resets in 20 hours.")))])
    with pytest.raises(BrowserError) as e:
        run(page)
    assert e.value.kind == "quota" and "上限" in str(e.value) and "plan limit" in str(e.value)


def test_quota_message_in_alert_banner():
    page = FakePage(GEMINI).after_submit([(2, lambda p: setattr(p, "alert", "你已达到今天的图片生成上限，请明天再来"))])
    with pytest.raises(BrowserError) as e:
        run(page, GEMINI)
    assert e.value.kind == "quota"


def test_content_refused():
    page = FakePage(CHATGPT).after_submit([
        (1, lambda p: p.show(CHATGPT.stop_button)),
        (5, lambda p: (p.hide(CHATGPT.stop_button),
                       setattr(p, "reply", "I can’t help create that image because it may violate our "
                                           "content policies.")))])
    with pytest.raises(BrowserError) as e:
        run(page)
    assert e.value.kind == "refused" and "拒绝" in str(e.value)
    assert page.t >= 5 + CHATGPT.no_image_grace


def test_text_reply_without_image():
    page = FakePage(GEMINI).after_submit([
        (1, lambda p: p.show(GEMINI.stop_button)),
        (4, lambda p: (p.hide(GEMINI.stop_button), setattr(p, "reply", "好的！你想要什么风格？")))])
    with pytest.raises(BrowserError) as e:
        run(page, GEMINI)
    assert e.value.kind == "no_image" and "你想要什么风格" in str(e.value)


def test_timeout_stops_generation():
    page = FakePage(CHATGPT).after_submit([(1, lambda p: p.show(CHATGPT.stop_button))])
    with pytest.raises(BrowserError) as e:
        run(page, timeout=30)
    assert e.value.kind == "timeout" and "30 秒" in str(e.value)
    assert ("click", CHATGPT.stop_button) in page.calls


def test_small_images_and_foreign_srcs_are_ignored():
    page = FakePage(CHATGPT)
    page.after_submit([
        (1, lambda p: (p.add_result("https://chatgpt.com/backend-api/estuary/content?id=avatar", 64, 64),
                       p.add_result("https://cdn.example.com/banner.png", 1600, 900))),
        (8, lambda p: p.add_result(GPT_FINAL))])
    out, _, _ = run(page)
    assert [o.src for o in out] == [GPT_FINAL]


def test_cancel_clicks_stop_and_raises():
    cancel = threading.Event()
    page = FakePage(CHATGPT).after_submit([(1, lambda p: p.show(CHATGPT.stop_button)),
                                           (5, lambda p: cancel.set())])
    with pytest.raises(JobCanceled):
        run(page, cancel=cancel)
    assert ("click", CHATGPT.stop_button) in page.calls


def test_download_failure_is_reported():
    page = FakePage(CHATGPT).after_submit([(2, lambda p: p.add_result(GPT_FINAL))])
    page.fetch_fails.add(GPT_FINAL)
    with pytest.raises(BrowserError) as e:
        run(page)
    assert e.value.kind == "page" and "下载原图失败" in str(e.value)


def test_send_button_missing_falls_back_to_enter_then_errors():
    page = FakePage(CHATGPT)
    page.hide(CHATGPT.send_button)
    with pytest.raises(BrowserError) as e:
        run(page)
    assert ("press", "Enter") in page.calls and "send_button" in str(e.value)
