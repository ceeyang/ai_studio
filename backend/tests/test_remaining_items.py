"""审查报告剩余条目：P1-5 自动重拍、P1-7 只出声的角色、P1-9 视频参考图减量、P1-12 剧本重写认人、
P2-5 三视图按画风、P2-6 首帧先写景别、P2-7 建议接上一镜、P2-8 中文字幕对齐实际开口、P2-9 镜头节奏。"""
from __future__ import annotations

from backend.domain.models import Project
from backend.domain.slots import selected_candidate, slots_for
from backend.pipeline.keyframes import load_shot, run_keyframe
from backend.pipeline.videos import run_video
from backend.tests.test_keyframes_videos import SsimFFmpeg, setup


# ---------------------------------------------------------------- P1-5 开头没接上首帧：自动重拍一次
class SeqSsim(SsimFFmpeg):
    """首帧相似度按顺序返回 values 里的值（最后一个重复用）。"""

    def __init__(self, values):
        super().__init__()
        self.values = list(values)

    def first_frame_ssim(self, video, image):
        self.compared.append((video, image))
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def _auto_retry(db, shot_id, on=True):
    with db.session() as s:
        _, _, proj, _ = load_shot(s, shot_id)
        proj.auto_retry_video = on


def test_auto_retry_reshoots_once_with_new_seed_and_selects_the_clean_take(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    _auto_retry(db, shot_id)
    ctx.services.ffmpeg = SeqSsim([0.3, 0.92])
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    videos = [c for c in runner.calls if "6" in c.workflow and "prompt" in c.workflow["6"]["inputs"]]
    assert len(videos) == 2 and videos[0].meta["seed"] != videos[1].meta["seed"]
    assert len(res["candidate_ids"]) == 2
    with db.session() as s:
        v = selected_candidate(s, "shot", shot_id, "video")
        assert v.id == res["candidate_ids"][1] and v.note == ""
        assert v.params["retry_of"] == res["candidate_ids"][0]
        first = next(c for sl in slots_for(s, "shot", shot_id) if sl.kind == "video"
                     for c in sl.candidates if c.id == res["candidate_ids"][0])
        assert "开头没接上首帧" in first.note
    assert any("自动重拍" in m for m in ctx.logs)


def test_auto_retry_only_once_even_if_retake_also_mismatches(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    _auto_retry(db, shot_id)
    ctx.services.ffmpeg = SeqSsim([0.3, 0.2])
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    assert len(res["candidate_ids"]) == 2
    assert len(runner.calls) == 3   # 首帧 1 次 + 视频 2 次
    with db.session() as s:
        # 两条都没接上：选定第一条（和以前一样），第二条不抢
        assert selected_candidate(s, "shot", shot_id, "video").id == res["candidate_ids"][0]


def test_no_auto_retry_when_project_setting_off(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    ctx.services.ffmpeg = SeqSsim([0.3, 0.92])
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    assert len(res["candidate_ids"]) == 1 and len(runner.calls) == 2


def test_no_auto_retry_for_clean_or_cut_only_take(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    _auto_retry(db, shot_id)
    ctx.services.ffmpeg = SeqSsim([0.9])
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    assert len(res["candidate_ids"]) == 1
    with db.session() as s:
        assert db is not None and s.get(Project, 1).auto_retry_video


# ---------------------------------------------------------------- P1-7 只出声的角色
def _structure_with_voice():
    from backend.tests.test_script import STRUCTURE

    return {**STRUCTURE, "characters": STRUCTURE["characters"] + [
        {"name": "站长", "description": "外卖站站长，只出声，不出镜，电话里催单"}]}


def _visual_with_voice():
    from backend.tests.test_script import VISUAL

    return {**VISUAL, "characters": VISUAL["characters"] + [
        {"name": "站长", "visual_prompt": "Chinese man in his late thirties, voice only",
         "core_features": "男声"}]}


def test_structure_marks_voice_only_characters_and_visual_prompt_says_so(tmp_path, db):
    from sqlalchemy import select

    from backend.domain.models import Character
    from backend.pipeline.script import run_script
    from backend.tests.test_script import setup as script_setup

    ctx, ep_id, prov = script_setup(tmp_path, db, [_structure_with_voice(), _visual_with_voice()])
    run_script(ctx, {"episode_id": ep_id})
    with db.session() as s:
        flags = {c.name: c.voice_only for c in s.scalars(select(Character))}
    assert flags == {"林夏": False, "老周": False, "站长": True}
    visual = prov.calls[1]
    system = visual["system"] if "system" in visual else ""
    user = visual["messages"][-1]["content"]
    assert "站长：外卖站站长，只出声，不出镜" in user and "（只出声，不出镜）" in user
    assert "voice only" in (system + user)


def test_voice_only_flag_does_not_change_visual_fingerprint_of_others():
    from backend.pipeline.script import visual_chars_and_locations

    class C:
        def __init__(self, name, vo=False):
            self.name, self.description, self.voice_only = name, "d", vo

    class P:
        characters = [C("林夏"), C("站长", True)]
        locations = []

    chars, _ = visual_chars_and_locations(P())
    assert chars[0] == {"name": "林夏", "description": "d"}       # 老角色的指纹不变
    assert chars[1] == {"name": "站长", "description": "d", "voice_only": True}


def _seed_voice_only(db, ep_id):
    from backend.domain.models import Character, Episode

    with db.session() as s:
        proj_id = s.get(Episode, ep_id).project_id
        ch = Character(project_id=proj_id, name="站长", voice_only=True,
                       visual_prompt="Chinese man in his late thirties, voice only")
        s.add(ch)
        s.flush()
        return ch.id


def test_storyboard_table_gives_voice_only_no_placeholder(tmp_path, db):
    from backend.pipeline.storyboard import run_storyboard
    from backend.tests.test_storyboard import setup as sb_setup, shots_json

    ctx, ep_id, prov = sb_setup(tmp_path, db, [shots_json(2), shots_json(2)])
    cid = _seed_voice_only(db, ep_id)
    run_storyboard(ctx, {"episode_id": ep_id})
    user = prov.calls[0]["messages"][0]["content"]
    line = next(x for x in user.splitlines() if x.startswith("- 站长"))
    assert "{{char:%d}}" % cid not in user and "只出声，不出镜" in line


def test_storyboard_rejects_voice_only_in_cast(tmp_path, db):
    from backend.pipeline.storyboard import token_problems

    class Sh:
        cast = ["站长"]
        image_prompt = "{{char:9}} The man stands."
        motion_prompt = audio_prompt = ""
        props: list = []

    problems = token_problems([Sh()], {"站长": 9}, voice_only={"站长"})
    assert any("只出声" in p for p in problems)


def test_character_api_voice_only_toggle(api):
    api.post("/api/projects", json={"title": "demo"})
    c = api.post("/api/projects/demo/characters", json={"name": "站长"}).json()
    assert c["voice_only"] is False
    r = api.patch(f"/api/characters/{c['id']}", json={"voice_only": True})
    assert r.status_code == 200 and r.json()["voice_only"] is True


def test_portrait_warnings_skip_voice_only(tmp_path, db):
    from backend.domain.models import Character
    from backend.pipeline.runs import portrait_warnings
    from backend.tests.helpers import make_services, seed_episode

    sv = make_services(tmp_path, db)
    proj_id, ep_id = seed_episode(db, image_engine="h3_image")
    with db.session() as s:
        s.add(Character(project_id=proj_id, name="站长", voice_only=True))
        s.add(Character(project_id=proj_id, name="林夏"))
    notes = portrait_warnings(db, sv.engines, ep_id, ["keyframes"])
    assert notes and "林夏" in notes[0] and "站长" not in notes[0]


def test_recommend_template_excludes_voice_only():
    from backend.llm.prompts import BUILTIN_DIR

    text = (BUILTIN_DIR / "character_recommend.md").read_text(encoding="utf-8")
    assert "只出声" in text


# ---------------------------------------------------------------- P2-7 建议接上一镜、P2-9 第一镜和镜头节奏
def test_storyboard_stores_continue_suggestion_but_never_on_block_first_shot(tmp_path, db):
    from backend.domain.models import Episode
    from backend.pipeline.storyboard import run_storyboard, shot_content_hash
    from backend.tests.test_storyboard import setup as sb_setup, shots_json

    a = shots_json(2)
    for sh in a["shots"]:
        sh["continue_from_prev"] = True
    ctx, ep_id, prov = sb_setup(tmp_path, db, [a, shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        shots = ep.shots
        assert [x.suggest_continue for x in shots] == [False, True, False, False]
        assert not any(x.continue_from_prev for x in shots)    # 只是建议，不自动打开
        rec = ep.stage_hashes[f"storyboard:{shots[0].scene_block_id}"]
        assert rec["rules"] == "7"
        # 建议不进镜头内容哈希：之后用户关掉建议不算「手改过镜头」
        assert rec["shots"][str(shots[1].id)] == shot_content_hash(shots[1])
    assert any("建议 1 个镜头接上一镜" in m for m in ctx.logs)


def test_first_block_prompt_asks_for_a_short_hook_shot(tmp_path, db):
    from backend.pipeline.storyboard import run_storyboard
    from backend.tests.test_storyboard import setup as sb_setup, shots_json

    ctx, ep_id, prov = sb_setup(tmp_path, db, [shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    assert "全集的第一段" in prov.calls[0]["system"]
    assert "全集的第一段" not in prov.calls[1]["system"]
    assert "continue_from_prev" in prov.calls[0]["system"]
    assert any("全集第一镜 5 秒，建议 2–3 秒" in m for m in ctx.logs)


def test_project_pace_sets_shot_seconds_and_count(tmp_path, db):
    from backend.domain.models import Episode
    from backend.pipeline.script import structure_inputs
    from backend.pipeline.storyboard import block_fps, build_storyboard_plan
    from backend.tests.test_storyboard import setup as sb_setup

    ctx, ep_id, prov = sb_setup(tmp_path, db, [])
    sv = ctx.services
    with db.session() as s:
        before = build_storyboard_plan(s, sv, ep_id)
        fps = [block_fps(before, b, "") for b in before.blocks]
        assert before.shot_seconds == 5.0 and sum(b["count"] for b in before.blocks) == 4
        s.get(Episode, ep_id).project.shot_seconds = 3
    with db.session() as s:
        plan = build_storyboard_plan(s, sv, ep_id)
        assert plan.shot_seconds == 3.0 and sum(b["count"] for b in plan.blocks) == 7
        ep = s.get(Episode, ep_id)
        system, user, _ = structure_inputs(sv, ep, ep.project)
        assert "大约 7 个镜头" in user and "每个镜头约 3 秒" in system
        s.get(Episode, ep_id).project.shot_seconds = None
    with db.session() as s:
        again = build_storyboard_plan(s, sv, ep_id)
        # 没设节奏：和以前完全一样，老剧集的分镜指纹不变
        assert [block_fps(again, b, "") for b in again.blocks] == fps


def test_shot_api_exposes_and_dismisses_continue_suggestion(api):
    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()
    sh = api.post(f"/api/episodes/{ep['id']}/shots", json={"image_prompt": "a"}).json()
    assert sh["suggest_continue"] is False
    r = api.patch(f"/api/shots/{sh['id']}", json={"suggest_continue": True})
    assert r.status_code == 200 and r.json()["suggest_continue"] is True
    r = api.patch(f"/api/shots/{sh['id']}", json={"suggest_continue": False})
    assert r.json()["suggest_continue"] is False


# ---------------------------------------------------------------- P2-5 三视图模板按画风写
LOOK_A = "Japanese teenage girl, short black bob, navy sailor school uniform with a red ribbon."


def test_sheet_prompt_for_photo_presets_is_unchanged():
    from backend.domain.turnaround import sheet_prompt

    anchor = "Photorealistic live-action photography, fine film grain."
    for preset in (None, "live_action", "commercial"):
        p = sheet_prompt("body", LOOK_A, anchor, preset)
        assert p == sheet_prompt("body", LOOK_A, anchor)
        assert "photographic model sheet" in p and "photographed four times" in p


def test_sheet_prompt_for_drawn_presets_says_drawn_not_photographed():
    from backend.domain.turnaround import sheet_prompt

    anchor = "2D cel-shaded anime, clean line art, flat colors with soft gradient shading."
    for preset in ("anime", "guofeng", "western_comic"):
        for sheet in ("body", "face"):
            low = sheet_prompt(sheet, LOOK_A, anchor, preset).lower()
            assert "photograph" not in low and "photorealistic" not in low, (preset, sheet)
            assert "drawn" in low and ("character model sheet" in low
                                       or "character headshot reference" in low)
    low = sheet_prompt("body", LOOK_A, "Clean bright colors.", "anime").lower()
    # 锚没写画风：也不补「写实照片」
    assert "photorealistic" not in low and "photograph" not in low


def test_sheet_prompt_for_cg_preset_says_rendered():
    from backend.domain.turnaround import sheet_prompt

    low = sheet_prompt("face", LOOK_A, "High-end 3D CG render, subsurface scattering.",
                       "cg_realistic").lower()
    assert "photograph" not in low and "rendered three times" in low


def test_sheet_job_uses_project_preset(tmp_path, db):
    from backend.domain.models import Character
    from backend.domain.turnaround import sheet_inputs
    from backend.tests import test_turnaround as tt

    ctx, runner, ids = tt.setup(tmp_path, db)
    with db.session() as s:
        s.get(Project, ids["project"]).style_preset = "anime"
        prompt, _ = sheet_inputs(s.get(Character, ids["chen"]), "body")
    assert "photographed" not in prompt and "drawn four times" in prompt


# ---------------------------------------------------------------- P2-6 首帧先写景别再写锚
def test_engine_image_prompt_puts_framing_before_anchor():
    from backend.pipeline.prompting import engine_image_prompt

    p = "Medium shot, 50mm, eye level. The young woman stands at frame right."
    assert engine_image_prompt(p, "ANCHOR words.") == \
        "Medium shot, 50mm, eye level. ANCHOR words. The young woman stands at frame right."
    # 第一句不是景别：照旧锚在前
    q = "The young woman stands at frame right. Rain outside."
    assert engine_image_prompt(q, "ANCHOR.") == "ANCHOR. " + q
    assert engine_image_prompt(p, "") == p
    assert engine_image_prompt("ANCHOR. " + p, "ANCHOR.") == "ANCHOR. " + p


def test_keyframe_sends_framing_first_but_hash_and_inputs_unchanged(tmp_path, db):
    from backend.domain.models import Shot
    from backend.pipeline.prompting import compose_image_prompt, keyframe_input_hash

    ctx, runner, shot_id = setup(tmp_path, db)
    with db.session() as s:
        s.get(Shot, shot_id).image_prompt = "Close-up, 85mm, eye level. A woman reads a script."
    run_keyframe(ctx, {"shot_id": shot_id})
    sent = runner.calls[-1].workflow["5"]["inputs"]["text"]
    assert sent == "Close-up, 85mm, eye level. ANCHOR A woman reads a script."
    with db.session() as s:
        shot, _, proj, style = load_shot(s, shot_id)
        c = selected_candidate(s, "shot", shot_id, "first_frame")
        assert c.input_hash == keyframe_input_hash(shot, proj, style, "zimage", [])
        assert c.params["inputs"]["prompt"] == compose_image_prompt(shot.image_prompt, "ANCHOR")


# ---------------------------------------------------------------- P1-9 视频每人只带一张脸
def test_video_brings_one_face_cell_per_character(tmp_path, db):
    from backend.tests import test_turnaround as tt

    ctx, runner, ids = tt.setup(tmp_path, db, image_engine="h3_image")
    sv = ctx.services
    tt.put_portrait(sv, ids["chen"])
    tt.gen_sheet(ctx, ids["chen"], "body")
    tt.gen_sheet(ctx, ids["chen"], "face")
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    run_video(ctx, {"shot_id": ids["shot"]})
    face = tt.cell(sv, ids["chen"], "face_front")[0]
    assert runner.calls[-1].meta["refs"] == [str(sv.media.abs(face.file_path))]
    with db.session() as s:
        v = selected_candidate(s, "shot", ids["shot"], "video")
        assert [r["label"] for r in v.params["inputs"]["refs"]] == ["陈师傅 三视图·正脸"]
    assert not tt.stale(sv, ids["shot"], "video")


def test_video_without_face_cells_brings_only_the_portrait(tmp_path, db):
    from backend.tests import test_turnaround as tt

    ctx, runner, ids = tt.setup(tmp_path, db, image_engine="h3_image")
    sv = ctx.services
    _, chen_path = tt.put_portrait(sv, ids["chen"])
    tt.gen_sheet(ctx, ids["chen"], "body")       # 全身格子不进视频
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    run_video(ctx, {"shot_id": ids["shot"]})
    assert runner.calls[-1].meta["refs"] == [str(chen_path)]


def test_old_video_with_portrait_and_cell_is_not_stale_and_skipped(tmp_path, db):
    """P1-9 之前出的视频（每人定妆照 + 三视图格）：换规则后不判过期、不白跑 GPU。"""
    from backend.app.views import shot_detail
    from backend.domain.refs import legacy_video_refs
    from backend.domain.slots import add_candidate, get_or_create_slot
    from backend.pipeline.prompting import video_input_hash
    from backend.tests import test_turnaround as tt

    ctx, runner, ids = tt.setup(tmp_path, db, image_engine="h3_image")
    sv = ctx.services
    tt.put_portrait(sv, ids["chen"])
    tt.gen_sheet(ctx, ids["chen"], "face")
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    with db.session() as s:
        shot, _, proj, style = load_shot(s, ids["shot"])
        old = [r.candidate_id for r in legacy_video_refs(s, shot)]
        assert len(old) == 2
        ff = selected_candidate(s, "shot", ids["shot"], "first_frame")
        h_old = video_input_hash(shot, proj, style, ff.id, "h3_video", True, old)
        add_candidate(s, get_or_create_slot(s, "shot", ids["shot"], "video"), file_path="x.mp4",
                      engine="h3_video", params={}, input_hash=h_old, job_id=None)
    n = len(runner.calls)
    assert run_video(ctx, {"shot_id": ids["shot"]}) == {"skipped": True, "candidate_ids": []}
    assert len(runner.calls) == n
    with db.session() as s:
        shot, _, proj, style = load_shot(s, ids["shot"])
        d = shot_detail(s, sv, shot, proj, style)
        assert not next(x for x in d.slots if x.kind == "video").stale


def test_project_settings_pace_and_auto_retry_via_api(api):
    api.post("/api/projects", json={"title": "demo"})
    r = api.get("/api/projects/demo").json()
    assert r["shot_seconds"] is None and r["auto_retry_video"] is False
    r = api.patch("/api/projects/demo", json={"shot_seconds": 3, "auto_retry_video": True})
    assert r.status_code == 200
    assert r.json()["shot_seconds"] == 3 and r.json()["auto_retry_video"] is True
    r = api.patch("/api/projects/demo", json={"shot_seconds": 0})
    assert r.json()["shot_seconds"] is None and r.json()["auto_retry_video"] is True
    assert api.patch("/api/projects/demo", json={"shot_seconds": 7}).status_code in (400, 422)
