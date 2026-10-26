import json
import os

import pytest

from gm_server import config
from gm_server.handlers import ai_settings
from gm_server.llm import LLMService


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "conf" / "ai_providers.json"
    monkeypatch.setattr(config, "AI_CONFIG_PATH", str(path))
    monkeypatch.setattr(config, "MODULE_DIR", str(tmp_path / "module"))
    return path


async def no_agy(prompt, conversation_id, timeout=None):
    return "", conversation_id or ""


async def test_request_sends_routing_without_key_values(make_ctx, sent, config_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyTESTVALUE0123456789abcdefghijklmno")
    llm = LLMService(gemini_client=object(), agy_runner=no_agy)
    await ai_settings.handle_request(make_ctx(llm), {})
    msg = sent[-1]
    assert msg["action"] == "ai_settings"
    assert not msg["config_exists"] and msg["config_path"] == str(config_path)
    assert {p["name"] for p in msg["providers"]} == {"gemini", "agy"}
    assert msg["tasks"]["default"] == ["gemini", "agy"] and msg["tasks"]["npc_dialogue"] == []
    assert "AIzaSyTESTVALUE" not in json.dumps(msg, ensure_ascii=False)
    gemini = next(p for p in msg["providers"] if p["name"] == "gemini")
    assert gemini["key_status"] == "設定済み" and gemini["api_key_env"] == "GEMINI_API_KEY"


async def test_save_writes_file_and_reloads_routing(make_ctx, sent, config_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    llm = LLMService(agy_runner=no_agy)
    llm.conversations["agy"] = "conv-1"
    await ai_settings.handle_save(make_ctx(llm), {
        "providers": {
            "agy": {"type": "agy", "timeout": "200", "model": "ignored"},
            "lm": {"type": "openai", "base_url": "http://localhost:1234/v1", "model": "qwen", "api_key": "sk-should-not-be-saved",
                   "concurrency": "2"},
        },
        "tasks": {"default": ["agy"], "npc_dialogue": ["lm", "agy"]},
    })
    msg = sent[-1]
    assert msg["ok"] and "保存しました" in msg["message"]
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["providers"]["agy"] == {"type": "agy", "timeout": 200.0}
    assert saved["providers"]["lm"] == {"type": "openai", "base_url": "http://localhost:1234/v1", "model": "qwen", "concurrency": 2}
    assert "sk-should-not-be-saved" not in config_path.read_text(encoding="utf-8")
    assert oct(os.stat(config_path).st_mode & 0o777) == "0o600"
    assert [p.name for p in llm.chain("npc_dialogue")] == ["lm", "agy"]
    assert llm.conversations == {}


async def test_save_keeps_notes_and_fields_the_screen_does_not_edit(make_ctx, sent, config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({
        "_手順": ["説明"],
        "providers": {"claude": {"type": "claude_code", "model": "opus", "extra_args": ["--max-turns", "1"], "api_key": "old"}},
        "tasks": {"default": "claude"},
    }), encoding="utf-8")
    llm = LLMService(agy_runner=no_agy)
    await ai_settings.handle_save(make_ctx(llm), {
        "providers": {"claude": {"type": "claude_code", "model": "sonnet"}},
        "tasks": {"default": ["claude"]},
    })
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["_手順"] == ["説明"]
    assert saved["providers"]["claude"] == {"type": "claude_code", "model": "sonnet", "extra_args": ["--max-turns", "1"]}


@pytest.mark.parametrize("data, expected", [
    ({"providers": {}, "tasks": {"default": ["a"]}}, "1 つ以上"),
    ({"providers": {"a b": {"type": "agy"}}, "tasks": {"default": ["a b"]}}, "英数字"),
    ({"providers": {"a": {"type": "nope"}}, "tasks": {"default": ["a"]}}, "使えません"),
    ({"providers": {"g": {"type": "gemini", "api_key_env": "AIzaSyABCDEFGHIJ0123456789"}}, "tasks": {"default": ["g"]}}, "環境変数の名前"),
    ({"providers": {"l": {"type": "openai", "model": "m", "base_url": "localhost:1234"}}, "tasks": {"default": ["l"]}}, "http://"),
    ({"providers": {"l": {"type": "openai"}}, "tasks": {"default": ["l"]}}, "モデル名"),
    ({"providers": {"c": {"type": "claude_code", "model": "--dangerously-skip-permissions"}}, "tasks": {"default": ["c"]}}, "モデル名"),
    ({"providers": {"a": {"type": "agy", "timeout": "abc"}}, "tasks": {"default": ["a"]}}, "数字"),
    ({"providers": {"a": {"type": "agy"}}, "tasks": {"default": ["b"]}}, "がありません"),
    ({"providers": {"a": {"type": "agy"}}, "tasks": {"npc_turn": ["a"]}}, "default"),
    ({"providers": {"a": {"type": "agy"}}, "tasks": {"default": ["a"], "unknown": ["a"]}}, "ありません"),
])
async def test_save_rejects_bad_settings(make_ctx, sent, config_path, data, expected):
    llm = LLMService(agy_runner=no_agy)
    await ai_settings.handle_save(make_ctx(llm), data)
    msg = sent[-1]
    assert msg["ok"] is False and expected in msg["message"]
    assert not config_path.exists()


def test_screen_cannot_send_a_key_to_a_new_remote_url():
    remote = {"type": "openai", "model": "m", "base_url": "http://192.168.0.9:9999/v1", "api_key_env": "GEMINI_API_KEY"}
    with pytest.raises(ai_settings.SettingsError, match="localhost 以外"):
        ai_settings.clean_provider("x", remote)
    # 設定ファイルに書いてあったものと同じなら保存できる (ほかの欄だけを変えるとき)
    previous = {**remote, "api_key_env": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1"}
    same = {**previous, "model": "m2"}
    assert ai_settings.clean_provider("x", same, previous)["model"] == "m2"
    with pytest.raises(ai_settings.SettingsError):
        ai_settings.clean_provider("x", {**same, "base_url": "https://evil.example/v1"}, previous)
    # キーを使わないか、localhost 宛てなら書ける
    assert ai_settings.clean_provider("x", {**remote, "api_key_env": ""})["base_url"].startswith("http://192")
    assert ai_settings.clean_provider("x", {**remote, "base_url": "http://127.0.0.1:1234/v1"})


def test_env_name_check_accepts_usual_names():
    for name in ("GEMINI_API_KEY", "LMSTUDIO_API_KEY", "OPENAI_API_KEY", "my_key"):
        spec = ai_settings.clean_provider("x", {"type": "openai", "model": "m", "api_key_env": name})
        assert spec["api_key_env"] == name


def test_sample_config_is_valid_and_loadable(monkeypatch):
    from gm_server.ai_routing import build_routing

    path = os.path.join(config.MODULE_DIR, "ai_providers.sample.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert any(k.startswith("_") for k in data)
    routing = build_routing(data)
    assert {"gemini", "agy", "claude", "lmstudio"} <= set(routing.providers)
    assert "api_key" not in json.dumps(data["providers"]).replace("api_key_env", "")
