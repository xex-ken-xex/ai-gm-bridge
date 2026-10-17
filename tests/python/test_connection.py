"""接続をまたいだ送信 (ConnectionHub)"""

import json

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
