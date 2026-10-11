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
    assert "和它的文字长短成比例" in system and "一句台词加听的人的一个反应" in system
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


ANCHOR_NEW = ANCHOR_OK.replace("ARRI Alexa", "Sony Venice").replace("teal", "green")
VISUAL_NEW = {
    "style": {"anchor_text": ANCHOR_NEW, "palette": "冷绿", "mood_keywords": ["cold"],
              "negative": "anime"},
    "characters": [{"name": "林夏", "visual_prompt": LOOK_LIN + ", red scarf", "core_features": "红围巾"},
                   {"name": "老周", "visual_prompt": LOOK_ZHOU + ", glasses", "core_features": "眼镜"}],
    "locations": [{"name": "直播间", "visual_prompt": "a huge bright television studio"},
                  {"name": "走廊", "visual_prompt": "a wide marble corridor"}],
}


def _add_location_image(s, name):
    from backend.domain.locations import add_location_image, location_spec

    loc = s.scalar(select(Location).where(Location.name == name))
    add_location_image(s, loc.id, file_path="demo/loc.png", engine="zimage",
                       params={"spec": location_spec(loc)}, input_hash="h", job_id=None)
    return loc.id


def test_rerun_visual_without_note_keeps_existing_style_anchor(tmp_path, db):
    """重跑视觉设定（没带修改意见）：已有的风格锚不覆盖——锚在所有首帧、三视图的哈希里，
    LLM 每次写的锚都不一样，一覆盖全片过期。日志说清楚要换风格得带修改意见。"""
    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL, STRUCTURE, VISUAL_NEW])
    run_script(ctx, {"episode_id": ep_id})
    assert run_script(ctx, {"episode_id": ep_id, "force": True})["visual"] == "done"
    with db.session() as s:
        sb = s.get(Episode, ep_id).project.style_bible
        assert sb.anchor_text == ANCHOR_OK and sb.negative == "cartoon" and sb.palette == "青橙"
        # 角色没选定妆照：外貌照常按新结果写
        assert s.scalar(select(Character).where(Character.name == "林夏")).visual_prompt \
            == LOOK_LIN + ", red scarf"
    assert any("已有风格锚，没覆盖；要换风格请带修改意见重新生成" in m for m in ctx.logs)


def test_rerun_visual_with_note_replaces_style_anchor(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL, STRUCTURE, VISUAL_NEW])
    run_script(ctx, {"episode_id": ep_id})
    run_script(ctx, {"episode_id": ep_id, "note": "换成冷色调"})
    with db.session() as s:
        sb = s.get(Episode, ep_id).project.style_bible
        assert sb.anchor_text == ANCHOR_NEW and sb.negative == "anime"
    assert not any("已有风格锚" in m for m in ctx.logs)


def test_rerun_visual_keeps_location_prompt_with_selected_location_image(tmp_path, db):
    """场景已经选定了场景图：外观提示词不覆盖（和已选定妆照的角色对称），场景图不过期；没场景图的照常写。
    带修改意见也一样（要换场景外观到「设定」里改，再重出场景图）。"""
    from backend.domain.locations import location_image_of, location_image_stale

    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL, STRUCTURE, VISUAL_NEW])
    run_script(ctx, {"episode_id": ep_id})
    with db.session() as s:
        studio = _add_location_image(s, "直播间")
    run_script(ctx, {"episode_id": ep_id, "note": "更紧凑"})
    with db.session() as s:
        loc = s.get(Location, studio)
        assert loc.visual_prompt == VISUAL["locations"][0]["visual_prompt"]
        assert not location_image_stale(loc, location_image_of(s, studio))
        hall = s.scalar(select(Location).where(Location.name == "走廊"))
        assert hall.visual_prompt == "a wide marble corridor"
    assert any("直播间 已经选定了场景图" in m for m in ctx.logs)


def test_changing_one_character_description_does_not_restyle_everything(tmp_path, db):
    """改一个角色的描述 → 下一次整集运行重跑视觉设定：锚不变（首帧、三视图哈希里的锚不变）、
    已选场景图的场景外观不变（场景图不过期）、已选定妆照的角色外貌不变。"""
    from backend.domain.characters import add_portrait
    from backend.domain.locations import location_image_of, location_image_stale

    ctx, ep_id, prov = setup(tmp_path, db, [STRUCTURE, VISUAL, VISUAL_NEW])
    run_script(ctx, {"episode_id": ep_id})
    with db.session() as s:
        studio = _add_location_image(s, "直播间")
        hall = _add_location_image(s, "走廊")
        lin = s.scalar(select(Character).where(Character.name == "林夏"))
        add_portrait(s, lin.id, file_path="demo/p.png", engine="zimage", params={},
                     input_hash="h", job_id=None)
        s.scalar(select(Character).where(Character.name == "老周")).description = "导播，戴眼镜"
    assert run_script(ctx, {"episode_id": ep_id}) == {"structure": "skipped", "visual": "done"}
    with db.session() as s:
        assert s.get(Episode, ep_id).project.style_bible.anchor_text == ANCHOR_OK
        for lid in (studio, hall):
            assert not location_image_stale(s.get(Location, lid), location_image_of(s, lid))
        assert s.scalar(select(Character).where(Character.name == "林夏")).visual_prompt == LOOK_LIN
        # 改了描述的老周照常按新结果写
        assert s.scalar(select(Character).where(Character.name == "老周")).visual_prompt \
            == LOOK_ZHOU + ", glasses"


# 内置剧本模板的内容哈希（统一成 LF 后 sha256 前 16 位）和规则版本绑在一起
SCRIPT_TEMPLATE_HASHES = {("script_structure", 2): "0f71c3456df6b738",
                          ("script_visual", 2): "d4a150a183a70949",
                          ("script_structure", 3): "a6e4688397666507",
                          ("script_visual", 3): "aa9130be0add3439",
                          ("script_structure", 4): "c7695bf3c309b984",
                          ("script_visual", 4): "81810d6712f799a6",
                          ("script_visual", 5): "bc57f650d77b38b9",
                          ("script_structure", 5): "c42eb0c982215277"}


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


# ------------------------------------------------------------ 爆款剧本规范（F）
def test_structure_prompt_has_hit_drama_rules():
    from backend.llm.prompts import PromptLibrary
    from backend.services import BUILTIN_DIR

    system, _ = PromptLibrary(BUILTIN_DIR, BUILTIN_DIR / "none").render(
        "script_structure", project_title="t", target_seconds=60, source_text="x", shot_count=12,
        shot_seconds=5, aspect="9:16")
    for words in ("3 秒钩子", "冲突", "反转", "结尾钩子", "每 5 秒至少 1 句", "不超过 16 个字",
                  "口语化", "不写长独白", "角色名：「台词」", "镜头预算优先"):
        assert words in system, words
    assert "约 12 句以上" in system
    check = system[system.index("输出前自检"):system.index("只输出一个 JSON")]
    assert "钩子" in check and "16 个字" in check and "反转" in check


def test_dialogue_problems_rules():
    from backend.pipeline.script import dialogue_lines, dialogue_problems, min_dialogue_lines

    texts = ["老周推门进来。老周：「这钱我不收。」林夏：“你凭什么？”",
             "林夏：「我在直播，你别乱来，你知道这样做后果有多严重吗」"]
    assert dialogue_lines(texts) == ["这钱我不收。", "你凭什么？",
                                     "我在直播，你别乱来，你知道这样做后果有多严重吗"]
    assert min_dialogue_lines(60) == 8 and min_dialogue_lines(3) == 1
    bad = dialogue_problems(texts, 20)
    assert len(bad) == 1 and "超过 16 个字" in bad[0]
    bad = dialogue_problems(["老周：「走。」"], 60)
    assert len(bad) == 1 and "只有 1 句台词" in bad[0] and "至少要 8 句" in bad[0]
    # 素材和段落都没有对白：无声的片子，不卡
    assert dialogue_problems(["林夏发现提词器被改了。"], 20, "林夏发现提词器被改了。") == []
    # 素材有对白、段落里却认不出：没按格式写
    bad = dialogue_problems(["林夏质问老周为什么改词。"], 20, "林夏问：“谁改的词？”")
    assert len(bad) == 1 and "角色名：「台词」" in bad[0]


def test_dialogue_length_ignores_punctuation_and_screen_text():
    from backend.pipeline.script import _speech_len, dialogue_lines, dialogue_problems

    # 16 个字加标点：不算超长
    line = "你知道吗，这钱，我一分都不会收下的！"
    assert _speech_len(line) == 15
    assert dialogue_problems([f"老周：「{line}」"], 3) == []
    assert _speech_len("好……走！ 快") == 3
    # 屏幕 / 字条 / 标牌上的字不是台词：不进对白、不按 16 字卡
    texts = ["手机屏幕显示：「余额不足，请于今日二十四点前还清全部欠款」",
             "桌上的字条写着：「别找我了，我已经离开这座城市再也不会回来」",
             "门口标牌：「营业时间早九点到晚十点节假日照常营业不休息」",
             "林夏看着屏幕说：「谁干的？」"]
    assert dialogue_lines(texts) == ["谁干的？"]
    assert dialogue_problems(texts, 3) == []


def test_structure_retries_on_long_dialogue_then_accepts(tmp_path, db):
    long_line = {**STRUCTURE, "scenes": [
        {"location": "直播间", "text": "林夏：「我在直播，你别乱来，你知道这样做后果有多严重吗」"},
        {"location": "走廊", "text": "老周：「不是我。」林夏：「那是谁？」"}]}
    good = {**STRUCTURE, "scenes": [
        {"location": "直播间", "text": "林夏：「我在直播！」林夏：「你别乱来。」"},
        {"location": "走廊", "text": "老周：「不是我。」林夏：「那是谁？」"}]}
    ctx, ep_id, prov = setup(tmp_path, db, [long_line, good, VISUAL], target=20.0)
    assert run_script(ctx, {"episode_id": ep_id})["structure"] == "done"
    retry = prov.calls[1]["messages"][-1]["content"]
    assert "超过 16 个字" in retry and "不写长独白" in retry
    with db.session() as s:
        assert s.get(Episode, ep_id).scene_blocks[0].text == good["scenes"][0]["text"]


def test_structure_last_attempt_keeps_sparse_dialogue(tmp_path, db):
    sparse = {**STRUCTURE, "scenes": [{"location": "直播间", "text": "林夏：「谁？」"},
                                      {"location": "走廊", "text": "林夏追上老周。"}]}
    ctx, ep_id, prov = setup(tmp_path, db, [sparse, sparse, sparse, VISUAL], target=60.0)
    assert run_script(ctx, {"episode_id": ep_id})["structure"] == "done"
    assert len(prov.calls) == 4   # 3 次结构（最后一次放行）+ 1 次视觉
    assert "至少要 8 句" in prov.calls[1]["messages"][-1]["content"]
    assert any("没完全达到爆款规范" in m for m in ctx.logs)
