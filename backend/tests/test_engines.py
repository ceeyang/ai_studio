import pytest

from backend.config import Config, EngineCfg
from backend.engines.base import UPLOAD_PREFIX, ImageRequest, VideoRequest
from backend.engines.comfyui.template import WorkflowTemplate
from backend.engines.registry import EngineRegistry


@pytest.fixture
def reg():
    return EngineRegistry(Config())


def test_all_templates_load(reg):
    assert reg.names() == ["h3_image", "h3_video", "wan22_i2v", "zimage"]
    assert reg.names("image") == ["h3_image", "zimage"]
    assert reg.names("video") == ["h3_video", "wan22_i2v"]
    assert "MiniMaxH3ReferenceToVideo" in reg.get("h3_video").tpl.class_types()


def test_template_rejects_bad_map(tmp_path):
    (tmp_path / "t.json").write_text('{"1": {"class_type": "X", "inputs": {"a": 1}}}', encoding="utf-8")
    (tmp_path / "t.map.yaml").write_text('params:\n  p: [["2", "a"]]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="不存在的节点"):
        WorkflowTemplate.load(tmp_path, "t")
    (tmp_path / "t.map.yaml").write_text('params:\n  p: [["1", "b"]]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="没有输入"):
        WorkflowTemplate.load(tmp_path, "t")


def test_template_missing_param_raises(tmp_path):
    (tmp_path / "t.json").write_text('{"1": {"class_type": "X", "inputs": {"a": 1}}}', encoding="utf-8")
    (tmp_path / "t.map.yaml").write_text('params:\n  p: [["1", "a"]]\n', encoding="utf-8")
    tpl = WorkflowTemplate.load(tmp_path, "t")
    with pytest.raises(KeyError, match="缺少参数 p"):
        tpl.render({})
    assert tpl.render({"p": 5})["1"]["inputs"]["a"] == 5
    assert tpl.workflow["1"]["inputs"]["a"] == 1  # 模板本身不被改动


def test_zimage_build(reg):
    call = reg.image("zimage").build(ImageRequest(prompt="a cat", negative="blurry", aspect="16:9",
                                                  seed=42, prefix="studio/demo/kf_S01"))
    wf = call.workflow
    assert wf["5"]["inputs"]["text"] == "a cat" and wf["6"]["inputs"]["text"] == "blurry"
    assert (wf["7"]["inputs"]["width"], wf["7"]["inputs"]["height"]) == (1344, 768)
    assert wf["8"]["inputs"]["seed"] == 42
    assert wf["10"]["inputs"]["filename_prefix"] == "studio/demo/kf_S01"
    assert call.model_family == "zimage" and call.output_kind == "image"


def test_unsupported_aspect(reg):
    with pytest.raises(ValueError, match="画幅"):
        reg.image("zimage").build(ImageRequest(prompt="x", aspect="4:3"))


def test_h3_image_refs_and_truncation(reg, tmp_path):
    refs = [tmp_path / f"r{i}.png" for i in range(11)]
    call = reg.image("h3_image").build(ImageRequest(prompt="p", refs=refs, seed=1))
    wf = call.workflow
    assert wf["6"]["inputs"]["ref_images.ref_image_0"] == ["ref_0", 0]
    assert wf["ref_0"]["inputs"]["image"] == UPLOAD_PREFIX + str(refs[0].resolve())
    assert "ref_images.ref_image_8" in wf["6"]["inputs"]
    assert "ref_images.ref_image_9" not in wf["6"]["inputs"]
    assert call.dropped_refs == [str(refs[9]), str(refs[10])]
    assert wf["9"]["inputs"]["steps"] == 20 and "1b" not in wf


def test_h3_turbo_rewires_model():
    reg = EngineRegistry(Config(engines={"h3_image": EngineCfg(params={"turbo": True})}))
    wf = reg.image("h3_image").build(ImageRequest(prompt="p")).workflow
    assert wf["1b"]["class_type"] == "LoraLoaderModelOnly"
    assert wf["7"]["inputs"]["model"] == ["1b", 0] and wf["9"]["inputs"]["model"] == ["1b", 0]
    assert wf["9"]["inputs"]["steps"] == 4


def test_h3_video_frames_and_first_frame(reg, tmp_path):
    eng = reg.video("h3_video")
    assert eng.frames_for(5) == 124 and eng.frames_for(3) == 73 and eng.frames_for(0.1) == 5
    assert all((eng.frames_for(s) - 5) % 17 == 0 for s in (1, 2.5, 4, 7, 10))
    ff = tmp_path / "首帧.png"
    call = eng.build(VideoRequest(prompt="walk\nAudio: rain", first_frame=ff, seconds=5, seed=7,
                                  refs=[tmp_path / "c.png"]))
    wf = call.workflow
    assert wf["5"]["inputs"]["image"] == UPLOAD_PREFIX + str(ff.resolve())
    assert wf["6"]["inputs"]["length"] == 124 and wf["14"]["inputs"]["fps"] == 24.0
    assert wf["6"]["inputs"]["prompt"] == "walk\nAudio: rain"
    assert wf["6"]["inputs"]["ref_images.ref_image_1"] == ["ref_0", 0]
    assert wf["1b"]["class_type"] == "LoraLoaderModelOnly"   # 视频默认 turbo
    assert eng.caps.has_audio and call.model_family == "h3" and call.output_kind == "video"


def test_h3_video_frames_round_to_nearest_grid_point(reg):
    """4.5s/6s 的镜头不再一律往上取到 5.17s/6.58s：取最近的 17k+5，最少 5 帧。"""
    eng = reg.video("h3_video")
    assert eng.frames_for(4.5) == 107      # 108 帧离 107 近，不是 124
    assert eng.frames_for(6) == 141        # 144 帧离 141 近，不是 158
    assert eng.frames_for(5) == 124        # 120 帧离 124 近（107 更远）
    assert eng.frames_for(5.5) == 124 and eng.frames_for(6.5) == 158
    assert eng.frames_for(0) == 5 and eng.frames_for(0.3) == 5
    assert eng.frames_for(4.1) == 90       # 98 帧：离 90 差 8，离 107 差 9
    for s in (0.5, 1, 2.2, 3.7, 4.5, 7.3, 9.9):
        f = eng.frames_for(s)
        assert f >= 5 and (f - 5) % 17 == 0
        assert abs(f - s * 24) <= 8.5      # 离目标帧数不超过半个网格


def test_h3_video_clamps_to_max_seconds(reg, tmp_path):
    eng = reg.video("h3_video")
    wf = eng.build(VideoRequest(prompt="p", first_frame=tmp_path / "f.png", seconds=30)).workflow
    assert wf["6"]["inputs"]["length"] == eng.frames_for(eng.caps.max_seconds)


def test_wan_frames_and_size(reg, tmp_path):
    eng = reg.video("wan22_i2v")
    assert eng.frames_for(5) == 81 and eng.frames_for(1) == 17
    wf = eng.build(VideoRequest(prompt="p", first_frame=tmp_path / "f.png", aspect="9:16",
                                seconds=5, seed=3)).workflow
    assert (wf["12"]["inputs"]["width"], wf["12"]["inputs"]["height"]) == (480, 832)
    assert wf["13"]["inputs"]["noise_seed"] == 3 and wf["14"]["inputs"]["noise_seed"] == 3
    assert wf["12"]["inputs"]["length"] == 81 and wf["16"]["inputs"]["fps"] == 16.0
    assert not eng.caps.has_audio


def test_config_override_and_disabled_engine():
    reg = EngineRegistry(Config(engines={
        "zimage": EngineCfg(params={"unet": "custom.safetensors", "steps": 6}),
        "wan22_i2v": EngineCfg(enabled=False)}))
    z = reg.image("zimage")
    wf = z.build(ImageRequest(prompt="p")).workflow
    assert wf["1"]["inputs"]["unet_name"] == "custom.safetensors"
    assert wf["8"]["inputs"]["steps"] == 6
    assert z.tpl.model_files(z.overrides)[0] == "custom.safetensors"
    with pytest.raises(KeyError, match="wan22_i2v"):
        reg.video("wan22_i2v")
    with pytest.raises(KeyError, match="不是视频引擎"):
        reg.video("zimage")
