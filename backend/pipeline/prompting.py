"""把镜头字段拼成最终提示词，并算输入哈希（用于判断候选是否过期）。"""
from __future__ import annotations

from backend.domain.hashing import input_hash


def compose_image_prompt(image_prompt: str, anchor: str) -> str:
    p, a = (image_prompt or "").strip(), (anchor or "").strip()
    return p if not a or a in p else f"{a} {p}"


def compose_video_prompt(motion_prompt: str, image_prompt: str, audio_prompt: str,
                         dialogue: list[dict], *, anchor: str, has_audio: bool) -> str:
    text = compose_image_prompt((motion_prompt or "").strip() or image_prompt, anchor)
    if not has_audio:
        return text
    audio = (audio_prompt or "").strip()
    lines = [f'{d.get("speaker", "")} says in Mandarin Chinese: "{d["zh"]}"'
             for d in dialogue or [] if d.get("zh") and d["zh"] not in audio]
    parts = [p for p in (audio, " ".join(lines)) if p]
    return text + ("\nAudio: " + " ".join(parts) if parts else "")


def join_negative(*parts: str | None) -> str:
    return ", ".join(p.strip() for p in parts if p and p.strip())


def shot_seed(base: int, shot_id: int, n: int) -> int:
    return (int(base) + shot_id * 1009 + n * 7919) % 2**32


def _anchor(style) -> str:
    return style.anchor_text if style else ""


def _negative(project, style) -> str:
    return join_negative(project.negative, style.negative if style else "")


def keyframe_input_hash(shot, project, style, engine_name: str) -> str:
    return input_hash("first_frame", compose_image_prompt(shot.image_prompt, _anchor(style)),
                      _negative(project, style), engine_name, project.aspect)


def video_input_hash(shot, project, style, first_frame_candidate_id: int, engine_name: str,
                     has_audio: bool) -> str:
    prompt = compose_video_prompt(shot.motion_prompt, shot.image_prompt, shot.audio_prompt,
                                  shot.dialogue, anchor=_anchor(style), has_audio=has_audio)
    return input_hash("video", prompt, first_frame_candidate_id, engine_name, project.aspect,
                      round(float(shot.duration), 2))
