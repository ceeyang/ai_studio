"""WebSocket 只接受本机页面（或不带 Origin 的非浏览器客户端）。"""
import pytest
from starlette.websockets import WebSocketDisconnect

from backend.app.deps import is_local_origin


@pytest.mark.parametrize("origin", ["http://evil.example", "https://127.0.0.1.evil.example",
                                    "null", "http://localhost.evil.example:5173", "file://"])
def test_ws_rejects_foreign_origin(api, origin):
    with pytest.raises(WebSocketDisconnect) as exc:
        with api.websocket_connect("/api/ws", headers={"origin": origin}) as ws:
            ws.receive_json()
    assert exc.value.code == 1008


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:8700",
                                    "http://localhost"])
def test_ws_accepts_local_origin(api, origin):
    with api.websocket_connect("/api/ws", headers={"origin": origin}) as ws:
        assert ws.receive_json()["type"] == "queue"


def test_ws_without_origin_still_works(api):
    with api.websocket_connect("/api/ws") as ws:
        assert ws.receive_json()["type"] == "queue"


def test_is_local_origin():
    assert is_local_origin("http://127.0.0.1:5173")
    assert is_local_origin("https://localhost")
    assert not is_local_origin("http://127.0.0.1.nip.io")
    assert not is_local_origin("http://192.168.1.5:8700")
