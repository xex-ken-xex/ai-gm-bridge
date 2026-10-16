#!/usr/bin/env python3
"""
prompt_manager.py - Ultimate Unified Version
=========================================================================
- 4段階シナリオ＆ワールドビルダー (1.地域 ➔ 2.集落・くらし ➔ 3.周辺・旅路 ➔ 4.廃墟・迷宮)
- NPC自律戦闘戦術 & NPC小説風会話（知識境界・記憶鮮明度・忘却対応）
- アンカリング（特定例示の固定化）を排除した全7種のデフォルトテンプレート内蔵
- 従来のチャットカード型ダンジョン生成関数との後方互換性を維持
"""

import os
import re
import json
import glob
from typing import Dict, List, Optional, Tuple

MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_DIR = os.path.join(MODULE_DIR, "prompts")
DATA_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", ".."))

_PROMPT_CACHE: Dict[str, str] = {}

# =========================================================================
# 外部ファイル未作成時でも即動作する内蔵デフォルトテンプレート (全7種)
# ※ 特定例示によるアンカリングを排除し、因果関係から多様に創発させる設計
# =========================================================================
DEFAULT_TEMPLATES: Dict[str, str] = {
    "1_region_prompt.txt": """あなたはファンタジーTRPGの熟練ワールドビルダー兼、環境ストーリーテラーです。
以下の基本設定とGM指示に基づき、冒険の舞台となる「地域（Region）」の風土・天然資源・歴史を多角的に構築してください。

【入力コンテキスト】
ワールド名: {world_title}
ワールド全体設定: {world_description}
GMからの地域テーマ指示: {user_prompt}

【設計指針】
1. 気候・地形・資源の因果関係:
   - 季節風、降水量、地質・植生を具体的に定義し、そこから採れる「天然資源（鉱物、石材、木材、農作物、水源、特殊資源）」に地理的な説得力を持たせてください。
2. 抽象形容詞の禁止:
   - 「不気味な」「美しい」「古い」などの抽象語を禁じます。空の色、風の温度、土や草の匂い、石の質感など、五感に訴える物理的ディテールで記述してください。
3. PL公開情報とDM秘匿情報の分離:
   - `public_lore` には、旅人や住民が誰でも知っている地理・気候・主要資源・表向きの歴史を記述します。
   - `dm_secrets` には、地域で密かに進行している環境異変、資源枯渇や汚染の真因、暗躍する勢力、古代の封印などの「裏設定」を記述します。

必ず以下のJSONフォーマットのみを出力してください（Markdownコードブロックや解説は不要）:
{{
  "region_name": "地域の名称（異名を含む風情ある名称）",
  "public_lore": {{
    "climate_and_terrain": "気候、季節の移ろい、特徴的な地形と空模様（2〜3文）",
    "natural_resources": [
      {{"name": "資源名1（鉱物・石材・燃料など）", "location": "産地の特徴", "usage": "地域での主な用途"}},
      {{"name": "資源名2（樹木・農作物・薬草など）", "location": "群生地の特徴", "usage": "地域での主な用途"}},
      {{"name": "資源名3（動物・水産物・特殊資源）", "location": "生息域の特徴", "usage": "地域での主な用途"}}
    ],
    "history_and_ruins": "かつてこの地域を治めた勢力や、街道沿いに点在する古い遺構の言い伝え（2〜3文）",
    "travel_impression": "旅人がこの地域に足を踏み入れた瞬間に感じる風・匂い・景観の初期描写（2文）"
  }},
  "dm_secrets": {{
    "hidden_threat": "現在この地域を蝕んでいる見えざる脅威や異変の正体（2〜3文）",
    "faction_tensions": "資源や領土をめぐる勢力間の対立、または山賊・怪物たちの縄張り構造",
    "ancient_secret": "地域の地下や廃墟に眠る、失われた歴史の真実"
  }},
  "audio_prompts": {{
    "region_ambience_en": "地域の自然環境・風・天候を表現する英語の環境音プロンプト（Stable Audio用・seamless loop指定）"
  }}
}}""",

    "2_settlement_prompt.txt": """あなたは人々の営みと哀歓を描く卓越した民俗学者兼、TRPGシナリオライターです。
親地域の「気候・地形・資源・歴史」を土台として、そこに生きる人々の「集落・街（Settlement）」を、マクロな経済からミクロな生活道具、そして人々の感情（くらし・楽しみ・悲しみ）まで多段階に掘り下げて構築してください。

【親地域のコンテキスト（FVTTジャーナルより読み込み）】
・地域の表向き設定（PL公開）:
{region_public_lore}
・地域の裏設定（DM秘匿）:
{region_dm_secrets}

【集落のビルド指定】
集落の規模: {settlement_scale}
GM追加指示: {user_prompt}

【設計指針（風土から経済・生活・感情への論理的連鎖）】
1. 風土と資源に根ざした建築と産業の階層化:
   - 【建築】: 親地域の気候（寒暖・風雨・乾燥など）をしのぎ、地元の建材を活かした建築様式と生活空間を描写してください。
   - 【一次産業（原材料）】: 地域の気候・地質から得られる農作物、畜産、水産、鉱物、林産資源。
   - 【二次産業（主要工芸品・交易品）】: 一次産業の資源を加工し、集落の経済を支える特産品や職人技術。
   - 【末端の手仕事・生活財（ミクロの生活工芸）】: 二次産業の技術や「端材・副産物」を用いて、住民が日常的に使う、あるいは家族や旅人のために手作業で作る【小さな日用品・道具・嗜好品・装身具・玩具】を1つ定義してください。地域の資源に応じた固有の品にすること。
2. くらし・楽しみ・悲しみ（人間のドラマとペーソス）:
   - 【くらし】: 朝夕の風景、気候や労働に合った地元の料理、住民の気質。
   - 【楽しみ】: 厳しい労働や自然の中で人々が心を癒やす娯楽、祭事、音楽、ささやかな幸福。
   - 【悲しみ】: その土地の歴史、過酷な産業、環境の悪化、あるいは近隣の異変によって人々の心に落ちている影（哀愁、喪失感、未解決の困りごと）。毎回同じパターンにせず、その集落の産業や歴史から必然的に生じる固有の悲哀を描いてください。

必ず以下のJSONフォーマットのみを出力してください（Markdownコードブロックや解説は不要）:
{{
  "settlement_name": "集落・街の名称",
  "public_lore": {{
    "architecture_and_scenery": "地域の建材を使った建物の特徴、街に漂う匂いや音、景観の第一印象（2〜3文）",
    "primary_industries": "一次産業の詳細（採取・生産している原材料と労働の風景）",
    "secondary_industries": "二次産業の詳細（加工技術、主要交易品、工房の様子）",
    "micro_craft": {{
      "item_name": "末端の手仕事・生活財の固有名称",
      "description": "使用されている地元の素材（端材等）、形状やサイズ感、住民のくらしの中でどう使われているか（2文）"
    }},
    "daily_life_and_food": "日々のくらしのリズム、労働の後に口にする地元の料理と飲み物",
    "local_joys": "人々の楽しみ（娯楽、季節の行事、音楽、笑顔が生まれる瞬間）",
    "visible_sorrows": "街に漂う悲しみや憂い（過去の傷跡、暮らしの苦しさ、あるいは最近起きている不穏な兆候など、旅人が肌で感じる哀愁）",
    "key_npcs": [
      {{"name": "NPC名1", "role": "生業・立場", "personality": "外見・口調と、この街の『楽しみ』または『悲しみ』にどう関わっているか"}}
    ]
  }},
  "dm_secrets": {{
    "sorrow_truth": "集落の『悲しみ・憂い』の背後にある真の構造や原因、および周辺の遺跡・ワイルダネスとの繋がり（2〜3文）",
    "micro_craft_hook": "集落固有の『手仕事・生活財』が、今後の野外探索や廃墟・迷宮の現場でどのように発見され、どんな文脈やギミックの手がかりになるかの設計",
    "npc_hidden_agenda": "集落内のNPCが抱えている秘密、葛藤、または外部勢力との関わり"
  }},
  "audio_prompts": {{
    "joy_bgm_en": "集落の『くらしと楽しみ』の情景に合った英語BGMプロンプト（楽器編成・テンポ・空気感を指定）",
    "sorrow_bgm_en": "集落の『悲しみと静寂』の情景に合った英語BGMプロンプト（楽器編成・テンポ・空気感を指定）"
  }}
}}""",

    "3_wilderness_prompt.txt": """あなたはワイルダネス・アドベンチャー（野外探索と旅路）の熟練レベルデザイナーです。
出発地である集落から、目的地の廃墟・迷宮へと至る「旅路のネットワーク（ポイントクロール）」の各ランドマークと経路を設計してください。

【地域・集落のコンテキスト】
・地域の風土・資源（PL公開）: {region_public_lore}
・集落の産業・手仕事・悲しみ（PL公開）: {settlement_public_lore}
・地域の脅威と集落の悲しみの真相（DM秘匿）: {settlement_dm_secrets}
・GM追加指示: {user_prompt}

【旅路のグラフ構造（トポロジー）】
{wilderness_topology_json}
※ `start: true` は集落からの出発口、`goal: true` は目的地の廃墟・迷宮の入口、`in_cycle: true` はメイン街道から外れた険しい迂回路上のランドマークを示します。

【設計指針】
1. 旅路のグラデーション:
   - `depth`（出発地からの距離）が浅い場所には「集落の一次産業の名残」を配置し、深くなるにつれて「手つかずの厳しい自然」と「敵勢力の斥候や異変の痕跡」を濃くしてください。
2. 集落の『産業・手仕事』と『悲しみ』の伏線配置:
   - 道中のランドマークに、集落の交易品や『末端の手仕事・生活財』の痕跡を配置し、目的地への追跡や探索の動機を高めてください。
3. 野営地の設計:
   - 中間地点に1〜2箇所、`safe_rest: true`（雨風をしのげる地形や古い遺構の土台）を設定してください。

必ず以下のJSONフォーマットのみを出力してください（Markdownコードブロックや解説は不要）:
{{
  "wilderness_title": "旅路エリアの名称",
  "public_lore": {{
    "route_overview": "集落から目的地へ向かう旅路の全体的な景観、天候の変化、メイン街道と迂回路の違い（2〜3文）",
    "weather_table": ["天候パターン1", "天候パターン2", "天候パターン3"]
  }},
  "dm_secrets": {{
    "stalking_threat": "旅路を行くPCたちを尾行・監視している脅威の動きと、野営時の襲撃条件",
    "shortcut_secret": "迂回路（in_cycleの経路）を通った場合に発見できる、目的地へ有利にアプローチする秘密"
  }},
  "landmarks": [
    {{
      "id": 0,
      "title": "ランドマーク名",
      "icon": "icons/svg/direction.svg",
      "safe_rest": false,
      "light_preset": "moonlight_dim",
      "tts_narration": "到達時に音声読み上げする五感の初期描写（2〜3文・ネタバレ厳禁・抽象語禁止）",
      "gm_notes": "【状況】: 現在の様子 / 【調査対象と判定】: 調べられる痕跡とDC / 【分岐ヒント】: 隣接ルート情報",
      "ambient_sound_en": "この地点の自然環境音を表す英語プロンプト"
    }}
  ]
}}""",

    "4a_site_lore_prompt.txt": """あなたはファンタジーTRPG (D&D5e / OSR) の熟練ダンジョンマスター兼、プレイヤーに最高のカタルシスを届けるシナリオディレクターです。
これまでの地域・集落・旅路の文脈と、参加PCのバイオグラフィーを統合し、マンネリを打破する「廃墟・迷宮の前提と3階層の目標構造」を構築してください。

【文脈コンテキスト】
・集落のくらし・手仕事・悲しみ（PL公開）:
{settlement_public_lore}
・集落の悲しみの真相・裏設定（DM秘匿）:
{settlement_dm_secrets}
・道中（ワイルダネス）の状況:
{wilderness_summary}

【参加PCのバイオグラフィー（物語の主役）】
{pc_profiles}

【関連NPC・人物設定】
{npc_profiles}

【舞台の空間スペック】
空間タイプ: {site_mode}
規模: {size_name} (約{room_count}区画)
GM追加指示: {user_prompt}

【シナリオ＆カタルシス設計指針（最重要）】
1. 地域・集落との深い結合:
   - 建築様式には地域の建材や歴史を反映させ、現在の占拠勢力が「集落の二次産業の産物」や「末端の手仕事・生活財」をどう扱っているかを具体化してください。
2. 物語構造の多様化（5つのミッション・アーキタイプ）:
   - 「①儀式・兵器起動の阻止」「②勢力分裂・同士討ち工作」「③救出・奪還＆脱出」「④無敵ボスの弱点ギミック起動」「⑤PCの宿敵の陰謀暴き」から最適なものを1つ選択してください。
3. 3階層の目的と「スカッとする逆転劇（Catharsis Payoff）」:
   - 【分かりやすい大目標】: 突入時に直感的に理解できるクリア条件（1〜2文）。
   - 【階層化された中目標（2〜3個）】: 袋小路やハブ区画で達成できる小目標。PCのバイオグラフィーや集落の手仕事・NPCに直結させてください。
   - 【スカッとする逆転劇】: 中目標を達成して最深部へ挑んだ際、敵の絶対優位が崩れ去り、プレイヤーの探索と知恵がド派手に報われる爽快な展開を設計してください。

必ず以下のJSONフォーマットのみを出力してください（Markdownコードブロックや解説は不要）:
{{
  "site_name": "廃墟・ダンジョンの正式名称",
  "mission_archetype": "選択したアーキタイプ名",
  "public_lore": {{
    "exterior_and_history": "外から見た廃墟/迷宮の威容、かつての建造目的、地域の気候に晒された現在の佇まい（2〜3文）",
    "clear_main_goal": "プレイヤーに提示される分かりやすい大目標（1〜2文）",
    "pc_narrative_hook": "参加PCのバイオグラフィーや集落の悲しみが、この場所の入口でどう共鳴するかの導入描写（2文）",
    "sensory_ambience": {{
      "visual": "建材の風化状態、主光源の色、地面や壁に残る特徴的な痕跡",
      "auditory": "吹き抜ける風や水音、遠くで響く活動音",
      "olfactory": "具体的な匂いのブレンド",
      "air": "温度・湿度・空気の重さ"
    }}
  }},
  "dm_secrets": {{
    "true_situation": "現在この場所で進行している危機の全体像と、集落の悲しみとの完全な答え合わせ（2〜3文）",
    "sub_objectives": [
      {{
        "title": "中目標1の名称",
        "target_topology": "浅層〜中層の袋小路 (deg=1)",
        "connection_to_town_or_pc": "集落の手仕事・特産品・またはPCの過去との関わり",
        "effect": "達成したときに起きる物理的・戦術的な有利効果"
      }},
      {{
        "title": "中目標2の名称",
        "target_topology": "中層〜深層の袋小路またはハブ (deg>=3)",
        "connection_to_town_or_pc": "集落の悲しみ・NPC・またはPCの過去との関わり",
        "effect": "達成したときに起きる物理的・戦術的な有利効果"
      }}
    ],
    "catharsis_payoff": "中目標を達成して最深部（ゴール）に到達した際、プレイヤーが最高にスカッとする逆転劇・ボスの弱体化・敵陣崩壊の具体的演出（2〜3文）",
    "factions": [
      {{"name": "勢力名1", "territory": "浅層〜中層の縄張り", "status": "現在の行動と内部の弱点・隙"}},
      {{"name": "勢力名2（または首領陣営）", "territory": "深層〜最深部の縄張り", "status": "現在進行中の企みと勢力1との関係"}}
    ]
  }},
  "audio_prompts": {{
    "exploration_bgm_en": "探索時の緊張感ある英語BGMプロンプト",
    "catharsis_boss_bgm_en": "最深部での逆転・決戦用の英語BGMプロンプト"
  }}
}}""",

    "4b_site_rooms_prompt.txt": """あなたはTRPGの戦術レベルデザイナー兼、Foundry VTTの空間演出ディレクターです。
廃墟・迷宮の「大目標・中目標・逆転カタルシス」と、各区画の「グラフ構造（トポロジー）」に基づき、プレイヤーが探索に熱中する各部屋の描写とGMノートを生成してください。

【廃墟・迷宮の前提と目標構造（GM確認済）】
名称: {site_name} (空間タイプ: {site_mode})
・表向きの歴史と大目標（PL公開）:
{site_public_lore}
・中目標・勢力・スカッとする逆転劇（DM秘匿）:
{site_dm_secrets}

【各区画のトポロジー構造（JSON配列）】
{rooms_topology_json}
※ `id`: 部屋インデックス、`deg`: 接続通路数（1=袋小路、2=通過路、3以上=ハブ広間）、`depth`: 入口からの距離、`start`: 入口、`goal`: 最深部、`in_cycle`: 周回路上か、`neighbors`: 隣接する部屋番号リスト

【部屋設計の鉄則】
1. 役割の割り当て:
   - `start: true`: 大目標の再確認と、集落から続く最初の痕跡を配置。
   - `deg == 1`（袋小路）: DM秘匿情報の【中目標ギミック・重要人物・集落の生活財・安全な野営地(`safe_rest: true`)】を優先配置。
   - `deg >= 3`（ハブ広間）: 勢力の検問所、中央広場、または中目標を達成した変化が目に見える要衝。
   - `goal: true`（最深部）: 【スカッとする逆転劇】が炸裂する決戦場。`gm_notes` に通常時と中目標達成時（敵の狼狽・弱体化）の両方を明記。
2. 部屋間の有機的な繋がり（Three Clue Rule）:
   - `neighbors`（隣接部屋番号）を参照し、「隣の#Xから物音や匂いが漏れている」「#Yの仕掛けを解く手がかりがここにある」等の部屋間リンクを含めてください。
3. `tts_narration` と `gm_notes` の分離:
   - `tts_narration` はトークン進入時にそのまま音声再生されます。ネタバレやメタ説明を含めず、足を踏み入れた瞬間の五感と初期視覚フックを2〜3文で記述してください。

必ず以下のJSON配列フォーマットのみを出力してください（Markdownコードブロックや解説は不要）:
[
  {{
    "id": 0,
    "role": "start | sub_goal | hub | ambush | sanctuary | boss | empty",
    "title": "区画の名称",
    "icon": "icons/svg/door-open-outline.svg, icons/svg/sword.svg, icons/svg/chest.svg, icons/svg/skull.svg, icons/svg/temple.svg, icons/svg/trap.svg, icons/svg/book.svg, icons/svg/campfire.svg, icons/svg/eye.svg から1つ選択",
    "is_empty": false,
    "safe_rest": false,
    "light_preset": "torch_warm | bonfire_bright | magic_blue | acid_green | moonlight_dim | dark から1つ選択",
    "tts_narration": "プレイヤー向け自動音声読み上げテキスト（五感と初期視覚フックのみ・2〜3文・抽象語禁止）",
    "gm_notes": "【進行中の状況】: 敵や環境の動き / 【調査と判定】: ①対象A [判定 DC] 結果 ②対象B [判定 DC] 結果 / 【中目標・隣接リンク】: 隣接区画への伏線やカタルシス効果",
    "ambient_sound_en": "この部屋固有の3D環境音英語プロンプト（不要な空室は null）"
  }}
]""",

    "npc_tactics_prompt.txt": """あなたはTRPGの戦術AI兼、キャラクターの背景と本能を体現するロールプレイヤーです。
現在手番を迎えたNPC/モンスターの「個体情報」「周囲の空間・部屋の状況」「知覚しているPCとの位置関係」に基づき、最も合理的かつドラマチックな行動と発言を決定してください。

【手番キャラクターの個体情報】
名称: {npc_name}
種族・分類: {npc_role}
外見・背景・行動原理: {npc_bio}
現在HP / 最大HP: {hp_current} / {hp_max} (状態: {health_status})
移動速度: {move_speed} ft
使用可能な武器・アクション一覧:
{available_actions}

【現在いる区画の環境・文脈】
区画名: {room_name}
空間の情景: {room_flavor}
環境ギミック・GMノート: {room_gm_notes}

【知覚している対象（PC）との距離・視線】
{perceived_targets_json}

【意思決定フレームワーク】
1. 生存本能と目的のバランス:
   - 知性ある存在でHPが危機的であれば、遮蔽への後退・交渉・増援要請・部屋のギミック利用を選択してください。
   - 狂信者、縄張りを守る獣、または優位にある場合は、最も脅威となる対象への攻撃や包囲を選択してください。
2. 発言（`speech`）と行動描写（`emote`）の分離:
   - 言語を持たない怪物の場合は `speech` には咆哮や鳴き声（または空文字）を入れ、`emote` に身体的な予備動作を描写してください。

必ず以下のJSONフォーマットのみを出力してください（Markdownコードブロックや解説は不要）:
{{
  "thought": "戦況・自身の状態・部屋の環境を踏まえた内部思考（1文）",
  "move_intent": "approach_target | flank_target | retreat_from_enemies | seek_cover_or_gimmick | hold_position から1つ選択",
  "target_token_id": "対象とするPCのtoken_id（対象がいない場合は null）",
  "action_type": "attack | skill_or_spell | interact_environment | dodge_or_flee | parley から1つ選択",
  "action_name": "使用する武器・アクション名（使用しない場合は null）",
  "dice_formula": "実行するダイス式（例: 1d20+5、不要なら null）",
  "emote": "チャットに表示する行動・予備動作の視覚描写（1〜2文）",
  "speech": "マップ上の吹き出しと音声で再生するセリフまたは咆哮（1〜2文・記号や括弧は含めない）"
}}""",

    "npc_dialogue_prompt.txt": """あなたは卓越したファンタジー小説家兼、TRPGのNPCロールプレイ・エンジンです。
現在その場にいるPCの発言・行動に対し、声が届いているNPCたちの「性格・目的」「知識の境界（知っていること／秘密／知らないこと）」「過去の記憶と鮮明度（うろ覚え・忘却）」を勘案し、最も自然でドラマチックな反応を生成してください。

【現在の場面・空間コンテキスト】
場所: {room_name}
空間の雰囲気・状況: {room_flavor}

【発言・行動したPC】
発言者: {speaker_name}
発言・行動内容: 「{player_input}」
判定結果の付加情報: {check_result_context}

【その場で声が届いているNPC一覧（JSON）】
{listening_npcs_json}
※ 各NPCのデータ説明:
- `goal`: 今このNPCが最も気にかけている目的や欲求
- `trust`: PCへの現在の信頼度（-5:敵対 〜 0:中立 〜 +5:全幅の信頼）
- `public_known`: 聞かれれば普通に話す表向きの知識
- `secret_known`: 隠している秘密と開示条件
- `ignorance`: 【重要】このNPCが「全く知らないこと」。該当する質問にはメタ知識で答えず、「知らん」と答えるか立場に応じた俗説を語らせること。
- `short_term_memory`: 直近のやり取り
- `long_term_memory`: 過去の記憶リスト。
  * `[鮮明]`: 正確に覚えており具体的に言及できる。
  * `[うろ覚え・曖昧]`: 名前や細部が曖昧。「確か…」「誰だったか…」と言葉を濁したり一部を言い間違えたりする。
  * `[ほぼ忘却・勘違い]`: ほとんど忘れている。「そんなことがあったか？」と反応する。

【演出・出力ルール】
1. 発言者の選定と脇役のサイレント反応:
   - 指名されたNPC、または自分の目的・秘密に最も関わるNPCを `primary_npc_id` に選んでください。
   - 他のNPCが同じ場にいる場合、セリフを発さずに場の空気に反応する短い仕草を `ambient_reactions` に入れてください。
2. 小説的な「地の文 (`narrative_action`)」と「セリフ (`speech`)」の分離:
   - `narrative_action` には、セリフ前後の【手元の動作、視線の動き、表情の変化、呼吸の間】を小説の地の文として情感豊かに記述してください。
   - `speech` には純粋なセリフのみを記述してください（括弧やト書きは一切含めないこと）。
3. 秘密の保護とダイス判定の提案 (`check_offer`):
   - 開示条件を満たしていないのにPCが `secret_known` に迫った場合、NPCは言葉を濁し、`check_offer` にスキル判定（説得・看破・威圧など）を提案してください。

必ず以下のJSONフォーマットのみを出力してください（Markdownコードブロックや解説は不要）:
{{
  "primary_npc_id": "メインで反応するNPCのtoken_id",
  "primary_npc_name": "メインで反応するNPCの名前",
  "internal_thought": "PCの発言に対するNPCの内心の思考・記憶の照合（1〜2文）",
  "narrative_action": "小説の地の文として表示する、NPCの態度・視線・手元の仕草・間の描写（2〜3文）",
  "speech": "NPCのセリフ本文のみ（括弧なし・1〜3文。無言なら空文字）",
  "ambient_reactions": [
    {{
      "npc_id": "脇役NPCのtoken_id",
      "npc_name": "脇役NPCの名前",
      "silent_emote": "セリフを発さずに見せる仕草や視線の地の文（1文）"
    }}
  ],
  "physical_action": "stay | approach_speaker | step_back | leave_scene | turn_hostile から1つ選択",
  "trust_delta": 0,
  "new_memory": {{
    "summary": "今回のやり取りでNPCの記憶に新しく刻まれた出来事の1文要約",
    "importance": 3
  }},
  "check_offer": {{
    "skill": "説得 | 看破 | 威圧 | 捜査 などの判定名（判定不要なら null）",
    "dc": 13,
    "target_secret_hint": "判定に成功した場合にNPCが打ち明ける秘密や態度の軟化内容"
  }}
}}"""
}


def load_cached_prompt(filename: str) -> str:
    """プロンプトファイルを読み込み（未作成の場合は内蔵デフォルトテンプレートを返却）"""
    if filename in _PROMPT_CACHE:
        return _PROMPT_CACHE[filename]

    filepath = os.path.join(PROMPTS_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        _PROMPT_CACHE[filename] = content
        return content

    if filename in DEFAULT_TEMPLATES:
        return DEFAULT_TEMPLATES[filename]

    raise FileNotFoundError(f"プロンプトファイルが見つかりません: {filepath}")


def get_active_world_info() -> Dict[str, str]:
    """Foundry VTTの world.json からワールド名・説明を取得"""
    worlds_dir = os.path.join(DATA_DIR, "worlds")
    if not os.path.exists(worlds_dir):
        return {"title": "The Antikythira", "description": "古代の遺構と辺境の荒野", "world_id": "default"}

    world_candidates = glob.glob(os.path.join(worlds_dir, "*", "world.json"))
    if not world_candidates:
        return {"title": "The Antikythira", "description": "古代の遺構と辺境の荒野", "world_id": "default"}

    target_json = world_candidates[0]
    for wc in world_candidates:
        if "the-antikythira" in wc:
            target_json = wc
            break

    try:
        with open(target_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_desc = data.get("description", "")
        clean_desc = re.sub(r"<[^>]*>", " ", raw_desc)
        clean_desc = re.sub(r"\s+", " ", clean_desc).strip()
        return {
            "title": data.get("title", "The Antikythira"),
            "description": clean_desc or "古代の遺構と辺境の荒野",
            "world_id": data.get("id", "default")
        }
    except Exception:
        return {"title": "The Antikythira", "description": "古代の遺構と辺境の荒野", "world_id": "default"}


def format_actors_context(actors_ctx: Dict) -> Tuple[str, str]:
    """FVTTから送られたPC/NPCのBiography情報をプロンプト用文字列に整形"""
    pcs = actors_ctx.get("pcs", []) if isinstance(actors_ctx, dict) else []
    npcs = actors_ctx.get("npcs", []) if isinstance(actors_ctx, dict) else []

    pc_lines = [f"・{p.get('name')} ({p.get('info', '')}): {p.get('bio', '')}" for p in pcs]
    npc_lines = [f"・{n.get('name')} ({n.get('role', '')}): {n.get('bio', '')}" for n in npcs]

    pc_str = "\n".join(pc_lines) if pc_lines else "・冒険者パーティ（特定の個人背景指定なし）"
    npc_str = "\n".join(npc_lines) if npc_lines else "・特になし（集落や地域の設定から適宜補完）"
    return pc_str, npc_str


def prioritize_key_topologies(topologies: List[Dict], max_count: int = 18) -> List[Dict]:
    """入口・最深部・袋小路(次数1)・ハブ(次数3以上)を優先選定"""
    if len(topologies) <= max_count:
        return topologies

    must_have = []
    high_priority = []
    normal = []

    for topo in topologies:
        if topo.get("start") or topo.get("goal"):
            must_have.append(topo)
        elif topo.get("deg", 2) == 1 or topo.get("deg", 2) >= 3:
            high_priority.append(topo)
        else:
            normal.append(topo)

    selected = list(must_have)
    needed = max_count - len(selected)
    if len(high_priority) <= needed:
        selected.extend(high_priority)
        needed = max_count - len(selected)
        selected.extend(normal[:needed])
    else:
        selected.extend(high_priority[:needed])

    selected.sort(key=lambda x: x["id"])
    return selected


# =========================================================================
# 4段階シナリオビルダー & 自律NPC プロンプト構築関数群
# =========================================================================
def build_region_stage_prompt(world_title: str, world_description: str, user_prompt: str = "") -> str:
    template = load_cached_prompt("1_region_prompt.txt")
    return template.format(
        world_title=world_title,
        world_description=world_description,
        user_prompt=user_prompt or "厳しい自然と古代の遺構が眠る辺境地域"
    )


def build_settlement_stage_prompt(region_ctx: Dict, settlement_scale: str, user_prompt: str = "") -> str:
    template = load_cached_prompt("2_settlement_prompt.txt")
    return template.format(
        region_public_lore=region_ctx.get("public_lore") or "厳しい自然環境と固有の鉱物・森林資源を持つ地域",
        region_dm_secrets=region_ctx.get("dm_secrets") or "周辺の古い遺構の奥で異変が進行している",
        settlement_scale=settlement_scale or "辺境の開拓村",
        user_prompt=user_prompt or "地域の資源を活かした産業と末端の手仕事、そして人々の哀歓がある集落"
    )


def build_wilderness_stage_prompt(
    region_ctx: Dict,
    settlement_ctx: Dict,
    topologies: List[Dict],
    user_prompt: str = ""
) -> str:
    template = load_cached_prompt("3_wilderness_prompt.txt")
    compact = [{
        "id": t["id"], "deg": t["deg"], "depth": t["depth"],
        "start": t["start"], "goal": t["goal"],
        "in_cycle": t.get("in_cycle", False), "neighbors": t.get("neighbors", [])
    } for t in topologies]

    return template.format(
        region_public_lore=region_ctx.get("public_lore") or "厳しい自然が広がる辺境地域",
        settlement_public_lore=settlement_ctx.get("public_lore") or "地域の資源を加工して暮らす集落",
        settlement_dm_secrets=settlement_ctx.get("dm_secrets") or "周辺の廃墟に関わる未解決の憂いがある",
        user_prompt=user_prompt or "集落から目的地の廃墟・迷宮へと続く旅路",
        wilderness_topology_json=json.dumps(compact, ensure_ascii=False)
    )


def build_site_lore_stage_prompt(
    settlement_ctx: Dict,
    wilderness_ctx: Dict,
    actors_ctx: Dict,
    site_mode: str,
    size_name: str,
    room_count: int,
    user_prompt: str = ""
) -> str:
    template = load_cached_prompt("4a_site_lore_prompt.txt")
    pc_profiles, npc_profiles = format_actors_context(actors_ctx)
    mode_label = "野外の廃墟・古戦場（風見が丘型：外周開放・崩落石垣）" if site_mode == "ruins" else "地下迷宮・城塞（完全閉鎖空間）"

    return template.format(
        settlement_public_lore=settlement_ctx.get("public_lore") or "地域の資源と手仕事で暮らす集落",
        settlement_dm_secrets=settlement_ctx.get("dm_secrets") or "廃墟に巣食う脅威が集落に影を落としている",
        wilderness_summary=wilderness_ctx.get("public_lore") or "厳しい自然を抜ける旧街道の旅路",
        pc_profiles=pc_profiles,
        npc_profiles=npc_profiles,
        site_mode=mode_label,
        size_name=size_name,
        room_count=room_count,
        user_prompt=user_prompt or "集落の悲しみに決着をつけ、プレイヤーがスカッとする逆転劇がある舞台"
    )


def build_site_rooms_stage_prompt(
    site_name: str,
    site_mode: str,
    site_public_lore: str,
    site_dm_secrets: str,
    topologies: List[Dict],
    max_rooms: int = 18
) -> str:
    template = load_cached_prompt("4b_site_rooms_prompt.txt")
    sampled = prioritize_key_topologies(topologies, max_count=max_rooms)
    compact = [{
        "id": t["id"],
        "deg": t["deg"],
        "depth": t["depth"],
        "start": t["start"],
        "goal": t["goal"],
        "in_cycle": t.get("in_cycle", False),
        "neighbors": [int(n) + 1 for n in t.get("neighbors", [])],
        "area": t["area"]
    } for t in sampled]

    mode_label = "野外の廃墟・古戦場（風見が丘型）" if site_mode == "ruins" else "地下迷宮・城塞"

    return template.format(
        site_name=site_name,
        site_mode=mode_label,
        site_public_lore=site_public_lore or "古い廃墟。最深部の脅威を打ち破ることが大目標。",
        site_dm_secrets=site_dm_secrets or "袋小路の中目標を達成すると、最深部でスカッとする逆転劇が起きる。",
        rooms_topology_json=json.dumps(compact, ensure_ascii=False)
    )


def build_npc_tactics_prompt(perc: Dict) -> str:
    template = load_cached_prompt("npc_tactics_prompt.txt")
    return template.format(
        npc_name=perc.get("npc_name", "モンスター"),
        npc_role=perc.get("npc_role", "NPC"),
        npc_bio=perc.get("npc_bio", ""),
        hp_current=perc.get("hp_current", 10),
        hp_max=perc.get("hp_max", 10),
        health_status=perc.get("health_status", "健在"),
        move_speed=perc.get("move_speed", 30),
        available_actions=perc.get("available_actions", "・基本攻撃"),
        room_name=perc.get("room_name", "区画"),
        room_flavor=perc.get("room_flavor", ""),
        room_gm_notes=perc.get("room_gm_notes", ""),
        perceived_targets_json=json.dumps(perc.get("perceived_targets", []), ensure_ascii=False)
    )


def build_npc_dialogue_prompt(data: Dict) -> str:
    template = load_cached_prompt("npc_dialogue_prompt.txt")
    return template.format(
        room_name=data.get("room_name", "現在の場所"),
        room_flavor=data.get("room_flavor", ""),
        speaker_name=data.get("speaker_name", "PC"),
        player_input=data.get("player_input", ""),
        check_result_context=data.get("check_result_context", "なし"),
        listening_npcs_json=json.dumps(data.get("listening_npcs", []), ensure_ascii=False)
    )


# 後方互換用ラッパー関数 (旧チャットカードフローからの呼び出し対応)
def build_lore_prompt(world_title: str, world_description: str, user_prompt: str = "", floor_number: int = 1, size_name: str = "小", room_count: int = 10) -> str:
    return build_site_lore_stage_prompt(
        settlement_ctx={"public_lore": world_description, "dm_secrets": ""},
        wilderness_ctx={},
        actors_ctx={},
        site_mode="underground",
        size_name=size_name,
        room_count=room_count,
        user_prompt=user_prompt
    )


def build_rooms_prompt(lore: Dict, topologies: List[Dict], max_rooms: int = 15) -> str:
    site_name = lore.get("site_name") or lore.get("dungeon_name", "地下迷宮")
    pub = json.dumps(lore.get("public_lore", lore), ensure_ascii=False)
    dm = json.dumps(lore.get("dm_secrets", {}), ensure_ascii=False)
    return build_site_rooms_stage_prompt(site_name, "underground", pub, dm, topologies, max_rooms=max_rooms)


def extract_json_from_response(resp_text: str) -> Optional[dict | list]:
    """AIの応答テキストからJSON部分（オブジェクトまたは配列）を安全に抽出・パース"""
    if not resp_text:
        return None
    cleaned = resp_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE).strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    obj_match = re.search(r"(\{[\s\S]*\})", cleaned)
    if obj_match:
        try:
            return json.loads(obj_match.group(1))
        except Exception:
            pass

    arr_match = re.search(r"(\[[\s\S]*\])", cleaned)
    if arr_match:
        try:
            return json.loads(arr_match.group(1))
        except Exception:
            pass

    return None
