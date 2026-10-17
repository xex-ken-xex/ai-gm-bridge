"""ハンドラ間で共有する依存オブジェクトとセッション状態"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict

from gm_server.config import DATA_DIR
from gm_server.llm import LLMService
from gm_server.tts import SpeechSynthesizer


@dataclass
class SessionState:
    # /ai で始まったダンジョン生成ウィザードの途中経過 (user_prompt など)
    pending_dungeon: Dict[str, Any] = field(default_factory=dict)


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


def websocket_sender(websocket) -> Callable[[Dict], Awaitable[None]]:
    async def _send(message: Dict) -> None:
        await websocket.send(json.dumps(message, ensure_ascii=False))

    return _send
