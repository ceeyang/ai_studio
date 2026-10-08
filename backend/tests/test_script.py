import pytest
from sqlalchemy import select

from backend.domain.models import Character, Episode, Location, Project, SceneBlock
from backend.pipeline.script import run_script
from backend.queue.errors import JobError, NeedsInput
from backend.tests.helpers import FakeCtx, make_gateway, make_services, seed_episode

STRUCTURE = {
    "title": "被改的提词器",
    "characters": [{"name": "林夏", "description": "新闻主播"}, {"name": "老周", "description": "导播"}],
    "locations": [{"name": "直播间", "time_of_day": "夜晚", "atmosphere": "压抑"},
                  {"name": "走廊", "time_of_day": "夜晚", "atmosphere": "冷清"}],
    "scenes": [{"location": "直播间", "text": "林夏照着提词器念，发现词被改了。"},
               {"location": "走廊", "text": "林夏追上老周质问。"}],
}
LOOK_LIN = ("a woman in her late twenties with an oval face and shoulder length black hair, "
            "slim build, charcoal wool blazer over a white silk blouse, small silver ear cuff")
LOOK_ZHOU = ("a man in his fifties with a square face, receding grey hair, heavy build, "
             "navy cardigan over a checked shirt, black headset around his neck")
VISUAL = {
    "style": {"anchor_text": "photorealistic live action short drama, shot on ARRI Alexa, "
                             "Kodak 2383 print look, cool teal shadows, warm skin tones",
              "palette": "青橙", "mood_keywords": ["tense", "night"], "negative": "cartoon"},
    "characters": [{"name": "林夏", "visual_prompt": LOOK_LIN, "core_features": "齐肩黑发、银耳骨夹"},
                   {"name": "老周", "visual_prompt": LOOK_ZHOU, "core_features": "灰发、耳麦"}],
    "locations": [{"name": "直播间", "visual_prompt": "a cramped live news studio with a ring light, "
                                                    "a teleprompter monitor and a glass desk"},
                  {"name": "走廊", "visual_prompt": "a narrow office corridor with flickering "
                                                   "fluorescent tubes and grey carpet"}],
}


def setup(tmp_path, db, texts, kind="openai", **seed):
    gw, prov = make_gateway(texts, kind=kind)
    sv = make_services(tmp_path, db, llm=gw)
    _, ep_id = seed_episode(db, **seed)
    return FakeCtx(sv), ep_id, prov


def test_script_creates_blocks_characters_and_style(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL])
    assert run_script(ctx, {"episode_id": ep_id}) == {"structure": "done", "visual": "done"}
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        assert ep.title == "被改的提词器" and ep.status == "scripted"
        blocks = ep.scene_blocks
        assert [b.text for b in blocks] == [STRUCTURE["scenes"][0]["text"], STRUCTURE["scenes"][1]["text"]]
        loc_names = {l.id: l.name for l in s.scalars(select(Location))}
        assert [loc_names[b.location_id] for b in blocks] == ["直播间", "走廊"]
        lin = s.scalar(select(Character).where(Character.name == "林夏"))
        assert lin.visual_prompt == LOOK_LIN and lin.core_features == "齐肩黑发、银耳骨夹"
        proj = s.get(Project, ep.project_id)
        assert proj.style_bible.anchor_text.startswith("photorealistic")
        assert proj.style_bible.mood_keywords == ["tense", "night"]
        assert set(ep.stage_hashes) == {"structure", "visual"}
    assert "提词器" in prov.calls[0]["messages"][0]["content"]


def test_script_skips_when_unchanged(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL])
    run_script(ctx, {"episode_id": ep_id})
    assert run_script(ctx, {"episode_id": ep_id}) == {"structure": "skipped", "visual": "skipped"}
    assert len(prov.calls) == 2
    assert any("跳过" in line for line in ctx.logs)


def test_force_reruns_and_replaces_blocks(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL, STRUCTURE, VISUAL])
    run_script(ctx, {"episode_id": ep_id})
    run_script(ctx, {"episode_id": ep_id, "force": True})
    assert len(prov.calls) == 4
    with db.session() as s:
        assert len(s.scalars(select(SceneBlock)).all()) == 2
        assert len(s.scalars(select(Character)).all()) == 2


def test_visual_missing_character_retries(tmp_path, db):
    partial = {**VISUAL, "characters": VISUAL["characters"][:1]}
    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, partial, VISUAL])
    run_script(ctx, {"episode_id": ep_id})
    assert len(prov.calls) == 3
    assert "缺少角色：老周" in prov.calls[2]["messages"][2]["content"]


def test_source_text_required(tmp_path, db):
    ctx, ep_id, _ = setup(tmp_path, db, [], source="  ")
    with pytest.raises(JobError, match="原文"):
        run_script(ctx, {"episode_id": ep_id})


def test_manual_provider_waits_for_input(tmp_path, db):
    ctx, ep_id, _ = setup(tmp_path, db, [], kind="manual")
    with pytest.raises(NeedsInput) as ei:
        run_script(ctx, {"episode_id": ep_id})
    assert ei.value.request["key"] == "script_structure"
    import json
    with pytest.raises(NeedsInput) as ei:
        run_script(ctx, {"episode_id": ep_id,
                         "manual": {"script_structure": json.dumps(STRUCTURE, ensure_ascii=False)}})
    assert ei.value.request["key"] == "script_visual"
