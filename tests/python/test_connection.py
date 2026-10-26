"""接続をまたいだ送信 (ConnectionHub)"""

import asyncio
import json

import pytest

from gm_server import app
from gm_server.context import ConnectionHub


class FakeSocket:
    def __init__(self, closed=False):
        self.closed = closed
        self.sent = []

    async def send(self, text):
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(json.loads(text))


async def test_hub_sends_to_the_new_connection_when_the_origin_closed():
    """生成中に FVTT を再読み込みしても、結果は再接続した GM に届く"""
    hub = ConnectionHub()
    old, new = FakeSocket(), FakeSocket()
    await hub.attach(old)
    send = hub.sender_for(old)
    await send({"action": "chat", "content": "1"})
    old.closed = True
    hub.detach(old)
    await hub.attach(new)
    await send({"action": "builder_stage_result", "stage": "settlement"})
    assert [m["action"] for m in old.sent] == ["chat"]
    assert [m["action"] for m in new.sent] == ["builder_stage_result"]


async def test_hub_keeps_results_until_someone_connects():
    hub = ConnectionHub(backlog_limit=2)
    old = FakeSocket(closed=True)
    send = hub.sender_for(old)
    assert await hub.send({"action": "progress", "message": "経過"}, origin=old) is False
    for i in range(3):
        await send({"action": "chat", "content": str(i)})
    new = FakeSocket()
    await hub.attach(new)
    # 途中経過は溜めず、溜める件数には上限がある
    assert [m["content"] for m in new.sent] == ["1", "2"]
    assert hub.backlog == []


@pytest.mark.parametrize("origin, allowed", [
    (None, True),                              # ブラウザ以外 (tools/test_send.py など)
    ("http://localhost:30000", True),
    ("http://127.0.0.1:30000", True),
    ("http://[::1]:30000", True),
    ("https://evil.example", False),           # GM が開いている別のサイト
    ("http://localhost.evil.example", False),
    ("http://192.168.1.10:30000", False),      # 足していない LAN の名前
    ("null", False),                           # file:// や sandbox の iframe
])
def test_is_allowed_origin(origin, allowed):
    assert app.is_allowed_origin(origin, allowed=[]) is allowed


def test_is_allowed_origin_accepts_configured_origins():
    assert app.is_allowed_origin("http://192.168.1.10:30000/", allowed=["http://192.168.1.10:30000"])


async def test_server_closes_connections_from_other_sites(monkeypatch):
    """別のサイトのページからは、AI の設定や /ai を受け付けない (接続した直後に閉じる)"""
    import websockets

    handled = []

    async def fake_serve(ctx, messages):
        async for raw in messages:
            handled.append(raw)

    monkeypatch.setattr(app, "serve_messages", fake_serve)
    monkeypatch.setattr(app, "_services", lambda: (None, None))
    monkeypatch.setattr(app, "_hub", ConnectionHub())
    async with websockets.serve(app.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}", origin="https://evil.example") as ws:
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await ws.send(json.dumps({"event": "ai_settings_request"}))
                await asyncio.wait_for(ws.recv(), 2)
            assert closed.value.rcvd.code == 1008
        async with websockets.connect(f"ws://127.0.0.1:{port}", origin="http://localhost:30000") as ws:
            await ws.send(json.dumps({"event": "ai_settings_request"}))
        await asyncio.sleep(0.1)
    assert handled == ['{"event": "ai_settings_request"}']
