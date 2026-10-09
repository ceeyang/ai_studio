import json
from pathlib import Path

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

import cli.studio as studio
from backend.app.views import shot_detail
from backend.domain.models import Character, Episode, Export, Project, Slot
from backend.importers.ai_videos import import_ai_videos
from backend.queue.errors import JobError
from backend.tests.helpers import make_services

YAML = """\
title: 测试广告 · 两镜
engines:
  image: minimax_h3
  video: minimax_h3
video:
  duration_sec: 5
  width: 768
  height: 1344
  steps: 20
  turbo: false
seed: 777
character_refs:
  - refs/master_chen.png
  - refs/xiao_yu.png
negative: >-
  lowres, blurry,
  watermark
end_card:
  seconds: 3
  lines:
    - {text: "测试店", size: 56}
dub:
  voices:
    chen: km-KH-PisethNeural
    xiaoyu: km-KH-SreymomNeural
  text: km
shots:
  - id: S01
    refs: [refs/xiao_yu.png, refs/master_chen.png]
    image_prompt: A woman (Xiao Yu) and a mechanic (Master Chen) at a counter.
    motion_prompt: Xiao Yu slides the phone.
    audio_prompt: Room tone. No music.
    subtitles:
      - {t: [0.6, 4.6], zh: "师傅，这个得换。", km: "ជាង!", speaker: "xiaoyu"}
    transition_out: hard_cut
  - id: S02
    refs: [refs/master_chen.png]
    duration_sec: 3
    image_prompt: Close-up of the mechanic (Master Chen).
    motion_prompt: He looks up.
    audio_prompt: A man's voice.
    subtitles:
      - {t: [0.2, 1.4], zh: "换？", km: "ដូរ?", speaker: "chen"}
      - {t: [1.5, 2.9], zh: "先让我看看。", km: "សុំមើលសិន។", speaker: "chen"}
    transition_out: fade_out_0.6
"""


def make_src(tmp_path: Path, name: str = "demo-ad") -> Path:
    src = tmp_path / "ai_videos" / name
    files = {
        "project.yaml": YAML.encode("utf-8"),
        "refs/master_chen.png": b"png-chen",
        "refs/xiao_yu.png": b"png-yu",
        "output/keyframes/kf_S01_00001_.png": b"kf1",
        "output/keyframes/kf_S02_00001_.png": b"kf2",
        "output/clips/clip_S01_00001_.mp4": b"clip1",
        "output/clips/clip_S02_00001_.mp4": b"clip2",
        "output/clips_dub/clip_S01_00001_.mp4": b"dub1",
        "output/clips_dub/clip_S02_00001_.mp4": b"dub2",
        "output/dub/S01_1.mp3": b"mp3",
        f"output/final/{name}.mp4": b"final",
        f"output/final/{name}_dub_captioned.mp4": b"final-dub-cap",
        f"output/final/{name}_subs.ass": b"ass",
    }
    for rel, data in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return src


def snapshot(src: Path) -> dict:
    return {p.relative_to(src).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in src.rglob("*") if p.is_file()}


@pytest.fixture
def sv(tmp_path, db):
    return make_services(tmp_path, db)


def test_import_maps_project_characters_episode_and_shots(tmp_path, sv):
    src = make_src(tmp_path)
    before = snapshot(src)
    summary = import_ai_videos(sv, src)
    assert snapshot(src) == before  # 原目录一字未动

    assert summary["slug"] == "demo-ad"
    assert summary["url"] == "/projects/demo-ad"
    c = summary["counts"]
    assert (c["characters"], c["shots"], c["keyframes"], c["videos"], c["dub_videos"],
            c["exports"], c["portraits"]) == (2, 2, 2, 2, 2, 2, 2)

    with sv.db.session() as s:
        p = s.scalar(select(Project).where(Project.slug == "demo-ad"))
        assert (p.title, p.aspect, p.image_engine, p.video_engine, p.seed) == (
            "测试广告 · 两镜", "9:16", "h3_image", "h3_video", 777)
        assert p.negative == "lowres, blurry, watermark"

        chars = {ch.name: ch for ch in s.scalars(select(Character).where(Character.project_id == p.id))}
        assert set(chars) == {"Master Chen", "Xiao Yu"}
        assert chars["Master Chen"].voice == "km-KH-PisethNeural"
        for ch in chars.values():
            slot = s.scalar(select(Slot).where(Slot.owner_type == "character",
                                               Slot.owner_id == ch.id, Slot.kind == "portrait"))
            assert slot.selected_candidate_id == slot.candidates[0].id
            assert sv.media.abs(slot.candidates[0].file_path).is_file()
        chen, yu = chars["Master Chen"], chars["Xiao Yu"]
        assert sv.media.abs(s.scalar(select(Slot).where(Slot.owner_id == chen.id, Slot.kind == "portrait"))
                            .candidates[0].file_path).read_bytes() == b"png-chen"

        ep = s.scalar(select(Episode).where(Episode.project_id == p.id))
        assert (ep.number, ep.title, ep.target_seconds, ep.status) == (1, "测试广告 · 两镜", 8.0, "exported")
        assert "Xiao Yu：师傅，这个得换。" in ep.source_text
        assert "Master Chen：先让我看看。" in ep.source_text

        s1, s2 = ep.shots
        assert (s1.code, s1.order, s2.code, s2.order) == ("S01", 0, "S02", 1)
        assert (s1.duration, s2.duration) == (5.0, 3.0)
        assert s2.transition_out == "fade_out_0.6"
        assert s1.motion_prompt == "Xiao Yu slides the phone."
        assert s1.dialogue == [{"speaker": "Xiao Yu", "zh": "师傅，这个得换。", "start": 0.6,
                                "end": 4.6, "km": "ជាង!"}]
        assert len(s2.dialogue) == 2
        assert s1.cast == [{"character_id": yu.id, "variation_id": None},
                           {"character_id": chen.id, "variation_id": None}]
        assert s2.cast == [{"character_id": chen.id, "variation_id": None}]

        for sh in ep.shots:
            d = shot_detail(s, sv, sh, p, p.style_bible)
            slots = {x.kind: x for x in d.slots}
            assert set(slots) == {"first_frame", "video"}
            ff, vid = slots["first_frame"], slots["video"]
            assert ff.selected_candidate_id and not ff.stale
            assert vid.selected_candidate_id == vid.candidates[0].id and not vid.stale
            assert len(vid.candidates) == 2
            dub = vid.candidates[1]
            assert dub.note == "高棉语配音版 (Edge TTS)"
            assert dub.params["imported_from"].endswith(f"clip_{sh.code}_00001_.mp4")
            assert "clips_dub" in dub.params["imported_from"]
            assert ff.candidates[0].params["imported_from"].endswith(f"kf_{sh.code}_00001_.png")

        exports = {e.variant: e for e in s.scalars(select(Export).where(Export.episode_id == ep.id))}
        assert set(exports) == {"9x16", "dub_captioned"}
        assert sv.media.abs(exports["dub_captioned"].file_path).read_bytes() == b"final-dub-cap"
        assert exports["9x16"].file_path.startswith("demo-ad/exports/")
        assert exports["9x16"].settings["clips"] == 2

    assert any("mp3" in w for w in summary["warnings"])
    notes = json.loads((sv.media.project_dir("demo-ad") / "imported" / "ai_videos.json")
                       .read_text(encoding="utf-8"))
    assert notes["end_card"]["seconds"] == 3
    assert notes["dub"]["voices"]["chen"] == "km-KH-PisethNeural"
    assert snapshot(src) == before


def test_import_custom_slug_and_engine_fallback(tmp_path, sv):
    src = make_src(tmp_path)
    y = (src / "project.yaml").read_text(encoding="utf-8")
    alt = tmp_path / "alt"
    alt.mkdir()
    for p in src.rglob("*"):
        if p.is_file():
            dst = alt / p.relative_to(src)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(p.read_bytes())
    (alt / "project.yaml").write_text(
        y.replace("image: minimax_h3", "image: zimage").replace("video: minimax_h3", "video: ltx23"),
        encoding="utf-8")
    summary = import_ai_videos(sv, alt, slug="other")
    assert summary["slug"] == "other"
    assert any("ltx23" in w for w in summary["warnings"])
    with sv.db.session() as s:
        p = s.scalar(select(Project).where(Project.slug == "other"))
        assert (p.image_engine, p.video_engine) == ("zimage", "h3_video")


def test_duplicate_slug_refused(tmp_path, sv):
    src = make_src(tmp_path)
    import_ai_videos(sv, src)
    with pytest.raises(JobError, match="已存在"):
        import_ai_videos(sv, src)
    with sv.db.session() as s:
        assert len(s.scalars(select(Project)).all()) == 1


def test_missing_yaml_refused(tmp_path, sv):
    with pytest.raises(JobError, match="project.yaml"):
        import_ai_videos(sv, tmp_path)


def test_api_import_endpoint(tmp_path, api):
    src = make_src(tmp_path)
    before = snapshot(src)
    r = api.post("/api/import/ai-videos", json={"path": str(src), "slug": "ad1"})
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["shots"] == 2
    assert snapshot(src) == before
    ep = api.get("/api/projects/ad1/episodes/by-number/1").json()
    assert len(ep["shots"]) == 2
    for sh in ep["shots"]:
        assert all(sl["selected_candidate_id"] and not sl["stale"] for sl in sh["slots"])
        url = next(sl for sl in sh["slots"] if sl["kind"] == "video")["candidates"][0]["url"]
        assert api.get(url).status_code == 200
    # 非标准转场值 fade_out_0.6 读写都不出错
    s2 = ep["shots"][1]
    assert s2["transition_out"] == "fade_out_0.6"
    r = api.patch(f"/api/shots/{s2['id']}", json={"duration": 3.5})
    assert r.status_code == 200, r.text
    assert r.json()["transition_out"] == "fade_out_0.6"
    exports = api.get(f"/api/episodes/{ep['id']}/exports").json()
    assert len(exports) == 2 and all(api.get(e["url"]).status_code == 200 for e in exports)

    r = api.post("/api/import/ai-videos", json={"path": str(src), "slug": "ad1"})
    assert r.status_code == 400 and "已存在" in r.json()["detail"]
    r = api.post("/api/import/ai-videos", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 400
    r = api.post("/api/import/ai-videos", json={"path": str(src), "slug": "bad/slug"})
    assert r.status_code == 400


def test_cli_import(tmp_path, api, monkeypatch):
    monkeypatch.setattr(studio, "_client", lambda *a: api)
    src = make_src(tmp_path)
    r = CliRunner().invoke(studio.app, ["import", "ai-videos", str(src), "--slug", "cli-ad"])
    assert r.exit_code == 0, r.output
    assert "已导入" in r.output and "cli-ad" in r.output and "镜头 2" in r.output
    assert "/projects/cli-ad" in r.output
    r = CliRunner().invoke(studio.app, ["--json", "import", "ai-videos", str(src), "--slug", "cli-ad2"])
    assert json.loads(r.stdout)["slug"] == "cli-ad2"
