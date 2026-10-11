"""资产选择器（列资产、拿资产当首帧 / 构图参考）与道具图（出图、选定、参考图打包顺序、老首帧不过期）。"""
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from backend.app.views import shot_detail
from backend.domain.assets import (ASSET_ENGINE, add_first_frame_from_asset,
                                   add_shot_refs_from_assets, center_crop, list_assets)
from backend.domain.characters import set_portrait
from backend.domain.locations import set_location_image
from backend.domain.models import (ArchivedCandidate, Character, Episode, Location, Project, Prop,
                                   SceneBlock, Shot, StyleBible)
from backend.domain.props import prop_image_of, set_prop_image, shot_props, sync_props
from backend.domain.refs import Ref, add_shot_ref, shot_ref_images, shot_refs, split_refs
from backend.domain.slots import add_candidate, get_or_create_slot, select_candidate, selected_candidate
from backend.pipeline.keyframes import run_keyframe
from backend.pipeline.props import prop_prompt, run_prop_image
from backend.queue.errors import JobError
from backend.tests.helpers import FakeCtx, FakeRunner, make_services, seed_episode

THERMOS = "a dented red thermos with a chipped lid"
ANCHOR = "Cinematic still, 35mm lens, Kodak film grain, natural skin texture, rainy night street."


def png(w: int = 9, h: int = 16, color=(200, 0, 0)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


class FrameFFmpeg:
    """只会截帧的假 ffmpeg：首帧 / 尾帧各写一张不同颜色的 PNG。"""

    def __init__(self):
        self.calls = []

    def _write(self, out, color):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 9), color).save(out, "PNG")
        return Path(out)

    def first_frame(self, video, out):
        self.calls.append(("first", Path(video).name))
        return self._write(out, (0, 255, 0))

    def last_frame(self, video, out):
        self.calls.append(("last", Path(video).name))
        return self._write(out, (0, 0, 255))


def setup(tmp_path, db, image_engine="h3_image"):
    runner = FakeRunner()
    sv = make_services(tmp_path, db, comfy=runner, ffmpeg=FrameFFmpeg())
    proj_id, ep_id = seed_episode(db, image_engine=image_engine)
    with db.session() as s:
        proj = s.get(Project, proj_id)
        proj.style_bible = StyleBible(anchor_text=ANCHOR)
        loc = Location(project_id=proj_id, name="修车行", visual_prompt="A cramped garage.")
        s.add(loc)
        s.flush()
        blk = SceneBlock(episode_id=ep_id, order=0, location_id=loc.id, text="陈师傅修车")
        chen = Character(project_id=proj_id, name="陈师傅", visual_prompt="a man in his fifties")
        s.add_all([blk, chen])
        s.flush()
        s1 = Shot(episode_id=ep_id, order=0, code="S01", scene_block_id=blk.id,
                  image_prompt="medium shot, a mechanic pours tea from " + THERMOS, shot_size="中景",
                  cast=[{"character_id": chen.id, "variation_id": None}],
                  props=[THERMOS, "  A DENTED red thermos with a chipped lid "])
        s2 = Shot(episode_id=ep_id, order=1, code="S02", scene_block_id=blk.id,
                  image_prompt="close-up of a wrench", shot_size="特写", props=["a rusty wrench"])
        s.add_all([s1, s2])
        s.flush()
        ids = {"project": proj_id, "episode": ep_id, "loc": loc.id, "chen": chen.id,
               "s1": s1.id, "s2": s2.id}
    return FakeCtx(sv, job_id=31), runner, ids


def _detail(sv, shot_id):
    with sv.db.session() as s:
        shot = s.get(Shot, shot_id)
        proj = s.get(Episode, shot.episode_id).project
        return shot_detail(s, sv, shot, proj, proj.style_bible)


def _ff_slot(d):
    return next(x for x in d.slots if x.kind == "first_frame")


def _props(sv, project_id):
    with sv.db.session() as s:
        return {p.name: p.id for p, _ in sync_props(s, s.get(Project, project_id))}


def _prop_image(sv, prop_id, color=(10, 10, 10)):
    with sv.db.session() as s:
        return set_prop_image(s, sv.media, s.get(Prop, prop_id), "demo", png(color=color), "p.png").id


def _portrait(sv, char_id):
    with sv.db.session() as s:
        return set_portrait(s, sv.media, s.get(Character, char_id), png(), "p.png").id


# ------------------------------------------------------------ 道具
def test_sync_props_dedupes_by_wording_and_keeps_order(tmp_path, db):
    ctx, _, ids = setup(tmp_path, db)
    names = _props(ctx.services, ids["project"])
    assert list(names) == [THERMOS, "a rusty wrench"]
    # 再列一次不重复建行
    assert _props(ctx.services, ids["project"]) == names
    with db.session() as s:
        assert s.query(Prop).count() == 2
        assert [p.name for p in shot_props(s, s.get(Shot, ids["s1"]), ids["project"])] == [THERMOS]


def test_prop_prompt_is_object_only_and_english():
    p = prop_prompt(Prop(name=THERMOS, visual_prompt=""), ANCHOR)
    assert p.startswith("A dented red thermos with a chipped lid, shown on its own as an object reference")
    assert "plain white seamless background" in p
    assert "Style: " in p and "Kodak film grain" in p
    assert "skin" not in p and "rainy" not in p.lower().split("style:")[0]
    assert "no people" not in p.lower()
    extra = prop_prompt(Prop(name=THERMOS, visual_prompt="Steel body with a faded sticker"), "")
    assert extra.endswith("Steel body with a faded sticker.")
    with pytest.raises(JobError, match="英文说法"):
        prop_prompt(Prop(name="红色保温杯", visual_prompt=""), "")
    assert prop_prompt(Prop(name="红色保温杯", visual_prompt="a red thermos"), "").startswith("A red thermos,")


def test_prop_image_job_creates_selects_and_uses_step_engine(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, image_engine="zimage")
    pid = _props(ctx.services, ids["project"])[THERMOS]
    ctx.services.settings.save({"step:prop": "krea2"})
    first = run_prop_image(ctx, {"prop_id": pid})["candidate_ids"][0]
    call = runner.calls[0]
    assert call.engine == "krea2" and call.meta["prompt"].startswith("A dented red thermos")
    ctx.job_id += 1
    second = run_prop_image(ctx, {"prop_id": pid, "engine": "zimage"})["candidate_ids"][0]
    assert runner.calls[1].engine == "zimage"
    with db.session() as s:
        sel = prop_image_of(s, pid)
        assert sel.id == first and second != first          # 第二张不抢选定
        assert sel.file_path.startswith("demo/media/prop/")


def test_pack_order_location_shot_ref_cast_then_props(tmp_path, db):
    ctx, _, ids = setup(tmp_path, db)
    sv = ctx.services
    pid = _props(sv, ids["project"])[THERMOS]
    portrait = _portrait(sv, ids["chen"])
    prop_img = _prop_image(sv, pid)
    with db.session() as s:
        loc = set_location_image(s, sv.media, s.get(Location, ids["loc"]), png(), "l.png").id
        ref = add_shot_ref(s, sv.media, s.get(Shot, ids["s1"]), "demo", png(), "r.png").id
    with db.session() as s:
        shot = s.get(Shot, ids["s1"])
        refs = shot_refs(s, shot)
        assert [r.candidate_id for r in refs] == [loc, ref, portrait, prop_img]
        assert [r.kind for r in refs] == ["location", "shot_ref", "portrait", "prop"]
        assert refs[-1].label.startswith("道具·a dented red thermos")
        # 视频不带道具
        assert [r.kind for r in shot_refs(s, shot, video=True)] == ["portrait"]
        # 别的镜头没写这个道具：不带
        assert all(r.kind != "prop" for r in shot_refs(s, s.get(Shot, ids["s2"])))


def test_split_refs_props_fill_last():
    packed = [Ref(1, "l", "场景图", "location"), Ref(2, "a", "构图参考 1", "shot_ref"),
              Ref(3, "p", "陈师傅 定妆照", "portrait"), Ref(4, "c", "陈师傅 三视图·正面", "turnaround"),
              Ref(5, "x", "道具·thermos", "prop")]
    assert [r.candidate_id for r in split_refs(packed, 4)[0]] == [1, 2, 3, 4]
    assert [r.candidate_id for r in split_refs(packed, 2)[0]] == [1, 3]
    assert [r.candidate_id for r in split_refs(packed, 5)[0]] == [1, 2, 3, 4, 5]


def test_old_first_frame_not_stale_after_prop_image(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    _portrait(sv, ids["chen"])
    run_keyframe(ctx, {"shot_id": ids["s1"]})
    assert not _ff_slot(_detail(sv, ids["s1"])).stale
    pid = _props(sv, ids["project"])[THERMOS]
    first_img = _prop_image(sv, pid)
    # 老首帧出的时候还没有道具图：不判过期，批量出首帧也跳过
    assert not _ff_slot(_detail(sv, ids["s1"])).stale
    ctx.job_id += 1
    assert run_keyframe(ctx, {"shot_id": ids["s1"]}).get("skipped") is True
    assert len(runner.calls) == 1
    # 重拍：这次带上道具图
    ctx.job_id += 1
    run_keyframe(ctx, {"shot_id": ids["s1"], "force": True})
    with db.session() as s:
        ff = selected_candidate(s, "shot", ids["s1"], "first_frame")
        assert [r.get("kind") for r in ff.params["inputs"]["refs"]] == ["portrait", "prop"]
    assert len(runner.calls[-1].meta["refs"]) == 2
    assert not _ff_slot(_detail(sv, ids["s1"])).stale
    # 换一张道具图：带道具图出的首帧过期
    with db.session() as s:
        second = set_prop_image(s, sv.media, s.get(Prop, pid), "demo", png(color=(1, 2, 3)), "q.png").id
    d = _ff_slot(_detail(sv, ids["s1"]))
    assert d.stale and "道具·" in d.stale_reason
    with db.session() as s:
        select_candidate(s, first_img)
    assert not _ff_slot(_detail(sv, ids["s1"])).stale
    assert second != first_img


def test_props_api_list_upload_use(api):
    api.post("/api/projects", json={"title": "提词员", "slug": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"number": 1, "title": "t"}).json()
    api.post(f"/api/episodes/{ep['id']}/shots", json={"props": [THERMOS]})
    api.post(f"/api/episodes/{ep['id']}/shots", json={"props": [THERMOS, "红色保温杯"]})
    rows = api.get("/api/projects/demo/props").json()
    assert [r["name"] for r in rows] == [THERMOS, "红色保温杯"]
    assert [u["shot_code"] for u in rows[0]["uses"]] == ["S01", "S02"]
    pid, bad = rows[0]["id"], rows[1]["id"]
    assert api.post(f"/api/props/{bad}/images/draw", json={"count": 1}).status_code == 400
    r = api.post(f"/api/props/{pid}/images/draw", json={"count": 2})
    assert r.status_code == 200 and len(r.json()["job_ids"]) == 2
    up = api.post(f"/api/props/{pid}/image", files={"file": ("a.png", png(), "image/png")}).json()
    first = up["image_candidate_id"]
    assert up["image_url"].startswith("/media/demo/media/prop/")
    up2 = api.post(f"/api/props/{pid}/image", files={"file": ("b.png", png(color=(0, 0, 0)), "image/png")}).json()
    assert up2["image_candidate_id"] != first and len(up2["images"]) == 2
    assert api.post(f"/api/props/{pid}/images/{first}/use").json()["image_candidate_id"] == first
    assert api.post(f"/api/props/{bad}/images/{first}/use").status_code == 404
    patched = api.patch(f"/api/props/{bad}", json={"visual_prompt": "a red thermos"}).json()
    assert patched["visual_prompt"] == "a red thermos"
    assert api.post(f"/api/props/{bad}/images/draw", json={"count": 1}).status_code == 200
    assert api.delete("/api/projects/demo").status_code == 204


# ------------------------------------------------------------ 资产列表
def _video(sv, shot_id, name="v.mp4"):
    dst = sv.media.new_media_path("demo", "video", ".mp4")
    dst.write_bytes(b"mp4")
    with sv.db.session() as s:
        return add_candidate(s, get_or_create_slot(s, "shot", shot_id, "video"),
                             file_path=sv.media.rel(dst), engine="h3_video", params={},
                             input_hash="v", job_id=None).id


def _first_frame(sv, shot_id, w=9, h=16):
    dst = sv.media.new_media_path("demo", "first_frame", ".png")
    dst.write_bytes(png(w, h))
    with sv.db.session() as s:
        return add_candidate(s, get_or_create_slot(s, "shot", shot_id, "first_frame"),
                             file_path=sv.media.rel(dst), engine="zimage", params={},
                             input_hash="f", job_id=None).id


def test_list_assets_by_category(tmp_path, db):
    ctx, _, ids = setup(tmp_path, db)
    sv = ctx.services
    portrait = _portrait(sv, ids["chen"])
    with db.session() as s:
        other = set_portrait(s, sv.media, s.get(Character, ids["chen"]), png(), "p2.png").id
        select_candidate(s, portrait)
        set_location_image(s, sv.media, s.get(Location, ids["loc"]), png(16, 9), "l.png")
        add_shot_ref(s, sv.media, s.get(Shot, ids["s2"]), "demo", png(), "r.png")
    _prop_image(sv, _props(sv, ids["project"])[THERMOS])
    ff = _first_frame(sv, ids["s2"])
    vid = _video(sv, ids["s2"])
    with db.session() as s:
        s.add(ArchivedCandidate(project_id=ids["project"], episode_id=ids["episode"], shot_code="S09",
                                kind="first_frame", file_path="demo/media/first_frame/old.png",
                                was_selected=True))
    with db.session() as s:
        ep = s.get(Episode, ids["episode"])
        assets = list_assets(s, sv.media, ep)
        by = {}
        for a in assets:
            by.setdefault(a.kind, []).append(a)
        assert set(by) == {"character", "location", "prop", "shot", "archived", "shot_ref"}
        chars = by["character"]
        assert [a.id for a in chars] == [f"cand-{portrait}", f"cand-{other}"]   # 选定的在前
        assert chars[0].selected and not chars[1].selected and chars[0].label == "陈师傅 定妆照"
        assert (chars[0].width, chars[0].height) == (9, 16)
        assert by["location"][0].label == "场景图·修车行" and by["location"][0].width == 16
        assert by["prop"][0].source == THERMOS
        shot = {a.id: a for a in by["shot"]}
        assert shot[f"cand-{ff}"].label == "S02 首帧" and shot[f"cand-{ff}"].shot_id == ids["s2"]
        assert shot[f"cand-{vid}"].media == "video" and shot[f"cand-{vid}"].width is None
        assert by["archived"][0].label == "S09 首帧（历史）"
        assert by["shot_ref"][0].label == "S02 构图参考 1"
        only = list_assets(s, sv.media, ep, "location")
        assert {a.kind for a in only} == {"location"}
        with pytest.raises(ValueError, match="资产分类"):
            list_assets(s, sv.media, ep, "nope")


def test_assets_api(api):
    api.post("/api/projects", json={"title": "提词员", "slug": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"number": 1, "title": "t"}).json()
    shot = api.post(f"/api/episodes/{ep['id']}/shots", json={}).json()
    api.post(f"/api/shots/{shot['id']}/refs", files={"file": ("a.png", png(), "image/png")})
    rows = api.get(f"/api/episodes/{ep['id']}/assets").json()
    assert len(rows) == 1 and rows[0]["kind"] == "shot_ref"
    assert rows[0]["url"].startswith("/media/demo/media/shot_ref/") and rows[0]["thumb_url"] == rows[0]["url"]
    assert rows[0]["width"] == 9 and rows[0]["height"] == 16 and rows[0]["id"].startswith("cand-")
    assert api.get(f"/api/episodes/{ep['id']}/assets?kind=character").json() == []
    assert api.get(f"/api/episodes/{ep['id']}/assets?kind=bad").status_code == 400
    assert api.get("/api/episodes/999/assets").status_code == 404


# ------------------------------------------------------------ 用资产
def test_first_frame_from_asset_selects_and_never_stale(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    _portrait(sv, ids["chen"])
    other = _first_frame(sv, ids["s2"])
    with db.session() as s:
        shot = s.get(Shot, ids["s1"])
        c = add_first_frame_from_asset(s, sv, shot, "demo", ids["project"], "9:16", f"cand-{other}")
        assert c.engine == ASSET_ENGINE and c.params["from"] == "S02 首帧"
        assert c.params["source"] == "asset" and c.params["asset"] == f"cand-{other}"
        assert sv.media.abs(c.file_path).read_bytes() == png()   # 复制了一份
        assert c.file_path.startswith("demo/media/first_frame/") and c.id != other
        cid = c.id
    d = _detail(sv, ids["s1"])
    ff = _ff_slot(d)
    assert ff.selected_candidate_id == cid and not ff.stale
    # 批量出首帧不覆盖用户选的图；重拍（force）照常出
    assert run_keyframe(ctx, {"shot_id": ids["s1"]}).get("skipped") is True
    assert runner.calls == []
    run_keyframe(ctx, {"shot_id": ids["s1"], "force": True})
    assert len(runner.calls) == 1
    # 开了接上一镜：资产首帧按接上一镜的规则过期
    with db.session() as s:
        select_candidate(s, cid)
        s.get(Shot, ids["s1"]).continue_from_prev = True
    assert _ff_slot(_detail(sv, ids["s1"])).stale


def test_first_frame_from_asset_without_select_and_crop(tmp_path, db):
    ctx, _, ids = setup(tmp_path, db)
    sv = ctx.services
    keep = _first_frame(sv, ids["s1"])
    with db.session() as s:
        loc = set_location_image(s, sv.media, s.get(Location, ids["loc"]), png(32, 18), "l.png").id
    with db.session() as s:
        shot = s.get(Shot, ids["s1"])
        c = add_first_frame_from_asset(s, sv, shot, "demo", ids["project"], "9:16", f"cand-{loc}",
                                       crop=True, select=False)
        assert c.params["cropped"] is True and c.params["from"] == "场景图·修车行"
        with Image.open(sv.media.abs(c.file_path)) as im:
            assert im.size == (10, 18)
        assert selected_candidate(s, "shot", ids["s1"], "first_frame").id == keep


def test_center_crop():
    data, did = center_crop(png(9, 16), "9:16")
    assert not did
    data, did = center_crop(png(100, 100), "16:9")
    assert did
    with Image.open(BytesIO(data)) as im:
        assert im.size == (100, 56)


def test_video_asset_needs_frame_and_extracts(tmp_path, db):
    ctx, _, ids = setup(tmp_path, db)
    sv = ctx.services
    vid = _video(sv, ids["s2"])
    with db.session() as s:
        shot = s.get(Shot, ids["s1"])
        with pytest.raises(ValueError, match="首帧还是尾帧"):
            add_first_frame_from_asset(s, sv, shot, "demo", ids["project"], "9:16", f"cand-{vid}")
        c = add_first_frame_from_asset(s, sv, shot, "demo", ids["project"], "9:16", f"cand-{vid}",
                                       "last")
        assert c.params["from"] == "S02 视频·尾帧" and c.params["frame"] == "last"
        with Image.open(sv.media.abs(c.file_path)) as im:
            assert im.getpixel((0, 0)) == (0, 0, 255)
        refs = add_shot_refs_from_assets(s, sv, shot, "demo", ids["project"], [(f"cand-{vid}", "first")])
        assert refs[0].params["from"] == "S02 视频·首帧"
    assert [x[0] for x in sv.ffmpeg.calls] == ["last", "first"]


def test_shot_refs_from_assets_limit_and_independent_copy(tmp_path, db):
    ctx, _, ids = setup(tmp_path, db)
    sv = ctx.services
    portrait = _portrait(sv, ids["chen"])
    ff = _first_frame(sv, ids["s2"])
    with db.session() as s:
        shot = s.get(Shot, ids["s1"])
        add_shot_refs_from_assets(s, sv, shot, "demo", ids["project"],
                                  [(f"cand-{portrait}", None), (f"cand-{ff}", None)])
        refs = shot_ref_images(s, ids["s1"])
        assert [c.engine for c in refs] == [ASSET_ENGINE, ASSET_ENGINE]
        assert [c.params["from"] for c in refs] == ["陈师傅 定妆照", "S02 首帧"]
        with pytest.raises(ValueError, match="最多 2 张"):
            add_shot_refs_from_assets(s, sv, shot, "demo", ids["project"], [(f"cand-{ff}", None)])
    # 原候选删掉：构图参考是复制的，照样能用
    with db.session() as s:
        from backend.domain.models import Candidate
        s.delete(s.get(Candidate, ff))
    with db.session() as s:
        packed = shot_refs(s, s.get(Shot, ids["s1"]))
        assert [r.kind for r in packed][:2] == ["shot_ref", "shot_ref"]
        assert all(sv.media.abs(r.file_path).is_file() for r in packed)


def test_assets_from_other_project_rejected(tmp_path, db):
    ctx, _, ids = setup(tmp_path, db)
    sv = ctx.services
    other_proj, other_ep = seed_episode(db, slug="other")
    with db.session() as s:
        o = Shot(episode_id=other_ep, order=0, code="S01")
        s.add(o)
        s.flush()
        oid = o.id
    foreign = _first_frame(sv, oid)
    with db.session() as s:
        shot = s.get(Shot, ids["s1"])
        with pytest.raises(LookupError, match="本项目没有"):
            add_first_frame_from_asset(s, sv, shot, "demo", ids["project"], "9:16", f"cand-{foreign}")
        with pytest.raises(ValueError, match="资产 id"):
            add_first_frame_from_asset(s, sv, shot, "demo", ids["project"], "9:16", "x-1")


def test_asset_use_api(api):
    api.post("/api/projects", json={"title": "提词员", "slug": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"number": 1, "title": "t"}).json()
    a = api.post(f"/api/episodes/{ep['id']}/shots", json={}).json()
    b = api.post(f"/api/episodes/{ep['id']}/shots", json={}).json()
    api.post(f"/api/shots/{a['id']}/refs", files={"file": ("a.png", png(16, 9), "image/png")})
    asset = api.get(f"/api/episodes/{ep['id']}/assets").json()[0]["id"]
    r = api.post(f"/api/shots/{b['id']}/refs/from-assets", json={"assets": [{"id": asset}]})
    assert r.status_code == 200 and r.json()["shot_refs"][0]["engine"] == "asset"
    r = api.post(f"/api/shots/{b['id']}/first-frame/from-asset", json={"id": asset, "crop": True})
    assert r.status_code == 200
    ff = next(x for x in r.json()["slots"] if x["kind"] == "first_frame")
    assert ff["selected_candidate_id"] == ff["candidates"][0]["id"] and ff["stale"] is False
    assert ff["candidates"][0]["params"]["cropped"] is True
    bad = api.post(f"/api/shots/{b['id']}/first-frame/from-asset", json={"id": asset, "frame": "middle"})
    assert bad.status_code == 422
    too_many = api.post(f"/api/shots/{b['id']}/refs/from-assets", json={"assets": [{"id": asset}, {"id": asset}]})
    assert too_many.status_code == 400 and "最多 2 张" in too_many.json()["detail"]
