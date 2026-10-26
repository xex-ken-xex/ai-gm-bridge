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
    assert gemini["key_status"] == "設定済み"
    # 設定ファイルが無いときは欄を空にし、今の値は defaults (画面では薄く見せる) にだけ入れる
    assert "api_key_env" not in gemini and "timeout" not in gemini
    assert gemini["defaults"]["api_key_env"] == "GEMINI_API_KEY" and gemini["defaults"]["timeout"] > 0


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


def test_screen_cannot_point_at_a_new_remote_url():
    remote = {"type": "openai", "model": "m", "base_url": "http://192.168.0.9:9999/v1", "api_key_env": "GEMINI_API_KEY"}
    with pytest.raises(ai_settings.SettingsError, match="localhost 以外"):
        ai_settings.clean_provider("x", remote)
    # キーを付けなくても、外の URL は断る (プレイヤーの発言が外へ出て、返事で NPC を操られるため)
    with pytest.raises(ai_settings.SettingsError, match="localhost 以外"):
        ai_settings.clean_provider("x", {**remote, "api_key_env": ""})
    # 127 で始まるだけのホスト名は自分の PC ではない
    for url in ("http://127.attacker.com/v1", "http://127.0.0.1.nip.io/v1", "http://localhost.attacker.com/v1"):
        with pytest.raises(ai_settings.SettingsError, match="localhost 以外"):
            ai_settings.clean_provider("x", {**remote, "base_url": url})
    # 設定ファイルに書いてあったものと同じなら保存できる (ほかの欄だけを変えるとき)
    previous = {**remote, "api_key_env": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1"}
    same = {**previous, "model": "m2"}
    assert ai_settings.clean_provider("x", same, previous)["model"] == "m2"
    with pytest.raises(ai_settings.SettingsError):
        ai_settings.clean_provider("x", {**same, "base_url": "https://evil.example/v1"}, previous)
    with pytest.raises(ai_settings.SettingsError):
        ai_settings.clean_provider("x", {**same, "api_key_env": "GEMINI_API_KEY"}, previous)
    # 自分の PC 宛てなら書ける
    for url in ("http://127.0.0.1:1234/v1", "http://localhost:1234/v1", "http://[::1]:1234/v1"):
        assert ai_settings.clean_provider("x", {**remote, "base_url": url})["base_url"] == url


def test_env_name_check_accepts_usual_names_and_rejects_key_values():
    for name in ("GEMINI_API_KEY", "LMSTUDIO_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MY_KEY2"):
        spec = ai_settings.clean_provider("x", {"type": "gemini", "api_key_env": name})
        assert spec["api_key_env"] == name
    hex64 = "d4f3a9c1" * 8
    for value in (hex64, hex64.upper(), "my_key", "AIzaSyABCDEFGHIJ0123456789", "sk-proj-abc", "ABCDEFGHIJKLMNOPQRSTUVWX",
                  "KEY_1234567890"):
        with pytest.raises(ai_settings.SettingsError) as e:
            ai_settings.clean_provider("x", {"type": "gemini", "api_key_env": value})
        assert value not in str(e.value)


def test_key_status_does_not_show_a_key_pasted_as_env_name(monkeypatch):
    from gm_server.ai_providers import OpenAICompatProvider

    hex64 = "D4F3A9C1" * 8
    monkeypatch.delenv(hex64, raising=False)
    assert OpenAICompatProvider("x", {"model": "m", "api_key_env": hex64}).key_status == "未設定"
    assert OpenAICompatProvider("x", {"model": "m", "api_key_env": "LM_KEY_NOT_SET"}).key_status == "未設定 (LM_KEY_NOT_SET)"


def test_sample_config_is_valid_and_loadable(monkeypatch):
    from gm_server.ai_routing import build_routing

    path = os.path.join(config.MODULE_DIR, "ai_providers.sample.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert any(k.startswith("_") for k in data)
    routing = build_routing(data)
    assert {"gemini", "agy", "claude", "lmstudio"} <= set(routing.providers)
    assert "api_key" not in json.dumps(data["providers"]).replace("api_key_env", "")
