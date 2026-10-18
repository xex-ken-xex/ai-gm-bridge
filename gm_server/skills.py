"""/ai のスキル一覧 (agy ルーターの判定プロンプトと /ai help の一覧の元)

GM が `/ai` に自由に書いた内容を agy が読み、ここに載っているスキルに当てはまればそのスキルを実行する。
プロンプトに埋め込む一覧はこの表から作るので、スキルを増やすときはここに足し、
実行内容を gm_server/handlers/ai_command.py の SKILL_RUNNERS に足す。
詳しい設計: /mnt/project-files/design/ai_skill_router.md (プロジェクトのファイル)
"""

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from tools.prompt_manager import extract_json_from_response, load_cached_prompt


@dataclass(frozen=True)
class Skill:
    name: str
    label: str  # GM に見せる名前 (「〇〇スキル」)
    description: str
    params: str = "なし"
    ready: bool = True  # False なら「準備中」


SKILLS: Tuple[Skill, ...] = (
    Skill("generate_dungeon", "ダンジョン生成スキル", "すぐ遊べるダンジョンマップ（シーン・壁・部屋メモ）を作る",
          "request（要望）、scene_mode（ruins=地上の廃墟 / underground=地下迷宮 / wilderness=野外、任意）"),
    Skill("build_region", "地域作成スキル", "国・地方・地域の設定を作る", "request（要望）"),
    Skill("build_town", "町生成スキル", "町・村・都市などの集落を作る", "request（要望）"),
    Skill("build_journey", "旅路作成スキル", "街道・荒野など、集落と目的地をつなぐ旅路を作る", "request（要望）"),
    Skill("build_scenario", "シナリオ作成スキル", "遺跡・迷宮・塔などの冒険の舞台を、伝承や目標から作り込む", "request（要望）"),
    Skill("create_npc", "NPC作成スキル", "NPC を作る（宿屋の女将、ギルド受付嬢など）",
          "role（役割）、name（任意）、traits（任意）、location（任意）", ready=False),
    Skill("adventure_log", "冒険記録スキル",
          "セッションの冒険日誌を作る・更新する（リプレイ風のまとめ、取得物一覧、知り合った人々、訪れた場所）",
          "mode（create=新しい回 / update=最新の回に追記）"),
    Skill("help", "ヘルプ", "できることの一覧を見せる"),
)
SKILLS_BY_NAME: Dict[str, Skill] = {s.name: s for s in SKILLS}

# ビルダーで開くタブ (scripts/builder.js の TABS の id)
BUILDER_TABS: Dict[str, str] = {
    "build_region": "region",
    "build_town": "settlement",
    "build_journey": "wilderness",
    "build_scenario": "site",
}

RESPONSE_TYPES = ("skill", "clarify", "answer")
MAX_OPTIONS = 4


def display_name(skill: Skill) -> str:
    return skill.label if skill.ready else f"{skill.label}（準備中）"


def skills_for_prompt() -> str:
    return "\n".join(f"- {s.name}（{display_name(s)}）: {s.description} / params: {s.params}" for s in SKILLS)


def help_text() -> str:
    """/ai help で見せる一覧 (ヘルプ自身は載せない)"""
    lines = [f"・{display_name(s)}: {s.description}" for s in SKILLS if s.name != "help"]
    return "私にできることはこれだよ！ /ai のあとに、やりたいことをそのまま書いてね。\n" + "\n".join(lines)


def build_router_prompt(gm_text: str, persona: str = "") -> str:
    # テンプレートには JSON の例 ({...}) があるため format ではなく置き換えで埋め込む
    return (load_cached_prompt("skill_router_prompt.txt")
            .replace("{persona}", persona.strip())
            .replace("{skills}", skills_for_prompt())
            .replace("{gm_text}", gm_text))


def _params(value) -> Dict:
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if isinstance(v, (str, int, float, bool)) and v != ""}


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_router_response(resp_text: str) -> Optional[Dict]:
    """
    agy の応答を {type, ...} に整える。JSON として読めなければ None (呼び出し側が文をそのまま回答にする)。
    一覧に無いスキルは answer 扱いにする (text が無ければ None)。
    """
    data = extract_json_from_response(resp_text)
    if not isinstance(data, dict) or data.get("type") not in RESPONSE_TYPES:
        return None
    kind = data["type"]
    if kind == "skill":
        name = data.get("skill")
        if name in SKILLS_BY_NAME:
            return {"type": "skill", "skill": name, "params": _params(data.get("params")),
                    "say": _text(data.get("say")), "text": _text(data.get("text"))}
        text = _text(data.get("text")) or _text(data.get("say"))
        return {"type": "answer", "text": text} if text else None
    if kind == "clarify":
        options: List[Dict] = []
        for opt in data.get("options") if isinstance(data.get("options"), list) else []:
            if isinstance(opt, dict) and opt.get("skill") in SKILLS_BY_NAME:
                label = _text(opt.get("label")) or display_name(SKILLS_BY_NAME[opt["skill"]])
                options.append({"label": label, "skill": opt["skill"], "params": _params(opt.get("params"))})
        options = options[:MAX_OPTIONS]
        if not options:
            text = _text(data.get("question"))
            return {"type": "answer", "text": text} if text else None
        return {"type": "clarify", "question": _text(data.get("question")) or "どれにしようか？", "options": options}
    text = _text(data.get("text"))
    return {"type": "answer", "text": text} if text else None


# 途中で切れた JSON から "text" の中身を拾う (閉じの " が無くても最後まで)
_TEXT_FIELD = re.compile(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)', re.S)


def salvage_text(resp_text: str) -> str:
    """
    JSON として読めなかった応答を GM に見せる文にする。
    `{"type":"answer","text":"…` のように JSON が途中で切れていたら text の中身だけを取り出す (生の JSON を出さない)
    """
    text = (resp_text or "").strip()
    if not text.startswith("{"):
        return text
    m = _TEXT_FIELD.search(text)
    if not m:
        return ""
    raw = m.group(1).rstrip("\\")
    try:
        return json.loads(f'"{raw}"').strip()
    except ValueError:
        return raw.replace("\\n", "\n").replace('\\"', '"').strip()
