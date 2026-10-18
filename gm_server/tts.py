"""音声合成 (edge-tts と Irodori-TTS を切り替える。Irodori に繋がらないときは edge-tts に戻す)"""

import asyncio
import itertools
import json
import os
import time
import urllib.error
import urllib.request
import zlib
from typing import Awaitable, Callable, Optional

from gm_server import config
from gm_server.config import DATA_DIR, DEFAULT_VOICE
from gm_server.progress import log
from gm_server.text import normalize_tts_pitch, normalize_tts_rate

TTS_ENGINES = ("edge", "irodori")
# 起動時にモデルを読み込ませるための短い文 (音声は使わない)
WARM_UP_TEXT = "こんにちは。冒険の準備ができました。"

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


def rate_to_speed(rate: Optional[str]) -> float:
    """edge-tts の話速 (+10% 等) を Irodori の speed (1.1 等。0.25〜4.0) に直す"""
    percent = int(normalize_tts_rate(rate)[:-1])
    return round(min(4.0, max(0.25, 1.0 + percent / 100)), 2)


# edge-tts のボイス名に合わせた声の説明文 (VoiceDesign)。知らないボイス名は女性の説明文を使う
def default_caption(voice: Optional[str], pitch: Optional[str] = None) -> str:
    """声の説明文が無いときの既定。声の高さ (edge-tts の Hz) が大きくずれていれば一言足す"""
    caption = config.IRODORI_CAPTION_MALE if "Keita" in (voice or "") else config.IRODORI_CAPTION_FEMALE
    hz = int(normalize_tts_pitch(pitch)[:-2])
    if hz >= 10:
        caption += "やや高めの声。"
    elif hz <= -10:
        caption += "やや低めの声。"
    return caption


def voice_seed(voice: Optional[str], pitch: Optional[str] = None, caption: Optional[str] = None) -> Optional[int]:
    """
    声の揺れを抑える乱数の種。ナレーター (voice なし) は AI_GM_IRODORI_SEED そのもの、
    NPC はボイス名・声の高さ・説明文から決めた値 (同じ設定の NPC は毎回同じ声、設定が違えば別の声)
    """
    base = config.IRODORI_SEED
    if base is None:
        return None
    if not voice and not caption:
        return base
    key = f"{voice or ''}|{normalize_tts_pitch(pitch)}|{(caption or '').strip()}"
    return (base + zlib.crc32(key.encode("utf-8"))) % 2_000_000_000


# Irodori-TTS-Server は同じ PC (WSL) で動かすため、http_proxy などのプロキシ設定を通さない
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post_json(url: str, body: dict, headers: dict, timeout: float) -> bytes:
    request = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST",
    )
    try:
        with _NO_PROXY_OPENER.open(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e


def _get(url: str, headers: dict, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with _NO_PROXY_OPENER.open(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}") from e


class IrodoriClient:
    """Irodori-TTS-Server (OpenAI 互換 API の /v1/audio/speech)。post はテスト時に差し替え可能"""

    def __init__(
        self,
        url: str = config.IRODORI_URL,
        model: str = config.IRODORI_MODEL,
        voice: str = config.IRODORI_VOICE,
        api_key: str = config.IRODORI_API_KEY,
        timeout: float = config.IRODORI_TIMEOUT,
        first_timeout: float = config.IRODORI_FIRST_TIMEOUT,
        health_timeout: float = config.IRODORI_HEALTH_TIMEOUT,
        post: Optional[Callable[[str, dict, dict, float], bytes]] = None,
        get: Optional[Callable[[str, dict, float], bytes]] = None,
    ):
        self.url = url.rstrip("/")
        self.model = model
        self.voice = voice
        self.api_key = api_key
        self.timeout = timeout
        # 最初の 1 回だけはモデルの読み込みを待つため長めに待つ (固まったサーバーで何度も長く待たないよう 2 回目からは timeout)
        self.first_timeout = max(first_timeout, timeout)
        self.health_timeout = health_timeout
        self.attempted = False
        self._post = post or _post_json
        self._get = get or _get

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def health(self) -> None:
        """/health に繋がるか確かめる。繋がらなければ例外"""
        await asyncio.to_thread(self._get, f"{self.url}/health", self._headers(), self.health_timeout)

    def request_body(self, text: str, caption: str = "", speed: float = 1.0, voice: Optional[str] = None,
                     seed: Optional[int] = None) -> dict:
        body = {
            "model": self.model,
            "input": text,
            "voice": voice or self.voice,
            "response_format": "mp3",
            "speed": speed,
        }
        options = {}
        if caption:
            options["caption"] = caption
        if seed is not None:
            options["seed"] = seed
        if options:
            body["irodori"] = options
        return body

    async def speech(self, text: str, caption: str = "", speed: float = 1.0, voice: Optional[str] = None,
                     seed: Optional[int] = None) -> bytes:
        """mp3 のバイト列を返す。失敗したら例外。voice は参照音声の ID (無ければ設定の既定)"""
        body = self.request_body(text, caption, speed, voice, seed)
        timeout = self.timeout if self.attempted else self.first_timeout
        self.attempted = True
        audio = await asyncio.to_thread(self._post, f"{self.url}/v1/audio/speech", body, self._headers(), timeout)
        if not audio:
            raise RuntimeError("音声が空でした")
        return audio


class SpeechSynthesizer:
    """
    音声ファイルを DATA_DIR に生成する。engine が irodori なら Irodori-TTS-Server を使い、
    失敗したら edge-tts で作り直す (その後 retry_after 秒は edge-tts だけを使う)。
    communicate_factory / irodori / clock はテスト時に差し替え可能
    """

    def __init__(
        self,
        data_dir: str = DATA_DIR,
        default_voice: str = DEFAULT_VOICE,
        communicate_factory: Optional[Callable] = None,
        settle_delay: float = 0.3,
        engine: Optional[str] = None,
        irodori: Optional[IrodoriClient] = None,
        retry_after: float = config.IRODORI_RETRY_AFTER,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.data_dir = data_dir
        self.default_voice = default_voice
        self.settle_delay = settle_delay
        engine = (engine or config.TTS_ENGINE or "edge").strip().lower()
        if engine not in TTS_ENGINES:
            log(f"音声エンジン '{engine}' は使えません。edge-tts を使います (AI_GM_TTS_ENGINE は edge か irodori)")
            engine = "edge"
        self.engine = engine
        self.irodori = irodori if irodori is not None else (IrodoriClient() if engine == "irodori" else None)
        self.retry_after = retry_after
        self._clock = clock
        self._irodori_paused_until = 0.0
        # edge-tts に切り替わったことを GM の画面に知らせる (app が設定する。切り替わった最初の 1 回だけ)
        self.notify: Optional[Callable[[str], Awaitable]] = None
        self._fallback_notified = False
        if communicate_factory is None:
            import edge_tts

            patch_edge_tts_endpoint()
            communicate_factory = edge_tts.Communicate
        self._communicate_factory = communicate_factory

    async def check_irodori(self) -> Optional[str]:
        """起動時の確認。Irodori を使わないなら None、繋がれば空文字、繋がらなければ理由"""
        if self.engine != "irodori":
            return None
        try:
            await self.irodori.health()
            return ""
        except Exception as e:
            # 繋がらない・固まっているサーバーで、最初の読み上げを長く待たないようにする
            self.irodori.attempted = True
            return str(e) or e.__class__.__name__

    async def warm_up_irodori(self) -> Optional[str]:
        """
        起動時に裏で短いあいさつを 1 回作り、Irodori-TTS-Server にモデルを読み込ませる (音声は捨てる)。
        長い待ち (first_timeout) はここで使い切るので、実際の読み上げは最初から短い待ち上限になる。
        Irodori を使わない・起動時の確認で繋がらなかったなら None、できたら空文字、失敗したら理由
        """
        if self.engine != "irodori" or self.irodori.attempted:
            return None
        started = self._clock()
        try:
            await self.irodori.speech(WARM_UP_TEXT, **self._irodori_voice(None, None, None, None))
            log(f"Irodori-TTS の準備ができました (モデルの読み込み {self._clock() - started:.1f}秒)")
            return ""
        except Exception as e:
            reason = str(e) or e.__class__.__name__
            log(f"Irodori-TTS の準備に失敗しました ({reason})。読み上げのときにもう一度試します")
            return reason

    @property
    def engine_name(self) -> str:
        if self.engine == "irodori":
            return f"Irodori-TTS ({self.irodori.url}。繋がらないときは edge-tts)"
        return f"edge-tts ({self.default_voice})"

    async def synthesize(
        self,
        text: str,
        file_name: str,
        voice: Optional[str] = None,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        caption: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> bool:
        """
        声色（ボイス名・話す速さ・声の高さ）を反映して音声ファイルを生成。voice が無ければナレーターの声。
        caption は Irodori の声の説明文 (VoiceDesign)。無ければボイス名と声の高さから既定を選ぶ (edge-tts では使わない)。
        seed は Irodori の乱数の種。無ければ voice_seed で決める (同じ声の設定なら毎回同じ声)
        """
        if not text or not text.strip():
            return False
        if self.engine == "irodori" and self._clock() >= self._irodori_paused_until:
            if await self._synthesize_irodori(text.strip(), file_name, voice, rate, pitch, caption, seed):
                return True
        return await self._synthesize_edge(text, file_name, voice, rate, pitch)

    def _irodori_voice(self, voice, pitch, caption, seed) -> dict:
        """Irodori に渡す参照音声・説明文・乱数の種"""
        caption = (caption or "").strip()
        if voice is None and not caption and config.IRODORI_NARRATOR_VOICE:
            # ナレーターに参照音声があれば、その声だけで作る (説明文と食い違うと声が不安定になるため付けない)
            return {"voice": config.IRODORI_NARRATOR_VOICE, "caption": "",
                    "seed": seed if seed is not None else config.IRODORI_SEED}
        return {
            "caption": caption or default_caption(voice or self.default_voice, pitch),
            "seed": seed if seed is not None else voice_seed(voice, pitch, caption),
        }

    async def _synthesize_irodori(self, text, file_name, voice, rate, pitch, caption, seed) -> bool:
        started = self._clock()
        try:
            audio = await self.irodori.speech(
                text, speed=rate_to_speed(rate), **self._irodori_voice(voice, pitch, caption, seed),
            )
            with open(os.path.join(self.data_dir, file_name), "wb") as f:
                f.write(audio)
            if self.settle_delay:
                await asyncio.sleep(self.settle_delay)
            self._fallback_notified = False
            return True
        except Exception as e:
            self._irodori_paused_until = self._clock() + self.retry_after
            log(f"Irodori-TTS で音声を作れませんでした ({e} / {self._clock() - started:.1f}秒)。"
                f"edge-tts で作り、{int(self.retry_after)}秒は edge-tts を使います")
            await self._notify_fallback()
            return False

    async def _notify_fallback(self) -> None:
        if self._fallback_notified or self.notify is None:
            return
        self._fallback_notified = True
        try:
            await self.notify("Irodori-TTS が準備中か繋がらないため、edge-tts で読み上げています (サーバーのログに理由があります)")
        except Exception as e:
            log(f"edge-tts への切り替えを GM に知らせられませんでした ({e})")

    async def _synthesize_edge(self, text, file_name, voice, rate, pitch) -> bool:
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
