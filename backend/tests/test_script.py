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
ANCHOR_OK = ("photorealistic live action short drama, shot on ARRI Alexa with a 50mm prime lens, "
             "Kodak 2383 print look with fine film grain, cool teal shadows and warm skin tones, "
             "moderate contrast, soft diffused key light with gentle falloff, natural skin texture "
             "and believable fabric detail, restrained saturation")
VISUAL = {
    "style": {"anchor_text": ANCHOR_OK,
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


def test_note_forces_rerun_and_goes_into_prompt(tmp_path, db):
    """输入没变、不 force，但带了修改意见：结构和视觉都真正调 LLM，提示词末尾带上修改意见。"""
    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL, STRUCTURE, VISUAL])
    run_script(ctx, {"episode_id": ep_id})
    with db.session() as s:
        before = dict(s.get(Episode, ep_id).stage_hashes)
    assert "修改意见" not in prov.calls[0]["messages"][0]["content"]
    assert run_script(ctx, {"episode_id": ep_id, "note": "更紧凑"}) == {"structure": "done",
                                                                       "visual": "done"}
    assert len(prov.calls) == 4
    for call in prov.calls[2:]:
        user = call["messages"][0]["content"]
        assert "修改意见：更紧凑" in user and "在满足以上全部要求的前提下按修改意见调整。" in user
    with db.session() as s:
        after = s.get(Episode, ep_id).stage_hashes
    # 存的是不含修改意见的基础指纹：之后不强制、输入没变的运行会跳过，不会把按意见改好的结果再重做
    assert after["structure"] == before["structure"] and after["visual"] == before["visual"]
    assert run_script(ctx, {"episode_id": ep_id}) == {"structure": "skipped", "visual": "skipped"}
    assert len(prov.calls) == 4


def test_blank_note_is_ignored(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL])
    run_script(ctx, {"episode_id": ep_id})
    assert run_script(ctx, {"episode_id": ep_id, "note": "   "}) == {"structure": "skipped",
                                                                    "visual": "skipped"}


# ---------------------------------------------------------------- 镜头预算、规则版本、不覆盖已定造型
def test_structure_prompt_knows_shot_budget_and_aspect(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL], target=25.0)
    with db.session() as s:
        s.get(Episode, ep_id).project.aspect = "16:9"
    run_script(ctx, {"episode_id": ep_id})
    system = prov.calls[0]["system"]
    user = prov.calls[0]["messages"][0]["content"]
    # h3_video 甜区 5 秒：25 秒 = 5 个镜头
    assert "大约 5 个镜头" in system and "每个镜头约 5 秒" in system and "节拍" in system
    assert "整集的节拍加起来不超过 5 个" in system and "不超过 87 个字" in system
    assert "16:9 横屏" in system and "9:16" not in system
    assert "反转" in system and "单独占一个节拍" in system
    assert "大约 5 个镜头" in user


def test_structure_budget_follows_video_engine(tmp_path, db):
    from backend.pipeline.script import shot_plan

    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL], target=20.0)
    assert shot_plan(ctx.services.engines, "h3_video", 20) == (4, 5.0)
    assert shot_plan(ctx.services.engines, "没有这个引擎", 20) == (4, 5.0)


def test_template_change_does_not_rerun_script(tmp_path, db, monkeypatch):
    """模板规则升级：指纹只算数据，已有剧集不会被一次不强制的运行重写（只提示规则已更新）。"""
    import backend.pipeline.script as sc

    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL])
    run_script(ctx, {"episode_id": ep_id})
    monkeypatch.setattr(sc, "STRUCTURE_RULES_VERSION", sc.STRUCTURE_RULES_VERSION + 1)
    monkeypatch.setattr(sc, "VISUAL_RULES_VERSION", sc.VISUAL_RULES_VERSION + 1)
    assert run_script(ctx, {"episode_id": ep_id}) == {"structure": "skipped", "visual": "skipped"}
    assert any("剧本规则已更新" in m for m in ctx.logs)
    from backend.pipeline.impact import rerun_impact
    with db.session() as s:
        im = rerun_impact(s, ctx.services, ep_id, "script", False)
    assert im.will_run is False and im.rules_outdated is True


def test_legacy_string_record_is_kept_and_upgraded(tmp_path, db):
    """以前存的是整段提示词哈希（字符串）：没法按新算法重算，当作没变，换成新格式，不重写段落。"""
    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL])
    run_script(ctx, {"episode_id": ep_id})
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        ep.stage_hashes = {**ep.stage_hashes, "structure": "abc123", "visual": "def456"}
    assert run_script(ctx, {"episode_id": ep_id}) == {"structure": "skipped", "visual": "skipped"}
    assert any("旧版记录" in m for m in ctx.logs)
    with db.session() as s:
        rec = s.get(Episode, ep_id).stage_hashes
        assert isinstance(rec["structure"], dict) and rec["structure"]["rules"] is None
    # 换成新格式之后，原文真的改了就能看出来
    with db.session() as s:
        s.get(Episode, ep_id).source_text = "完全不同的素材"
    prov.texts += [STRUCTURE, VISUAL]
    assert run_script(ctx, {"episode_id": ep_id})["structure"] == "done"


def test_visual_anchor_with_scene_words_is_rewritten(tmp_path, db):
    bad = {**VISUAL, "style": {**VISUAL["style"],
                               "anchor_text": ANCHOR_OK + ", rain on the street at night, bare bulbs"}}
    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, bad, VISUAL])
    run_script(ctx, {"episode_id": ep_id})
    assert len(prov.calls) == 3
    err = prov.calls[2]["messages"][2]["content"]
    assert "rain" in err and "night" in err and "成像风格" in err


def test_visual_prompt_asks_imaging_only_anchor():
    from backend.llm.prompts import PromptLibrary
    from backend.services import BUILTIN_DIR

    system, _ = PromptLibrary(BUILTIN_DIR, BUILTIN_DIR / "none").render(
        "script_visual", project_title="t", characters=[], locations=[], excerpt="x")
    assert "40–70 个英文单词" in system and "只写全剧通用的成像风格" in system
    assert "天气" in system and "具体光源" in system and "开头先写性别、年龄和族裔" in system
    assert "只有支持负面提示词的引擎会用" in system


def test_rerun_visual_keeps_chosen_look(tmp_path, db):
    """角色已经选定了定妆照造型：带意见重跑视觉设定不覆盖外貌提示词（定妆照和文字对得上），空的照样补。"""
    from backend.domain.characters import add_portrait

    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL, STRUCTURE, VISUAL])
    run_script(ctx, {"episode_id": ep_id})
    with db.session() as s:
        lin = s.scalar(select(Character).where(Character.name == "林夏"))
        lin.visual_prompt = "CHOSEN LOOK " * 5
        add_portrait(s, lin.id, file_path="demo/p.png", engine="zimage", params={},
                     input_hash="h", job_id=None)
        zhou = s.scalar(select(Character).where(Character.name == "老周"))
        zhou.visual_prompt = "old zhou look"
    run_script(ctx, {"episode_id": ep_id, "note": "更紧凑"})
    with db.session() as s:
        assert s.scalar(select(Character).where(Character.name == "林夏")).visual_prompt \
            == "CHOSEN LOOK " * 5
        assert s.scalar(select(Character).where(Character.name == "老周")).visual_prompt == LOOK_ZHOU
    assert any("林夏 已经选定了定妆照造型" in m for m in ctx.logs)


# 内置剧本模板的内容哈希（统一成 LF 后 sha256 前 16 位）和规则版本绑在一起
SCRIPT_TEMPLATE_HASHES = {("script_structure", 2): "5d1f0d3de3c6698f",
                          ("script_visual", 2): "d4a150a183a70949"}


@pytest.mark.parametrize("name,attr", [("script_structure", "STRUCTURE_RULES_VERSION"),
                                       ("script_visual", "VISUAL_RULES_VERSION")])
def test_builtin_script_template_hash_is_bound_to_rules_version(name, attr):
    import hashlib

    import backend.pipeline.script as sc
    from backend.llm.prompts import BUILTIN_DIR

    text = (BUILTIN_DIR / f"{name}.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    version = getattr(sc, attr)
    assert SCRIPT_TEMPLATE_HASHES.get((name, version)) == h, (
        f"改了 {name}.md 要把 {attr} 加一，并更新这里的哈希（现在是 {h}）")


def test_structure_prompt_keeps_synopsis_cast_and_beats():
    """第二次样片：剧本把画外的小女孩加成了出镜角色，丢了「抬头看阿青」「摆手不收钱」。"""
    from backend.llm.prompts import PromptLibrary
    from backend.services import BUILTIN_DIR

    system, _ = PromptLibrary(BUILTIN_DIR, BUILTIN_DIR / "none").render(
        "script_structure", project_title="t", target_seconds=25, source_text="x", shot_count=5,
        shot_seconds=5, aspect="9:16")
    assert "不要新增素材里没有的出镜人物" in system and "只出声，不出镜" in system
    assert "情绪节点和动作" in system and "一个不少" in system
    assert "输出前自检" in system and system.index("输出前自检") < system.index("只输出一个 JSON")


def test_visual_prompt_asks_for_renderable_signature_details():
    from backend.llm.prompts import PromptLibrary
    from backend.services import BUILTIN_DIR

    lib = PromptLibrary(BUILTIN_DIR, BUILTIN_DIR / "none")
    system, _ = lib.render("script_visual", project_title="t", characters=[], locations=[],
                           excerpt="x")
    assert "不写老茧、缠线" in system and "黄金时段" in system
