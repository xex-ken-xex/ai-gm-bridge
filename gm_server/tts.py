"""edge-tts による音声合成"""

import asyncio
import itertools
import os
import time
from typing import Callable, Optional

from gm_server.config import DATA_DIR, DEFAULT_VOICE
from gm_server.text import normalize_tts_pitch, normalize_tts_rate

_counter = itertools.count()


def patch_edge_tts_endpoint() -> None:
    """Microsoft Edge TTS エンドポイント移行 (api.msedgeservices.com) への恒久対応 (403回避)"""
    from edge_tts import communicate, constants

    constants.WSS_URL = (
        "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
        f"?Ocp-Apim-Subscription-Key={constants.TRUSTED_CLIENT_TOKEN}"
    )
    communicate.WSS_URL = constants.WSS_URL


def voice_filename(prefix: str = "voice") -> str:
    """同一秒内の連続生成でも上書きされない音声ファイル名 (voice_<ミリ秒>_<連番>.mp3)"""
    return f"{prefix}_{int(time.time() * 1000)}_{next(_counter)}.mp3"


class SpeechSynthesizer:
    """音声ファイルを DATA_DIR に生成する。communicate_factory はテスト時に差し替え可能"""

    def __init__(
        self,
        data_dir: str = DATA_DIR,
        default_voice: str = DEFAULT_VOICE,
        communicate_factory: Optional[Callable] = None,
        settle_delay: float = 0.3,
    ):
        self.data_dir = data_dir
        self.default_voice = default_voice
        self.settle_delay = settle_delay
        if communicate_factory is None:
            import edge_tts

            patch_edge_tts_endpoint()
            communicate_factory = edge_tts.Communicate
        self._communicate_factory = communicate_factory

    async def synthesize(
        self,
        text: str,
        file_name: str,
        voice: Optional[str] = None,
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> bool:
        """声色（ボイス名・話す速さ・声の高さ）を反映して音声ファイルを生成"""
        if not text or not text.strip():
            return False
        try:
            file_path = os.path.join(self.data_dir, file_name)
            comm = self._communicate_factory(
                text.strip(),
                voice or self.default_voice,
                rate=normalize_tts_rate(rate),
                pitch=normalize_tts_pitch(pitch),
            )
            await comm.save(file_path)
            # FVTT 側がファイルを確実に読めるよう、書き込み完了後に少し待つ
            if self.settle_delay:
                await asyncio.sleep(self.settle_delay)
            return True
        except Exception as e:
            print(f"音声生成警告 (TTS一時スキップ): {e}")
            return False
