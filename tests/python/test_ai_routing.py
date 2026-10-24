import asyncio
import json
import sys

import pytest

from gm_server import logs
from gm_server.ai_providers import ClaudeCodeProvider, OpenAICompatProvider, Provider, build_claude_command
from gm_server.ai_routing import DEFAULT_TASK, TASKS, build_routing, default_routing, load_routing
from gm_server.llm import LLMService


class FakeProvider(Provider):
    kind = "fake"
    kind_label = "テスト用"
    keeps_conversation = True

    def __init__(self, name, answers=None, available=True, error=None):
        super().__init__(name, {})
        self.answers = list(answers or [])
        self._available = available
        self.error = error
        self.calls = []

    @property
    def available(self):
        return self._available

    async def complete(self, prompt, system=None, conversation=None, timeout=None, json_mode=True):
        self.calls.append({"prompt": prompt, "system": system, "conversation": conversation, "timeout": timeout})
        if self.error:
            raise self.error
        text = self.answers.pop(0) if self.answers else ""
        return text, conversation or f"{self.name}-conv"


def routing_of(providers, tasks):
    from gm_server.ai_routing import Routing

    return Routing({p.name: p for p in providers}, tasks)


def test_default_routing_keeps_previous_behavior():
    with_key = default_routing(gemini_client=object())
    assert [p.name for p in with_key.chain("dungeon")] == ["gemini", "agy"]
    assert [p.name for p in with_key.chain("support_gm")] == ["agy"]
    assert [p.name for p in with_key.chain("skill_router")] == ["agy"]
    assert [p.name for p in default_routing().chain("ai_gm")] == ["agy"]


def test_build_routing_reads_tasks_and_skips_unknown(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    routing = build_routing({
        "providers": {
            "gemini": {"type": "gemini"},
            "agy": {"type": "agy"},
            "claude": {"type": "claude_code", "model": "sonnet"},
            "lm": {"type": "openai", "base_url": "http://localhost:1234/v1/", "model": "qwen"},
            "bad": {"type": "nope"},
        },
        "tasks": {
            "default": ["gemini", "agy"],
            "npc_dialogue": ["lm", "missing", "gemini"],
            "npc_turn": "claude",
            "unknown_task": ["agy"],
        },
    })
    assert set(routing.providers) == {"gemini", "agy", "claude", "lm"}
    # キーの無い Gemini は飛ばす
    assert [p.name for p in routing.chain("dungeon")] == ["agy"]
    assert [p.name for p in routing.chain("npc_dialogue")] == ["lm"]
    assert [p.name for p in routing.chain("npc_turn")] == ["claude"]
    assert "unknown_task" not in routing.tasks
    assert routing.providers["lm"].base_url == "http://localhost:1234/v1"


def test_build_routing_without_default_uses_all_providers():
    routing = build_routing({"providers": {"agy": {"type": "agy"}}, "tasks": {}})
    assert routing.tasks[DEFAULT_TASK] == ["agy"]
    with pytest.raises(ValueError):
        build_routing({"providers": {"x": {"type": "nope"}}})


def test_describe_shows_only_whether_keys_are_set(monkeypatch):
    monkeypatch.setenv("LM_KEY", "local-secret-value-123")
    routing = build_routing({
        "providers": {"lm": {"type": "openai", "model": "qwen", "api_key_env": "LM_KEY"},
                      "other": {"type": "openai", "api_key_env": "NOT_SET_KEY"}},
        "tasks": {"default": "lm"},
    })
    text = "\n".join(routing.describe())
    assert "local-secret-value-123" not in text
    assert "API キー: 設定済み" in text and "未設定 (NOT_SET_KEY)" in text
    for task in TASKS:
        assert task in text


def test_load_routing_missing_or_broken_file(tmp_path):
    assert load_routing(str(tmp_path / "none.json")) is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_routing(str(broken)) is None


def test_load_routing_registers_literal_api_keys(tmp_path):
    path = tmp_path / "ai.json"
    path.write_text(json.dumps({
        "providers": {"lm": {"type": "openai", "model": "m", "api_key": "literal-key-abcdef"}},
        "tasks": {"default": "lm"},
    }), encoding="utf-8")
    routing = load_routing(str(path))
    assert routing.source == str(path)
    assert "literal-key-abcdef" not in logs.mask_secrets("key is literal-key-abcdef")


async def test_query_json_routes_task_and_falls_back():
    broken = FakeProvider("lm", error=RuntimeError("connection refused"))
    gemini = FakeProvider("gemini", answers=['{"line": "やあ"}'])
    dungeon = FakeProvider("dungeon-ai", answers=['[{"id": 0}]'])
    llm = LLMService(routing=routing_of([broken, gemini, dungeon], {
        "default": ["dungeon-ai"], "npc_dialogue": ["lm", "gemini"],
    }))
    assert await llm.query_json("p", task="npc_dialogue") == {"line": "やあ"}
    assert len(broken.calls) == 1 and len(gemini.calls) == 1 and dungeon.calls == []
    assert await llm.query_json("p", unbounded=True) == [{"id": 0}]
    assert dungeon.calls[0]["timeout"] == 0


async def test_query_json_unreadable_answer_tries_next():
    first = FakeProvider("a", answers=["JSON ではない"])
    second = FakeProvider("b", answers=['{"ok": 1}'])
    llm = LLMService(routing=routing_of([first, second], {"default": ["a", "b"]}))
    assert await llm.query_json("p", task="adventure_log") == {"ok": 1}


async def test_support_gm_keeps_conversation_per_provider():
    claude = FakeProvider("claude", answers=["回答1", "回答2"])
    llm = LLMService(routing=routing_of([claude], {"default": ["claude"]}))
    assert llm.conversation_id is None
    assert await llm.ask_support_gm("相談1") == "回答1"
    assert llm.conversation_id == "claude-conv" and llm.has_conversation("skill_router")
    await llm.ask_support_gm("相談2")
    assert "星詠みのサポートGM" in claude.calls[0]["prompt"]
    assert claude.calls[1]["prompt"] == "相談2" and claude.calls[1]["conversation"] == "claude-conv"


async def test_ai_gm_on_a_non_gemini_provider_uses_json_prompt():
    lm = FakeProvider("lm", answers=['{"narrative": "扉が開く", "speech": "開いた", "dice": null}'])
    llm = LLMService(routing=routing_of([lm], {"default": ["lm"]}))
    assert (await llm.query_ai_gm("扉を開ける"))["narrative"] == "扉が開く"
    assert "扉を開ける" in lm.calls[0]["prompt"] and '"narrative"' in lm.calls[0]["prompt"]


def test_concurrency_and_step_limit_follow_the_first_provider():
    fast = FakeProvider("fast")
    fast.concurrency, fast.timeout = 4, 30.0
    slow = FakeProvider("slow")
    llm = LLMService(routing=routing_of([fast, slow], {"default": ["slow"], "builder": ["fast", "slow"]}))
    assert llm.concurrency_for("builder") == 4 and llm.step_limit("builder") == 30.0
    assert llm.concurrency == 1


async def test_openai_compatible_provider_sends_chat_and_keeps_history(monkeypatch):
    monkeypatch.setenv("LMSTUDIO_TEST_KEY", "lm-test-key-9876")
    posts = []

    def fake_post(url, payload, headers, timeout):
        posts.append((url, json.loads(json.dumps(payload)), headers, timeout))
        return {"choices": [{"message": {"content": f"答え{len(posts)}"}}]}

    provider = OpenAICompatProvider("lm", {"base_url": "http://host:1234/v1", "model": "qwen",
                                           "api_key_env": "LMSTUDIO_TEST_KEY", "json_response_format": True},
                                    post=fake_post)
    text, conv = await provider.complete("質問1", system="あなたはGM", json_mode=True)
    assert text == "答え1" and conv
    text, _ = await provider.complete("質問2", conversation=conv, json_mode=False)
    url, payload, headers, _ = posts[1]
    assert url == "http://host:1234/v1/chat/completions" and payload["model"] == "qwen"
    assert [m["role"] for m in payload["messages"]] == ["system", "user", "assistant", "user"]
    assert "response_format" in posts[0][1] and "response_format" not in payload
    assert headers == {"Authorization": "Bearer lm-test-key-9876"}
    assert "lm-test-key-9876" not in logs.mask_secrets("キーは lm-test-key-9876")
    assert provider.key_status == "設定済み"


def test_build_claude_command():
    assert build_claude_command() == ["claude", "-p", "--output-format", "json"]
    assert build_claude_command("sonnet", "s-1", ["--max-turns", "1"]) == [
        "claude", "-p", "--output-format", "json", "--model", "sonnet", "--resume", "s-1", "--max-turns", "1",
    ]


async def test_claude_code_provider_reads_result_and_session(monkeypatch, tmp_path):
    real_exec = asyncio.create_subprocess_exec
    commands = []
    script = (
        "import json, sys; prompt = sys.stdin.read(); "
        "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'ok:' + prompt, 'session_id': 'sess-1'}))"
    )

    async def fake_exec(*cmd, **kwargs):
        commands.append(cmd)
        return await real_exec(sys.executable, "-c", script, **kwargs)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    provider = ClaudeCodeProvider("claude", {"model": "sonnet", "workdir": str(tmp_path / "ws")})
    text, session = await provider.complete("こんにちは", system="システム")
    assert text == "ok:システム\n\nこんにちは"
    assert session == "sess-1"
    await provider.complete("続き", conversation=session)
    assert "--resume" in commands[1] and "sess-1" in commands[1]
    assert "--model" in commands[0] and "sonnet" in commands[0]


async def test_claude_code_provider_error_and_missing_binary(monkeypatch, tmp_path):
    real_exec = asyncio.create_subprocess_exec
    script = "import json; print(json.dumps({'is_error': True, 'result': 'Not logged in'}))"

    async def fake_exec(*cmd, **kwargs):
        return await real_exec(sys.executable, "-c", script, **kwargs)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    provider = ClaudeCodeProvider("claude", {"workdir": str(tmp_path)})
    with pytest.raises(RuntimeError):
        await provider.complete("x")

    async def missing(*cmd, **kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr("asyncio.create_subprocess_exec", missing)
    assert await provider.complete("x", conversation="c") == ("", "c")


async def test_ai_command_router_uses_skill_router_task(make_ctx):
    from conftest import FakeLLM
    from gm_server.handlers import ai_command

    llm = FakeLLM(support_responses=['{"skill": "none"}'])
    ctx = make_ctx(llm)
    await ai_command.handle_command(ctx, {"prompt": "今日の天気は？"})
    assert "skill_router" in llm.tasks
