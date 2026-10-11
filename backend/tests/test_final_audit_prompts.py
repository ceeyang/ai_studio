"""最终审查（2026-10-11）：首帧外貌词数上限、视频对白说话人、插入镜头、新入画外貌截断、负面词去重。
数据取自第三次样片 ep 8（雨夜外卖）。"""
from backend.pipeline.prompting import (Speaker, compose_video_prompt, entering_clause,
                                        resolve_char_tokens)

AJIE = ("Chinese man in his early twenties, lean angular face with high cheekbones and thick straight black "
        "eyebrows, short slightly messy black hair, slim wiry build of medium height. Wears a bright yellow "
        "hooded PVC rain jacket over a dark gray cotton hoodie, black nylon work trousers and worn black canvas "
        "sneakers. Signature detail: the glossy bright yellow rain jacket that dominates his silhouette, paired "
        "with a matte yellow open-face delivery helmet with a clear visor.")
SUQING = ("Chinese woman in her late twenties, oval face with a defined jawline and straight dark eyebrows, "
          "shoulder-length straight black hair tucked behind her ears, slender build of average height. Wears a "
          "long oversized oatmeal-colored chunky knit cardigan over a plain white cotton T-shirt, charcoal "
          "straight-leg trousers and simple gray felt indoor slippers. Signature detail: the long oatmeal knit "
          "cardigan hanging below her hips, a soft warm block of color against cool surroundings.")
LOOKS = {13: AJIE, 14: SUQING}
S01 = ("Medium shot, 50mm, eye level, over the shoulder from just inside the doorway. {{char:14}} The young woman "
       "stands at frame right as a soft-focus foreground shape, body still, staring straight at the delivery man. "
       "{{char:13}} The young man stands on the landing at frame left of center, rain dripping off him, holding a "
       "white plastic takeout bag with a knotted top out toward her with his right arm extended, eyebrows slightly "
       "raised. The dented green-painted steel security door hangs half open between them, a worn red doormat under "
       "his sneakers, cracked whitewashed walls with peeling flyers behind him, a rain-streaked frosted window at "
       "the back. A single bare bulb above casts warm amber top light fading to cold blue-gray at the edges. The wet "
       "yellow jacket is the color accent; focus is on the young man's face.")
S02 = ("Extreme close-up, 100mm macro, slightly high angle looking down. {{char:14}} The young woman's fingers hold "
       "a faded yellowed 4x6 inch paper photograph upright in front of her knit sleeve, just about to turn it.")
S04 = ("Extreme close-up, 100mm macro, slightly high angle looking down at the hip. {{char:13}} The young man stands "
       "motionless, his right hip filling the frame, a black smartphone sticking halfway out of the open side "
       "pocket of the wet yellow rain jacket.")


# ---------------------------------------------------------------- P0-1 双人镜头不丢服装
def test_two_person_medium_shot_keeps_costume_for_everyone():
    out = resolve_char_tokens(S01, LOOKS, "中景")
    assert "oatmeal-colored chunky knit cardigan" in out
    assert "yellow hooded PVC rain jacket" in out
    assert "trousers" not in out and "slippers" not in out


def test_over_cap_trims_per_person_to_identity_and_costume():
    """超上限时每人退到「身份句 + 服装句」，不退成只写脸；服装两人都在。"""
    long_text = S01 + " " + " ".join(["Rain streaks the frosted window glass."] * 5)
    notes: list = []
    out = resolve_char_tokens(long_text, LOOKS, "中景", notes)
    assert "oatmeal-colored chunky knit cardigan" in out and "rain jacket" in out
    assert "oval face" in out and "lean angular face" in out
    assert notes and "服装" in notes[0]


def test_face_regex_has_word_boundaries():
    from backend.pipeline.prompting import _FACE

    assert not _FACE.search("a matte yellow open-face delivery helmet")
    assert not _FACE.search("the polished surface of the table")
    assert _FACE.search("oval face with a defined jawline")


# ---------------------------------------------------------------- 插入镜头：只露手 / 腰 / 物件
def test_insert_shot_of_hands_gets_no_face_or_hair():
    """S02 拍手里的照片：大特写里不能填进「oval face / shoulder-length hair」，只留手和衣袖能看到的服装。"""
    out = resolve_char_tokens(S02, LOOKS, "大特写")
    assert "oval face" not in out and "hair" not in out
    assert "cardigan" in out   # 衣袖（服装）留着，和定妆照对得上


def test_insert_shot_of_waist_gets_clothing_only():
    out = resolve_char_tokens(S04, LOOKS, "大特写")
    assert "angular face" not in out and "eyebrows" not in out
    assert "rain jacket" in out


def test_insert_shot_detection():
    from backend.pipeline.prompting import insert_kind

    assert insert_kind(S02, "大特写") == "hands"
    assert insert_kind(S04, "大特写") == "body"
    assert insert_kind("Extreme close-up. {{char:13}} The young man's eyes welling up.", "大特写") == ""
    assert insert_kind(S01, "中景") == ""
    assert insert_kind("Extreme close-up of a faded photograph on the doormat.", "大特写") == "object"


# ---------------------------------------------------------------- P0-2 视频对白
def test_off_screen_voice_is_gendered_and_lines_are_separate_sentences():
    dlg = [{"speaker": "苏晴", "zh": "这单是我故意点的。", "start": 0.5, "end": 2.3},
           {"speaker": "站长", "zh": "阿杰，下一单要超时了！", "start": 2.6, "end": 4.6}]
    speakers = {"苏晴": Speaker("the Chinese woman in her late twenties", False, "female"),
                "站长": Speaker("the Chinese man in his late thirties", False, "male"),
                "阿杰": Speaker("the young man", True, "male")}
    v = compose_video_prompt("he listens", "img", "Rain hissing outside.", dlg, anchor="", has_audio=True,
                             speakers=speakers)
    audio = v.split("\nAudio: ", 1)[1]
    assert ('An off-screen female voice (the Chinese woman in her late twenties) says in Mandarin Chinese: '
            '"这单是我故意点的。"') in audio
    assert "Then an off-screen male voice (the Chinese man in his late thirties) says" in audio
    assert "lips stay closed. Then" in audio      # 两句之间有句号分开
    assert "closed an off-screen" not in audio


def test_on_screen_speaker_uses_same_ref_as_action():
    dlg = [{"speaker": "苏晴", "zh": "你终于来了。", "start": 0.6, "end": 2.0},
           {"speaker": "阿杰", "zh": "您……认识我？", "start": 2.6, "end": 4.5}]
    speakers = {"苏晴": Speaker("the young woman", True, "female"),
                "阿杰": Speaker("the young man", True, "male")}
    v = compose_video_prompt("The young man holds the bag out toward the young woman.", "img", "", dlg,
                             anchor="", has_audio=True, speakers=speakers)
    audio = v.split("\nAudio: ", 1)[1]
    assert audio.startswith('The young woman says in Mandarin Chinese: "你终于来了。" '
                            'Then the young man says in Mandarin Chinese: "您……认识我？"')


def test_speakers_take_ref_from_image_prompt_after_token():
    from types import SimpleNamespace

    from backend.pipeline.videos import _speakers

    proj = SimpleNamespace(characters=[SimpleNamespace(id=13, name="阿杰", visual_prompt=AJIE),
                                       SimpleNamespace(id=14, name="苏晴", visual_prompt=SUQING),
                                       SimpleNamespace(id=15, name="站长",
                                                       visual_prompt="Chinese man in his late thirties, round "
                                                                     "fleshy face.")])
    shot = SimpleNamespace(image_prompt=S01, cast=[{"character_id": 14}, {"character_id": 13}])
    sp = _speakers(proj, shot)
    assert sp["苏晴"] == Speaker("the young woman", True, "female")
    assert sp["阿杰"] == Speaker("the young man", True, "male")
    assert sp["站长"] == Speaker("the Chinese man in his late thirties", False, "male")


def test_hash_version_of_video_prompt_unchanged():
    dlg = [{"speaker": "林夏", "zh": "词被改了", "start": 0.5, "end": 2}]
    v = compose_video_prompt("she looks up", "img", "quiet hum", dlg, anchor="A", has_audio=True)
    assert v == 'A she looks up\nAudio: quiet hum 林夏 says in Mandarin Chinese: "词被改了"'


# ---------------------------------------------------------------- P1-3 新入画外貌不在词中间截断
def test_entering_clause_cuts_at_sentence_boundary():
    c = entering_clause([SUQING])
    assert not c.endswith("over a plain.")
    assert "oatmeal-colored chunky knit cardigan over a plain white cotton T-shirt" in c
    assert c.startswith("Entering the frame: Chinese woman in her late twenties")


# ---------------------------------------------------------------- P2-1 负面词去重（只在发给引擎时）
def test_request_negative_is_deduplicated():
    from pathlib import Path

    from backend.engines.base import ImageRequest, VideoRequest

    neg = "cartoon, anime, text, watermark, cartoon, Anime , watermark"
    assert ImageRequest(prompt="p", negative=neg).negative == "cartoon, anime, text, watermark"
    assert VideoRequest(prompt="p", first_frame=Path("x"), negative=neg).negative == \
        "cartoon, anime, text, watermark"


def test_insert_shot_video_takes_no_portraits(tmp_path, db):
    """S02 这种只露手的大特写：首帧锁过脸也不把定妆照带进视频；按以前规则出的视频不判过期。"""
    from backend.domain.models import Shot
    from backend.domain.slots import selected_candidate
    from backend.pipeline.keyframes import run_keyframe
    from backend.pipeline.videos import run_video
    from backend.tests.test_keyframes_videos import _with_portrait, setup

    ctx, runner, shot_id = setup(tmp_path, db)
    cid = _with_portrait(db, shot_id, "h3_image")
    with db.session() as s:
        sh = s.get(Shot, shot_id)
        sh.shot_size = "大特写"
        sh.image_prompt = ("Extreme close-up, 100mm macro. {{char:%d}} The stocky man's fingers hold a faded "
                           "photograph upright." % cid)
    run_keyframe(ctx, {"shot_id": shot_id})
    run_video(ctx, {"shot_id": shot_id})
    assert runner.calls[-1].meta["refs"] == []
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "video").params["identity"] == "first_frame"
