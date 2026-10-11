"""角色三视图：一张设定图（全身 4 视角 / 头像 3 视角）一次出图，自动切成单个视角的格子；
首帧 / 视频参考图按景别和机位带上最匹配的一格。"""
import io

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from PIL import Image, ImageDraw
from sqlalchemy import text

from backend.app.views import shot_detail
from backend.domain.characters import set_portrait
from backend.domain.db import MIGRATIONS, Database
from backend.domain.models import Candidate, Character, Episode, Job, Shot, StyleBible
from backend.domain.refs import shot_refs
from backend.domain.slots import get_or_create_slot, selected_candidate
from backend.domain.turnaround import (SHEETS, VIEW_KEYS, VIEW_LABELS, cell_state, crop_cells,
                                       select_sheet, sheet_prompt, sheet_slot_kind, sheet_state,
                                       style_line, view_for_shot, view_slot_kind)
from backend.llm.schemas import TurnaroundLook
from backend.pipeline.characters import run_turnaround_look, run_turnaround_sheet
from backend.pipeline.common import load_episode
from backend.pipeline.keyframes import run_keyframe
from backend.pipeline.prompting import keyframe_input_hash
from backend.pipeline.videos import run_video
from backend.queue.errors import JobError
from backend.tests.helpers import FakeCtx, FakeRunner, make_gateway, make_services, seed_episode

LOOK = ("A 45-year-old Chinese male auto mechanic, lean wiry build, short black hair with grey "
        "temples, navy cotton-twill work jacket with orange reflective band")
FULL = ("A lean 45-year-old Chinese mechanic, navy cotton-twill work jacket half zipped over a grey "
        "T-shirt, matching navy work trousers, black leather work shoes, beige rag in the left chest "
        "pocket, short salt-and-pepper hair, sun-tanned skin, crow's feet at the eyes")
ANCHOR = ("Photorealistic live-action commercial photography, fine film grain. Warm golden-hour "
          "palette in a busy garage.")
IMG = ("medium shot, 50mm. a mechanic in a navy jacket looks at a phone in a bright "
       "workshop office")


def words(n: int, w: str = "detail") -> str:
    return " ".join([w] * n)


def png(n: int = 4, w: int = 1792, h: int = 768) -> bytes:
    """n 个"人物"均匀排在纯白背景上的设定图。"""
    im = Image.new("RGB", (w, h), (248, 248, 248))
    d = ImageDraw.Draw(im)
    step = w // n
    for i in range(n):
        cx = step * i + step // 2
        d.rectangle([cx - 70, 40, cx + 70, h - 40], fill=(30, 40, 80))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class SheetRunner(FakeRunner):
    """出图时写一张真的设定图（全身 4 个人物，头像 3 个），好让切图有东西可切。"""

    def __init__(self, counts: list[int] | None = None):
        super().__init__()
        self.counts = list(counts or [])

    def run(self, call, *, run_dir, cancel, on_progress=None, timeout=3600.0):
        files = super().run(call, run_dir=run_dir, cancel=cancel, on_progress=on_progress)
        n = self.counts.pop(0) if self.counts else (3 if "_face" in str(call.workflow) else 4)
        files[0].write_bytes(png(n))
        return files


def setup(tmp_path, db, *, image_engine="zimage", llm=None, counts=None):
    runner = SheetRunner(counts)
    sv = make_services(tmp_path, db, comfy=runner, llm=llm)
    proj_id, ep_id = seed_episode(db, image_engine=image_engine, video_engine="h3_video")
    with db.session() as s:
        sb = StyleBible(anchor_text=ANCHOR, negative="cartoon", character_rules="只有两个角色")
        s.add(sb)
        s.flush()
        s.get(Episode, ep_id).project.style_bible_id = sb.id
        chen = Character(project_id=proj_id, name="陈师傅", description="45 岁中国汽修师傅",
                         visual_prompt=LOOK, core_features="藏青工装、灰白鬓角")
        yu = Character(project_id=proj_id, name="小雨", visual_prompt="young woman " + words(10))
        s.add_all([chen, yu])
        s.flush()
        sh = Shot(episode_id=ep_id, order=0, code="S01", shot_size="特写", image_prompt=IMG,
                  motion_prompt="he nods",
                  cast=[{"character_id": yu.id, "variation_id": None},
                        {"character_id": chen.id, "variation_id": None}])
        s.add(sh)
        s.flush()
        ids = {"chen": chen.id, "yu": yu.id, "project": proj_id, "episode": ep_id, "shot": sh.id}
    return FakeCtx(sv, job_id=21), runner, ids


def put_portrait(sv, char_id, name="p.png"):
    with sv.db.session() as s:
        c = set_portrait(s, sv.media, s.get(Character, char_id), png(1, 64, 96), name)
        return c.id, sv.media.abs(c.file_path)


def gen_sheet(ctx, char_id, sheet="body", engine=None) -> int:
    ctx.job_id += 1
    payload = {"character_id": char_id, "sheet": sheet}
    if engine:
        payload["engine"] = engine
    return run_turnaround_sheet(ctx, payload)["candidate_ids"][0]


def cell(sv, char_id, view):
    with sv.db.session() as s:
        return cell_state(s, s.get(Character, char_id), view)


def stale(sv, shot_id, kind):
    with sv.db.session() as s:
        sh = s.get(Shot, shot_id)
        _, proj = load_episode(s, sh.episode_id)
        d = shot_detail(s, sv, sh, proj, proj.style_bible)
        return next(x.stale for x in d.slots if x.kind == kind)


# ---------------------------------------------------------------- 视角与设定图
def test_views_and_sheets():
    assert VIEW_KEYS == ("front_full", "three_quarter", "profile", "back",
                         "face_front", "face_three_quarter", "face_profile")
    assert [VIEW_LABELS[k] for k in VIEW_KEYS] == [
        "正面全身", "3/4 侧", "侧面", "背面", "正脸", "3/4 脸", "侧脸"]
    assert SHEETS["body"].views == ("front_full", "three_quarter", "profile", "back")
    assert SHEETS["face"].views == ("face_front", "face_three_quarter", "face_profile")
    assert SHEETS["body"].aspect == "21:9" and SHEETS["face"].aspect == "21:9"
    assert sheet_slot_kind("body") == "turnaround_sheet_body"
    assert view_slot_kind("back") == "turnaround_view_back"


def test_style_line_keeps_only_the_medium():
    assert style_line(ANCHOR) == ("Photorealistic live-action commercial photography, fine film grain, "
                                  "everything in sharp focus.")
    assert style_line("") == "Photorealistic live-action photography, everything in sharp focus."
    # 动漫风格锚：photo_style 认不出成像词时退到默认写实（以前取第一句）
    assert style_line("Anime cel shading, bold outlines").endswith("everything in sharp focus.")


def test_style_line_drops_shallow_focus_and_anamorphic():
    """设定图要正交、整张清晰：锚里的浅景深、变形宽银幕、光晕、焦段、剧情类型词不进风格句。"""
    anchor = ("Photorealistic live-action cinematic still, Chinese urban-fringe drama, shot on a 35mm "
              "anamorphic lens with shallow depth of field, subtle Kodak Vision3 500T film grain, "
              "colors are slightly desaturated, skin looks real.")
    s = style_line(anchor).lower()
    for bad in ("depth of field", "anamorphic", "35mm", "drama", "cinematic", "still"):
        assert bad not in s, bad
    assert "desaturated" in s and s.endswith("everything in sharp focus.")


def test_sheet_templates_have_no_negations():
    """默认三视图引擎（H3）没有负面提示词：模板里不写「No text / without borders / no smile」这类否定句，
    改成正面写法。"""
    import re as _re

    for sheet in ("body", "face"):
        low = sheet_prompt(sheet, FULL, ANCHOR).lower()
        assert not _re.search(r"\bno\b|\bwithout\b|\bnot\b", low.replace(FULL.lower(), "")), sheet
        assert "nothing else in the image" in low and "seamless backdrop" in low


def test_old_sheet_made_with_v1_template_is_not_stale(tmp_path, db):
    """模板换成正面写法后，按老模板出的设定图不判过期（过期的格子不进参考图，会牵连首帧、视频）。"""
    from backend.domain.slots import add_candidate
    from backend.domain.turnaround import SHEET_NEGATIVE, legacy_sheet_hash, look_of, sheet_state
    from backend.pipeline.prompting import join_negative

    sv = make_services(tmp_path, db)
    proj_id, ep_id = seed_episode(db, image_engine="zimage")
    with db.session() as s:
        ch = Character(project_id=proj_id, name="陈师傅", visual_prompt=LOOK)
        s.add(ch)
        s.flush()
        neg = join_negative("", "", SHEET_NEGATIVE)
        h = legacy_sheet_hash("body", look_of(ch), "", neg, "zimage", None)
        add_candidate(s, get_or_create_slot(s, "character", ch.id, sheet_slot_kind("body")),
                      file_path="x.png", engine="zimage", params={}, input_hash=h, job_id=None)
        sel, stale = sheet_state(s, ch, "body")
        assert sel is not None and stale is False
        ch.visual_prompt = LOOK + ", red scarf"
        s.flush()
        assert sheet_state(s, ch, "body")[1] is True


def test_body_sheet_prompt_is_fixed_template():
    p = sheet_prompt("body", FULL, ANCHOR)
    low = p.lower()
    assert FULL in p
    # 版式：一排四个全身，顺序固定，同比例，正交，纯色背景，平光
    for phrase in ("turnaround sheet", "exactly four full-body figures", "far left: front view",
                   "three-quarter view", "side profile", "90 degrees", "far right: back view",
                   "same height and the same scale", "orthographic", "seamless", "white",
                   "even", "nothing else in the image"):
        assert phrase in low, phrase
    assert low.index("front view") < low.index("three-quarter view") < low.index("side profile") \
        < low.index("back view")
    # 风格只取第一句（画风），不带场景和光线，免得背景被带成车间
    assert "Photorealistic live-action commercial photography" in p
    assert "garage" not in low and "golden-hour" not in low


def test_face_sheet_prompt():
    low = sheet_prompt("face", FULL, ANCHOR).lower()
    for phrase in ("exactly three", "head and shoulders", "far left: front view",
                   "three-quarter view", "far right: side profile", "90 degrees", "white"):
        assert phrase in low, phrase
    assert "back view" not in low


@pytest.mark.parametrize("size,extra,expected", [
    ("大特写", "", "face_front"),
    ("特写", "", "face_front"),
    ("近景", "", "face_three_quarter"),
    ("中近景", "", "face_three_quarter"),
    ("中景", "", "three_quarter"),
    ("全景", "", "front_full"),
    ("远景", "", "front_full"),
    ("", "", "three_quarter"),
    ("中景", "陈师傅的背影走向车间", "back"),
    ("近景", "shot from behind, he walks away", "back"),
    ("全景", "golden sun from behind the camera casts long shadows", "front_full"),
    ("中景", "侧面，他低头看手机", "profile"),
    ("全景", "side profile of the mechanic", "profile"),
    ("特写", "侧脸，他低头看手机", "face_profile"),
    ("近景", "side profile of the mechanic", "face_profile"),
    # 仰拍 / 俯拍是机位不是视角：按景别挑
    ("近景", "仰拍，他站在升降机下", "face_three_quarter"),
    ("全景", "low-angle shot of the lift", "front_full"),
    ("中景", "俯拍车间", "three_quarter"),
])
def test_view_for_shot(size, extra, expected):
    sh = Shot(shot_size=size, camera_move="", action=extra, image_prompt="a mechanic")
    assert view_for_shot(sh) == expected


# ---------------------------------------------------------------- character.turnaround_look
def test_look_condenses_visual_prompt(tmp_path, db):
    gw, prov = make_gateway([{"description": FULL}])
    ctx, runner, ids = setup(tmp_path, db, llm=gw)
    assert run_turnaround_look(ctx, {"character_id": ids["chen"]}) == {"words": len(FULL.split())}
    user = prov.calls[0]["messages"][0]["content"]
    assert LOOK in user and "藏青工装" in user
    assert "鞋" in prov.calls[0]["system"]
    with db.session() as s:
        assert s.get(Character, ids["chen"]).turnaround_look == FULL


def test_look_retries_on_bad_length(tmp_path, db):
    gw, prov = make_gateway([{"description": "navy jacket"}, {"description": FULL}])
    ctx, runner, ids = setup(tmp_path, db, llm=gw)
    run_turnaround_look(ctx, {"character_id": ids["chen"]})
    assert len(prov.calls) == 2 and "英文单词" in prov.calls[1]["messages"][-1]["content"]
    with pytest.raises(ValueError):
        TurnaroundLook.model_validate({"description": words(200)})


def test_look_needs_visual_prompt(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, llm=make_gateway([])[0])
    with db.session() as s:
        s.get(Character, ids["chen"]).visual_prompt = ""
    with pytest.raises(JobError, match="外貌提示词"):
        run_turnaround_look(ctx, {"character_id": ids["chen"]})


# ---------------------------------------------------------------- image.turnaround：出设定图 + 切图
def test_body_sheet_zimage_text_only_and_cropped(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    with db.session() as s:
        s.get(Character, ids["chen"]).turnaround_look = FULL
    sid = gen_sheet(ctx, ids["chen"], engine="zimage")       # zimage 不吃参考图：不需要定妆照
    call = runner.calls[0]
    assert call.engine == "zimage" and call.dropped_refs == []
    assert call.meta["size"] == [1792, 768]
    assert FULL in call.meta["prompt"] and "cartoon" in call.meta["negative_ignored"]
    with db.session() as s:
        ch = s.get(Character, ids["chen"])
        sel, is_stale = sheet_state(s, ch, "body")
        assert sel.id == sid and not is_stale
        assert sel.params["sheet"] == "body"
        cells = [cell_state(s, ch, v) for v in SHEETS["body"].views]
        assert all(c is not None and not st for c, st in cells)
        for v, (c, _) in zip(SHEETS["body"].views, cells):
            assert c.params["sheet_candidate_id"] == sid and c.params["view"] == v
            assert c.params["method"] == "segment"
            w, h = Image.open(sv.media.abs(c.file_path)).size
            assert h > w                                  # 切出来是竖的单人
        boxes = [c.params["box"] for c, _ in cells]
        assert [b[0] for b in boxes] == sorted(b[0] for b in boxes)
        assert cell_state(s, ch, "face_front") == (None, False)


def test_h3_sheet_uses_portrait_ref(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, image_engine="h3_image")
    sv = ctx.services
    with pytest.raises(JobError, match="定妆照"):
        gen_sheet(ctx, ids["chen"], engine="h3_image")
    pid, ppath = put_portrait(sv, ids["chen"])
    sid = gen_sheet(ctx, ids["chen"], engine="h3_image")
    call = runner.calls[-1]
    assert call.meta["refs"] == [str(ppath)]
    assert LOOK in call.meta["prompt"]                   # 没有全身描述时用外貌提示词
    with db.session() as s:
        assert s.get(Candidate, sid).params["portrait_candidate_id"] == pid
    # 换定妆照：设定图和切出来的格子都过期
    put_portrait(sv, ids["chen"], "v2.png")
    with db.session() as s:
        ch = s.get(Character, ids["chen"])
        assert sheet_state(s, ch, "body")[1] is True
        assert cell_state(s, ch, "back")[1] is True


def test_default_engine_is_h3_when_portrait_exists(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, image_engine="zimage")
    put_portrait(ctx.services, ids["chen"])
    gen_sheet(ctx, ids["chen"])
    assert runner.calls[-1].engine == "h3_image"


def test_face_sheet(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    put_portrait(ctx.services, ids["chen"])
    gen_sheet(ctx, ids["chen"], "face")
    call = runner.calls[-1]
    assert call.meta["size"] == [1792, 768]
    with db.session() as s:
        ch = s.get(Character, ids["chen"])
        assert all(cell_state(s, ch, v)[0] is not None for v in SHEETS["face"].views)
        assert cell_state(s, ch, "front_full")[0] is None
    with pytest.raises(JobError, match="设定图"):
        run_turnaround_sheet(ctx, {"character_id": ids["chen"], "sheet": "hands"})


def test_regenerate_keeps_selection_and_select_recrops(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    first = gen_sheet(ctx, ids["chen"], engine="zimage")
    second = gen_sheet(ctx, ids["chen"], engine="zimage")
    assert runner.calls[0].meta["seed"] != runner.calls[1].meta["seed"]
    with db.session() as s:
        slot = get_or_create_slot(s, "character", ids["chen"], sheet_slot_kind("body"))
        assert [c.id for c in slot.candidates] == [first, second]
        assert slot.selected_candidate_id == first          # 输入没变：不抢选定
    back_before = cell(sv, ids["chen"], "back")[0].id
    with db.session() as s:
        select_sheet(s, sv.media, s.get(Character, ids["chen"]), second)
    back_after = cell(sv, ids["chen"], "back")[0]
    assert back_after.id != back_before and back_after.params["sheet_candidate_id"] == second
    # 选回第一张：复用当时切的格子，不重复存
    with db.session() as s:
        select_sheet(s, sv.media, s.get(Character, ids["chen"]), first)
    assert cell(sv, ids["chen"], "back")[0].id == back_before
    with db.session() as s:
        assert len(get_or_create_slot(s, "character", ids["chen"],
                                      view_slot_kind("back")).candidates) == 2
        with pytest.raises(LookupError):
            select_sheet(s, sv.media, s.get(Character, ids["chen"]), back_before)


def test_wrong_figure_count_falls_back_to_equal_and_manual_recrop(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, counts=[5])
    sv = ctx.services
    gen_sheet(ctx, ids["chen"], engine="zimage")
    assert cell(sv, ids["chen"], "front_full")[0].params["method"] == "equal"
    assert any("等宽" in m for m in ctx.logs)
    with db.session() as s:
        ch = s.get(Character, ids["chen"])
        assert crop_cells(s, sv.media, ch, "body", method="equal") == "equal"
        with pytest.raises(LookupError, match="还没有"):
            crop_cells(s, sv.media, ch, "face")


def test_look_change_makes_sheet_stale(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    gen_sheet(ctx, ids["chen"], engine="zimage")
    with db.session() as s:
        s.get(Character, ids["chen"]).turnaround_look = FULL
    with db.session() as s:
        ch = s.get(Character, ids["chen"])
        assert sheet_state(s, ch, "body")[1] and cell_state(s, ch, "front_full")[1]
    gen_sheet(ctx, ids["chen"], engine="zimage")            # 过期了：新出的自动选定并重新切
    with db.session() as s:
        ch = s.get(Character, ids["chen"])
        assert not sheet_state(s, ch, "body")[1] and not cell_state(s, ch, "front_full")[1]


# ---------------------------------------------------------------- 参考图打包
def test_refs_portraits_first_then_matching_cells(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    yu_pid, _ = put_portrait(sv, ids["yu"], "yu.png")
    chen_pid, _ = put_portrait(sv, ids["chen"], "chen.png")
    gen_sheet(ctx, ids["chen"], "face")
    gen_sheet(ctx, ids["yu"], "body")
    chen_face = cell(sv, ids["chen"], "face_front")[0].id
    yu_tq = cell(sv, ids["yu"], "three_quarter")[0].id
    with db.session() as s:
        refs = shot_refs(s, s.get(Shot, ids["shot"]))  # 特写 → 正脸；小雨没有头像，退到 3/4 侧
    assert [r.candidate_id for r in refs] == [yu_pid, chen_pid, yu_tq, chen_face]
    assert [r.label for r in refs] == ["小雨 定妆照", "陈师傅 定妆照", "小雨 三视图·3/4 侧",
                                       "陈师傅 三视图·正脸"]


def test_refs_skip_stale_cells(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    put_portrait(sv, ids["chen"])
    gen_sheet(ctx, ids["chen"], "face")
    new_pid, _ = put_portrait(sv, ids["chen"], "v2.png")  # 设定图是按旧定妆照出的
    with db.session() as s:
        assert [r.candidate_id for r in shot_refs(s, s.get(Shot, ids["shot"]))] == [new_pid]


def test_keyframe_packs_cell_and_hash_changes_only_when_cell_used(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, image_engine="h3_image")
    sv = ctx.services
    chen_pid, chen_path = put_portrait(sv, ids["chen"])
    with db.session() as s:
        sh, proj = s.get(Shot, ids["shot"]), s.get(Episode, ids["episode"]).project
        old = keyframe_input_hash(sh, proj, proj.style_bible, "h3_image", [chen_pid])
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    with db.session() as s:
        # 没有三视图时哈希和以前完全一样：老项目不会因为这个功能变过期
        assert selected_candidate(s, "shot", ids["shot"], "first_frame").input_hash == old
    gen_sheet(ctx, ids["chen"], "body")          # 这一镜是特写，全身设定图的格子用不上……
    # ……但正脸没有时会退到 3/4 侧：退路里有全身的格子，所以要看实际选到哪一格
    with db.session() as s:
        used = [r.label for r in shot_refs(s, s.get(Shot, ids["shot"]))]
    assert used == ["陈师傅 定妆照", "陈师傅 三视图·3/4 侧"]
    assert stale(sv, ids["shot"], "first_frame")
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    assert not stale(sv, ids["shot"], "first_frame")
    gen_sheet(ctx, ids["chen"], "face")
    assert stale(sv, ids["shot"], "first_frame")         # 现在用正脸了
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    call = runner.calls[-1]
    face = cell(sv, ids["chen"], "face_front")[0]
    assert call.meta["refs"] == [str(chen_path), str(sv.media.abs(face.file_path))]
    assert any("陈师傅 三视图·正脸" in m for m in ctx.logs)
    assert not stale(sv, ids["shot"], "first_frame")
    # 再出一张全身设定图并选定（全身格子全换了）：特写镜头用的是正脸，哈希不变
    other = gen_sheet(ctx, ids["chen"], "body")
    with db.session() as s:
        select_sheet(s, sv.media, s.get(Character, ids["chen"]), other)
    assert not stale(sv, ids["shot"], "first_frame")


def test_video_packs_cell(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, image_engine="h3_image")
    sv = ctx.services
    put_portrait(sv, ids["chen"])
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    run_video(ctx, {"shot_id": ids["shot"]})
    assert not stale(sv, ids["shot"], "video")
    gen_sheet(ctx, ids["chen"], "face")
    assert stale(sv, ids["shot"], "video")
    run_video(ctx, {"shot_id": ids["shot"]})
    assert len(runner.calls[-1].meta["refs"]) == 2
    assert not stale(sv, ids["shot"], "video")


# ---------------------------------------------------------------- 迁移
def test_migration_replaces_plan_with_look(tmp_path):
    d = Database(tmp_path / "m.db")
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", d.url)
    command.upgrade(cfg, "d5e8a1c3b9f2")
    with d.engine.begin() as conn:
        conn.execute(text("INSERT INTO projects (slug, title, aspect, image_engine, video_engine, seed, "
                          "negative, created_at) VALUES ('p', 'p', '9:16', 'h3_image', 'h3_video', 1, '', "
                          "'2026-10-09')"))
        conn.execute(text("INSERT INTO characters (project_id, name, description, visual_prompt, "
                          "core_features, design_options, turnaround_plan) VALUES "
                          "(1, '陈', '', '', '', '[]', '{\"back\": \"x\"}')"))
    d.upgrade()
    with d.session() as s:
        assert s.get(Character, 1).turnaround_look == ""
    with d.engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(characters)"))]
    assert "turnaround_plan" not in cols
    command.downgrade(cfg, "d5e8a1c3b9f2")
    d.engine.dispose()


# ---------------------------------------------------------------- API
def make_char(api, look=LOOK):
    api.post("/api/projects", json={"title": "demo", "image_engine": "h3_image"})
    api.post("/api/projects/demo/episodes", json={"number": 1, "source_text": "剧本"})
    r = api.post("/api/projects/demo/characters", json={"name": "陈师傅", "visual_prompt": look})
    assert r.status_code == 201, r.text
    return r.json()


def jobs(api, job_type):
    with api.app.state.services.db.session() as s:
        return [(j.owner_type, j.owner_id, dict(j.payload), j.gpu)
                for j in s.query(Job).filter(Job.type == job_type).order_by(Job.id)]


def test_api_look_and_listing(api):
    ch = make_char(api)
    r = api.post(f"/api/characters/{ch['id']}/turnaround/look", json={})
    assert r.status_code == 200, r.text
    assert jobs(api, "character.turnaround_look") == [
        ("character", ch["id"], {"character_id": ch["id"]}, False)]
    assert api.post("/api/characters/999/turnaround/look", json={}).status_code == 404
    listed = api.get("/api/projects/demo/characters").json()[0]
    t = listed["turnaround"]
    assert t["look"] == "" and t["default_engine"] == "h3_image"
    assert [x["sheet"] for x in t["sheets"]] == ["body", "face"]
    assert t["sheets"][0] == {"sheet": "body", "label": "全身三视图",
                              "views": ["front_full", "three_quarter", "profile", "back"],
                              "selected_candidate_id": None, "url": None, "stale": False,
                              "crop_method": None, "candidates": []}
    assert [c["view"] for c in t["cells"]] == list(VIEW_KEYS)
    assert t["cells"][3] == {"view": "back", "label": "背面", "sheet": "body",
                             "selected_candidate_id": None, "url": None, "stale": False}
    assert [j["type"] for j in listed["active_jobs"]] == ["character.turnaround_look"]
    r = api.patch(f"/api/characters/{ch['id']}/turnaround/look", json={"look": f"  {FULL}  "})
    assert r.status_code == 200 and r.json()["turnaround"]["look"] == FULL


def test_api_look_without_prompt_is_400(api):
    ch = make_char(api, look="")
    r = api.post(f"/api/characters/{ch['id']}/turnaround/look", json={})
    assert r.status_code == 400 and "外貌提示词" in r.json()["detail"]


def test_api_generate_submits_one_job_per_sheet(api):
    ch = make_char(api)
    sv = api.app.state.services
    r = api.post(f"/api/characters/{ch['id']}/turnaround/generate", json={})
    assert r.status_code == 400 and "定妆照" in r.json()["detail"]
    with sv.db.session() as s:
        set_portrait(s, sv.media, s.get(Character, ch["id"]), png(1, 64, 96), "chen.png")
    r = api.post(f"/api/characters/{ch['id']}/turnaround/generate", json={})
    assert r.status_code == 200, r.text
    js = jobs(api, "image.turnaround")
    assert [j[2] for j in js] == [{"character_id": ch["id"], "sheet": "body", "engine": "h3_image"},
                                  {"character_id": ch["id"], "sheet": "face", "engine": "h3_image"}]
    assert js[0][3] is True
    r = api.post(f"/api/characters/{ch['id']}/turnaround/generate",
                 json={"sheets": ["face"], "count": 2, "engine": "zimage"})
    assert len(r.json()["job_ids"]) == 2
    assert jobs(api, "image.turnaround")[-1][2]["engine"] == "zimage"
    assert api.post(f"/api/characters/{ch['id']}/turnaround/generate",
                    json={"sheets": ["hands"]}).status_code == 422
    r = api.post(f"/api/characters/{ch['id']}/turnaround/generate", json={"engine": "wan22_i2v"})
    assert r.status_code == 400
    listed = api.get("/api/projects/demo/characters").json()[0]
    running = [j for j in listed["active_jobs"] if j["type"] == "image.turnaround"]
    assert [j["sheet"] for j in running] == ["body", "face", "face", "face"]


def test_api_select_and_recrop(api):
    ch = make_char(api)
    sv = api.app.state.services
    with sv.db.session() as s:
        set_portrait(s, sv.media, s.get(Character, ch["id"]), png(1, 64, 96), "chen.png")
    ctx = FakeCtx(sv, job_id=50)
    sv.comfy = SheetRunner()
    a = gen_sheet(ctx, ch["id"])
    b = gen_sheet(ctx, ch["id"])
    t = api.get("/api/projects/demo/characters").json()[0]["turnaround"]
    body = t["sheets"][0]
    assert body["selected_candidate_id"] == a and len(body["candidates"]) == 2
    assert body["url"].startswith("/media/") and body["crop_method"] == "segment"
    cells = {c["view"]: c for c in t["cells"]}
    assert cells["back"]["url"].startswith("/media/") and cells["back"]["stale"] is False
    r = api.post(f"/api/characters/{ch['id']}/turnaround/sheets/body/select", json={"candidate_id": b})
    assert r.status_code == 200, r.text
    assert r.json()["turnaround"]["sheets"][0]["selected_candidate_id"] == b
    r = api.post(f"/api/characters/{ch['id']}/turnaround/sheets/body/crop", json={"method": "equal"})
    assert r.status_code == 200 and r.json()["turnaround"]["sheets"][0]["crop_method"] == "equal"
    r = api.post(f"/api/characters/{ch['id']}/turnaround/sheets/face/crop", json={})
    assert r.status_code == 404
    r = api.post(f"/api/characters/{ch['id']}/turnaround/sheets/face/select", json={"candidate_id": b})
    assert r.status_code == 404
    with sv.db.session() as s:
        set_portrait(s, sv.media, s.get(Character, ch["id"]), png(1, 64, 96), "chen2.png")
    t = api.get("/api/projects/demo/characters").json()[0]["turnaround"]
    assert t["sheets"][0]["stale"] is True
    assert {c["view"]: c for c in t["cells"]}["back"]["stale"] is True
    assert {c["view"]: c for c in t["cells"]}["face_front"]["stale"] is False  # 没出过不算过期
