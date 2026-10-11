"""多引擎协作：换引擎不让已有候选过期、批量运行不重出已选的；参考图资产换了、提示词 / 时长改了照样过期。
过期按候选自己的引擎判断（pipeline.freshness），参考图按那个引擎的上限截断。"""
from backend.app.views import shot_detail
from backend.domain.characters import add_portrait
from backend.domain.models import Character, Project, Shot, StyleBible
from backend.domain.slots import add_candidate, get_or_create_slot, select_candidate, selected_candidate
from backend.pipeline.keyframes import load_shot, run_keyframe
from backend.pipeline.prompting import keyframe_input_hash, stale_reason
from backend.pipeline.videos import run_video
from backend.tests.helpers import FakeCtx, FakeRunner, make_services, seed_episode
from backend.tests.test_browser_engines import FakeBrowserRunner

IMG = "medium close-up, 85mm. people wait in a dark repair shop"
NAMES = ["老周", "阿梅", "小陈", "老板"]


def setup(tmp_path, db, *, image_engine="h3_image", n_chars=4):
    """一个镜头、n_chars 个出场角色（各有定妆照）。H3 图像吃 9 张参考图，Gemini 网页只吃 3 张。"""
    runner = FakeRunner()
    sv = make_services(tmp_path, db, comfy=runner)
    sv.browser = FakeBrowserRunner()
    proj_id, ep_id = seed_episode(db, image_engine=image_engine, video_engine="h3_video")
    with db.session() as s:
        proj = s.get(Project, proj_id)
        proj.style_bible = StyleBible(anchor_text="ANCHOR", negative="cartoon")
        cast = []
        for i, name in enumerate(NAMES[:n_chars]):
            ch = Character(project_id=proj_id, name=name, visual_prompt=f"person {i}")
            s.add(ch)
            s.flush()
            add_portrait(s, ch.id, file_path=f"demo/media/portrait/{i}.png", engine="h3_image",
                         params={}, input_hash=f"p{i}", job_id=None)
            cast.append({"character_id": ch.id, "variation_id": None})
        sh = Shot(episode_id=ep_id, order=0, code="S01", image_prompt=IMG, cast=cast,
                  motion_prompt="they look up", audio_prompt="rain", duration=6.0)
        s.add(sh)
        s.flush()
        shot_id = sh.id
    return FakeCtx(sv, job_id=21), runner, shot_id


def slot_state(ctx, db, shot_id, kind="first_frame"):
    with db.session() as s:
        shot, _, proj, style = load_shot(s, shot_id)
        d = shot_detail(s, ctx.services, shot, proj, style)
        sl = next(x for x in d.slots if x.kind == kind)
        return sl.stale, sl.stale_reason


def set_shot(db, shot_id, **fields):
    with db.session() as s:
        sh = s.get(Shot, shot_id)
        for k, v in fields.items():
            setattr(sh, k, v)


# ------------------------------------------------------------ 首帧
def test_switch_shot_engine_keeps_selected_first_frame_fresh_and_batch_skips(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    first = run_keyframe(ctx, {"shot_id": shot_id})["candidate_ids"][0]   # H3：4 张定妆照全带上
    assert slot_state(ctx, db, shot_id) == (False, None)
    set_shot(db, shot_id, image_engine="gemini_image")                    # Gemini 网页只吃 3 张
    assert slot_state(ctx, db, shot_id) == (False, None)
    # 批量运行（不强制、一个候选）：选定的还是最新，跳过，不重出
    assert run_keyframe(ctx, {"shot_id": shot_id}) == {"skipped": True, "candidate_ids": []}
    assert len(runner.calls) == 1 and sv_browser_calls(ctx) == 0
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "first_frame").id == first


def sv_browser_calls(ctx) -> int:
    return len(ctx.services.browser.calls)


def test_candidates_from_engines_with_different_ref_caps_are_all_fresh(tmp_path, db):
    """Gemini 网页出的（3 张参考图）和 H3 出的（4 张）都不过期：只因引擎容量不同的截断不算。"""
    ctx, runner, shot_id = setup(tmp_path, db)
    h3 = run_keyframe(ctx, {"shot_id": shot_id})["candidate_ids"][0]
    set_shot(db, shot_id, image_engine="gemini_image")
    gem = run_keyframe(ctx, {"shot_id": shot_id, "force": True})["candidate_ids"][0]  # 手动重拍
    with db.session() as s:
        c = selected_candidate(s, "shot", shot_id, "first_frame")
        assert c.id == gem and c.engine == "gemini_image" and len(c.params["inputs"]["refs"]) == 3
    assert slot_state(ctx, db, shot_id) == (False, None)
    set_shot(db, shot_id, image_engine=None)                               # 回到项目的 H3
    assert slot_state(ctx, db, shot_id) == (False, None)
    assert run_keyframe(ctx, {"shot_id": shot_id}).get("skipped") is True
    with db.session() as s:
        select_candidate(s, h3)
    assert slot_state(ctx, db, shot_id) == (False, None)
    set_shot(db, shot_id, image_engine="gemini_image")
    assert slot_state(ctx, db, shot_id) == (False, None)


def test_portrait_swap_still_stales_candidate_of_other_engine(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    run_keyframe(ctx, {"shot_id": shot_id})
    set_shot(db, shot_id, image_engine="gemini_image")
    with db.session() as s:
        ch = s.query(Character).filter_by(name="老周").one()
        c = add_portrait(s, ch.id, file_path="demo/media/portrait/new.png", engine="h3_image",
                         params={}, input_hash="pnew", job_id=None)
        select_candidate(s, c.id)
    assert slot_state(ctx, db, shot_id) == (True, "老周 定妆照换了")
    # 过期了：批量运行按当前引擎重出
    assert run_keyframe(ctx, {"shot_id": shot_id}).get("skipped") is None
    assert sv_browser_calls(ctx) == 1
    assert slot_state(ctx, db, shot_id) == (False, None)


def test_prompt_change_stales_candidate_of_other_engine(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    run_keyframe(ctx, {"shot_id": shot_id})
    set_shot(db, shot_id, image_engine="gemini_image", image_prompt=IMG + ", rain")
    assert slot_state(ctx, db, shot_id) == (True, "提示词改了")


def test_dropped_portrait_beyond_cap_still_stales_on_swap(tmp_path, db):
    """Gemini 候选没带上第 4 个人的定妆照：换这张定妆照不影响它（它本来就没用），换前 3 个的会过期。"""
    ctx, runner, shot_id = setup(tmp_path, db, image_engine="gemini_image")
    run_keyframe(ctx, {"shot_id": shot_id})
    with db.session() as s:
        ch = s.query(Character).filter_by(name="老板").one()
        select_candidate(s, add_portrait(s, ch.id, file_path="demo/media/portrait/n.png",
                                         engine="h3_image", params={}, input_hash="pn",
                                         job_id=None).id)
    assert slot_state(ctx, db, shot_id) == (False, None)
    set_shot(db, shot_id, image_engine="h3_image")
    assert slot_state(ctx, db, shot_id) == (False, None)


def test_extra_candidates_from_new_engine_do_not_steal_selection(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    first = run_keyframe(ctx, {"shot_id": shot_id})["candidate_ids"][0]
    set_shot(db, shot_id, image_engine="gemini_image")
    run_keyframe(ctx, {"shot_id": shot_id, "candidates": 2})
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "first_frame").id == first
    # 强制重拍一张照常选中新的（现有规则）
    new = run_keyframe(ctx, {"shot_id": shot_id, "force": True})["candidate_ids"][0]
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "first_frame").id == new


def test_legacy_candidate_without_inputs_judged_by_its_own_engine(tmp_path, db):
    """老候选没有 inputs 摘要：哈希按它自己的引擎对得上就不过期；输入真变了时原因照旧是「旧记录无法细分」。"""
    ctx, runner, shot_id = setup(tmp_path, db, image_engine="zimage", n_chars=1)
    with db.session() as s:
        shot, _, proj, style = load_shot(s, shot_id)
        h = keyframe_input_hash(shot, proj, style, "zimage")
        add_candidate(s, get_or_create_slot(s, "shot", shot_id, "first_frame"),
                      file_path="demo/media/first_frame/old.png", engine="zimage", params={},
                      input_hash=h, job_id=None)
    set_shot(db, shot_id, image_engine="h3_image")
    assert slot_state(ctx, db, shot_id) == (False, None)
    assert run_keyframe(ctx, {"shot_id": shot_id}).get("skipped") is True
    set_shot(db, shot_id, image_prompt=IMG + ", fog")
    assert slot_state(ctx, db, shot_id) == (True, "输入已变化（旧记录无法细分）")


def test_stale_reason_never_mentions_engine():
    old = {"prompt": "p", "negative": "", "engine": "h3_image", "aspect": "9:16", "refs": []}
    new = {**old, "engine": "gemini_image"}
    assert stale_reason(old, new) == "输入已变化"
    assert stale_reason(old, {**new, "aspect": "16:9"}) == "画幅改了"


# ------------------------------------------------------------ 视频
def test_switch_video_engine_keeps_selected_video_fresh_and_batch_skips(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db, n_chars=1)
    run_keyframe(ctx, {"shot_id": shot_id})
    vid = run_video(ctx, {"shot_id": shot_id})["candidate_ids"][0]       # h3_video：有声、带定妆照
    assert slot_state(ctx, db, shot_id, "video") == (False, None)
    set_shot(db, shot_id, video_engine="wan22_i2v")                       # 无声、不吃参考图
    assert slot_state(ctx, db, shot_id, "video") == (False, None)
    n = len(runner.calls)
    assert run_video(ctx, {"shot_id": shot_id}) == {"skipped": True, "candidate_ids": []}
    assert len(runner.calls) == n
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "video").id == vid
    # 时长改了照样过期，批量运行按当前引擎重出
    set_shot(db, shot_id, duration=4.0)
    assert slot_state(ctx, db, shot_id, "video") == (True, "时长改了")
    assert run_video(ctx, {"shot_id": shot_id}).get("skipped") is None
    assert runner.calls[-1].model_family == "wan22"


def test_video_portrait_swap_still_stales_across_engines(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db, n_chars=1)
    run_keyframe(ctx, {"shot_id": shot_id})
    run_video(ctx, {"shot_id": shot_id})
    set_shot(db, shot_id, video_engine="wan22_i2v")
    with db.session() as s:
        ch = s.query(Character).filter_by(name="老周").one()
        select_candidate(s, add_portrait(s, ch.id, file_path="demo/media/portrait/n.png",
                                         engine="h3_image", params={}, input_hash="pn",
                                         job_id=None).id)
    # 首帧带过这张定妆照：首帧过期（原因是定妆照）；视频按它自己的引擎（h3_video 带参考图）也过期
    assert slot_state(ctx, db, shot_id) == (True, "老周 定妆照换了")
    assert slot_state(ctx, db, shot_id, "video") == (True, "老周 定妆照换了")
