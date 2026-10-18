"""ハンドラ間で共有する依存オブジェクトとセッション状態"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List

from gm_server import logs
from gm_server.config import DATA_DIR
from gm_server.llm import LLMService
from gm_server.tts import SpeechSynthesizer


@dataclass
class SessionState:
    # /ai で始まったダンジョン生成ウィザードの途中経過 (user_prompt など)
    pending_dungeon: Dict[str, Any] = field(default_factory=dict)
    # /ai のスキル判定で聞き返した選択肢 ({label, skill, params} のリスト。ボタンの番号で選ぶ)
    pending_skill_options: List[Dict[str, Any]] = field(default_factory=list)
    # タイムアウト延長確認カードの回答待ち (request_id -> Future[bool])
    timeout_requests: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Context:
    send_json: Callable[[Dict], Awaitable[None]]
    llm: LLMService
    tts: SpeechSynthesizer
    state: SessionState
    data_dir: str = DATA_DIR
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def send(self, message: Dict) -> None:
        await self.send_json(message)


# 接続が無いあいだは溜めない種類 (途中経過は後から届いても意味が無い)
NO_BACKLOG_ACTIONS = ("progress",)
# 詳細ログに書かない種類 (数秒ごとに届く途中経過。中身はコンソールの上書き行と同じ)
NO_LOG_ACTIONS = ("progress", "builder_progress")


class ConnectionHub:
    """
    FVTT との接続をまたいで送り先を選ぶ。

    シナリオビルダーの生成は数分かかるため、その間に FVTT を再読み込みすると、依頼を受けた接続は閉じてしまう。
    そこで、依頼元の接続が閉じていたら、いま繋がっている別の接続 (再読み込み後の GM) へ送る。
    どこにも繋がっていなければ溜めておき、次に接続したときにまとめて送る。
    """

    def __init__(self, backlog_limit: int = 20):
        self.connections: List[Any] = []
        self.backlog: List[Dict] = []
        self.backlog_limit = backlog_limit

    def sender_for(self, websocket) -> Callable[[Dict], Awaitable[None]]:
        async def _send(message: Dict) -> None:
            await self.send(message, origin=websocket)

        return _send

    async def send(self, message: Dict, origin=None) -> bool:
        """送れたら True。送れなければ (途中経過以外は) 溜めて False"""
        text = json.dumps(message, ensure_ascii=False)
        if message.get("action") not in NO_LOG_ACTIONS:
            logs.logger.info(f"送信 {message.get('action')}: {logs.clip_for_log(text)}")
        targets = [origin] if origin is not None else []
        targets += [ws for ws in reversed(self.connections) if ws is not origin]
        for ws in targets:
            try:
                await ws.send(text)
                return True
            except Exception:
                # 閉じた接続 (ConnectionClosed) は次の候補へ
                continue
        if message.get("action") not in NO_BACKLOG_ACTIONS:
            self.backlog = (self.backlog + [message])[-self.backlog_limit:]
            print(f"FVTT に接続していないため、再接続したときに送ります ({message.get('action')})")
        return False

    async def attach(self, websocket) -> None:
        self.connections.append(websocket)
        pending, self.backlog = self.backlog, []
        if pending:
            print(f"接続が切れていたあいだの結果 {len(pending)} 件を送ります")
        for message in pending:
            await self.send(message, origin=websocket)

    def detach(self, websocket) -> None:
        if websocket in self.connections:
            self.connections.remove(websocket)


def websocket_sender(websocket) -> Callable[[Dict], Awaitable[None]]:
    async def _send(message: Dict) -> None:
        await websocket.send(json.dumps(message, ensure_ascii=False))

    return _send
