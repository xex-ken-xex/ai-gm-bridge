"""AI の接続先 (Gemini API / agy CLI / Claude Code CLI / OpenAI 互換 API)

どの接続先も complete(prompt, system, conversation, timeout) で「応答の文字列と会話 ID」を返す。
JSON の取り出しやフォールバックの順番は LLMService (gm_server/llm.py) と振り分け設定 (gm_server/ai_routing.py) が受け持つ。
- timeout: None なら接続先の既定の上限、0 なら上限なし (呼び出し側が run_with_deadline で管理する)
- 失敗したら例外を投げるか空文字を返す (LLMService が次の接続先へ回す)
"""

import asyncio
import contextlib
import json
import os
import time
import urllib.request
import uuid
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from gm_server import config, logs
from gm_server.progress import progress

Completion = Tuple[str, Optional[str]]

# 一時的な混雑・レート制限とみなすエラー文字列
_RETRYABLE_MARKERS = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded")


def is_retryable_error(err: Exception) -> bool:
    msg = str(err)
    return any(m in msg for m in _RETRYABLE_MARKERS)


def with_system(prompt: str, system: Optional[str]) -> str:
    """システム指示を別に渡せない接続先では、プロンプトの前に付ける"""
    return f"{system.strip()}\n\n{prompt}" if system else prompt


def _number(value, default: float, low: float = 0.0) -> float:
    try:
        return max(low, float(value))
    except (TypeError, ValueError):
        return default


class Provider:
    """接続先の共通部分。spec は設定ファイルの providers.<名前> の中身"""

    kind = ""
    kind_label = ""
    default_timeout = 120.0
    default_concurrency = 1
    # 会話を続けられるか (サポートGM の相談で前のやり取りを覚えているか)
    keeps_conversation = False

    def __init__(self, name: str, spec: Optional[dict] = None):
        spec = spec or {}
        self.name = name
        self.spec = spec
        self.model = str(spec.get("model") or "").strip()
        self.timeout = _number(spec.get("timeout"), self.default_timeout, 1.0)
        self.concurrency = max(1, int(_number(spec.get("concurrency"), self.default_concurrency, 1)))

    @property
    def key_status(self) -> str:
        """API キーの状態 (値は出さない): 設定済み / 未設定 / 不要"""
        return "不要"

    @property
    def available(self) -> bool:
        return True

    def describe(self) -> str:
        parts = [self.kind_label]
        if self.model:
            parts.append(f"モデル: {self.model}")
        if self.key_status != "不要":
            parts.append(f"API キー: {self.key_status}")
        parts.append(f"同時実行: {self.concurrency}")
        return f"{self.name} ({', '.join(parts)})"

    def limit_of(self, timeout: Optional[float]) -> Optional[float]:
        if timeout is None:
            return self.timeout
        return timeout or None

    async def complete(self, prompt: str, system: Optional[str] = None, conversation: Optional[str] = None,
                       timeout: Optional[float] = None, json_mode: bool = True) -> Completion:
        raise NotImplementedError


class GeminiProvider(Provider):
    kind = "gemini"
    kind_label = "Gemini API"

    def __init__(self, name: str, spec: Optional[dict] = None, client=None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        spec = dict(spec or {})
        spec.setdefault("model", config.GEMINI_MODEL)
        spec.setdefault("timeout", config.GEMINI_TIMEOUT)
        spec.setdefault("concurrency", config.LLM_CONCURRENCY)
        super().__init__(name, spec)
        self.api_key_env = str(spec.get("api_key_env") or "GEMINI_API_KEY")
        self.api_key = spec.get("api_key") or os.environ.get(self.api_key_env) or ""
        logs.register_secret(self.api_key)
        self.client = client if client is not None else self._make_client()
        self.sleep = sleep

    def _make_client(self):
        if not self.api_key:
            return None
        from google import genai

        return genai.Client(api_key=self.api_key)

    @property
    def key_status(self) -> str:
        return "設定済み" if self.client is not None else "未設定"

    @property
    def available(self) -> bool:
        return self.client is not None

    def _generate(self, contents: str, system_instruction: Optional[str], json_mode: bool):
        from google.genai import types

        cfg = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json" if json_mode else None,
        )
        return self.client.models.generate_content(model=self.model, contents=contents, config=cfg)

    async def query_gemini_with_retry(self, contents: str, system_instruction: Optional[str] = None,
                                      retries: int = 3, timeout: Optional[float] = None,
                                      json_mode: bool = True) -> str:
        """503 エラーなどの一時的な混雑に備えて指数バックオフで再試行し、応答の文字列を返す"""
        if not self.client:
            raise ValueError(f"{self.api_key_env} が環境変数に設定されていません。")
        limit = self.limit_of(timeout)
        for attempt in range(retries):
            rid = logs.ai_request(
                f"Gemini[{self.name}]", contents, model=self.model, attempt=f"{attempt + 1}/{retries}",
                system_instruction=f"{len(system_instruction)}文字" if system_instruction else None,
            )
            started = time.monotonic()
            try:
                # 同期 SDK 呼び出しでイベントループを止めないよう別スレッドで実行
                async with progress("Gemini 応答待ち", limit=limit):
                    response = await asyncio.wait_for(
                        asyncio.to_thread(self._generate, contents, system_instruction, json_mode), timeout=limit,
                    )
                logs.ai_response(rid, "Gemini", response.text, time.monotonic() - started)
                return response.text or ""
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
        return ""

    async def complete(self, prompt, system=None, conversation=None, timeout=None, json_mode=True) -> Completion:
        return await self.query_gemini_with_retry(prompt, system, timeout=timeout, json_mode=json_mode), conversation


AgyRunner = Callable[..., Awaitable[Tuple[str, str]]]


class AgyProvider(Provider):
    kind = "agy"
    kind_label = "Antigravity Agent CLI"
    keeps_conversation = True

    def __init__(self, name: str, spec: Optional[dict] = None, runner: Optional[AgyRunner] = None):
        spec = dict(spec or {})
        spec.setdefault("timeout", config.AGY_TIMEOUT)
        # agy のモデル・effort・同時実行数は環境変数 (AI_GM_AGY_MODEL など) で決まる (call_agy が全体で数える)
        spec["concurrency"] = config.AGY_CONCURRENCY
        spec.pop("model", None)
        super().__init__(name, spec)
        if runner is None:
            from gm_server.llm import call_agy

            runner = call_agy
        self.runner = runner

    def describe(self) -> str:
        model = config.AGY_MODEL or "agy の既定"
        effort = f", effort: {config.AGY_EFFORT}" if config.AGY_EFFORT else ""
        return f"{self.name} ({self.kind_label}, モデル: {model}{effort}, 同時実行: {self.concurrency})"

    async def complete(self, prompt, system=None, conversation=None, timeout=None, json_mode=True) -> Completion:
        text, conv = await self.runner(with_system(prompt, system), conversation,
                                       timeout=self.timeout if timeout is None else timeout)
        return text, conv or conversation


def build_claude_command(model: str = "", session_id: Optional[str] = None, extra_args=None) -> List[str]:
    """Claude Code CLI (`claude -p`)。プロンプトは標準入力で渡す (長いプロンプトでも引数の上限に掛からない)"""
    cmd = ["claude", "-p", "--output-format", "json"]
    if model:
        cmd.extend(["--model", model])
    if session_id:
        cmd.extend(["--resume", session_id])
    cmd.extend(str(a) for a in (extra_args or []))
    return cmd


class ClaudeCodeProvider(Provider):
    """Claude Code を `claude -p` で呼ぶ。ログインは `claude` を一度起動して済ませておく (API キーなら ANTHROPIC_API_KEY)"""

    kind = "claude_code"
    kind_label = "Claude Code (claude CLI)"
    default_timeout = 300.0
    keeps_conversation = True

    def __init__(self, name: str, spec: Optional[dict] = None):
        super().__init__(name, spec)
        self.command = str((spec or {}).get("command") or "claude")
        extra = (spec or {}).get("extra_args") or []
        self.extra_args = [str(a) for a in extra] if isinstance(extra, list) else []
        # agy と同じく、モジュールのソースを読み回らないよう中身の無い専用フォルダで動かす
        self.workdir = os.path.expanduser(str((spec or {}).get("workdir") or os.path.join(config.LOG_DIR, "claude-workspace")))
        self._slots: Optional[asyncio.Semaphore] = None

    @property
    def key_status(self) -> str:
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "設定済み (ANTHROPIC_API_KEY)"
        return "不要 (claude にログイン済みなら)"

    def describe(self) -> str:
        return f"{self.name} ({self.kind_label}, モデル: {self.model or 'claude の既定'}, 同時実行: {self.concurrency})"

    async def complete(self, prompt, system=None, conversation=None, timeout=None, json_mode=True) -> Completion:
        if self._slots is None:
            self._slots = asyncio.Semaphore(self.concurrency)
        async with self._slots:
            return await self._run(with_system(prompt, system), conversation, self.limit_of(timeout))

    async def _run(self, prompt: str, session_id: Optional[str], limit: Optional[float]) -> Completion:
        cmd = build_claude_command(self.model, session_id, self.extra_args)
        cmd[0] = self.command
        rid = logs.ai_request(f"Claude Code[{self.name}]", prompt, command=" ".join(cmd), session=session_id)
        started = time.monotonic()
        with contextlib.suppress(OSError):
            os.makedirs(self.workdir, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=self.workdir if os.path.isdir(self.workdir) else None,
            )
        except FileNotFoundError:
            print(f"Claude Code の CLI ({self.command}) が見つかりません (PATH を確認してください)")
            logs.ai_error(rid, "Claude Code", f"{self.command} が見つかりません", time.monotonic() - started)
            return "", session_id
        try:
            async with progress("Claude Code 応答待ち", limit=limit):
                out, err = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout=limit)
        except (asyncio.TimeoutError, asyncio.CancelledError) as e:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            logs.ai_error(rid, "Claude Code", "打ち切り" if isinstance(e, asyncio.CancelledError) else f"{limit:.0f} 秒以内に応答なし",
                          time.monotonic() - started)
            if isinstance(e, asyncio.CancelledError):
                raise
            return "", session_id
        text = out.decode("utf-8", errors="replace").strip()
        logs.ai_response(rid, "Claude Code", text, time.monotonic() - started, exit_code=proc.returncode)
        if err.strip():
            logs.ai_note(rid, f"claude stderr: {err.decode('utf-8', errors='replace').strip()[-2000:]}")
        try:
            res = json.loads(text)
        except ValueError:
            return text, session_id
        if not isinstance(res, dict):
            return text, session_id
        if res.get("is_error"):
            raise RuntimeError(f"Claude Code がエラーを返しました: {str(res.get('result') or res.get('subtype'))[:200]}")
        return str(res.get("result") or ""), res.get("session_id") or session_id


def http_post_json(url: str, payload: dict, headers: Dict[str, str], timeout: Optional[float]) -> dict:
    """標準ライブラリだけで JSON を POST する (別スレッドで呼ぶ)"""
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


class OpenAICompatProvider(Provider):
    """OpenAI 互換の Chat Completions API (LM Studio・Ollama・OpenAI など)。base_url と model を指定する"""

    kind = "openai"
    kind_label = "OpenAI 互換 API"
    keeps_conversation = True
    # 会話として覚えておくやり取りの上限 (古いものから捨てる)
    MAX_HISTORY = 20

    def __init__(self, name: str, spec: Optional[dict] = None, post=http_post_json):
        super().__init__(name, spec)
        spec = spec or {}
        self.base_url = str(spec.get("base_url") or "http://localhost:1234/v1").rstrip("/")
        self.api_key_env = str(spec.get("api_key_env") or "")
        self.api_key = str(spec.get("api_key") or (os.environ.get(self.api_key_env) if self.api_key_env else "") or "")
        logs.register_secret(self.api_key)
        self.temperature = spec.get("temperature")
        self.json_format = bool(spec.get("json_response_format", False))
        self.post = post
        self.histories: Dict[str, List[dict]] = {}

    @property
    def key_status(self) -> str:
        if self.api_key:
            return "設定済み"
        return f"未設定 ({self.api_key_env})" if self.api_key_env else "不要"

    def describe(self) -> str:
        key = f", API キー: {self.key_status}" if self.key_status != "不要" else ""
        return f"{self.name} ({self.kind_label} {self.base_url}, モデル: {self.model or '(未指定)'}{key}, 同時実行: {self.concurrency})"

    async def complete(self, prompt, system=None, conversation=None, timeout=None, json_mode=True) -> Completion:
        history = list(self.histories.get(conversation, [])) if conversation else []
        messages = ([{"role": "system", "content": system}] if system and not history else []) + history
        messages.append({"role": "user", "content": prompt})
        payload = {"model": self.model, "messages": messages}
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if json_mode and self.json_format:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        limit = self.limit_of(timeout)
        rid = logs.ai_request(f"OpenAI互換[{self.name}]", prompt, url=self.base_url, model=self.model)
        started = time.monotonic()
        try:
            async with progress(f"{self.name} 応答待ち", limit=limit):
                res = await asyncio.wait_for(
                    asyncio.to_thread(self.post, f"{self.base_url}/chat/completions", payload, headers, limit),
                    timeout=limit,
                )
            text = str(res["choices"][0]["message"].get("content") or "")
        except asyncio.CancelledError:
            logs.ai_error(rid, self.name, "打ち切り (キャンセル)", time.monotonic() - started)
            raise
        except Exception as e:
            logs.ai_error(rid, self.name, e, time.monotonic() - started)
            raise
        logs.ai_response(rid, self.name, text, time.monotonic() - started)
        conv = conversation or uuid.uuid4().hex
        self.histories[conv] = (messages + [{"role": "assistant", "content": text}])[-self.MAX_HISTORY:]
        return text, conv


PROVIDER_CLASSES = {
    "gemini": GeminiProvider,
    "agy": AgyProvider,
    "claude_code": ClaudeCodeProvider,
    "openai": OpenAICompatProvider,
}
