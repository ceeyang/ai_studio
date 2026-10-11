"""角色卡：本地 AI 抽定妆照（image.portrait）、切换角色造型、AI 推荐造型和按剧本推荐角色。"""
import re

import pytest

from backend.domain.characters import set_portrait, use_portrait
from backend.domain.models import Character, Episode, Job, SceneBlock, StyleBible
from backend.domain.slots import get_or_create_slot, selected_candidate
from backend.pipeline.characters import run_portrait, run_recommend, run_suggest
from backend.queue.errors import JobError
from backend.tests.helpers import FakeCtx, FakeRunner, make_gateway, make_services, seed_episode

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
LOOK = ("A 45-year-old Chinese male auto mechanic, lean wiry build, short black hair with grey "
        "temples, navy cotton-twill work jacket with orange reflective band")
ANCHOR = "Photorealistic live-action commercial photography, warm golden palette, fine film grain."


def words(n: int, w: str = "detail") -> str:
    return " ".join([w] * n)


def setup(tmp_path, db, *, image_engine="h3_image", llm=None, look=LOOK, preset="cg_realistic"):
    """preset 默认 3D 写实：它没有推荐的定妆照引擎，定妆照跟项目的图像引擎（真人写实会推荐 krea2）。"""
    runner = FakeRunner()
    sv = make_services(tmp_path, db, comfy=runner, llm=llm)
    proj_id, ep_id = seed_episode(db, image_engine=image_engine,
                                  source="陈师傅在修车厂接待小雨，帮她看报价单。")
    with db.session() as s:
        sb = StyleBible(anchor_text=ANCHOR, negative="cartoon", character_rules="只有两个角色")
        s.add(sb)
        s.flush()
        s.get(Episode, ep_id).project.style_bible_id = sb.id
        s.get(Episode, ep_id).project.style_preset = preset
        s.add(SceneBlock(episode_id=ep_id, order=0, text="修车厂里，陈师傅擦着扳手。"))
        ch = Character(project_id=proj_id, name="陈师傅", description="45 岁中国汽修师傅",
                       visual_prompt=look)
        s.add(ch)
        s.flush()
        ids = {"char": ch.id, "project": proj_id, "episode": ep_id}
    return FakeCtx(sv, job_id=11), runner, ids


def portrait_slot(db, char_id):
    with db.session() as s:
        slot = get_or_create_slot(s, "character", char_id, "portrait")
        return slot.selected_candidate_id, [(c.id, dict(c.params)) for c in slot.candidates]


# ---------------------------------------------------------------- image.portrait
def test_portrait_draw_uses_visual_prompt_anchor_and_3_4(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    out = run_portrait(ctx, {"character_id": ids["char"]})
    call = runner.calls[0]
    prompt = call.meta["prompt"]
    # 景别放最前面；风格锚只取画质 / 胶片 / 色彩词，接在最后
    assert prompt.startswith("Character reference portrait")
    assert "Photorealistic live-action commercial photography" in prompt
    assert "fine film grain" in prompt
    assert LOOK in prompt and "cropped at the waist" in prompt and "front" in prompt
    assert "neutral" in prompt.lower() or "grey" in prompt.lower()
    # 引擎有 3:4 就用 3:4（比 9:16 短，留不下腿）：zimage 864×1152
    assert call.meta["size"] == [864, 1152]
    assert call.meta["refs"] == []  # 抽新造型不带旧定妆照
    sel, cands = portrait_slot(db, ids["char"])
    assert out["candidate_ids"] == [cands[0][0]]
    assert cands[0][1]["visual_prompt"] == LOOK
    assert cands[0][1]["workflow"].endswith("workflow.api.json")
    assert sel == cands[0][0]  # 之前没有定妆照：自动选定


def test_portrait_draw_does_not_replace_selected(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    with db.session() as s:
        up = set_portrait(s, sv.media, s.get(Character, ids["char"]), PNG, "chen.png").id
    run_portrait(ctx, {"character_id": ids["char"], "visual_prompt": "OTHER LOOK " + words(10)})
    ctx.job_id = 12
    run_portrait(ctx, {"character_id": ids["char"]})
    sel, cands = portrait_slot(db, ids["char"])
    assert sel == up and len(cands) == 3
    assert cands[1][1]["visual_prompt"].startswith("OTHER LOOK")
    assert cands[2][1]["visual_prompt"] == LOOK
    # 不同任务种子不同，抽出来的卡不一样
    assert runner.calls[0].meta["seed"] != runner.calls[1].meta["seed"]
    with db.session() as s:
        assert s.get(Character, ids["char"]).visual_prompt == LOOK  # 抽卡不改角色提示词


E2E_ANCHOR = (
    "Photorealistic live-action cinematic still, Chinese urban-fringe drama, shot on a 35mm "
    "anamorphic lens with shallow depth of field, subtle Kodak Vision3 500T film grain and gentle "
    "halation around light sources. The palette sets warm tungsten amber interiors against cold "
    "teal-blue rainy night exteriors. Colors are slightly desaturated, with deep but detailed "
    "shadows. Lighting is motivated and practical: bare incandescent bulbs, fluorescent tubes, wet "
    "reflections on concrete and rim light catching raindrops. Skin looks real, with visible pores, "
    "fine wrinkles, natural imperfections and a sheen of rain. Materials are tactile: greasy steel "
    "tools, worn denim, damp cotton, rusted metal and oil-stained concrete. Quiet, intimate, "
    "melancholic yet warm atmosphere, natural proportions, consistent characters across frames.")
E2E_LOOK = (
    "Chinese man in his mid-fifties with a weathered square face, deep forehead lines, a strong jaw "
    "and grey stubble. Short-cropped salt-and-pepper hair, receding at the temples. Stocky, "
    "broad-shouldered build with thick forearms. Faded navy-blue cotton work jacket over a grey "
    "crew-neck undershirt, sleeves rolled to the elbows. Dark canvas work trousers and scuffed black "
    "leather work boots, all smeared with old engine grease. Large calloused hands, knuckles and "
    "fingernails permanently stained black with motor oil. Signature detail: an old steel "
    "wristwatch with a scratched face and a greasy off-white shop rag tucked into his back pocket.")


def test_portrait_prompt_keeps_only_photographic_style_terms():
    """实跑里风格锚的雨夜、灯泡、积水和「纯色影棚背景」拼在一起，出来的是挂在修车铺里的背景布。
    定妆照只取风格锚里的画质 / 胶片 / 皮肤质感词，场景、时间、天气、光源一律不要。"""
    from backend.pipeline.characters import portrait_prompt

    p = portrait_prompt(E2E_LOOK, E2E_ANCHOR)
    low = p.lower()
    for bad in ("rain", "night", "bulb", "fluorescent", "concrete", "interior", "exterior",
                "urban", "reflection", "practical", "tools", "atmosphere"):
        assert not re.search(rf"\b{bad}", low), bad   # 「grain」不算 rain
    for good in ("photorealistic", "35mm", "film grain", "desaturated", "pores", "wrinkles",
                 "natural proportions"):
        assert good in low, good
    # 构图写在最前面，明确正面、腰部以上；背景是中性纯色影棚
    assert p.startswith("Character reference portrait")
    assert "front-facing" in low and "cropped at the waist" in low
    assert "bottom edge of the frame cuts across the belt" in low
    assert "light-grey studio backdrop" in low
    # zimage 是 cfg 1，负面词不起作用：正面提示词里只写要什么，不写「腿不入画」「no props」这类否定
    assert not re.search(r"\blegs?\b|\bno (props|environment|other people)", low)
    # 半身照：只写下半身的句子（裤子、靴子）去掉，脸、上衣、手表都保留
    assert "trousers" not in low and "boots" not in low
    assert "weathered square face" in p and "work jacket" in p and "wristwatch" in p


def test_portrait_prompt_without_anchor():
    from backend.pipeline.characters import portrait_prompt

    p = portrait_prompt(LOOK, "")
    assert LOOK in p and "Photographic style" not in p and "Style:" not in p


# ep 8（yuye-waimai-3）的苏晴：标志性细节那句的主语分句写了 hips，去掉下半身后只剩半句
SUQING_LOOK = (
    "Chinese woman in her late twenties, oval face with a defined jawline and straight dark eyebrows, "
    "shoulder-length straight black hair tucked behind her ears, slender build of average height. "
    "Wears a long oversized oatmeal-colored chunky knit cardigan over a plain white cotton T-shirt, "
    "charcoal straight-leg trousers and simple gray felt indoor slippers. Signature detail: the long "
    "oatmeal knit cardigan hanging below her hips, a soft warm block of color against cool "
    "surroundings.")
EP8_ANCHOR = (
    "Photorealistic live-action cinematic still, shot on 35mm and 50mm prime lenses with shallow "
    "depth of field, subtle fine film grain and gentle halation on highlights, muted desaturated "
    "color grade with cool shadows and restrained warm accents, moderate contrast with deep but "
    "detailed blacks, soft directional light with natural falloff, natural skin texture, realistic "
    "fabric weave and material detail, grounded documentary realism.")


def test_portrait_prompt_drops_sentence_whose_subject_clause_was_removed():
    """「Signature detail: … hanging below her hips, a soft warm block of color against cool
    surroundings.」去掉含 hips 的第一个分句后只剩「A soft warm block of color against cool
    surroundings.」：主语没了，还和灰背景矛盾——整句不要。其余上半身外貌照留。"""
    from backend.pipeline.characters import portrait_prompt

    p = portrait_prompt(SUQING_LOOK, EP8_ANCHOR)
    assert "soft warm block" not in p and "cool surroundings" not in p
    assert "hips" not in p and "trousers" not in p and "slippers" not in p
    assert "Chinese woman in her late twenties, oval face" in p
    assert "Wears a long oversized oatmeal-colored chunky knit cardigan over a plain white cotton T-shirt." in p


def test_portrait_prompt_keeps_orphan_clause_that_names_upper_body_clothing():
    """第一个分句去掉了、但剩下的分句自己写了上衣 / 配饰：保留（只丢没主语的空话）。"""
    from backend.pipeline.characters import portrait_prompt

    p = portrait_prompt("A tall man. Black trousers, a red wool scarf around his neck.", "")
    assert "A red wool scarf around his neck." in p and "trousers" not in p


def test_portrait_prompt_style_line_follows_art_style():
    """定妆照风格句写「Style:」（和场景图一致）：动漫、国风预设不能被说成照片。"""
    from backend.pipeline.characters import portrait_prompt

    p = portrait_prompt(SUQING_LOOK, EP8_ANCHOR)
    assert "Photographic style" not in p
    assert "Style: Photorealistic live-action cinematic still" in p
    anime = ("2D cel-shaded anime, clean line art, flat colors with soft gradient shading, vivid "
             "saturated palette, crisp soft-edged shadows.")
    p = portrait_prompt(SUQING_LOOK, anime)
    assert "Photographic" not in p and "Style: 2D cel-shaded anime" in p


def test_portrait_draw_needs_prompt_and_character(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, look="")
    with pytest.raises(JobError, match="外貌提示词"):
        run_portrait(ctx, {"character_id": ids["char"]})
    with pytest.raises(JobError, match="角色"):
        run_portrait(ctx, {"character_id": 999})
    assert runner.calls == []


def test_portrait_draw_zimage_negative_is_recorded_but_not_sent(tmp_path, db):
    """zimage 是 cfg 1，负面提示词不起作用：工作流里写空，原文记在 negative_ignored。"""
    ctx, runner, ids = setup(tmp_path, db, image_engine="zimage")
    run_portrait(ctx, {"character_id": ids["char"]})
    assert "cartoon" in runner.calls[0].meta["negative_ignored"]
    assert runner.calls[0].workflow["6"]["inputs"]["text"] == ""
    assert runner.calls[0].engine == "zimage"


# ---------------------------------------------------------------- 切换造型
def test_use_portrait_switches_selection_and_prompt(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    with db.session() as s:
        up = set_portrait(s, sv.media, s.get(Character, ids["char"]), PNG, "chen.png").id
    new_look = "NEW LOOK " + words(12)
    drawn = run_portrait(ctx, {"character_id": ids["char"], "visual_prompt": new_look})["candidate_ids"][0]
    with db.session() as s:
        ch = s.get(Character, ids["char"])
        use_portrait(s, ch, drawn)
        assert ch.visual_prompt == new_look
        assert selected_candidate(s, "character", ch.id, "portrait").id == drawn
    # 切回上传的那张：恢复切走时的提示词
    with db.session() as s:
        ch = s.get(Character, ids["char"])
        use_portrait(s, ch, up)
        assert ch.visual_prompt == LOOK
        assert selected_candidate(s, "character", ch.id, "portrait").id == up


def test_use_portrait_rejects_other_characters_candidate(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    with db.session() as s:
        other = Character(project_id=ids["project"], name="小雨", visual_prompt="young woman " + words(10))
        s.add(other)
        s.flush()
        other_id = other.id
    cid = run_portrait(ctx, {"character_id": other_id})["candidate_ids"][0]
    with db.session() as s:
        with pytest.raises(LookupError):
            use_portrait(s, s.get(Character, ids["char"]), cid)
        with pytest.raises(LookupError):
            use_portrait(s, s.get(Character, ids["char"]), 9999)


# ---------------------------------------------------------------- character.suggest
def option(label, n=80):
    return {"label": label, "visual_prompt": words(n, label.lower()), "core_features": "藏青工装"}


def test_suggest_stores_design_options(tmp_path, db):
    gw, prov = make_gateway([{"options": [option("Veteran"), option("Neat"), option("Rugged")]}])
    ctx, runner, ids = setup(tmp_path, db, llm=gw)
    out = run_suggest(ctx, {"character_id": ids["char"], "count": 3, "episode_id": ids["episode"]})
    assert out == {"options": 3}
    user = prov.calls[0]["messages"][0]["content"]
    assert "陈师傅在修车厂接待小雨" in user  # 原文
    assert "修车厂里，陈师傅擦着扳手。" in user  # 段落
    assert "45 岁中国汽修师傅" in user and ANCHOR in user and "3" in user
    with db.session() as s:
        opts = s.get(Character, ids["char"]).design_options
    assert [o["label"] for o in opts] == ["Veteran", "Neat", "Rugged"]
    assert opts[0]["visual_prompt"].startswith("veteran") and opts[0]["core_features"] == "藏青工装"


def test_suggest_retries_on_wrong_count_and_short_prompt(tmp_path, db):
    gw, prov = make_gateway([
        {"options": [option("A"), option("B")]},
        {"options": [option("A", 10), option("B"), option("C")]},
        {"options": [option("A"), option("B"), option("C")]},
    ])
    ctx, runner, ids = setup(tmp_path, db, llm=gw)
    run_suggest(ctx, {"character_id": ids["char"], "count": 3})  # 不给 episode_id 时读全部剧集
    assert len(prov.calls) == 3
    assert "3" in prov.calls[1]["messages"][-1]["content"]
    assert "英文单词" in prov.calls[2]["messages"][-1]["content"]
    with db.session() as s:
        assert len(s.get(Character, ids["char"]).design_options) == 3


# ---------------------------------------------------------------- character.recommend
def test_recommend_skips_existing_characters(tmp_path, db):
    gw, prov = make_gateway([{"characters": [
        {"name": "陈师傅", "description": "已有", "visual_prompt": words(70), "core_features": ""},
        {"name": "前台小李", "description": "修车厂前台", "visual_prompt": words(70, "clerk"),
         "core_features": "工牌"},
    ]}])
    ctx, runner, ids = setup(tmp_path, db, llm=gw)
    out = run_recommend(ctx, {"episode_id": ids["episode"], "count": 3})
    assert [c["name"] for c in out["characters"]] == ["前台小李"]
    assert out["characters"][0]["visual_prompt"].startswith("clerk")
    assert "陈师傅" in prov.calls[0]["messages"][0]["content"]  # 已有角色告诉模型
    with db.session() as s:
        assert s.query(Character).count() == 1  # 只推荐，不自动建


# ---------------------------------------------------------------- API
def make_char(api, look="LOOK " + words(10)):
    api.post("/api/projects", json={"title": "demo", "image_engine": "h3_image"})
    api.post("/api/projects/demo/episodes", json={"number": 1, "source_text": "剧本"})
    r = api.post("/api/projects/demo/characters", json={"name": "陈师傅", "visual_prompt": look})
    assert r.status_code == 201, r.text
    return r.json()


def jobs(api, job_type):
    with api.app.state.services.db.session() as s:
        return [(j.type, j.owner_type, j.owner_id, dict(j.payload), j.gpu)
                for j in s.query(Job).filter(Job.type == job_type).order_by(Job.id)]


def test_api_draw_submits_portrait_jobs(api):
    ch = make_char(api)
    r = api.post(f"/api/characters/{ch['id']}/portraits/draw", json={"count": 2})
    assert r.status_code == 200, r.text
    assert len(r.json()["job_ids"]) == 2
    js = jobs(api, "image.portrait")
    # 项目默认真人写实：没单独设置定妆照引擎时用预设推荐的 krea2
    assert js[0] == ("image.portrait", "character", ch["id"],
                     {"character_id": ch["id"], "engine": "krea2"}, True)
    r = api.post(f"/api/characters/{ch['id']}/portraits/draw",
                 json={"count": 1, "visual_prompt": "  custom look  "})
    assert jobs(api, "image.portrait")[-1][3] == {"character_id": ch["id"], "engine": "krea2",
                                                  "visual_prompt": "custom look"}
    assert api.post(f"/api/characters/{ch['id']}/portraits/draw", json={"count": 5}).status_code == 422
    assert api.post("/api/characters/999/portraits/draw", json={"count": 1}).status_code == 404
    # 角色列表带上进行中的抽卡任务
    listed = api.get("/api/projects/demo/characters").json()[0]
    assert [j["type"] for j in listed["active_jobs"]] == ["image.portrait"] * 3


def test_api_draw_without_prompt_is_400(api):
    ch = make_char(api, look="")
    r = api.post(f"/api/characters/{ch['id']}/portraits/draw", json={"count": 1})
    assert r.status_code == 400 and "外貌提示词" in r.json()["detail"]


def test_api_use_portrait_and_listing(api):
    ch = make_char(api)
    sv = api.app.state.services
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "character", ch["id"], "portrait")
        from backend.domain.slots import add_candidate
        a = add_candidate(s, slot, file_path="demo/media/portrait/a.png", engine="h3_image",
                          params={"visual_prompt": "LOOK A"}, input_hash="a", job_id=None).id
        b = add_candidate(s, slot, file_path="demo/media/portrait/b.png", engine="h3_image",
                          params={"visual_prompt": "LOOK B"}, input_hash="b", job_id=None,
                          auto_select=False).id
    listed = api.get("/api/projects/demo/characters").json()[0]
    assert [p["id"] for p in listed["portraits"]] == [a, b]
    assert listed["portraits"][1]["url"] == "/media/demo/media/portrait/b.png"
    assert listed["portrait_candidate_id"] == a and listed["design_options"] == []
    r = api.post(f"/api/characters/{ch['id']}/portraits/{b}/use")
    assert r.status_code == 200, r.text
    assert r.json()["portrait_candidate_id"] == b and r.json()["visual_prompt"] == "LOOK B"
    assert api.post(f"/api/characters/{ch['id']}/portraits/9999/use").status_code == 404


def test_api_suggest_and_recommend_submit_jobs(api):
    ch = make_char(api)
    ep_id = api.get("/api/projects/demo/episodes").json()[0]["id"]
    r = api.post(f"/api/characters/{ch['id']}/suggest", json={"count": 3, "episode_id": ep_id})
    assert r.status_code == 200, r.text
    assert jobs(api, "character.suggest")[0][:4] == (
        "character.suggest", "character", ch["id"],
        {"character_id": ch["id"], "count": 3, "episode_id": ep_id})
    assert api.post(f"/api/characters/{ch['id']}/suggest", json={"count": 9}).status_code == 422
    r = api.post(f"/api/episodes/{ep_id}/characters/recommend", json={})
    assert r.status_code == 200, r.text
    assert jobs(api, "character.recommend")[0][:4] == (
        "character.recommend", "episode", ep_id, {"episode_id": ep_id, "count": 3})
    assert api.post("/api/episodes/999/characters/recommend", json={}).status_code == 404


def test_portrait_aspect_falls_back_to_9_16_when_engine_has_no_3_4():
    from types import SimpleNamespace as NS

    from backend.pipeline.characters import portrait_aspect

    assert portrait_aspect(NS(caps=NS(sizes={"9:16": (1, 2), "3:4": (3, 4)}))) == "3:4"
    assert portrait_aspect(NS(caps=NS(sizes={"9:16": (1, 2), "16:9": (2, 1)}))) == "9:16"


# ---------------------------------------------------------------- 把镜头里的外貌更新为当前造型
OLD = ("Chinese man in his mid-fifties with a weathered square face and grey stubble. "
       "Faded navy-blue cotton work jacket over a grey T-shirt. Oil-stained khaki trousers and "
       "worn brown work boots.")
NEW = "Chinese man in his mid-fifties, clean-shaven, round glasses. Brown corduroy jacket."


def test_refresh_shot_looks_replaces_exact_old_text_with_placeholder(tmp_path, db):
    from backend.domain.characters import refresh_shot_looks
    from backend.domain.models import Shot
    from backend.pipeline.prompting import keyframe_input_hash, shot_image_prompt
    from backend.pipeline.keyframes import load_shot

    ctx, runner, ids = setup(tmp_path, db, look=OLD)
    cast = [{"character_id": ids["char"], "variation_id": None}]
    with db.session() as s:
        s.add_all([
            Shot(episode_id=ids["episode"], order=0, code="S01", shot_size="中景", cast=cast,
                 image_prompt=f"medium shot, 35mm. {OLD} He crouches by the scooter."),
            Shot(episode_id=ids["episode"], order=1, code="S02", shot_size="特写", cast=cast,
                 image_prompt="close-up. the stocky man with grey stubble frowns."),
            Shot(episode_id=ids["episode"], order=2, code="S03", shot_size="全景", cast=cast,
                 image_prompt=f"wide. {{{{char:{ids['char']}}}}} He waits."),
        ])
        s.flush()
        s1 = s.query(Shot).filter_by(code="S01").one()
        sh, _, proj, style = load_shot(s, s1.id)
        before = keyframe_input_hash(sh, proj, style, "zimage")
        ch = s.get(Character, ids["char"])
        ch.visual_prompt = NEW
        res = refresh_shot_looks(s, ch, [OLD])
    assert res == {"updated": ["第1集 S01"], "replacements": 1, "already": ["第1集 S03"],
                   "unmatched": ["第1集 S02"], "partial": []}
    with db.session() as s:
        s1 = s.query(Shot).filter_by(code="S01").one()
        assert s1.image_prompt == f"medium shot, 35mm. {{{{char:{ids['char']}}}}} He crouches by the scooter."
        sh, _, proj, style = load_shot(s, s1.id)
        assert shot_image_prompt(sh, proj) == f"medium shot, 35mm. {NEW} He crouches by the scooter."
        # 首帧跟着过期
        assert keyframe_input_hash(sh, proj, style, "zimage") != before


def test_refresh_look_api_uses_portrait_history(api):
    """不给旧外貌：用定妆照候选里记着的外貌（切换造型时当前外貌会记到切走的那张上）。"""
    from backend.domain.models import Shot
    from backend.domain.slots import add_candidate

    sv = api.app.state.services
    api.post("/api/projects", json={"title": "demo"})
    ep_id = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()["id"]
    cid = api.post("/api/projects/demo/characters", json={"name": "老周", "visual_prompt": OLD}).json()["id"]
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "character", cid, "portrait")
        a = add_candidate(s, slot, file_path="a.png", engine="zimage", params={"visual_prompt": OLD},
                          input_hash="a", job_id=None)
        b = add_candidate(s, slot, file_path="b.png", engine="zimage", params={"visual_prompt": NEW},
                          input_hash="b", job_id=None, auto_select=False)
        sh = Shot(episode_id=ep_id, order=0, code="S01", image_prompt=f"wide. {OLD} He waits.",
                  shot_size="全景", cast=[{"character_id": cid, "variation_id": None}])
        s.add(sh)
        s.flush()
        bid, sid = b.id, sh.id
    assert api.get(f"/api/shots/{sid}").json()["image_prompt_resolved"] is None
    assert api.post(f"/api/characters/{cid}/portraits/{bid}/use").json()["visual_prompt"] == NEW
    r = api.post(f"/api/characters/{cid}/shots/refresh-look", json={})
    assert r.status_code == 200 and r.json()["updated"] == ["第1集 S01"]
    d = api.get(f"/api/shots/{sid}").json()
    assert d["image_prompt"] == f"wide. {{{{char:{cid}}}}} He waits."
    assert d["image_prompt_resolved"] == f"wide. {NEW} He waits."
    assert api.post("/api/characters/999/shots/refresh-look", json={}).status_code == 404


# ---------------------------------------------------------------- 第二次样片的真实外貌：定妆照只留腰部以上
RUN2_LOOKS = {
    '老陈': (
        'Chinese man in his mid-sixties, lean and slightly stooped frame, long weathered face wit'
        'h deep nasolabial folds, sun-darkened leathery skin, sparse short-cropped gray-white hai'
        'r receding at the temples, faint gray stubble. Wears a faded navy-blue cotton work jacke'
        't with frayed cuffs over a gray undershirt, a dark brown canvas apron stained with shoe '
        'polish and glue, loose black cotton trousers, worn black cloth shoes. Large rough hands '
        'with thick yellowed calluses, cracked knuckles and polish-blackened fingertips. Signatur'
        'e detail: old wire-rimmed reading glasses with one temple wrapped in black thread, hangi'
        'ng from a cord around his neck.'
    ),
    '阿青': (
        'Chinese woman in her mid-twenties, slim athletic build, oval face with defined cheekbone'
        's, light tan skin with faint freckles and a slight sun flush, bright dark eyes. Shoulder'
        '-length black hair pulled into a low messy ponytail, loose strands framing her temples. '
        'Wears a bright yellow food-delivery rider jacket of lightweight waterproof nylon with si'
        'lver reflective strips and an unbranded patch, black stretch cargo trousers, scuffed whi'
        'te sneakers, a yellow half-shell rider helmet hooked at her hip. Signature detail: a sma'
        'll red plastic strawberry-shaped hair clip above her left ear, clearly borrowed from a c'
        'hild.'
    ),
    '小满': (
        'Chinese girl about five or six years old, small frame, round face with chubby rosy cheek'
        's, bright round dark eyes, short soft straight bangs. Black hair tied into two short hig'
        'h pigtails with red elastic bands topped by small red fuzzy bobbles. Wears a pale pink k'
        'nitted cotton cardigan over a white T-shirt printed with a tiny rabbit, a faded denim pi'
        'nafore dress, white ankle socks, and old dull gray canvas slip-on shoes worn thin at the'
        ' toes. Signature detail: a faded red string bracelet with a tiny brass bell on her left '
        'wrist.'
    ),
}
RUN2_ANCHOR = (
    'Photorealistic live-action cinematic style, shot on 35mm film with an anamorphic lens, s'
    'hallow depth of field and subtle natural film grain. Restrained, nostalgic Chinese old-s'
    'treet drama aesthetic. Warm golden-hour palette dominated by amber, burnt orange and hon'
    'ey tones, balanced by muted teal shadows and desaturated greens. Low-angle sunset sideli'
    'ght with long soft shadows, gentle backlit rim light and faint atmospheric haze. Realist'
    'ic skin with visible pores, fine lines and natural imperfections, no beauty retouching. '
    'Tactile materials: worn leather, weathered wood, frayed cotton, polished bluestone. Quie'
    't, intimate framing in medium and close shots, Kodak Vision3 500T color science, gentle '
    'halation on highlights.'
)


@pytest.mark.parametrize("name", list(RUN2_LOOKS))
def test_run2_portrait_prompt_is_waist_up_only(name):
    """第二次样片：krea2（cfg 1，没有负面提示词）照着外貌里的裤子、鞋、挂在腰上的头盔画成了全身 / 坐姿，
    锚里的 atmospheric haze 画成了烟雾，「老茧」画成了金戒指。定妆照提示词里这些都不能出现。"""
    from backend.pipeline.characters import portrait_prompt

    p = portrait_prompt(RUN2_LOOKS[name], RUN2_ANCHOR)
    low = p.lower()
    for bad in ("trousers", "shoes", "sneakers", "socks", "hip", "calluses", "knuckles",
                "fingertips", "haze", "golden-hour", "backlit", "sitting", "squatting"):
        assert not re.search(rf"\b{bad}\b", low.replace("shoe polish", "")), (name, bad)
    assert "standing upright" in low and "cropped at the waist" in low and "waist-up crop" in low
    assert "grey studio backdrop" in low and "Kodak Vision3 500T" in p
    # 上半身的外貌都还在
    if name == "老陈":
        assert "navy-blue cotton work jacket" in p and "apron stained with shoe polish" in p
        assert "reading glasses" in p
    if name == "阿青":
        assert "rider jacket" in p and "strawberry-shaped hair clip" in p
    if name == "小满":
        assert "pinafore dress" in p and "pigtails" in p


def test_portrait_look_keeps_upper_part_of_mixed_clause():
    from backend.pipeline.prompting import portrait_look, upper_body_look

    look = "A tall man. Wears a long navy coat over grey trousers, sitting on a stool, black boots."
    assert portrait_look(look) == "A tall man. Wears a long navy coat."
    assert upper_body_look(look) == "A tall man. Wears a long navy coat, sitting on a stool."


def test_refresh_does_not_half_replace_a_v2_paraphrase(tmp_path, db):
    """真实的 v2 写法：特写只原样抄了外貌第一句、服装是改写的（还带着旧外貌连续 6 个词）。
    换掉第一句会把旧服装留在提示词里——不换，列进 partial；中景只抄了第一句的也不按「只留脸」去换。"""
    from backend.domain.characters import refresh_shot_looks
    from backend.domain.models import Shot

    ctx, runner, ids = setup(tmp_path, db, look=OLD)
    cast = [{"character_id": ids["char"], "variation_id": None}]
    first = "Chinese man in his mid-fifties with a weathered square face and grey stubble."
    close = (f"close-up, 85mm. {first} He wears a faded navy-blue cotton work jacket over a grey "
             "T-shirt, frowning at the shoe.")
    mid = f"medium shot, 50mm. {first} He wears a dark jacket and crouches by the scooter."
    with db.session() as s:
        s.add_all([Shot(episode_id=ids["episode"], order=0, code="S01", shot_size="特写", cast=cast,
                        image_prompt=close),
                   Shot(episode_id=ids["episode"], order=1, code="S02", shot_size="中景", cast=cast,
                        image_prompt=mid)])
        s.flush()
        ch = s.get(Character, ids["char"])
        ch.visual_prompt = NEW
        res = refresh_shot_looks(s, ch, [OLD])
    assert res["updated"] == [] and res["partial"] == ["第1集 S01"] and res["unmatched"] == ["第1集 S02"]
    with db.session() as s:
        assert s.query(Shot).filter_by(code="S01").one().image_prompt == close
        assert s.query(Shot).filter_by(code="S02").one().image_prompt == mid


def test_refresh_close_up_with_clean_face_copy_is_replaced(tmp_path, db):
    from backend.domain.characters import refresh_shot_looks
    from backend.domain.models import Shot

    ctx, runner, ids = setup(tmp_path, db, look=OLD)
    first = "Chinese man in his mid-fifties with a weathered square face and grey stubble."
    with db.session() as s:
        s.add(Shot(episode_id=ids["episode"], order=0, code="S01", shot_size="特写",
                   cast=[{"character_id": ids["char"], "variation_id": None}],
                   image_prompt=f"close-up, 85mm. {first} He frowns at the shoe."))
        s.flush()
        ch = s.get(Character, ids["char"])
        ch.visual_prompt = NEW
        assert refresh_shot_looks(s, ch, [OLD])["updated"] == ["第1集 S01"]
