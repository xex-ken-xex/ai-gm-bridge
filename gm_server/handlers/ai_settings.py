"""G. AI の設定画面 (Geminiちゃんのメニューの「AI の設定」)

FVTT (GM) が ai_settings_request を送ると、サーバーは今の振り分け (接続先・作業ごとの接続先) を ai_settings で返す。
ai_settings_save を受けたら、中身を検めて振り分け設定ファイル (config.AI_CONFIG_PATH) に書き、読み直して ai_settings を返す。
API キーの値は扱わない: 画面に出すのは「設定済み / 未設定」だけで、保存できるのはキーを入れた環境変数の名前 (api_key_env) だけ。
"""

import json
import os
import re
from typing import Dict, List, Optional, Tuple

from gm_server import config, logs, protocol
from gm_server.ai_providers import PROVIDER_CLASSES
from gm_server.ai_routing import DEFAULT_TASK, TASKS
from gm_server.progress import log_step

# 画面で書き換えられる項目 (接続先の種類ごと)。ほかの項目 (claude の extra_args など) はファイルの値を残す
EDITABLE_FIELDS: Dict[str, Tuple[str, ...]] = {
    "gemini": ("model", "api_key_env", "timeout", "concurrency"),
    "agy": ("timeout",),
    "claude_code": ("model", "timeout", "concurrency"),
    "openai": ("base_url", "model", "api_key_env", "timeout", "concurrency", "json_response_format"),
}
TYPE_LABELS = {
    "gemini": "Gemini API",
    "agy": "agy",
    "claude_code": "Claude Code",
    "openai": "OpenAI 互換 (LM Studio など)",
}
_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


class SettingsError(ValueError):
    pass


def is_loopback_url(url: str) -> bool:
    """自分の PC 宛ての URL か (localhost か、ループバックの IP アドレスだけ。127.example.com などは違う)"""
    import ipaddress
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def looks_like_env_name(value: str) -> bool:
    """
    環境変数の名前らしいか (大文字・数字・_ だけ)。キーの値を貼ってしまったもの (16 進の長い文字列など) は断る:
    数字が 4 文字より多いもの、_ を含まずに 20 文字以上のもの、40 文字より長いもの
    """
    if not _ENV_NAME.match(value) or len(value) > 40:
        return False
    if sum(c.isdigit() for c in value) > 4:
        return False
    return "_" in value or len(value) < 20


def read_config_file(path: Optional[str] = None) -> dict:
    path = path or config.AI_CONFIG_PATH
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def provider_view(provider, file_spec: Optional[dict] = None) -> Dict:
    """
    画面に出す接続先の中身 (キーの値は含めない)。
    欄に入れるのは設定ファイルに書いてある値だけ。今の実際の値 (環境変数の既定など) は defaults に入れ、画面では薄く見せる
    (そのまま保存しても既定値がファイルに書き込まれず、あとで環境変数を変えれば効くように)
    """
    file_spec = file_spec if isinstance(file_spec, dict) and file_spec.get("type") == provider.kind else {}
    view = {
        "name": provider.name,
        "type": provider.kind,
        "type_label": TYPE_LABELS.get(provider.kind, provider.kind),
        "key_status": provider.key_status,
        "available": provider.available,
        "defaults": {},
    }
    for field in EDITABLE_FIELDS.get(provider.kind, ()):
        if field == "json_response_format":
            view[field] = bool(file_spec.get(field))
            continue
        if file_spec.get(field) not in (None, ""):
            view[field] = file_spec[field]
        effective = provider.concurrency if field == "concurrency" else getattr(provider, field, None)
        if effective not in (None, ""):
            view["defaults"][field] = effective
    return view


def settings_payload(llm, message: str = "", ok: bool = True) -> Dict:
    routing = llm.routing
    path = config.AI_CONFIG_PATH
    raw = read_config_file(path).get("providers")
    file_providers = raw if isinstance(raw, dict) else {}
    return {
        "config_path": path,
        "config_exists": os.path.isfile(path),
        "providers": [provider_view(p, file_providers.get(p.name)) for p in routing.providers.values()],
        "tasks": {task: list(routing.tasks.get(task, [])) for task in [DEFAULT_TASK, *TASKS]},
        "task_labels": {DEFAULT_TASK: "書いていない作業 (default)", **TASKS},
        "type_labels": TYPE_LABELS,
        "editable_fields": {k: list(v) for k, v in EDITABLE_FIELDS.items()},
        "message": message,
        "ok": ok,
    }


def _number(value, field: str, name: str, integer: bool = False):
    if value in (None, ""):
        return None
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{name} の {field} は数字で書いてください")
    if number <= 0:
        raise SettingsError(f"{name} の {field} は 0 より大きい数にしてください")
    return number


def clean_provider(name: str, raw: dict, previous: Optional[dict] = None) -> dict:
    """画面から届いた 1 つの接続先を検める。ファイルにあった項目 (画面で触れないもの) は残す"""
    if not _NAME.match(name):
        raise SettingsError(f"接続先の名前「{name}」は英数字と _ - の 32 文字までにしてください")
    kind = str(raw.get("type") or "").strip()
    if kind not in PROVIDER_CLASSES:
        raise SettingsError(f"{name} の種類「{kind}」は使えません")
    spec = {k: v for k, v in (previous or {}).items() if k != "api_key"} if (previous or {}).get("type") == kind else {}
    spec["type"] = kind
    for field in EDITABLE_FIELDS[kind]:
        value = raw.get(field)
        if field == "json_response_format":
            spec.pop(field, None)
            if value:
                spec[field] = True
            continue
        if field in ("timeout", "concurrency"):
            value = _number(value, field, name, integer=field == "concurrency")
        elif value is not None:
            value = str(value).strip()
        if value in (None, ""):
            spec.pop(field, None)
            continue
        if field == "api_key_env":
            if not looks_like_env_name(value):
                # 断った値はキーかもしれないので、メッセージにもログにも出さない
                raise SettingsError(f"{name} の API キーの欄には、キーの値ではなく環境変数の名前 "
                                    f"(大文字・数字・_ だけ。例: GEMINI_API_KEY) を書いてください")
        if field == "model" and not re.match(r"^[A-Za-z0-9][A-Za-z0-9._:/@\-]{0,99}$", value):
            # claude -p --model <値> のようにコマンドの引数になるので、記号や「-」始まりは断る
            raise SettingsError(f"{name} のモデル名「{value}」は英数字と . _ : / @ - だけで書いてください")
        if field == "base_url" and not re.match(r"^https?://", value):
            raise SettingsError(f"{name} の URL は http:// か https:// で始めてください")
        spec[field] = value
    if kind == "openai" and not spec.get("model"):
        raise SettingsError(f"{name} (OpenAI 互換) にはモデル名を書いてください")
    # サーバーは LAN からも繋げるため、画面から「外の URL へ送る」設定は作らせない
    # (キーの持ち出しや、プレイヤーの発言を外へ送って返事で NPC を操ることを防ぐ)。
    # 自分の PC (localhost) 宛てか、設定ファイルに書いてあったものと同じ URL・環境変数のままなら保存できる
    if kind == "openai" and not is_loopback_url(spec.get("base_url") or "http://localhost:1234/v1"):
        before = previous or {}
        unchanged = (before.get("type") == kind and before.get("base_url") == spec.get("base_url")
                     and before.get("api_key_env") == spec.get("api_key_env"))
        if not unchanged:
            raise SettingsError(f"{name}: localhost 以外の URL は、画面からは設定できません。"
                                f"設定ファイル ({config.AI_CONFIG_PATH}) に直接書いてください")
    return spec


def clean_settings(data: dict, current: dict) -> dict:
    """画面から届いた設定を、設定ファイルに書く形にする。おかしければ SettingsError"""
    raw_providers = data.get("providers")
    if not isinstance(raw_providers, dict) or not raw_providers:
        raise SettingsError("接続先を 1 つ以上書いてください")
    previous = current.get("providers") if isinstance(current.get("providers"), dict) else {}
    providers = {}
    for name, raw in raw_providers.items():
        name = str(name).strip()
        if not isinstance(raw, dict):
            raise SettingsError(f"{name} の設定が読めません")
        providers[name] = clean_provider(name, raw, previous.get(name))

    tasks: Dict[str, List[str]] = {}
    raw_tasks = data.get("tasks") if isinstance(data.get("tasks"), dict) else {}
    for task, names in raw_tasks.items():
        if task != DEFAULT_TASK and task not in TASKS:
            raise SettingsError(f"作業「{task}」はありません")
        chain = [str(n).strip() for n in (names if isinstance(names, list) else [names]) if str(n).strip()]
        unknown = [n for n in chain if n not in providers]
        if unknown:
            raise SettingsError(f"{TASKS.get(task, task)} の接続先 {'・'.join(unknown)} がありません")
        if chain:
            tasks[task] = chain
    if not tasks.get(DEFAULT_TASK):
        raise SettingsError("「書いていない作業 (default)」の接続先を 1 つ以上書いてください")

    # 「_」で始まる説明書き (sample の _手順 など) は残す
    notes = {k: v for k, v in current.items() if str(k).startswith("_")}
    return {**notes, "providers": providers, "tasks": tasks}


def write_config_file(content: dict, path: Optional[str] = None) -> str:
    """設定ファイルを書く (本人だけが読める 600。途中で落ちても壊れないよう一時ファイルから置き換える)"""
    path = path or config.AI_CONFIG_PATH
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, mode=0o700, exist_ok=True)
    tmp = f"{path}.saving"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return path


async def handle_request(ctx, data: dict) -> None:
    log_step("[AI の設定] 今の振り分けを FVTT に送ります")
    await ctx.send(protocol.ai_settings(settings_payload(ctx.llm)))


async def handle_save(ctx, data: dict) -> None:
    current = read_config_file()
    try:
        content = clean_settings(data, current)
        path = write_config_file(content)
    except SettingsError as e:
        await ctx.send(protocol.ai_settings(settings_payload(ctx.llm, f"保存できませんでした: {e}", ok=False)))
        return
    except OSError as e:
        logs.ai_logger.warning(f"AI の振り分け設定を書けませんでした ({e})")
        await ctx.send(protocol.ai_settings(settings_payload(ctx.llm, f"設定ファイルを書けませんでした ({e})", ok=False)))
        return
    ctx.llm.reload_config()
    log_step(f"[AI の設定] {path} に保存し、振り分けを読み直しました")
    print("\n".join(f"  {line}" for line in ctx.llm.routing.describe()))
    await ctx.send(protocol.ai_settings(settings_payload(ctx.llm, f"保存しました ({path})。次の問い合わせから使います。")))
