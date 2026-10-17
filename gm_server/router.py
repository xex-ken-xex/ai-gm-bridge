"""受信 event 名 → ハンドラの振り分け"""

import json
import traceback
from typing import Awaitable, Callable, Dict

from gm_server.deadline import is_timeout_choice
from gm_server import builder
from gm_server.handlers import ai_command, player_message, room_entered

Handler = Callable[[object, dict], Awaitable[None]]

HANDLERS: Dict[str, Handler] = {
    "ai_command": ai_command.handle_command,
    "ai_choice": ai_command.handle_choice,
    "room_entered": room_entered.handle,
    "player_message": player_message.handle,
    "builder_generate_stage": builder.handle_generate_stage,
    "builder_build_scene": builder.handle_build_scene,
}


def is_immediate(raw_message) -> bool:
    """
    受信キューを待たずに処理するメッセージか。
    延長確認の回答は、回答を待っている処理 (ダンジョン生成) が受信キューを塞いでいるため先に通す。
    """
    try:
        data = json.loads(raw_message)
    except (TypeError, ValueError):
        return False
    return isinstance(data, dict) and data.get("event") == "ai_choice" and is_timeout_choice(data.get("value"))


async def dispatch(ctx, raw_message) -> bool:
    """1件の受信メッセージを処理する。処理したら True、無視・失敗したら False"""
    try:
        data = json.loads(raw_message)
    except (TypeError, ValueError):
        print(f"不正なJSONを受信したため無視します: {str(raw_message)[:80]}")
        return False
    if not isinstance(data, dict):
        return False

    event_type = data.get("event")
    handler = HANDLERS.get(event_type)
    if handler is None:
        print(f"未対応のイベントを無視します: {event_type}")
        return False

    try:
        await handler(ctx, data)
        return True
    except Exception as e:
        print(f"エラー ({event_type}): {e}")
        traceback.print_exc()
        return False
