"""续季 / 跨项目复用资产：把角色、场景、道具连同图片从一个项目复制到另一个项目。"""
import pytest
from sqlalchemy import select

from backend.domain.asset_import import import_assets
from backend.domain.characters import portrait_of
from backend.domain.locations import location_image_of, location_image_stale, location_spec
from backend.domain.media import MediaStore
from backend.domain.models import (Candidate, Character, Location, Project, Prop, Slot, StyleBible,
                                   Variation)
from backend.domain.props import prop_image_of
from backend.domain.slots import add_candidate, get_or_create_slot, slots_for
from backend.domain.turnaround import (cell_hash, cell_state, sheet_hash, sheet_inputs, sheet_slot_kind,
                                       sheet_state, view_slot_kind, look_of)

ANCHOR = "Photorealistic live-action photography, warm palette."


def _file(media: MediaStore, slug: str, kind: str, content: bytes) -> str:
    p = media.new_media_path(slug, kind, "png")
    p.write_bytes(content)
    return media.rel(p)


def seed_source(db, media: MediaStore, slug="s1"):
    """源项目：林夏（两张定妆照选第二张、全身设定图 + 一格、变体）、直播间（场景图）、保温杯（道具图）。"""
    with db.session() as s:
        sb = StyleBible(anchor_text=ANCHOR, palette="warm", mood_keywords=["tense"], negative="blur")
        s.add(sb)
        p = Project(slug=slug, title="提词员", style_bible=sb, negative="lowres")
        s.add(p)
        s.flush()
        c = Character(project_id=p.id, name="林夏", description="主播", visual_prompt="a young anchor",
                      core_features="short hair", voice="zh-female-1", turnaround_look="full body look",
                      design_options=[{"label": "A", "visual_prompt": "x", "core_features": "y"}])
        s.add(c)
        s.flush()
        s.add(Variation(character_id=c.id, name="婚纱", visual_prompt="in a wedding dress"))
        slot = get_or_create_slot(s, "character", c.id, "portrait")
        add_candidate(s, slot, file_path=_file(media, slug, "portrait", b"p1"), engine="upload",
                      params={}, input_hash="h1", job_id=None)
        p2 = add_candidate(s, slot, file_path=_file(media, slug, "portrait", b"p2"), engine="upload",
                           params={}, input_hash="h2", job_id=None, force_select=True)
        s.flush()
        _, negative = sheet_inputs(c, "body")
        sh = sheet_hash("body", look_of(c), ANCHOR, negative, "h3_image", p2.id)
        sslot = get_or_create_slot(s, "character", c.id, sheet_slot_kind("body"))
        sheet = add_candidate(s, sslot, file_path=_file(media, slug, "turnaround", b"sheet"),
                              engine="h3_image", params={"portrait_candidate_id": p2.id},
                              input_hash=sh, job_id=7)
        vslot = get_or_create_slot(s, "character", c.id, view_slot_kind("front_full"))
        add_candidate(s, vslot, file_path=_file(media, slug, "turnaround", b"cell"), engine="crop",
                      params={"view": "front_full", "sheet": "body", "sheet_candidate_id": sheet.id,
                              "box": [0, 0, 1, 1]},
                      input_hash=cell_hash(sh, "front_full"), job_id=7)
        loc = Location(project_id=p.id, name="直播间", time_of_day="夜", visual_prompt="a studio room")
        s.add(loc)
        s.flush()
        lslot = get_or_create_slot(s, "location", loc.id, "location_image")
        add_candidate(s, lslot, file_path=_file(media, slug, "location", b"loc"), engine="zimage",
                      params={"spec": location_spec(loc)}, input_hash="l", job_id=None)
        prop = Prop(project_id=p.id, name="a dented red thermos", description="保温杯")
        s.add(prop)
        s.flush()
        pslot = get_or_create_slot(s, "prop", prop.id, "prop_image")
        add_candidate(s, pslot, file_path=_file(media, slug, "prop", b"prop"), engine="zimage",
                      params={}, input_hash="x", job_id=None)
        return {"project": p.id, "char": c.id, "loc": loc.id, "prop": prop.id, "portrait": p2.id}


def make_target(db, slug="s2", **kw):
    with db.session() as s:
        p = Project(slug=slug, title="提词员 第二季", **kw)
        s.add(p)
        s.flush()
        return p.id


def run(db, media, src_slug="s1", dst_slug="s2", **kw):
    with db.session() as s:
        src = s.scalar(select(Project).where(Project.slug == src_slug))
        dst = s.scalar(select(Project).where(Project.slug == dst_slug))
        return import_assets(s, media, source=src, target=dst, **kw)


@pytest.fixture
def media(tmp_path):
    return MediaStore(tmp_path / "data")


def test_import_copies_rows_media_and_selection(db, media):
    ids = seed_source(db, media)
    make_target(db)
    rep = run(db, media, character_ids=[ids["char"]], location_ids=[ids["loc"]],
              prop_ids=[ids["prop"]], with_style=True)
    assert [(x.kind, x.name) for x in rep.imported] == [
        ("character", "林夏"), ("location", "直播间"), ("prop", "a dented red thermos")]
    assert rep.skipped == []
    # 定妆照只复制选定的那张 + 设定图 + 一格 + 场景图 + 道具图
    assert rep.files_copied == 5
    with db.session() as s:
        dst = s.scalar(select(Project).where(Project.slug == "s2"))
        c = s.scalar(select(Character).where(Character.project_id == dst.id))
        assert c.id != ids["char"]
        assert (c.description, c.visual_prompt, c.core_features, c.voice, c.turnaround_look) == (
            "主播", "a young anchor", "short hair", "zh-female-1", "full body look")
        assert c.design_options == [{"label": "A", "visual_prompt": "x", "core_features": "y"}]
        assert [(v.name, v.visual_prompt) for v in c.variations] == [("婚纱", "in a wedding dress")]
        # 定妆照：一个候选，选定，文件在目标项目目录里、内容是源项目选定的那张
        pslot = next(x for x in slots_for(s, "character", c.id) if x.kind == "portrait")
        assert len(pslot.candidates) == 1
        pc = portrait_of(s, c.id)
        assert pc is not None and pc.id != ids["portrait"]
        assert pc.file_path.startswith("s2/media/portrait/")
        assert media.abs(pc.file_path).read_bytes() == b"p2"
        # 三视图：格子指向复制后的设定图；按新定妆照 id 重算哈希，不显示过期
        sheet, stale = sheet_state(s, c, "body")
        assert sheet is not None and not stale and sheet.job_id is None
        assert sheet.params["portrait_candidate_id"] == pc.id
        cell, cell_stale = cell_state(s, c, "front_full")
        assert cell.params["sheet_candidate_id"] == sheet.id and not cell_stale
        assert cell.input_hash == cell_hash(sheet.input_hash, "front_full")
        loc = s.scalar(select(Location).where(Location.project_id == dst.id))
        li = location_image_of(s, loc.id)
        assert li.file_path.startswith("s2/media/location/") and not location_image_stale(loc, li)
        prop = s.scalar(select(Prop).where(Prop.project_id == dst.id))
        assert prop.description == "保温杯"
        assert media.abs(prop_image_of(s, prop.id).file_path).read_bytes() == b"prop"
        assert dst.negative == "lowres" and dst.style_bible.anchor_text == ANCHOR


def test_sheet_is_stale_when_target_style_differs(db, media):
    """不沿用风格时目标项目的风格锚不同：设定图本来就是按别的风格出的，显示过期（如实）。"""
    ids = seed_source(db, media)
    make_target(db)
    rep = run(db, media, character_ids=[ids["char"]])
    assert not rep.style_copied
    with db.session() as s:
        dst = s.scalar(select(Project).where(Project.slug == "s2"))
        assert dst.style_bible_id is None
        c = dst.characters[0]
        _, stale = sheet_state(s, c, "body")
        assert stale


def test_source_untouched_and_no_cross_project_links(db, media):
    ids = seed_source(db, media)
    make_target(db)
    with db.session() as s:
        before = {(c.id, c.file_path, c.slot_id) for c in s.scalars(select(Candidate))}
    run(db, media, all_assets=True)
    with db.session() as s:
        src_char = s.get(Character, ids["char"])
        assert src_char.project_id == ids["project"] and src_char.name == "林夏"
        assert portrait_of(s, ids["char"]).id == ids["portrait"]
        after = {(c.id, c.file_path, c.slot_id) for c in s.scalars(select(Candidate))}
        assert before <= after
        for _, path, _ in before:
            assert media.abs(path).is_file()
        # 新候选全在 s2 目录、挂在 s2 的资产上
        dst = s.scalar(select(Project).where(Project.slug == "s2"))
        dst_owner = {("character", x.id) for x in dst.characters} | {
            ("location", x.id) for x in dst.locations} | {("prop", x.id) for x in dst.props}
        all_ids = {c.id for c in s.scalars(select(Candidate))}
        for c in s.scalars(select(Candidate)):
            if (c.id, c.file_path, c.slot_id) in before:
                continue
            slot = s.get(Slot, c.slot_id)
            assert (slot.owner_type, slot.owner_id) in dst_owner
            assert c.file_path.startswith("s2/")
            for k, v in (c.params or {}).items():
                if k.endswith("_candidate_id") and v is not None:
                    assert v in all_ids and v not in {b[0] for b in before}
    # 删掉源项目的角色不影响目标项目
    with db.session() as s:
        s.delete(s.get(Character, ids["char"]))
    with db.session() as s:
        dst = s.scalar(select(Project).where(Project.slug == "s2"))
        assert [c.name for c in dst.characters] == ["林夏"]


def test_all_candidates_option(db, media):
    ids = seed_source(db, media)
    make_target(db)
    rep = run(db, media, character_ids=[ids["char"]], candidates="all")
    assert rep.files_copied == 4   # 两张定妆照 + 设定图 + 一格
    with db.session() as s:
        c = s.scalar(select(Character).where(Character.name == "林夏", Character.id != ids["char"]))
        pslot = next(x for x in slots_for(s, "character", c.id) if x.kind == "portrait")
        assert len(pslot.candidates) == 2
        assert media.abs(portrait_of(s, c.id).file_path).read_bytes() == b"p2"


def test_conflict_skip_by_default_and_rename(db, media):
    ids = seed_source(db, media)
    tid = make_target(db)
    with db.session() as s:
        s.add(Character(project_id=tid, name="林夏", visual_prompt="mine"))
        s.add(Prop(project_id=tid, name="A Dented  Red Thermos"))   # 按英文说法判重名，不分大小写
    rep = run(db, media, all_assets=True)
    assert {(x.kind, x.name) for x in rep.skipped} == {("character", "林夏"),
                                                        ("prop", "a dented red thermos")}
    assert all("同名" in x.reason for x in rep.skipped)
    assert [x.name for x in rep.imported] == ["直播间"]
    with db.session() as s:
        mine = s.scalar(select(Character).where(Character.project_id == tid))
        assert mine.visual_prompt == "mine"   # 原来的不被覆盖

    rep = run(db, media, character_ids=[ids["char"]], on_conflict="rename", rename_suffix="（第二季）")
    assert [(x.name, x.renamed_from) for x in rep.imported] == [("林夏（第二季）", "林夏")]
    rep = run(db, media, character_ids=[ids["char"]], on_conflict="rename", rename_suffix="（第二季）")
    assert rep.imported[0].name == "林夏（第二季2）"
    with db.session() as s:
        names = sorted(c.name for c in s.scalars(select(Character).where(Character.project_id == tid)))
        assert names == ["林夏", "林夏（第二季2）", "林夏（第二季）"]


def test_copy_style_makes_own_copy(db, media):
    seed_source(db, media)
    make_target(db)
    rep = run(db, media, with_style=True)
    assert rep.style_copied and rep.imported == []
    with db.session() as s:
        src = s.scalar(select(Project).where(Project.slug == "s1"))
        dst = s.scalar(select(Project).where(Project.slug == "s2"))
        assert dst.style_bible_id not in (None, src.style_bible_id)
        assert dst.style_bible.anchor_text == ANCHOR and dst.style_bible.mood_keywords == ["tense"]
        dst.style_bible.anchor_text = "changed"
    with db.session() as s:
        src = s.scalar(select(Project).where(Project.slug == "s1"))
        assert src.style_bible.anchor_text == ANCHOR


def test_bad_requests(db, media):
    seed_source(db, media)
    make_target(db)
    with pytest.raises(ValueError):
        run(db, media, dst_slug="s1", all_assets=True)
    with pytest.raises(LookupError):
        run(db, media, character_ids=[999])
    with pytest.raises(ValueError):
        run(db, media, all_assets=True, on_conflict="overwrite")


def test_missing_file_is_warned_and_partial_failure_cleans_up(db, media, monkeypatch):
    ids = seed_source(db, media)
    make_target(db)
    with db.session() as s:
        media.abs(location_image_of(s, ids["loc"]).file_path).unlink()
    rep = run(db, media, location_ids=[ids["loc"]])
    assert rep.imported[0].files == 0 and rep.warnings and "找不到" in rep.warnings[0]
    with db.session() as s:
        loc = s.scalar(select(Location).where(Location.name == "直播间", Location.id != ids["loc"]))
        assert slots_for(s, "location", loc.id) == []

    # 复制到一半出错：已拷的文件删掉，数据库回滚
    import backend.domain.asset_import as mod
    real = mod._copy_row
    calls = {"n": 0}

    def boom(s, kind, row, target, name):
        calls["n"] += 1
        if kind == "prop":
            raise RuntimeError("boom")
        return real(s, kind, row, target, name)

    monkeypatch.setattr(mod, "_copy_row", boom)
    make_target(db, slug="s3")
    with pytest.raises(RuntimeError):
        run(db, media, dst_slug="s3", all_assets=True)
    assert not (media.root / "s3" / "media").exists() or not any(
        p.is_file() for p in (media.root / "s3" / "media").rglob("*"))
    with db.session() as s:
        dst = s.scalar(select(Project).where(Project.slug == "s3"))
        assert dst.characters == [] and dst.locations == []


# ---------------------------------------------------------------- API

def test_api_import_and_summary(api, tmp_path):
    sv = api.app.state.services
    seed_source(sv.db, sv.media)
    api.post("/api/projects", json={"title": "第二季", "slug": "s2"})
    summary = api.get("/api/projects/s1/asset-summary").json()
    assert [c["name"] for c in summary["characters"]] == ["林夏"]
    assert summary["characters"][0]["thumb_url"].startswith("/media/s1/media/portrait/")
    assert summary["characters"][0]["image_count"] == 4
    assert summary["props"][0]["name"] == "a dented red thermos" and summary["has_style"]

    char_id = summary["characters"][0]["id"]
    r = api.post("/api/projects/s2/import-assets",
                 json={"source": "s1", "character_ids": [char_id],
                       "location_ids": [summary["locations"][0]["id"]]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["name"] for x in body["imported"]] == ["林夏", "直播间"]
    assert body["files_copied"] == 4
    chars = api.get("/api/projects/s2/characters").json()
    assert chars[0]["name"] == "林夏" and chars[0]["portrait_url"].startswith("/media/s2/")
    assert api.get(chars[0]["portrait_url"]).content == b"p2"

    again = api.post("/api/projects/s2/import-assets",
                     json={"source": "s1", "character_ids": [char_id]}).json()
    assert again["imported"] == [] and again["skipped"][0]["name"] == "林夏"

    assert api.post("/api/projects/s2/import-assets",
                    json={"source": "nope", "all": True}).status_code == 404
    assert api.post("/api/projects/s2/import-assets",
                    json={"source": "s2", "all": True}).status_code == 400
    assert api.post("/api/projects/s2/import-assets",
                    json={"source": "s1", "all": True, "on_conflict": "overwrite"}).status_code == 422


def test_api_create_project_with_import(api):
    sv = api.app.state.services
    seed_source(sv.db, sv.media)
    r = api.post("/api/projects", json={"title": "第二季", "slug": "s2",
                                         "import_from": {"source": "s1", "kinds": ["character"]}})
    assert r.status_code == 201, r.text
    assert r.json()["style_bible_id"] is not None
    assert [c["name"] for c in api.get("/api/projects/s2/characters").json()] == ["林夏"]
    assert api.get("/api/projects/s2/locations").json() == []
    assert api.get("/api/projects/s2/style").json()["anchor_text"] == ANCHOR

    # 源项目不存在：整个建项目回滚
    r = api.post("/api/projects", json={"title": "x", "slug": "s3", "import_from": {"source": "nope"}})
    assert r.status_code == 404
    assert api.get("/api/projects/s3").status_code == 404
