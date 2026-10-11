"""第五次实跑（楼道·复验五）报告里的问题：接上一镜只带画面里的人的定妆照、只出声角色不进画面、
中段叠入参考图的检测、API 建项目按预设给引擎、分号后的标志性细节、场景句截成半句、成片限幅。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.domain.models import Character, Project, Shot
from backend.domain.slots import add_candidate, get_or_create_slot, selected_candidate
from backend.pipeline.continuity import run_continue_frame
from backend.pipeline.keyframes import load_shot, run_keyframe
from backend.pipeline.prompting import (Speaker, compose_video_prompt, face_look, fingerprint_mode,
                                        keyframe_input_hash, keyframe_inputs, location_summary,
                                        portrait_look, shot_image_prompt, upper_body_look,
                                        video_input_hash)
from backend.pipeline.videos import qc_bad, qc_note, run_video
from backend.tests import test_continuity as tc
from backend.tests.test_keyframes_videos import SsimFFmpeg, setup

# 第五次实跑江屿、许棠的外貌（视觉设定原文：整段一句，标志性细节写在分号后面）
JIANG = ("Chinese man around thirty years old, lean angular face with a defined jawline and light stubble, "
         "short slightly messy black hair, tall lean build, wearing a plain heather-gray cotton fleece hooded "
         "sweatshirt with the hood down, black straight-leg trousers and worn black leather shoes; signature "
         "detail: a pair of thick black-framed rectangular glasses.")
XU = ("Chinese woman, 26 years old, slim oval face with sharp alert dark eyes and straight thin eyebrows, "
      "shoulder-length straight black hair with blunt bangs, petite slender build, wearing an oversized "
      "mustard-yellow chunky knit cardigan over a plain white cotton crew-neck T-shirt, loose charcoal-gray "
      "cotton lounge pants and white canvas sneakers; signature detail: the bright mustard-yellow chunky knit "
      "cardigan that stands out clearly against dark surroundings.")
QIN = "Chinese man in his mid-fifties, voice only"
LOC = ("Narrow fifth-floor stairwell landing of an old Chinese residential building at 11 pm at night, worn "
       "concrete stairs descending on one side, walls with peeling pale green paint and flaking plaster, a "
       "dented dark-red steel security door marked 502 opening onto a pitch-black interior, half-unpacked "
       "cardboard boxes and packing tape piled beside a faded doormat, rusty iron handrail, layered advertising "
       "stickers on the wall, a single dim flickering motion-sensor bulb overhead as the main light, weak "
       "yellowish light against cold blue-green shadows, empty and oppressive.")


# ---------------------------------------------------------------- 5. 分号后的标志性细节
@pytest.mark.parametrize("look", [portrait_look, upper_body_look, face_look])
def test_signature_after_semicolon_survives_lower_body_strip(look):
    out = look(JIANG)
    assert "signature detail: a pair of thick black-framed rectangular glasses" in out
    assert "trousers" not in out and "shoes" not in out
    assert out.startswith("Chinese man around thirty") and out.endswith("glasses.")
    assert "hood down; signature detail" in out   # 分号留着，前面的分句不丢


def test_signature_cardigan_after_pants_semicolon_kept():
    out = upper_body_look(XU)
    assert "pants" not in out and "sneakers" not in out
    assert out.endswith("; signature detail: the bright mustard-yellow chunky knit cardigan that stands out "
                        "clearly against dark surroundings.")


def test_clause_strip_with_semicolons_drops_only_lower_body():
    vp = "A tall man, grey wool coat; black jeans, brown boots; round silver earrings."
    assert upper_body_look(vp) == "A tall man, grey wool coat; round silver earrings."


def test_fingerprint_mode_keeps_old_comma_only_split():
    """算指纹按老写法（只按逗号拆），已出的首帧不过期。"""
    with fingerprint_mode():
        old = upper_body_look(JIANG)
    assert old.endswith("sweatshirt with the hood down.") and "glasses" not in old


def _jiang_shot(proj_chars, size="近景"):
    shot = SimpleNamespace(image_prompt="Medium close-up, 85mm. {{char:21}} The lean man holds up a key.",
                           shot_size=size, motion_prompt="he holds the key up", audio_prompt="",
                           dialogue=[], duration=4.0)
    proj = SimpleNamespace(characters=proj_chars, negative="", aspect="9:16")
    return shot, proj


def test_engine_prompt_has_glasses_but_hash_unchanged():
    shot, proj = _jiang_shot([SimpleNamespace(id=21, name="江屿", visual_prompt=JIANG, voice_only=False)])
    assert "glasses" in shot_image_prompt(shot, proj)
    # 输入哈希和 inputs 摘要按老写法：和修复前一样（没有眼镜那句），已有首帧、视频不判过期
    inputs = keyframe_inputs(shot, proj, None, "h3_image")
    assert "glasses" not in inputs["prompt"] and "sweatshirt with the hood down." in inputs["prompt"]
    with fingerprint_mode():
        legacy = shot_image_prompt(shot, proj)
    from backend.domain.hashing import input_hash
    assert keyframe_input_hash(shot, proj, None, "h3_image") == input_hash(
        "first_frame", legacy, "", "h3_image", "9:16")
    assert video_input_hash(shot, proj, None, 7, "h3_video", True) == video_input_hash(
        shot, proj, None, 7, "h3_video", True)


def test_portrait_prompt_keeps_glasses():
    from backend.pipeline.characters import portrait_upper_look

    assert "black-framed rectangular glasses" in portrait_upper_look(JIANG)


# ---------------------------------------------------------------- 6. 场景句按分句截
def test_location_summary_cuts_at_clause_boundary():
    out = location_summary(LOC)
    assert out == ("a narrow fifth-floor stairwell landing of an old Chinese residential building at "
                   "11 pm at night, worn concrete stairs descending on one side")
    assert len(out.split()) <= 30


def test_location_summary_long_first_clause_has_no_dangling_word():
    text = ("Tiny kitchen " + " ".join(["with"] * 1) + " " + "old tiles " * 13 + "and a window over the sink.")
    out = location_summary(text)
    assert len(out.split()) <= 30
    assert not out.rstrip().split()[-1].lower() in ("and", "with", "of", "a", "the", "over")


# ---------------------------------------------------------------- 2. 只出声的角色不进画面
def _speakers_two_on_screen():
    return {"许棠": Speaker("the young woman", True, "female"),
            "江屿": Speaker("the lean man", True, "male"),
            "老秦": Speaker("the middle-aged man", False, "male", True)}


def test_video_prompt_says_voice_only_speaker_never_seen():
    dlg = [{"speaker": "老秦", "zh": "我说了，别开门。", "start": 0.6, "end": 2.4}]
    p = compose_video_prompt("She turns back.", "", "Bulb buzzes.", dlg, anchor="", has_audio=True,
                             speakers=_speakers_two_on_screen())
    body, _, audio = p.partition("\nAudio:")
    assert ("The speaker (the middle-aged man) is never seen on screen; only the voice is heard."
            in body)
    # 两人同框：复数
    assert "the on-screen characters' lips stay closed." in audio
    # 算哈希的老写法不变
    old = compose_video_prompt("She turns back.", "", "Bulb buzzes.", dlg, anchor="", has_audio=True)
    assert "never seen" not in old and old == ("She turns back.\nAudio: Bulb buzzes. "
                                               "老秦 says in Mandarin Chinese: \"我说了，别开门。\"")


def test_off_screen_speaker_who_is_not_voice_only_gets_no_never_seen_line():
    sp = {"许棠": Speaker("the young woman", True, "female"), "江屿": Speaker("the lean man", False, "male")}
    dlg = [{"speaker": "江屿", "zh": "开门。", "start": 0.5, "end": 1.5}]
    p = compose_video_prompt("She listens.", "", "", dlg, anchor="", has_audio=True, speakers=sp)
    assert "never seen" not in p and "the on-screen character's lips stay closed." in p


def _qin_shot(db, shot_id, image_prompt="Medium close-up, 85mm. {{char:%d}} The young woman freezes."):
    with db.session() as s:
        shot, _, proj, _ = load_shot(s, shot_id)
        xu = Character(project_id=proj.id, name="许棠", visual_prompt=XU)
        qin = Character(project_id=proj.id, name="老秦", visual_prompt=QIN, voice_only=True)
        s.add_all([xu, qin])
        s.flush()
        shot.image_prompt = image_prompt % xu.id + " {{char:%d}}" % qin.id
        shot.shot_size = "近景"
        shot.cast = [{"character_id": xu.id, "variation_id": None}]
        shot.audio_prompt = "Off-screen voice of the middle-aged man from the stairwell below."
        shot.dialogue = [{"speaker": "老秦", "zh": "我说了，别开门。", "start": 0.6, "end": 2.4}]
        return xu.id, qin.id


def test_voice_only_speaker_kept_out_of_first_frame_and_video(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    _qin_shot(db, shot_id)
    run_keyframe(ctx, {"shot_id": shot_id})
    kf = runner.calls[-1].workflow["5"]["inputs"]["text"]
    assert "voice only" not in kf and "mid-fifties" not in kf          # 只出声角色的占位符去掉
    assert kf.endswith("The young woman is the only person in the frame.")
    assert "middle-aged" not in kf
    with db.session() as s:
        shot, _, proj, style = load_shot(s, shot_id)
        ff = selected_candidate(s, "shot", shot_id, "first_frame")
        # 首帧哈希仍按老写法（占位符照填），已有首帧不过期
        assert ff.input_hash == keyframe_input_hash(shot, proj, style, "zimage")
        with fingerprint_mode():
            assert "voice only" in shot_image_prompt(shot, proj)
    run_video(ctx, {"shot_id": shot_id})
    vp = runner.calls[-1].workflow["6"]["inputs"]["prompt"]
    assert "The speaker (the middle-aged man) is never seen on screen; only the voice is heard." in vp
    assert "An off-screen male voice (the middle-aged man) says" in vp


def test_voice_only_character_in_cast_still_counts_as_off_screen(tmp_path, db):
    from backend.pipeline.videos import _speakers

    ctx, runner, shot_id = setup(tmp_path, db)
    xu, qin = _qin_shot(db, shot_id)
    with db.session() as s:
        shot, ep, proj, _ = load_shot(s, shot_id)
        shot.cast = [{"character_id": xu, "variation_id": None}, {"character_id": qin, "variation_id": None}]
        s.flush()
        sp = _speakers(proj, shot, ep.shots)
    assert sp["老秦"].on_screen is False and sp["老秦"].voice_only is True


def test_first_frame_without_voice_only_lines_has_no_only_person_line(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    _qin_shot(db, shot_id)
    with db.session() as s:
        s.get(Shot, shot_id).dialogue = [{"speaker": "许棠", "zh": "谁？", "start": 0.5, "end": 1.2}]
    run_keyframe(ctx, {"shot_id": shot_id})
    assert "only person" not in runner.calls[-1].workflow["5"]["inputs"]["text"]


# ---------------------------------------------------------------- 1. 接上一镜只带画面里的人
def _locked_both(tmp_path, db):
    """S01 两人都锁过脸（视频带了两人定妆照）；S02 接上一镜，cast 两人，画面描述只写了苏晴。"""
    ctx, runner, _, _, ids = tc.setup(tmp_path, db)
    sv = ctx.services
    chars = tc._cast_with_portraits(db, ids)
    both = [{"character_id": chars["阿杰"], "variation_id": None},
            {"character_id": chars["苏晴"], "variation_id": None}]
    with db.session() as s:
        s.get(Shot, ids[0]).cast = both
        s.get(Shot, ids[1]).cast = both
        s.get(Shot, ids[1]).image_prompt = ("Medium close-up, 85mm. {{char:%d}} The woman turns back toward "
                                            "the stairwell." % chars["苏晴"])
    vid = tc._prev_video(sv, ids[0], "refs")
    with db.session() as s:
        from backend.domain.models import Candidate
        s.get(Candidate, vid).params = {"identity": "refs",
                                        "locked_chars": sorted(chars.values())}
    run_continue_frame(ctx, {"shot_id": ids[1]})
    return ctx, runner, ids, chars


def test_continue_video_only_brings_portraits_of_people_in_frame(tmp_path, db):
    ctx, runner, ids, chars = _locked_both(tmp_path, db)
    run_video(ctx, {"shot_id": ids[1]})
    with db.session() as s:
        v = selected_candidate(s, "shot", ids[1], "video")
        assert [r["label"] for r in v.params["inputs"]["refs"]] == ["苏晴 定妆照"]
        assert v.params["locked_chars"] == [chars["苏晴"]]
    assert not next(x for x in tc.detail(ctx.services, ids[1]).slots if x.kind == "video").stale


def test_continue_video_made_under_old_rule_is_not_stale(tmp_path, db):
    """修复前接上一镜带了 cast 里所有锁过脸的人（包括画面里没写的）：这种老视频不判过期、不重出。"""
    ctx, runner, ids, chars = _locked_both(tmp_path, db)
    sv = ctx.services
    with db.session() as s:
        shot, _, proj, style = load_shot(s, ids[1])
        ff = selected_candidate(s, "shot", ids[1], "first_frame")
        from backend.domain.refs import shot_refs
        old_refs = [r.candidate_id for r in shot_refs(s, shot, video=True)]
        assert len(old_refs) == 2
        h_old = video_input_hash(shot, proj, style, ff.id, "h3_video", True, old_refs)
        add_candidate(s, get_or_create_slot(s, "shot", ids[1], "video"), file_path="x.mp4",
                      engine="h3_video", params={}, input_hash=h_old, job_id=None)
    assert not next(x for x in tc.detail(sv, ids[1]).slots if x.kind == "video").stale
    n = len(runner.calls)
    assert run_video(ctx, {"shot_id": ids[1]})["skipped"] is True and len(runner.calls) == n


def test_continue_without_placeholders_uses_whole_cast(tmp_path, db):
    """画面描述里没写占位符（老镜头、手写镜头）：照旧按 cast 算画面里的人。"""
    ctx, runner, ids, chars = _locked_both(tmp_path, db)
    with db.session() as s:
        s.get(Shot, ids[1]).image_prompt = "the woman turns back"
    run_video(ctx, {"shot_id": ids[1]})
    with db.session() as s:
        v = selected_candidate(s, "shot", ids[1], "video")
        assert [r["label"] for r in v.params["inputs"]["refs"]] == ["阿杰 定妆照", "苏晴 定妆照"]


# ---------------------------------------------------------------- 3. 中段叠入参考图
class GhostFFmpeg(tc.TailFFmpeg):
    """假 ffmpeg：截尾帧 + 帧和参考图的相似度（按参考图文件名和时间点给值）。"""

    def __init__(self, table, fail=None):
        super().__init__()
        self.table, self.fail, self.asked = table, fail, []

    def video_duration(self, path):
        return 4.0

    def frame_ssim(self, video, at, image):
        self.asked.append((round(at, 2), Path(image).name))
        if self.fail:
            raise self.fail
        return self.table(round(at, 2), Path(image).name)


def _run_ghost(tmp_path, db, table, fail=None):
    ctx, runner, ids, chars = _locked_both(tmp_path, db)
    with db.session() as s:   # 画面里两人都写了：视频带两张定妆照
        s.get(Shot, ids[1]).image_prompt = ("Two-shot. {{char:%d}} The man. {{char:%d}} The woman."
                                            % (chars["阿杰"], chars["苏晴"]))
    ff = GhostFFmpeg(table, fail)
    ctx.services.ffmpeg = ff
    res = run_video(ctx, {"shot_id": ids[1]})
    return ctx, ids, chars, ff, res


def test_ghost_ref_flagged_when_mid_frame_matches_portrait(tmp_path, db):
    # 第五次实跑 S05 第一版的实测：江屿定妆照 第 0 帧 0.01，25% 处 0.30
    from backend.domain.characters import portrait_of
    from backend.domain.models import Candidate

    # 先出一条干净的（选定），再用造好的值重拍一条
    ctx2, ids2, chars2, ff, res = _run_ghost(tmp_path, db, lambda t, n: 0.0)
    with db.session() as s:
        ghost = Path(portrait_of(s, chars2["苏晴"]).file_path).name
    vals = {0.0: 0.01, 1.0: 0.30, 2.0: 0.23, 3.0: 0.03}
    ff2 = GhostFFmpeg(lambda t, name: vals[t] if name == ghost else -0.02)
    ctx2.services.ffmpeg = ff2
    res = run_video(ctx2, {"shot_id": ids2[1], "force": True})
    assert res["qc"]["ghost_refs"] == [{"ref": "苏晴 定妆照", "t": 1.0, "ssim": 0.3, "base": 0.01}]
    assert sorted({t for t, _ in ff2.asked}) == [0.0, 1.0, 2.0, 3.0]
    assert qc_bad(res["qc"])
    with db.session() as s:
        new = s.get(Candidate, res["candidate_ids"][0])
        assert new.note.startswith("疑似叠入参考图：1.0s 前后画面和苏晴 定妆照很像（相似度 0.30，开头 0.01）")
        # 不抢选定：第一条干净、没过期，还是它
        assert selected_candidate(s, "shot", ids2[1], "video").id != new.id
    assert any("疑似叠入参考图" in m for m in ctx2.logs)


@pytest.mark.parametrize("values", [
    {0.0: 0.18, 1.0: 0.18, 2.0: 0.17, 3.0: 0.18},   # 本来就有点像（近景脸）：没升，不标
    {0.0: -0.01, 1.0: 0.13, 2.0: 0.11, 3.0: 0.08},  # 干净片子里升得最多的那类（第四次 S02）
    {0.0: 0.05, 1.0: 0.21, 2.0: 0.2, 3.0: 0.1},     # 差一点：不标
])
def test_ghost_ref_not_flagged_on_clean_like_values(tmp_path, db, values):
    ctx, ids, chars, ff, res = _run_ghost(tmp_path, db, lambda t, n: values[t])
    assert "ghost_refs" not in (res.get("qc") or {})


def test_ghost_check_failure_is_logged_not_flagged(tmp_path, db):
    from backend.engines.ffmpeg import FFmpegError

    ctx, ids, chars, ff, res = _run_ghost(tmp_path, db, lambda t, n: 0.9, fail=FFmpegError("boom"))
    assert "ghost_refs" not in (res.get("qc") or {})
    assert any("参考图叠入检测没跑成" in m for m in ctx.logs)


def test_ghost_check_skipped_without_refs(tmp_path, db):
    """视频没带参考图（插入镜头、首帧没锁脸）：不查，也不调 ffmpeg。"""
    ctx, runner, shot_id = setup(tmp_path, db)
    ff = GhostFFmpeg(lambda t, n: 0.9)
    ff.scene_cuts = lambda p: []
    ctx.services.ffmpeg = ff
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    assert ff.asked == [] and "ghost_refs" not in (res.get("qc") or {})


def test_qc_note_includes_ghost():
    qc = {"hard_cuts": [], "ghost_refs": [{"ref": "江屿 定妆照", "t": 1.1, "ssim": 0.3, "base": 0.01}]}
    assert qc_bad(qc)
    assert qc_note(qc).startswith("疑似叠入参考图：1.1s 前后画面和江屿 定妆照很像")


def test_parse_ssim_reads_negative_channels():
    from backend.engines.ffmpeg import parse_ssim

    log = "[Parsed_ssim_4 @ 0x1] SSIM Y:-0.023421 (-0.1) U:0.515031 (3.1) V:0.549572 (3.4) All:0.15 (0.7)"
    assert parse_ssim(log) == pytest.approx(-0.023421)


# ---------------------------------------------------------------- 4. API 建项目按预设给引擎
def test_create_project_without_engines_uses_preset_recommendation(api):
    r = api.post("/api/projects", json={"title": "a", "style_preset": "live_action"}).json()
    assert r["image_engine"] == "h3_image" and r["video_engine"] == "h3_video"


def test_create_project_uses_settings_engine_when_preset_allows_it(api):
    api.put("/api/settings", json={"image_engine": "krea2"})
    r = api.post("/api/projects", json={"title": "a", "style_preset": "live_action"}).json()
    assert r["image_engine"] == "krea2"     # 真人写实推荐 h3_image / krea2 / zimage，设置的 krea2 合得来


def test_create_project_ignores_settings_engine_outside_preset(api):
    from backend.domain.style_presets import PRESETS

    key = next(k for k, p in PRESETS.items() if "h3_image" not in p.image_engines)
    api.put("/api/settings", json={"image_engine": "h3_image"})
    r = api.post("/api/projects", json={"title": "a", "style_preset": key}).json()
    names = api.app.state.services.engines.names("image")
    want = next((n for n in PRESETS[key].image_engines if n in names), None)
    assert r["image_engine"] != "h3_image" and (want is None or r["image_engine"] == want)


def test_create_project_explicit_engine_wins(api):
    r = api.post("/api/projects", json={"title": "a", "image_engine": "zimage"}).json()
    assert r["image_engine"] == "zimage"
