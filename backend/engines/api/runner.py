"""执行官方 API 引擎的 EngineCall（engines/api/engines.py），产物落到任务目录。

各家接口（按官方文档写；没用真实 Key 跑过的点见 docs 里的报告）：
- OpenAI 图像：没有参考图 POST {base}/images/generations（JSON：model、prompt、size、n）；
  有参考图 POST {base}/images/edits（multipart：model、prompt、size、多张 image[]）。
  返回 data[0].b64_json（gpt-image-1 总是 base64）；万一给的是 url，用不带 Key 的客户端下载。
- Gemini 图像：POST {base}/v1beta/models/{model}:generateContent，参考图作 inlineData，
  generationConfig.responseModalities=["IMAGE"]、imageConfig.aspectRatio。返回 parts[].inlineData。
- Veo：POST {base}/v1beta/models/{model}:predictLongRunning，instances[0] = {prompt, image:{bytesBase64Encoded,
  mimeType}}，parameters = {aspectRatio, negativePrompt?, durationSeconds?}；返回 operation.name，
  轮询 GET {base}/v1beta/{name} 到 done，取 response.generateVideoResponse.generatedSamples[0].video.uri 下载
  （同一个域名才带 Key 头）。
- Sora：POST {base}/videos（multipart：model、prompt、seconds、size、input_reference=首帧，首帧先裁缩到 size），
  轮询 GET {base}/videos/{id} 到 status=completed，下载 GET {base}/videos/{id}/content。
- Seedance（火山方舟）：POST {base}/contents/generations/tasks（JSON：model、content = 文本 + image_url
  （首帧 role=first_frame、尾帧 role=last_frame；参考图直出时每张 role=reference_image，data URI）、ratio、duration、resolution、generate_audio、
  watermark=false），轮询 GET {base}/contents/generations/tasks/{id} 到 succeeded / failed / expired，
  马上下载 content.video_url（24 小时有效，CDN 地址，不带 Key）。
- 可灵：POST {base}/v1/videos/image2video（JSON：model_name、image / image_tail 是不带 data: 前缀的 base64、
  prompt、negative_prompt、mode、duration、sound），轮询 GET {base}/v1/videos/image2video/{task_id} 到
  task_status = succeed / failed（原因在 task_status_msg），下载 data.task_result.videos[0].url。
  业务错误看响应顶层 code（≠ 0 就是错，HTTP 200 也可能是错）。
  细节和核对情况见 engines.py 里 SeedanceEngine / KlingEngine 的 docstring。

Key 每次现读（creds 回调），只放请求头；可灵 AK/SK 的 JWT 每个请求现签（api_http.KlingJwtAuth），
也只在请求头里。workflow.api.json / call.json 里没有 Key / SecretKey / JWT；错误信息都 scrub 过。
"""
from __future__ import annotations

import base64
import io
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from backend.domain.api_keys import SERVICES, Creds, MissingKey, env_creds, scrub
from backend.engines.base import EngineCall
from backend.engines.browser.runner import crop_to_aspect
from backend.llm.api_http import client, http_error_text, transport_error_text, vendor_hint
from backend.llm.base import image_media_type
from backend.queue.errors import JobCanceled

EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4"}
POLL_RETRIES = 3
SORA_STATE = {"queued": "排队中", "in_progress": "生成中", "completed": "完成", "failed": "失败"}
SEEDANCE_STATE = {"queued": "排队中", "running": "生成中", "succeeded": "完成", "failed": "失败",
                  "expired": "超时过期", "cancelled": "已取消"}
SEEDANCE_DONE = ("succeeded", "failed", "expired", "cancelled")
KLING_STATE = {"submitted": "已提交", "processing": "生成中", "succeed": "完成", "failed": "失败"}
KLING_PROMPT_MAX = 2500
# 可灵失败原因（task_status_msg）里出现这些词时按内容审核说明
MODERATION_WORDS = ("risk", "sensitive", "moderation", "安全", "审核", "违规", "敏感")


class ApiError(RuntimeError):
    """API 引擎调用失败。信息给用户看（中文、不含 Key）。"""


def _b64_file(path: Path) -> tuple[str, str]:
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as e:
        raise ApiError(f"读不到图片 {p}：{e}") from None
    try:
        mt = image_media_type(p)
    except Exception as e:  # noqa: BLE001
        raise ApiError(str(e)) from None
    return mt, base64.b64encode(data).decode("ascii")


def fit_image_bytes(path: Path, w: int, h: int) -> bytes:
    """首帧居中裁到 w:h 再缩到 w×h，PNG（Sora 要求 input_reference 和输出尺寸一样）。"""
    im = Image.open(path)
    im = im.convert("RGB")
    cropped = crop_to_aspect(im, f"{w}:{h}") or im
    out = io.BytesIO()
    cropped.resize((w, h), Image.LANCZOS).save(out, format="PNG")
    return out.getvalue()


class ApiRunner:
    def __init__(self, creds: Callable[[str], Creds] | None = None, *,
                 transport: httpx.BaseTransport | None = None, poll_interval: float = 5.0):
        self._creds = creds or env_creds
        self._transport = transport
        self.poll_interval = poll_interval

    # ------------------------------------------------------------ 公共
    def creds(self, service: str) -> Creds:
        try:
            c = self._creds(service)
            c.require()
        except MissingKey as e:
            raise ApiError(str(e)) from None
        return c

    def _http(self, c: Creds, timeout: float) -> httpx.Client:
        return client(c, timeout=timeout, transport=self._transport)

    def _send(self, c: Creds, http: httpx.Client, method: str, path: str, *, model: str | None = None,
              **kw) -> httpx.Response:
        try:
            r = http.request(method, path, **kw)
        except httpx.TransportError as e:
            raise ApiError(transport_error_text(c.service, c, e)) from None
        if r.status_code >= 400:
            raise ApiError(http_error_text(c.service, r, c, model))
        return r

    @staticmethod
    def _json(c: Creds, r: httpx.Response) -> dict:
        try:
            d = r.json()
        except ValueError:
            raise ApiError(scrub(f"{SERVICES[c.service].vendor} 返回的不是 JSON：{r.text[:300]}",
                                 *c.secrets())) from None
        return d if isinstance(d, dict) else {}

    def _download(self, c: Creds, http: httpx.Client, url: str) -> bytes:
        """同一个域名（API 自己的文件下载地址）带 Key 头；别的域名（CDN 签名地址）用不带 Key 的客户端，
        免得把 Key 发给第三方。"""
        same = urlparse(url).netloc in ("", urlparse(c.base_url).netloc)
        try:
            if same:
                r = http.get(url)
            else:
                with httpx.Client(timeout=300.0, transport=self._transport, follow_redirects=True,
                                  trust_env=self._transport is None) as bare:
                    r = bare.get(url)
        except httpx.TransportError as e:
            raise ApiError(transport_error_text(c.service, c, e)) from None
        if r.status_code >= 400:
            raise ApiError(f"下载 {SERVICES[c.service].vendor} 的产物失败（HTTP {r.status_code}）")
        return r.content

    @staticmethod
    def _write_meta(call: EngineCall, run_dir: Path) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "workflow.api.json").write_text(json.dumps(call.workflow, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
        (run_dir / "call.json").write_text(json.dumps(
            {"engine": call.engine, "runner": "api", "output_kind": call.output_kind,
             "model_family": call.model_family, "meta": call.meta, "dropped_refs": call.dropped_refs},
            ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ------------------------------------------------------------ 入口
    def run(self, call: EngineCall, *, run_dir: Path, cancel: threading.Event,
            on_progress: Callable[[int, int], None] | None = None,
            log: Callable[[str], None] | None = None) -> list[Path]:
        emit = log or (lambda m: None)
        run_dir = Path(run_dir)
        self._write_meta(call, run_dir)
        req = call.workflow
        service, op = req.get("service"), req.get("op")
        if cancel.is_set():
            raise JobCanceled()
        if (service, op) == ("openai", "image"):
            imgs = self._openai_image(req, emit)
        elif (service, op) == ("gemini", "image"):
            imgs = self._gemini_image(req, emit)
        elif (service, op) == ("gemini", "video"):
            return [self._veo(req, run_dir, cancel, emit, on_progress)]
        elif (service, op) == ("openai", "video"):
            return [self._sora(req, run_dir, cancel, emit, on_progress)]
        elif (service, op) == ("ark", "video"):
            return [self._seedance(req, run_dir, cancel, emit, on_progress)]
        elif (service, op) == ("kling", "video"):
            return [self._kling(req, run_dir, cancel, emit, on_progress)]
        else:
            raise ApiError(f"不认识的 API 调用：{service} / {op}")
        return self._save_images(imgs, run_dir, service, req.get("aspect"),
                                 bool(req.get("fit_aspect", True)), emit)

    # ------------------------------------------------------------ 图像
    def _openai_image(self, req: dict, emit) -> list[tuple[bytes, str]]:
        c = self.creds("openai")
        model = req["model"]
        refs = [Path(p) for p in req.get("refs") or []]
        with self._http(c, float(req.get("timeout") or 300)) as http:
            if refs:
                data = {"model": model, "prompt": req["prompt"], "size": req.get("size") or "auto", "n": "1"}
                if req.get("quality"):
                    data["quality"] = req["quality"]
                files = []
                for p in refs:
                    mt, _ = _b64_file(p)
                    files.append(("image[]", (p.name, p.read_bytes(), mt)))
                emit(f"OpenAI 图像：{model} · /images/edits · 参考图 {len(refs)} 张 · {data['size']}")
                r = self._send(c, http, "POST", "/images/edits", model=model, data=data, files=files)
            else:
                body = {"model": model, "prompt": req["prompt"], "size": req.get("size") or "auto", "n": 1}
                if req.get("quality"):
                    body["quality"] = req["quality"]
                emit(f"OpenAI 图像：{model} · /images/generations · {body['size']}")
                r = self._send(c, http, "POST", "/images/generations", model=model, json=body)
            d = self._json(c, r)
            out: list[tuple[bytes, str]] = []
            for item in d.get("data") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("b64_json"):
                    out.append((base64.b64decode(item["b64_json"]), "image/png"))
                elif item.get("url"):
                    out.append((self._download(c, http, item["url"]), "image/png"))
        if not out:
            raise ApiError("OpenAI 图像接口没有返回图片")
        return out

    def _gemini_image(self, req: dict, emit) -> list[tuple[bytes, str]]:
        c = self.creds("gemini")
        model = req["model"]
        parts: list[dict] = []
        for p in req.get("refs") or []:
            mt, data = _b64_file(Path(p))
            parts.append({"inlineData": {"mimeType": mt, "data": data}})
        parts.append({"text": req["prompt"]})
        body = {"contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"responseModalities": ["IMAGE"],
                                     "imageConfig": {"aspectRatio": req.get("aspect_ratio") or req.get("aspect")}}}
        emit(f"Gemini 图像：{model} · 画幅 {req.get('aspect')} · 参考图 {len(parts) - 1} 张")
        with self._http(c, float(req.get("timeout") or 300)) as http:
            r = self._send(c, http, "POST", f"/v1beta/models/{model}:generateContent", model=model, json=body)
            d = self._json(c, r)
        cands = d.get("candidates") or []
        out: list[tuple[bytes, str]] = []
        texts: list[str] = []
        for cand in cands:
            for part in ((cand or {}).get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if isinstance(inline, dict) and inline.get("data"):
                    mt = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                    out.append((base64.b64decode(inline["data"]), mt))
                elif part.get("text"):
                    texts.append(part["text"])
        if not out:
            block = (d.get("promptFeedback") or {}).get("blockReason")
            reason = (cands[0] or {}).get("finishReason") if cands else None
            why = (f"被拦下：{block}" if block else f"finishReason={reason}" if reason
                   else "没有给原因")
            said = f"；模型回了文字：{' '.join(texts)[:200]}" if texts else ""
            raise ApiError(f"Gemini 图像没有出图（{why}）{said}")
        return out

    @staticmethod
    def _save_images(imgs: list[tuple[bytes, str]], run_dir: Path, service: str, aspect: str | None,
                     fit: bool, emit) -> list[Path]:
        paths: list[Path] = []
        for i, (data, mt) in enumerate(imgs):
            raw = run_dir / f"{service}_{i:02d}_orig{EXT.get(mt, '.png')}"
            raw.write_bytes(data)
            final = raw
            if fit and aspect:
                try:
                    im = Image.open(io.BytesIO(data))
                    im.load()
                except Exception as e:  # noqa: BLE001
                    raise ApiError(f"{SERVICES[service].vendor} 返回的图片打不开：{e}") from None
                cropped = crop_to_aspect(im.convert("RGB") if im.mode not in ("RGB", "RGBA") else im, aspect)
                if cropped is not None:
                    final = run_dir / f"{service}_{i:02d}.png"
                    cropped.save(final)
                    emit(f"原图 {im.width}×{im.height} 不是 {aspect}，居中裁成 {cropped.width}×{cropped.height}")
            paths.append(final)
        if len(paths) > 1:
            emit(f"一次出了 {len(paths)} 张，候选用第一张，其余留在任务目录 {run_dir}")
        return paths

    # ------------------------------------------------------------ 轮询
    def _poll(self, c: Creds, get: Callable[[], dict], done: Callable[[dict], bool], *,
              cancel: threading.Event, timeout: float, label: str,
              on_state: Callable[[dict], None]) -> dict:
        deadline = time.monotonic() + timeout
        fails = 0
        while True:
            if cancel.is_set():
                raise JobCanceled()
            try:
                d = get()
            except ApiError as e:
                if "连不上" not in str(e):
                    raise
                fails += 1
                if fails >= POLL_RETRIES:
                    raise ApiError(f"{label}：连续 {fails} 次{e}") from None
                cancel.wait(self.poll_interval)
                continue
            fails = 0
            on_state(d)
            if done(d):
                return d
            if time.monotonic() >= deadline:
                raise ApiError(f"{label} 超过 {timeout:.0f} 秒没完成（云端任务可能还在跑，可以稍后重试）")
            cancel.wait(self.poll_interval)

    # ------------------------------------------------------------ Veo
    def _veo(self, req: dict, run_dir: Path, cancel, emit, on_progress) -> Path:
        c = self.creds("gemini")
        model = req["model"]
        mt, data = _b64_file(Path(req["first_frame"]))
        params: dict = {"aspectRatio": req.get("aspect")}
        if req.get("negative"):
            params["negativePrompt"] = req["negative"]
        if req.get("seconds"):
            params["durationSeconds"] = int(req["seconds"])
        body = {"instances": [{"prompt": req["prompt"], "image": {"bytesBase64Encoded": data, "mimeType": mt}}],
                "parameters": params}
        timeout = float(req.get("timeout") or 1200)
        with self._http(c, 120.0) as http:
            r = self._send(c, http, "POST", f"/v1beta/models/{model}:predictLongRunning", model=model, json=body)
            op = self._json(c, r)
            name = op.get("name")
            if not name:
                raise ApiError(f"Veo 没有返回任务名：{scrub(str(op)[:300], *c.secrets())}")
            emit(f"Veo 任务已提交：{model} · {req.get('aspect')}"
                 f"{' · ' + str(params['durationSeconds']) + ' 秒' if 'durationSeconds' in params else ''}")
            seen = {"t": time.monotonic()}

            def state(d):
                if not d.get("done") and time.monotonic() - seen["t"] >= 30:
                    seen["t"] = time.monotonic()
                    emit("Veo 生成中…")

            d = self._poll(c, lambda: self._json(c, self._send(c, http, "GET", f"/v1beta/{name}")),
                           lambda d: bool(d.get("done")), cancel=cancel, timeout=timeout,
                           label="Veo 任务", on_state=state)
            if d.get("error"):
                e = d["error"]
                raise ApiError(scrub(f"Veo 生成失败：{e.get('message') or e}", *c.secrets()))
            resp = (d.get("response") or {}).get("generateVideoResponse") or d.get("response") or {}
            samples = resp.get("generatedSamples") or resp.get("generatedVideos") or []
            uri = None
            for smp in samples:
                v = (smp or {}).get("video") or {}
                uri = v.get("uri")
                if uri:
                    break
            if not uri:
                why = resp.get("raiMediaFilteredReasons")
                raise ApiError(f"Veo 没有返回视频{f'（被安全过滤：{why}）' if why else ''}")
            emit("Veo 完成，下载视频")
            content = self._download(c, http, uri)
        if on_progress:
            on_progress(100, 100)
        dst = run_dir / "veo_00.mp4"
        dst.write_bytes(content)
        return dst

    # ------------------------------------------------------------ Sora
    def _sora(self, req: dict, run_dir: Path, cancel, emit, on_progress) -> Path:
        c = self.creds("openai")
        model = req["model"]
        size = req.get("size") or "720x1280"
        w, h = (int(x) for x in size.split("x"))
        first = Path(req["first_frame"])
        try:
            ref = fit_image_bytes(first, w, h)
        except OSError as e:
            raise ApiError(f"读不到首帧 {first}：{e}") from None
        data = {"model": model, "prompt": req["prompt"], "size": size}
        if req.get("seconds"):
            data["seconds"] = str(int(req["seconds"]))
        timeout = float(req.get("timeout") or 1200)
        with self._http(c, 120.0) as http:
            r = self._send(c, http, "POST", "/videos", model=model, data=data,
                           files={"input_reference": ("first_frame.png", ref, "image/png")})
            job = self._json(c, r)
            vid = job.get("id")
            if not vid:
                raise ApiError(f"Sora 没有返回任务 id：{scrub(str(job)[:300], *c.secrets())}")
            emit(f"Sora 任务 {vid} 已提交：{model} · {size} · {data.get('seconds', '?')} 秒")
            last = {"status": None}

            def state(d):
                st = d.get("status")
                if st != last["status"]:
                    last["status"] = st
                    emit(f"Sora 任务 {vid}：{SORA_STATE.get(st, st)}")
                if on_progress and isinstance(d.get("progress"), (int, float)):
                    on_progress(int(d["progress"]), 100)

            d = self._poll(c, lambda: self._json(c, self._send(c, http, "GET", f"/videos/{vid}")),
                           lambda d: d.get("status") in ("completed", "failed", "cancelled", "expired"),
                           cancel=cancel, timeout=timeout, label=f"Sora 任务 {vid}", on_state=state)
            if d.get("status") != "completed":
                err = d.get("error") or {}
                msg = err.get("message") if isinstance(err, dict) else err
                raise ApiError(scrub(f"Sora 生成失败（{d.get('status')}）：{msg or '没有给原因'}", *c.secrets()))
            r = self._send(c, http, "GET", f"/videos/{vid}/content")
        dst = run_dir / "sora_00.mp4"
        dst.write_bytes(r.content)
        return dst

    # ------------------------------------------------------------ Seedance（火山方舟）
    def _seedance(self, req: dict, run_dir: Path, cancel, emit, on_progress) -> Path:
        c = self.creds("ark")
        model = req["model"]
        content: list[dict] = [{"type": "text", "text": req["prompt"]}]
        for role, key in (("first_frame", "first_frame"), ("last_frame", "end_frame")):
            if req.get(key):
                mt, data = _b64_file(Path(req[key]))
                content.append({"type": "image_url", "image_url": {"url": f"data:{mt};base64,{data}"},
                                "role": role})
        # 参考图直出（没有首帧）：多模态参考，每张 role=reference_image；和首尾帧互斥，有首帧时不发
        refs = [] if req.get("first_frame") else list(req.get("refs") or [])
        for p in refs:
            mt, data = _b64_file(Path(p))
            content.append({"type": "image_url", "image_url": {"url": f"data:{mt};base64,{data}"},
                            "role": "reference_image"})
        body: dict = {"model": model, "content": content, "ratio": req.get("aspect"), "watermark": False}
        if req.get("seconds"):
            body["duration"] = int(req["seconds"])
        if req.get("resolution"):
            body["resolution"] = req["resolution"]
        if req.get("generate_audio") is not None:
            body["generate_audio"] = bool(req["generate_audio"])
        timeout = float(req.get("timeout") or 1200)
        with self._http(c, 120.0) as http:
            r = self._send(c, http, "POST", "/contents/generations/tasks", model=model, json=body)
            job = self._json(c, r)
            tid = job.get("id")
            if not tid:
                raise ApiError(f"Seedance 没有返回任务 id：{scrub(str(job)[:300], *c.secrets())}")
            emit(f"Seedance 任务 {tid} 已提交：{model} · {body['ratio']} · {body.get('duration', '?')} 秒"
                 f"{' · ' + body['resolution'] if body.get('resolution') else ''}"
                 f"{' · 有声' if body.get('generate_audio') else ''}"
                 f"{' · 带尾帧' if req.get('end_frame') else ''}"
                 f"{f' · 参考图直出 {len(refs)} 张' if refs else ''}")
            last = {"status": None}

            def state(d):
                st = d.get("status")
                if st != last["status"]:
                    last["status"] = st
                    emit(f"Seedance 任务 {tid}：{SEEDANCE_STATE.get(st, st)}")

            d = self._poll(c, lambda: self._json(c, self._send(c, http, "GET", f"/contents/generations/tasks/{tid}")),
                           lambda d: d.get("status") in SEEDANCE_DONE, cancel=cancel, timeout=timeout,
                           label=f"Seedance 任务 {tid}", on_state=state)
            if d.get("status") != "succeeded":
                err = d.get("error") or {}
                code = err.get("code") if isinstance(err, dict) else None
                msg = err.get("message") if isinstance(err, dict) else err
                hint = vendor_hint("ark", str(code) if code else None)
                why = "；".join(x for x in (hint, f"{msg}（{code}）" if code and msg else (msg or code)) if x)
                raise ApiError(scrub(f"Seedance 生成失败（{SEEDANCE_STATE.get(d.get('status'), d.get('status'))}）："
                                     f"{why or '没有给原因'}", *c.secrets()))
            url = (d.get("content") or {}).get("video_url")
            if not url:
                raise ApiError("Seedance 任务完成了，但没有返回视频地址")
            emit("Seedance 完成，下载视频（地址 24 小时内有效）")
            data = self._download(c, http, url)
        if on_progress:
            on_progress(100, 100)
        dst = run_dir / "seedance_00.mp4"
        dst.write_bytes(data)
        return dst

    # ------------------------------------------------------------ 可灵
    def _kling_data(self, c: Creds, r: httpx.Response, model: str | None = None) -> dict:
        """可灵的响应：{code, message, data}；code ≠ 0 是业务错误（HTTP 200 也可能是）。返回 data。"""
        d = self._json(c, r)
        if d.get("code") not in (None, 0, "0"):
            raise ApiError(http_error_text("kling", r, c, model))
        data = d.get("data")
        return data if isinstance(data, dict) else {}

    def _kling(self, req: dict, run_dir: Path, cancel, emit, on_progress) -> Path:
        c = self.creds("kling")
        model = req["model"]
        _, first = _b64_file(Path(req["first_frame"]))
        body: dict = {"model_name": model, "image": first, "prompt": req["prompt"][:KLING_PROMPT_MAX],
                      "mode": req.get("mode") or "std"}
        if req.get("end_frame"):
            body["image_tail"] = _b64_file(Path(req["end_frame"]))[1]
        if req.get("negative"):
            body["negative_prompt"] = req["negative"][:KLING_PROMPT_MAX]
        if req.get("seconds"):
            body["duration"] = str(int(req["seconds"]))
        if req.get("sound"):
            body["sound"] = req["sound"]
        if req.get("cfg_scale") is not None:
            body["cfg_scale"] = float(req["cfg_scale"])
        timeout = float(req.get("timeout") or 1200)
        with self._http(c, 120.0) as http:
            r = self._send(c, http, "POST", "/v1/videos/image2video", model=model, json=body)
            job = self._kling_data(c, r, model)
            tid = job.get("task_id")
            if not tid:
                raise ApiError(f"可灵没有返回任务 id：{scrub(str(job)[:300], *c.secrets())}")
            if req.get("mode_forced"):
                emit(f"可灵：{req['mode_forced']}，这次用 pro")
            emit(f"可灵任务 {tid} 已提交：{model} · {body['mode']} · {body.get('duration', '?')} 秒"
                 f"{' · 有声' if body.get('sound') == 'on' else ''}{' · 带尾帧' if 'image_tail' in body else ''}")
            last = {"status": None}

            def state(d):
                st = d.get("task_status")
                if st != last["status"]:
                    last["status"] = st
                    emit(f"可灵任务 {tid}：{KLING_STATE.get(st, st)}")

            d = self._poll(c, lambda: self._kling_data(c, self._send(c, http, "GET", f"/v1/videos/image2video/{tid}")),
                           lambda d: d.get("task_status") in ("succeed", "failed"), cancel=cancel,
                           timeout=timeout, label=f"可灵任务 {tid}", on_state=state)
            if d.get("task_status") != "succeed":
                msg = str(d.get("task_status_msg") or "")
                hint = ("内容没通过安全审核：改写提示词或换首帧后重试"
                        if any(w in msg.lower() for w in MODERATION_WORDS) else None)
                why = "；".join(x for x in (hint, msg) if x)
                raise ApiError(scrub(f"可灵生成失败：{why or '没有给原因'}", *c.secrets()))
            videos = ((d.get("task_result") or {}).get("videos") or [])
            url = next((v.get("url") for v in videos if isinstance(v, dict) and v.get("url")), None)
            if not url:
                raise ApiError("可灵任务完成了，但没有返回视频地址")
            emit("可灵完成，下载视频")
            data = self._download(c, http, url)
        if on_progress:
            on_progress(100, 100)
        dst = run_dir / "kling_00.mp4"
        dst.write_bytes(data)
        return dst
