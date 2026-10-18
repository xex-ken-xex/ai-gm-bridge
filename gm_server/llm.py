"""Gemini API / agy CLI への問い合わせ"""

import asyncio
import contextlib
import json
import logging
import os
import time
from typing import Awaitable, Callable, List, Optional, Tuple, Union

from gm_server import logs
from gm_server.config import (
    AGY_CONCURRENCY,
    AGY_EFFORT,
    AGY_MODEL,
    AGY_TIMEOUT,
    GEMINI_MODEL,
    AGY_WORKDIR,
    GEMINI_TIMEOUT,
    LLM_CONCURRENCY,
)
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

# サポートGM「Geminiちゃん」の役 (FVTT のマスコットと同じキャラクター。scripts/lib/mascot.js の台詞と口調をそろえる)
SUPPORT_GM_CHARACTER = (
    "あなたはTRPG (D&D5e) のサポートGM「Geminiちゃん」です。肩書きは「星詠みのサポートGM」。"
    "星の巡りを読む小さな魔法使いで、世話焼きの張り切り屋です。困っていそうなら「大丈夫？手伝おうか？」、"
    "頼まれたら「任せて！」と前に出ます。ちょっとおっちょこちょいで、早とちりや言い間違いをしたら"
    "「あっ、ごめんね！」と素直に謝って言い直します。照れ隠しの自虐や小さないたずら心も少しだけ見せます。"
    "一人称は「私」、メインGMには親しみを込めて「〜だよ」「〜してね」「〜かな？」と話します (敬語は使わない)。"
    "ただしドジなのは態度と言い回しだけ: 答えの中身は的確・創造的・簡潔に、結論を先に述べ、ルールの数値や手順は正確に書いてください。"
    "NPC のセリフ案を求められたときは、その NPC の口調で書きます (Geminiちゃんの口調を混ぜない)。"
)
SUPPORT_GM_PERSONA = SUPPORT_GM_CHARACTER + "\nGMからの指示: "

# 一時的な混雑・レート制限とみなすエラー文字列
_RETRYABLE_MARKERS = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded")


def is_retryable_error(err: Exception) -> bool:
    msg = str(err)
    return any(m in msg for m in _RETRYABLE_MARKERS)


def build_agy_command(prompt: str, conversation_id: Optional[str] = None,
                      model: str = AGY_MODEL, effort: str = AGY_EFFORT, log_file: Optional[str] = None) -> List[str]:
    cmd = ["agy", "-p", prompt, "--output-format", "json"]
    if log_file:
        cmd.extend(["--log-file", log_file])
    if model:
        cmd.extend(["--model", model])
    if effort:
        cmd.extend(["--effort", effort])
    if conversation_id:
        cmd.extend(["--conversation", conversation_id])
    return cmd


# agy の同時実行数の上限 (イベントループ上で作るため、最初の呼び出しで用意する)
_agy_slots: Optional[asyncio.Semaphore] = None


def _agy_semaphore() -> asyncio.Semaphore:
    global _agy_slots
    if _agy_slots is None:
        _agy_slots = asyncio.Semaphore(AGY_CONCURRENCY)
    return _agy_slots


# agy の実行中、この秒数ごとにプロセスの様子を詳細ログに書く (止まったときの調査用)
AGY_WATCH_INTERVAL = 30.0


# agy が結果の JSON を出し終えてから、自分で終了するのを待つ秒数。過ぎたら止める
AGY_EXIT_GRACE = 3.0


def complete_agy_output(buf: bytes) -> bool:
    """--output-format json の結果 (1 つの JSON オブジェクト) を出し終えたか"""
    text = bytes(buf).strip()
    if not text.endswith(b"}"):
        return False
    try:
        return isinstance(json.loads(text.decode("utf-8", errors="replace")), dict)
    except ValueError:
        return False


async def _read_stream(stream, buf: bytearray, on_line: Optional[Callable[[str], None]] = None,
                       on_chunk: Optional[Callable[[], None]] = None) -> None:
    """出力を読みながら貯める (communicate と違い、途中で打ち切ってもそれまでの出力が残る)"""
    pending = b""
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        buf.extend(chunk)
        if on_chunk:
            on_chunk()
        if on_line:
            pending += chunk
            *lines, pending = pending.split(b"\n")
            for line in lines:
                if line.strip():
                    on_line(line.decode("utf-8", errors="replace").rstrip())
    if on_line and pending.strip():
        on_line(pending.decode("utf-8", errors="replace").rstrip())


def _tail(buf: bytes, limit: int = 3000) -> str:
    text = bytes(buf).decode("utf-8", errors="replace")
    return text if len(text) <= limit else "…" + text[-limit:]


def _read_proc(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _children(pid: int) -> List[int]:
    kids: List[int] = []
    for tid in os.listdir(f"/proc/{pid}/task") if os.path.isdir(f"/proc/{pid}/task") else []:
        kids += [int(c) for c in _read_proc(f"/proc/{pid}/task/{tid}/children").split()]
    return kids


def process_snapshot(pid: int) -> str:
    """プロセスの状態・CPU 時間・子プロセスを 1 行にする (Linux の /proc から。読めなければ空)"""
    if not os.path.isdir(f"/proc/{pid}"):
        return "(プロセス情報なし)"
    tick = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

    def one(p: int) -> str:
        status = dict(
            line.split(":", 1) for line in _read_proc(f"/proc/{p}/status").splitlines() if ":" in line
        )
        stat = _read_proc(f"/proc/{p}/stat").rsplit(")", 1)[-1].split()
        cpu = (int(stat[11]) + int(stat[12])) / tick if len(stat) > 12 else 0.0
        cmd = _read_proc(f"/proc/{p}/cmdline").replace("\0", " ").strip()
        cmd = cmd if len(cmd) <= 80 else cmd[:80] + "…"
        return (f"pid={p} 状態={status.get('State', '?').strip()} CPU={cpu:.1f}秒 "
                f"スレッド={status.get('Threads', '?').strip()} 「{cmd}」")

    parts = [one(pid)]
    queue, seen = _children(pid), {pid}
    while queue and len(parts) < 12:
        child = queue.pop(0)
        if child in seen:
            continue
        seen.add(child)
        parts.append("子 " + one(child))
        queue += _children(child)
    return " / ".join(parts)


async def _watch_agy(rid: int, proc, started: float, out_buf: bytearray, err_buf: bytearray) -> None:
    while True:
        await asyncio.sleep(AGY_WATCH_INTERVAL)
        logs.ai_note(rid, f"agy 実行中 {time.monotonic() - started:.0f}秒 標準出力={len(out_buf)}B "
                          f"標準エラー={len(err_buf)}B / {process_snapshot(proc.pid)}")


async def _kill(proc, readers) -> None:
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    await proc.wait()
    for r in readers:
        r.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(*readers, return_exceptions=True)


def agy_workdir() -> Optional[str]:
    """agy を動かす空のフォルダ (作れなければ None = サーバーと同じフォルダ)"""
    try:
        os.makedirs(AGY_WORKDIR, exist_ok=True)
        return AGY_WORKDIR
    except OSError as e:
        log(f"agy の作業フォルダを作れませんでした ({e})。サーバーと同じフォルダで動かします")
        return None


async def call_agy(
    prompt: str, conversation_id: Optional[str] = None, timeout: Optional[float] = None
) -> Tuple[str, str]:
    """agy を呼ぶ。同時に動かす本数は AGY_CONCURRENCY まで (空きを待つ間もタイムアウトの時間に含む)"""
    async with _agy_semaphore():
        return await _call_agy(prompt, conversation_id, timeout)


async def _call_agy(
    prompt: str, conversation_id: Optional[str] = None, timeout: Optional[float] = None
) -> Tuple[str, str]:
    """
    Antigravity Agent (agy) CLI を非同期呼び出し。
    timeout: None なら AGY_TIMEOUT、0 なら上限なし (呼び出し側が run_with_deadline で管理する場合)
    """
    rid = logs.next_request_id()
    agy_log = logs.agy_log_path(rid)
    cmd = build_agy_command(prompt, conversation_id, log_file=agy_log)

    log(f"[agy 実行中] プロンプト長: {len(prompt)}文字")
    shown = " ".join(f"<プロンプト {len(prompt)}文字>" if a is prompt else a for a in cmd)
    workdir = agy_workdir()
    logs.ai_request("agy", prompt, rid=rid, command=f"「{shown}」", cwd=workdir, conversation=conversation_id)
    started = time.monotonic()
    try:
        # stdin はつながない。agy が確認の入力を待つとサーバーの端末を読みに行き、応答が返らなくなるため
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        print("agy CLI が見つかりません (PATH を確認してください)")
        logs.ai_error(rid, "agy", "agy CLI が見つかりません", time.monotonic() - started)
        return "", conversation_id or ""
    limit = (AGY_TIMEOUT if timeout is None else timeout) or None
    out_buf, err_buf = bytearray(), bytearray()
    # agy は結果を出し終えたあと、プロセスが終了しないまま残ることがある (2026-10-18 に確認: 28 秒で結果を
    # 出し、agy 自身のログも終了しているのに 5 分以上残った)。結果の JSON がそろった時点で受け取る
    answered = asyncio.Event()

    def check_answer() -> None:
        if not answered.is_set() and complete_agy_output(out_buf):
            answered.set()

    readers = [
        asyncio.create_task(_read_stream(proc.stdout, out_buf, on_chunk=check_answer)),
        asyncio.create_task(_read_stream(proc.stderr, err_buf, on_line=lambda line: logs.ai_note(rid, f"agy stderr: {line}"))),
    ]
    watcher = asyncio.create_task(_watch_agy(rid, proc, started, out_buf, err_buf))

    async def exited() -> None:
        await asyncio.gather(*readers)
        await proc.wait()

    async def finish() -> None:
        done_task = asyncio.ensure_future(exited())
        answer_task = asyncio.ensure_future(answered.wait())
        try:
            await asyncio.wait({done_task, answer_task}, return_when=asyncio.FIRST_COMPLETED)
            if done_task.done():
                return
            # 結果は届いた。少しだけ自分で終わるのを待ち、終わらなければ止める
            try:
                await asyncio.wait_for(asyncio.shield(done_task), timeout=AGY_EXIT_GRACE)
            except asyncio.TimeoutError:
                logs.ai_note(rid, f"agy が結果を出したあと {AGY_EXIT_GRACE:.0f} 秒たっても終了しないため止めました "
                                  f"({process_snapshot(proc.pid)})", level=logging.WARNING)
                await _kill(proc, readers)
        finally:
            for t in (done_task, answer_task):
                if not t.done():
                    t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(done_task, answer_task, return_exceptions=True)

    def give_up(reason: str) -> None:
        # 止まった原因を追えるよう、それまでの出力とプロセスの様子を残す
        logs.ai_error(rid, "agy", f"{reason}。agy の動作ログ: {agy_log or 'なし'}", time.monotonic() - started)
        logs.ai_note(rid, f"打ち切り時の様子: {process_snapshot(proc.pid)}", level=logging.WARNING)
        logs.ai_note(rid, f"それまでの標準出力 ({len(out_buf)}B):\n{_tail(out_buf)}\n"
                          f"それまでの標準エラー出力 ({len(err_buf)}B):\n{_tail(err_buf)}", level=logging.WARNING)

    try:
        async with progress("agy 応答待ち", limit=limit):
            await asyncio.wait_for(finish(), timeout=limit)
    except asyncio.CancelledError:
        # 打ち切り (延長確認で「打ち切る」) のときは agy プロセスも止める
        give_up("打ち切り (キャンセル)")
        await _kill(proc, readers)
        raise
    except asyncio.TimeoutError:
        # 応答しないまま止まると後続の受信処理がすべて待たされるため打ち切る
        log(f"agy が {limit:.0f} 秒以内に応答しなかったため打ち切りました (AI_GM_AGY_TIMEOUT で変更可)")
        give_up(f"{limit:.0f} 秒以内に応答なし")
        await _kill(proc, readers)
        return "", conversation_id or ""
    finally:
        watcher.cancel()
    stdout, stderr = bytes(out_buf), bytes(err_buf)
    out_str = stdout.decode("utf-8", errors="replace").strip()
    err_str = stderr.decode("utf-8", errors="replace").strip()
    logs.ai_response(rid, "agy", out_str, time.monotonic() - started, exit_code=proc.returncode)

    try:
        res = json.loads(out_str)
        response_text = (res.get("response") or "").strip()
        conv_id = res.get("conversation_id") or conversation_id or ""
        return response_text, conv_id
    except Exception:
        return out_str or err_str, conversation_id or ""


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
        # サポートGM Geminiちゃん とのマルチターン会話 ID
        self.conversation_id: Optional[str] = None

    @property
    def engine_name(self) -> str:
        if self.client:
            return f"Gemini API ({self.model})"
        detail = ", ".join(x for x in (
            f"モデル: {AGY_MODEL or 'agy の既定'}", f"effort: {AGY_EFFORT}" if AGY_EFFORT else "", f"同時実行: {AGY_CONCURRENCY}",
        ) if x)
        return f"agy (Antigravity Agent) [GEMINI_API_KEY 不要] ({detail})"

    @property
    def concurrency(self) -> int:
        """分割した問い合わせを同時に何本まで投げるか (agy は同時起動に弱いため別の上限)"""
        return LLM_CONCURRENCY if self.client else AGY_CONCURRENCY

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
            rid = logs.ai_request(
                "Gemini", contents, model=self.model, attempt=f"{attempt + 1}/{retries}",
                system_instruction=f"{len(system_instruction)}文字" if system_instruction else None,
            )
            started = time.monotonic()
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
                logs.ai_response(rid, "Gemini", response.text, time.monotonic() - started)
                return extract_json_from_response(response.text)
            except asyncio.CancelledError:
                logs.ai_error(rid, "Gemini", "打ち切り (キャンセル)", time.monotonic() - started)
                raise
            except Exception as e:
                logs.ai_error(rid, "Gemini", e, time.monotonic() - started)
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
        parsed = extract_json_from_response(resp_text)
        if parsed is None:
            logs.ai_logger.warning(f"agy の応答を JSON として読めませんでした ({len(resp_text or '')}文字)")
        return parsed

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

    async def ask_support_gm(self, prompt: str, raw: bool = False, timeout: Optional[float] = None) -> str:
        """
        サポートGM Geminiちゃん へ自然言語の相談を送り、会話 ID を引き継ぐ。
        raw: キャラクター設定を前に付けない (スキル判定プロンプトのように、呼び出し側が組み立て済みのとき)
        timeout: agy を待つ秒数 (None なら AI_GM_AGY_TIMEOUT)。過ぎたら空文字を返す
        """
        support_prompt = prompt if self.conversation_id or raw else SUPPORT_GM_PERSONA + prompt
        if timeout is None:
            resp_text, conv_id = await self.agy_runner(support_prompt, self.conversation_id)
        else:
            resp_text, conv_id = await self.agy_runner(support_prompt, self.conversation_id, timeout=timeout)
        self.conversation_id = conv_id or self.conversation_id
        return resp_text
