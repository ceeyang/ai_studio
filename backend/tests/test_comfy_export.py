"""API 格式工作流 → ComfyUI 界面格式，以及下载接口。"""
import json
import uuid

from backend.domain.models import Shot
from backend.domain.shots import renumber_shots
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.engines.comfyui.errors import ComfyError
from backend.engines.comfyui.export_ui import MissingNodeError, info_lookup, to_ui_graph

INFO = {
    "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": [["a.safetensors", "b.safetensors"]]}},
        "output": ["MODEL", "CLIP", "VAE"], "output_name": ["MODEL", "CLIP", "VAE"]},
    "KSampler": {
        "input": {"required": {"model": ["MODEL"],
                               "seed": ["INT", {"default": 0, "control_after_generate": True}],
                               "steps": ["INT", {"default": 20}],
                               "sampler_name": ["COMBO", {"options": ["euler"]}],
                               "denoise": ["FLOAT", {"default": 1.0}]}},
        "output": ["LATENT"], "output_name": ["LATENT"]},
    "LoadImage": {
        "input": {"required": {"image": [["x.png"], {"image_upload": True}]}},
        "output": ["IMAGE", "MASK"], "output_name": ["IMAGE", "MASK"]},
    "RefNode": {
        "input": {"required": {"ref_images": ["COMFY_AUTOGROW_V3", {}]},
                  "optional": {"strength": ["FLOAT", {"default": 0.5}]}},
        "output": ["CONDITIONING"]},
}
API = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "b.safetensors"}},
    "7": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "seed": 42, "steps": 8,
                                               "sampler_name": "euler"}},
    "9": {"class_type": "LoadImage", "inputs": {"image": "ref.png"}},
    "12": {"class_type": "RefNode", "inputs": {"ref_images.ref_image_0": ["9", 0]}},
}


def by_type(graph, t):
    return next(n for n in graph["nodes"] if n["type"] == t)


def test_widgets_follow_declaration_order_with_ui_extras():
    g = to_ui_graph(API, info_lookup(INFO))
    assert [n["id"] for n in g["nodes"]] == [1, 2, 3, 4]
    assert by_type(g, "CheckpointLoaderSimple")["widgets_values"] == ["b.safetensors"]
    # seed 后面多一个 control_after_generate；没给的 denoise 用默认值
    assert by_type(g, "KSampler")["widgets_values"] == [42, "fixed", 8, "euler", 1.0]
    # LoadImage 多一个上传按钮的占位
    assert by_type(g, "LoadImage")["widgets_values"] == ["ref.png", "image"]
    # autogrow 输入不算 widget；可选的 strength 用默认值
    assert by_type(g, "RefNode")["widgets_values"] == [0.5]


def test_links_and_slots():
    g = to_ui_graph(API, info_lookup(INFO))
    assert g["links"] == [[1, 1, 0, 2, 0, "MODEL"], [2, 3, 0, 4, 0, "IMAGE"]]
    ks = by_type(g, "KSampler")
    assert ks["inputs"] == [{"name": "model", "type": "MODEL", "link": 1}]
    ref = by_type(g, "RefNode")
    # 没在 object_info 里声明的 autogrow 子插槽：类型取上游输出类型
    assert ref["inputs"] == [{"name": "ref_images.ref_image_0", "type": "IMAGE", "link": 2}]
    ckpt = by_type(g, "CheckpointLoaderSimple")
    assert ckpt["outputs"][0] == {"name": "MODEL", "type": "MODEL", "links": [1], "slot_index": 0}
    assert ckpt["outputs"][1]["links"] == []
    # 没有 output_name 时用类型名
    assert ref["outputs"] == [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [],
                               "slot_index": 0}]


def test_layout_and_header():
    g = to_ui_graph(API, info_lookup(INFO))
    assert by_type(g, "CheckpointLoaderSimple")["pos"] == [0, 0]
    assert by_type(g, "LoadImage")["pos"] == [0, 200]
    assert by_type(g, "KSampler")["pos"] == [420, 0]
    assert by_type(g, "RefNode")["pos"] == [420, 200]
    assert g["last_node_id"] == 4 and g["last_link_id"] == 2 and g["version"] == 0.4
    uuid.UUID(g["id"])  # 必须是字符串 UUID，否则界面报"无法找到工作流"


def test_missing_node_type_is_reported_by_name():
    try:
        to_ui_graph(API, info_lookup({k: v for k, v in INFO.items() if k != "RefNode"}))
    except MissingNodeError as e:
        assert "RefNode" in str(e)
    else:
        raise AssertionError("应该抛 MissingNodeError")


class FakeInfoClient:
    def __init__(self, info=None, error=None):
        self.info, self.error, self.calls = info or {}, error, 0

    def object_info(self, *, tries=None, timeout=120):
        self.calls += 1
        if self.error:
            raise self.error
        return self.info


def seed_candidate(api, *, with_workflow=True):
    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()
    sv = api.app.state.services
    with sv.db.session() as s:
        shot = Shot(episode_id=ep["id"], order=0, image_prompt="p")
        s.add(shot)
        s.flush()
        renumber_shots(s, ep["id"])
        run_dir = sv.media.run_dir("demo", 42)
        (run_dir / "workflow.api.json").write_text(json.dumps(API), encoding="utf-8")
        img = sv.media.new_media_path("demo", "first_frame", "png")
        img.write_bytes(b"png")
        params = {"seed": 1}
        if with_workflow:
            params["workflow"] = sv.media.rel(run_dir / "workflow.api.json")
        slot = get_or_create_slot(s, "shot", shot.id, "first_frame")
        return add_candidate(s, slot, file_path=sv.media.rel(img), engine="zimage",
                             params=params, input_hash="h", job_id=42).id


def test_comfy_workflow_download(api):
    cid = seed_candidate(api)
    fake = FakeInfoClient(INFO)
    api.app.state.services.comfy_client = fake
    r = api.get(f"/api/candidates/{cid}/comfy-workflow")
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"] == f'attachment; filename="zimage_{cid}.json"'
    g = r.json()
    assert [n["type"] for n in g["nodes"]] == ["CheckpointLoaderSimple", "KSampler", "LoadImage",
                                              "RefNode"]
    assert g["last_link_id"] == 2 and fake.calls == 1


def test_comfy_workflow_not_recorded_or_missing_candidate(api):
    cid = seed_candidate(api, with_workflow=False)
    api.app.state.services.comfy_client = FakeInfoClient(INFO)
    r = api.get(f"/api/candidates/{cid}/comfy-workflow")
    assert r.status_code == 404 and "工作流" in r.json()["detail"]
    assert api.get("/api/candidates/999/comfy-workflow").status_code == 404


def test_comfy_workflow_missing_node_and_comfy_down(api):
    cid = seed_candidate(api)
    sv = api.app.state.services
    sv.comfy_client = FakeInfoClient({k: v for k, v in INFO.items() if k != "RefNode"})
    r = api.get(f"/api/candidates/{cid}/comfy-workflow")
    assert r.status_code == 400 and "RefNode" in r.json()["detail"]
    sv.comfy_client = FakeInfoClient(error=ComfyError("连不上 ComfyUI（http://127.0.0.1:9）",
                                                      retryable=True))
    r = api.get(f"/api/candidates/{cid}/comfy-workflow")
    assert r.status_code == 502 and "连不上" in r.json()["detail"]
