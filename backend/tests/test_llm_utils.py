import jinja2
import pytest
from pydantic import ValidationError

from backend.llm.base import LLMError
from backend.llm.jsonutil import extract_json
from backend.llm.prompts import PromptLibrary
from backend.llm.schemas import ScriptStructure, ShotOut

WORDS = " ".join(["cinematic"] * 90)


def test_extract_json_variants():
    assert extract_json('好的：\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('<think>先想想 {"x": 0}</think>{"a": [1, 2,],}') == {"a": [1, 2]}
    with pytest.raises(LLMError, match="没有返回 JSON"):
        extract_json("对不起，我做不到")
    with pytest.raises(LLMError, match="JSON 解析失败"):
        extract_json('{"a": }')


def test_script_structure_location_must_exist():
    ok = {"title": "t", "characters": [{"name": "林", "description": "d"}],
          "locations": [{"name": "直播间"}], "scenes": [{"location": "直播间", "text": "x"}]}
    assert ScriptStructure.model_validate(ok).scenes[0].location == "直播间"
    bad = {**ok, "scenes": [{"location": "天台", "text": "x"}]}
    with pytest.raises(ValidationError, match="天台"):
        ScriptStructure.model_validate(bad)


def shot(**kw):
    base = {"shot_size": "近景", "camera_move": "static", "action": "a", "image_prompt": WORDS,
            "motion_prompt": "m", "duration": 5}
    return {**base, **kw}


def test_shot_rules():
    assert ShotOut.model_validate(shot()).transition_out == "hard_cut"
    with pytest.raises(ValidationError, match="60–120"):
        ShotOut.model_validate(shot(image_prompt="too short"))
    with pytest.raises(ValidationError, match="same as before"):
        ShotOut.model_validate(shot(image_prompt=WORDS + " same as before"))
    with pytest.raises(ValidationError):
        ShotOut.model_validate(shot(dialogue=[{"speaker": "林", "zh": "这句台词实在是太长太长了超过了十六个字",
                                              "start": 0.5, "end": 2}]))
    with pytest.raises(ValidationError, match="duration"):
        ShotOut.model_validate(shot(dialogue=[{"speaker": "林", "zh": "你好", "start": 0.5, "end": 6}]))
    with pytest.raises(ValidationError):
        ShotOut.model_validate(shot(transition_out="spin"))


def test_render_builtin_storyboard():
    lib = PromptLibrary()
    system, user = lib.render("storyboard", anchor="ANCHOR", count=3, shot_seconds=5,
                              max_seconds=10, text="剧本段落", prev_action="",
                              characters=[{"name": "林", "visual_prompt": "LOOK"}],
                              location={"name": "直播间", "time_of_day": "夜", "atmosphere": "压抑",
                                        "visual_prompt": "ROOM"})
    assert "正好 3 个镜头" in user and "LOOK" in user and "ROOM" in user and "ANCHOR" in user
    assert "image_prompt" in system and "=== user ===" not in system + user


def test_render_all_builtin_templates():
    lib = PromptLibrary()
    s, u = lib.render("script_structure", project_title="提词员", target_seconds=90,
                      source_text="梗概")
    assert "梗概" in u and "90" in u and "JSON" in s
    s, u = lib.render("script_visual", project_title="提词员",
                      characters=[{"name": "林", "description": "主播"}],
                      locations=[{"name": "直播间", "time_of_day": "夜", "atmosphere": "压抑"}],
                      excerpt="节选")
    assert "林" in u and "直播间" in u and "anchor_text" in s


def test_project_override_and_strict_variables(tmp_path):
    (tmp_path / "storyboard.md").write_text("SYS\n=== user ===\nUSER {{ count }}", encoding="utf-8")
    lib = PromptLibrary(override_dir=tmp_path)
    assert lib.render("storyboard", count=3) == ("SYS", "USER 3")
    with pytest.raises(jinja2.UndefinedError):
        lib.render("storyboard")
