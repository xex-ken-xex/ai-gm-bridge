"""AI の接続先の振り分け (作業ごとに、どの接続先を使うか)

設定ファイル (JSON。既定は ~/.config/ai-gm-bridge/ai_providers.json、AI_GM_AI_CONFIG で変更。
モジュールのフォルダは Foundry がブラウザに配信するので置かない) の形:

    {
      "providers": {
        "gemini":   {"type": "gemini", "model": "gemini-3.8-flash", "api_key_env": "GEMINI_API_KEY"},
        "agy":      {"type": "agy"},
        "claude":   {"type": "claude_code", "model": "sonnet"},
        "lmstudio": {"type": "openai", "base_url": "http://localhost:1234/v1", "model": "qwen3-8b"}
      },
      "tasks": {
        "default": ["gemini", "agy"],
        "npc_dialogue": ["lmstudio", "gemini"]
      }
    }

tasks の値は接続先の名前 (1 つなら文字列でもよい)。左から順に試し、失敗したら次へ回す。
書いていない作業は default を使う。設定ファイルが無ければ今までどおり (Gemini のキーがあれば Gemini → agy、
サポートGM とスキル判定は agy)。API キーは環境変数の名前 (api_key_env) で指定し、値は表示しない (設定済みかどうかだけ)。
`python3 -m gm_server.ai_routing` で、いまの振り分けを確かめられる。
"""

import json
import os
import sys
from typing import Dict, List, Optional

from gm_server import config, logs
from gm_server.ai_providers import PROVIDER_CLASSES, AgyProvider, GeminiProvider, Provider

DEFAULT_TASK = "default"

# 振り分けの単位 (作業の名前と、表示に使う説明)
TASKS: Dict[str, str] = {
    "ai_gm": "AI GM の語り (プレイヤーの発言への応答)",
    "support_gm": "サポートGM Geminiちゃん への相談",
    "skill_router": "/ai のスキル判定",
    "builder": "シナリオビルダー",
    "dungeon": "ダンジョン生成 (伝承・部屋の情景)",
    "npc_dialogue": "NPC の会話",
    "npc_turn": "NPC の戦闘の手番",
    "adventure_log": "冒険記録",
}


class Routing:
    """接続先の一覧と、作業ごとの接続先の並び"""

    def __init__(self, providers: Dict[str, Provider], tasks: Dict[str, List[str]], source: str = ""):
        self.providers = providers
        self.tasks = tasks
        self.source = source

    def names_for(self, task: str) -> List[str]:
        names = self.tasks.get(task) or self.tasks.get(DEFAULT_TASK) or list(self.providers)
        return [n for n in names if n in self.providers]

    def chain(self, task: str) -> List[Provider]:
        """作業に使う接続先 (使えないもの = Gemini のキーが無いなど、は飛ばす)"""
        usable = [self.providers[n] for n in self.names_for(task) if self.providers[n].available]
        if usable:
            return usable
        # 指定した接続先がどれも使えなければ、使えるものを順に試す
        return [p for p in self.providers.values() if p.available]

    def describe(self) -> List[str]:
        """起動時の表示。API キーは設定済みかどうかだけ"""
        lines = [f"設定: {self.source or '設定ファイルなし (既定の振り分け)'}", "接続先:"]
        lines += [f"  - {p.describe()}" + ("" if p.available else " ※使えません") for p in self.providers.values()]
        lines.append("作業ごとの接続先:")
        for task, label in TASKS.items():
            names = [p.name for p in self.chain(task)]
            mark = "" if task in self.tasks else " (default)"
            lines.append(f"  - {task} {label}: {' → '.join(names) or '(なし)'}{mark}")
        return lines


def _names(value) -> List[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def default_routing(gemini_client=None, agy_runner=None, sleep=None, gemini_timeout: Optional[float] = None,
                    model: Optional[str] = None) -> Routing:
    """設定ファイルが無いとき: 今までと同じ (Gemini のキーがあれば Gemini → agy。相談とスキル判定は agy)"""
    providers: Dict[str, Provider] = {}
    if gemini_client is not None:
        spec = {"model": model or config.GEMINI_MODEL}
        if gemini_timeout is not None:
            spec["timeout"] = gemini_timeout
        kwargs = {"sleep": sleep} if sleep else {}
        providers["gemini"] = GeminiProvider("gemini", spec, client=gemini_client, **kwargs)
    providers["agy"] = AgyProvider("agy", runner=agy_runner)
    chain = ["gemini", "agy"] if gemini_client is not None else ["agy"]
    return Routing(providers, {DEFAULT_TASK: chain, "support_gm": ["agy"], "skill_router": ["agy"]})


def build_routing(data: dict, gemini_client=None, agy_runner=None, source: str = "") -> Routing:
    """設定ファイルの中身から作る。おかしなところは警告を出して飛ばす"""
    providers: Dict[str, Provider] = {}
    raw_providers = data.get("providers") if isinstance(data, dict) else None
    for name, spec in (raw_providers or {}).items() if isinstance(raw_providers, dict) else []:
        if str(name).startswith("_"):
            continue  # 「_」で始まる項目は説明書き (ai_providers.sample.json の _説明 など)
        if not isinstance(spec, dict):
            logs.ai_logger.warning(f"AI の接続先 {name}: 設定が読めないため飛ばします")
            continue
        kind = str(spec.get("type") or "").strip()
        cls = PROVIDER_CLASSES.get(kind)
        if cls is None:
            logs.ai_logger.warning(f"AI の接続先 {name}: type「{kind}」は使えません ({' / '.join(PROVIDER_CLASSES)})")
            continue
        if cls is GeminiProvider:
            # 環境変数 GEMINI_API_KEY で作った接続をそのまま使う (同じキーなら作り直さない)
            same_key = str(spec.get("api_key_env") or "GEMINI_API_KEY") == "GEMINI_API_KEY"
            providers[name] = cls(name, spec, client=gemini_client if same_key else None)
        elif cls is AgyProvider:
            providers[name] = cls(name, spec, runner=agy_runner)
        else:
            providers[name] = cls(name, spec)
    if not providers:
        raise ValueError("providers に使える接続先がありません")

    tasks: Dict[str, List[str]] = {}
    raw_tasks = data.get("tasks") if isinstance(data.get("tasks"), dict) else {}
    for task, value in raw_tasks.items():
        if str(task).startswith("_"):
            continue
        if task != DEFAULT_TASK and task not in TASKS:
            logs.ai_logger.warning(f"AI の振り分け: 作業「{task}」はありません ({' / '.join(TASKS)})")
            continue
        names = _names(value)
        unknown = [n for n in names if n not in providers]
        if unknown:
            logs.ai_logger.warning(f"AI の振り分け: {task} の接続先 {'・'.join(unknown)} は providers にありません")
        tasks[task] = [n for n in names if n in providers]
    if not tasks.get(DEFAULT_TASK):
        tasks[DEFAULT_TASK] = list(providers)
    return Routing(providers, tasks, source=source)


def warn_public_config() -> None:
    """モジュールのフォルダ (Foundry がブラウザに配信する) に設定ファイルがあれば、読まずに警告する"""
    public = os.path.join(config.MODULE_DIR, "ai_providers.json")
    if os.path.isfile(public) and os.path.abspath(public) != os.path.abspath(config.AI_CONFIG_PATH):
        print(f"⚠️ {public} は Foundry からブラウザで読めてしまう場所なので読みません。"
              f"{config.AI_CONFIG_PATH} に移してください")


def load_routing(path: Optional[str] = None, gemini_client=None, agy_runner=None) -> Optional[Routing]:
    """設定ファイルを読む。無ければ None (既定の振り分けを使う)。読めなければ警告して None"""
    path = path or config.AI_CONFIG_PATH
    warn_public_config()
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 設定ファイルに直接書いたキーは使わない (環境変数の名前 api_key_env だけ)。警告し、ログでは伏せる
        raw = data.get("providers") if isinstance(data, dict) else None
        for name, spec in raw.items() if isinstance(raw, dict) else []:
            if isinstance(spec, dict) and spec.get("api_key"):
                logs.register_secret(str(spec.get("api_key")))
                print(f"⚠️ AI の接続先 {name}: 設定ファイルの api_key は使いません。キーは環境変数に入れ、"
                      f"その名前を api_key_env に書いてください")
                logs.ai_logger.warning(f"AI の接続先 {name}: 設定ファイルの api_key は使いません (api_key_env を使ってください)")
        return build_routing(data, gemini_client=gemini_client, agy_runner=agy_runner, source=path)
    except (OSError, ValueError, AttributeError) as e:
        print(f"⚠️ AI の振り分け設定 ({path}) を読めませんでした ({e})。既定の振り分けを使います")
        logs.ai_logger.warning(f"AI の振り分け設定を読めませんでした: {path} ({e})")
        return None


def main() -> int:
    """いまの振り分けを表示する (API キーは設定済みかどうかだけ)"""
    gemini_client = None
    if config.API_KEY:
        try:
            from google import genai

            gemini_client = genai.Client(api_key=config.API_KEY)
        except Exception as e:
            print(f"Gemini の接続を作れませんでした ({e})")
    routing = load_routing(gemini_client=gemini_client) or default_routing(gemini_client=gemini_client)
    print("\n".join(routing.describe()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
