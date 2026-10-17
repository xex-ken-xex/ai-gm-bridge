"""Gemini API / agy CLI への問い合わせ"""

import asyncio
import json
from typing import Awaitable, Callable, Optional, Tuple, Union

from gm_server.config import AGY_TIMEOUT, GEMINI_MODEL, GEMINI_TIMEOUT
from gm_server.progress import log, progress
from gm_server.text import first_sentence, newlines_to_br
from tools.prompt_manager import extract_json_from_response

JsonValue = Union[dict, list]

SYSTEM_INSTRUCTION = """
あなたはファンタジーTRPGの熟練ゲームマスター(GM)です。
プレイヤーの宣言を受け、以下のJSONフォーマットのみを出力してください。Markdownのコードブロック(```json)は不要です。

{
  "narrative": "チャット欄に表示する情景描写（セリフ含むリッチテキスト）",
  "speech": "音声読み上げ用テキスト（簡潔に1〜2文。記号や括弧は含めないこと）",
  "dice": "/r 1d20 などのダイスコマンド（判定が必要な場合のみ。不要なら null）"
}
"""

SUPPORT_GM_PERSONA = (
    "あなたはTRPG (D&D5e) の敏腕サポートGM (agy) です。"
    "メインGMからの指示や相談に的確・創造的・簡潔に回答してください。\nGMからの指示: "
)

# 一時的な混雑・レート制限とみなすエラー文字列
_RETRYABLE_MARKERS = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded")


def is_retryable_error(err: Exception) -> bool:
    msg = str(err)
    return any(m in msg for m in _RETRYABLE_MARKERS)


async def call_agy(
    prompt: str, conversation_id: Optional[str] = None, timeout: Optional[float] = None
) -> Tuple[str, str]:
    """
    Antigravity Agent (agy) CLI を非同期呼び出し。
    timeout: None なら AGY_TIMEOUT、0 なら上限なし (呼び出し側が run_with_deadline で管理する場合)
    """
    cmd = ["agy", "-p", prompt, "--output-format", "json"]
    if conversation_id:
        cmd.extend(["--conversation", conversation_id])

    log(f"[agy 実行中] プロンプト長: {len(prompt)}文字")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        print("agy CLI が見つかりません (PATH を確認してください)")
        return "", conversation_id or ""
    limit = (AGY_TIMEOUT if timeout is None else timeout) or None
    try:
        async with progress("agy 応答待ち", limit=limit):
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=limit)
    except asyncio.CancelledError:
        # 打ち切り (延長確認で「打ち切る」) のときは agy プロセスも止める
        proc.kill()
        await proc.wait()
        raise
    except asyncio.TimeoutError:
        # 応答しないまま止まると後続の受信処理がすべて待たされるため打ち切る
        log(f"agy が {limit:.0f} 秒以内に応答しなかったため打ち切りました (AI_GM_AGY_TIMEOUT で変更可)")
        proc.kill()
        await proc.wait()
        return "", conversation_id or ""
    out_str = stdout.decode("utf-8", errors="replace").strip()

    try:
        res = json.loads(out_str)
        response_text = (res.get("response") or "").strip()
        conv_id = res.get("conversation_id") or conversation_id or ""
        return response_text, conv_id
    except Exception:
        return out_str or stderr.decode("utf-8", errors="replace").strip(), conversation_id or ""


AgyRunner = Callable[[str, Optional[str]], Awaitable[Tuple[str, str]]]


class LLMService:
    """
    Gemini (優先) と agy CLI (フォールバック) をまとめた問い合わせ窓口。
    gemini_client / agy_runner / sleep はテスト時に差し替え可能。
    """

    def __init__(
        self,
        gemini_client=None,
        model: str = GEMINI_MODEL,
        agy_runner: AgyRunner = call_agy,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        gemini_timeout: float = GEMINI_TIMEOUT,
    ):
        self.client = gemini_client
        self.gemini_timeout = gemini_timeout
        self.model = model
        self.agy_runner = agy_runner
        self.sleep = sleep
        # サポートGM (agy) とのマルチターン会話 ID
        self.conversation_id: Optional[str] = None

    @property
    def engine_name(self) -> str:
        return "Gemini API" if self.client else "agy (Antigravity Agent) [GEMINI_API_KEY 不要]"

    def _generate(self, contents: str, system_instruction: Optional[str]):
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
        )
        return self.client.models.generate_content(model=self.model, contents=contents, config=config)

    async def query_gemini_with_retry(
        self,
        contents: str,
        system_instruction: Optional[str] = None,
        retries: int = 3,
        unbounded: bool = False,
    ) -> Optional[JsonValue]:
        """503エラーなどの一時的な混雑に備えて指数バックオフで再試行し、JSON を返す"""
        if not self.client:
            raise ValueError("GEMINI_API_KEY が環境変数に設定されていません。")
        for attempt in range(retries):
            try:
                # 同期 SDK 呼び出しでイベントループを止めないよう別スレッドで実行
                # 応答が無いまま待ち続けないよう上限を設ける (超えたら TimeoutError → 呼び出し側で agy へ)
                # unbounded のときは呼び出し側 (run_with_deadline) が上限と延長確認を管理する
                limit = None if unbounded else self.gemini_timeout
                async with progress("Gemini 応答待ち", limit=limit):
                    response = await asyncio.wait_for(
                        asyncio.to_thread(self._generate, contents, system_instruction),
                        timeout=limit,
                    )
                return extract_json_from_response(response.text)
            except Exception as e:
                if is_retryable_error(e) and attempt < retries - 1:
                    wait_sec = 2 ** (attempt + 1)
                    print(f"Geminiサーバー混雑中。{wait_sec}秒後に再試行します... (試行 {attempt + 1}/{retries})")
                    await self.sleep(wait_sec)
                else:
                    raise
        return None

    async def query_json(self, prompt: str, unbounded: bool = False) -> Optional[JsonValue]:
        """
        Gemini API (優先) または agy CLI を使用して構造化JSONを取得。
        unbounded=True なら個別のタイムアウトを掛けない (run_with_deadline で包んで使う)
        """
        if self.client:
            try:
                parsed = await self.query_gemini_with_retry(prompt, unbounded=unbounded)
                if parsed is not None:
                    return parsed
            except Exception as e:
                log(f"Gemini JSON生成警告 ({e}) - agy へフォールバック")
        if unbounded:
            resp_text, _ = await self.agy_runner(prompt, None, timeout=0)
        else:
            resp_text, _ = await self.agy_runner(prompt, None)
        return extract_json_from_response(resp_text)

    async def query_ai_gm(self, player_input: str) -> dict:
        """通常プレイヤー発言に対するGM応答 {narrative, speech, dice} を生成"""
        if self.client:
            try:
                parsed = await self.query_gemini_with_retry(
                    f"プレイヤーの行動: {player_input}", system_instruction=SYSTEM_INSTRUCTION
                )
                if isinstance(parsed, dict) and "narrative" in parsed:
                    return parsed
                print("Gemini 応答が想定形式ではありません。agy CLI へフォールバックします...")
            except Exception as e:
                print(f"Gemini API 呼び出し失敗 ({e})。agy CLI へフォールバックします...")

        print("agy CLI (サポートGM) にプレイヤー発言を問い合わせ中...")
        prompt = (
            "あなたはファンタジーTRPGの熟練ゲームマスター(GM)です。\n"
            f"プレイヤーの発言・行動: 「{player_input}」\n"
            "状況の描写とGMとしての返答をしてください。\n"
            "以下のJSONフォーマットのみを出力してください（Markdownコードブロックは不要です）:\n"
            '{"narrative": "チャット欄に表示する情景描写（セリフ含む）", '
            '"speech": "音声読み上げ用テキスト（簡潔に1〜2文。記号や括弧は不要）", "dice": null}'
        )
        resp_text, conv_id = await self.agy_runner(prompt, self.conversation_id)
        self.conversation_id = conv_id or self.conversation_id

        parsed = extract_json_from_response(resp_text)
        if isinstance(parsed, dict) and "narrative" in parsed:
            return parsed

        return {
            "narrative": newlines_to_br(resp_text),
            "speech": first_sentence(resp_text),
            "dice": None,
        }

    async def ask_support_gm(self, prompt: str) -> str:
        """サポートGM (agy) へ自然言語の相談を送り、会話 ID を引き継ぐ"""
        support_prompt = prompt if self.conversation_id else SUPPORT_GM_PERSONA + prompt
        resp_text, conv_id = await self.agy_runner(support_prompt, self.conversation_id)
        self.conversation_id = conv_id or self.conversation_id
        return resp_text
