# AI Agent ガイドライン (AGENTS.md)

本ドキュメントは、このリポジトリ（`ai-gm-bridge`）で作業を行う AI エージェントおよび開発者向けの開発方針、設計仕様、環境設定、運用規約をまとめたものです。

---

## 1. プロジェクト概要

`ai-gm-bridge` は、**Foundry Virtual Tabletop (FVTT)** 上で AI がゲームマスター (AI GM) として振る舞うための双方向連携システムです。

- **Foundry VTT モジュール (フロントエンド)**: GM クライアント上で動作し、プレイヤーの発言を検知して WebSocket でバックエンドに転送。バックエンドからの指示に応じてチャット表示、ダイスロール、音声再生を行います。
- **Python バックエンド (サーバー)**: WebSocket サーバーとして待機。プレイヤーの発言を受信後、Google Gemini API を用いて情景描写・セリフ・ダイスコマンドを生成し、`edge-tts` により音声を自動合成して FVTT に返送します。

---

## 2. ディレクトリ構造と主要ファイル

```text
ai-gm-bridge/
├── Setup.sh             # 環境セットアップスクリプト (冪等実行可能)
├── README.md            # プロジェクト概要 & 利用ガイド
├── requirements.txt     # Python 依存ライブラリ一覧
├── AGENTS.md            # 本ドキュメント (AI エージェント向け仕様・規約)
├── module.json          # Foundry VTT モジュール定義ファイル
├── bridge.js            # FVTT 側 ESModule エントリーポイント (フック登録)
├── styles/builder.css   # シナリオビルダー画面の CSS (module.json の styles。DialogV2 の content 内 <style> は効かない)
├── scripts/             # FVTT 側の実装 (bridge.js から import)
│   ├── actions.js       # サーバーからの action 処理 (Foundry 依存)
│   ├── builder.js       # シナリオビルダー画面 (DialogV2) と地図帳ジャーナル (Foundry 依存)
│   ├── npc.js           # NPC 会話 (聴き手の収集・反応の表示・記憶の更新・技能判定・/ai NPC のブレイン管理画面) (Foundry 依存)
│   ├── net/connection.js # WebSocket 接続・自動再接続・受信の直列処理
│   └── lib/             # Foundry 非依存の純粋関数 (html / commands / chat-filter / rooms / scene-docs / cards / atlas / npc-brain / npc-listen)
├── ai_gm_server.py      # サーバー起動スクリプト (実装は gm_server/)
├── gm_server/           # バックエンド実装 (config / logs (詳細ログ: ~/ai-gm-logs/<日付>/server.log) / text / protocol / tts / llm / progress / deadline / dungeon_scene / builder / handlers (npc_dialogue ほか) / router / app)
├── tests/
│   ├── python/          # pytest (python3 -m pytest)
│   └── js/              # node:test (npm test)
├── package.json         # JS テスト用 (依存パッケージなし)
├── pytest.ini / requirements-dev.txt
├── prompts/             # プロンプトテンプレート (キャッシュ保持)
│   ├── dungeon_lore_prompt.txt   # ダンジョン伝承・生態系生成用
│   ├── dungeon_rooms_prompt.txt  # 部屋トポロジー・情景補完用
│   ├── builder_rules.txt         # シナリオビルダー共通ルール (整合性・マンネリ防止・自己点検)
│   ├── builder_*_prompt.txt      # シナリオビルダー各段階 (region / settlement / wilderness / site_lore / site_rooms)
│   └── npc_dialogue_prompt.txt   # NPC 会話 (知識の境界・記憶の鮮明度・判定の提案)
├── tools/
│   ├── prompt_manager.py    # プロンプト補完・ワールド情報取得
│   ├── dungeon_generator.py # ダンジョン自動生成ツール (幾何学/壁/グラフ)
│   ├── scenario_decks.py    # シナリオビルダーの発想カード (マンネリ防止の抽選)
│   ├── scenario_prompts.py  # シナリオビルダーのプロンプト組み立て・遺跡区画の役割割り当て
│   ├── test_send.py         # チャット & ダイス音送信の単体疎通テスト用スクリプト
│   └── test_voice.py        # 音声生成 & 再生指示の単体疎通テスト用スクリプト
└── docs/
    ├── specification.md # 詳細仕様書 (アーキテクチャ・プロトコル・シーケンス)
    ├── Todo.md          # 開発タスクリスト & ロードマップ
    ├── investigation_fvtt_dnd5e_control.md # FVTT & D&D5e 制御仕様調査レポート (シーン・マップ・トークン・ロール)
    ├── dungeon_generator_spec.md # ダンジョン自動生成ツール仕様書
    ├── reference_porting_review.md # reference/ 移植レビュー (問題点リスト・移植フェーズ)
    ├── dungeon_math_investigation.md # ダンジョン生成の数学的モデリング調査・仕様化検討レポート
    ├── ai_dungeon_flavor_spec.md # AI ダンジョン階層フレーバー生成仕様 & DMノウハウ調査レポート
    └── scenario_builder_spec.md  # 4 段階シナリオビルダー仕様 (整合性とマンネリ防止のしくみ)
```

### 各ファイルの役割

- **[`Setup.sh`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/Setup.sh)**
  環境自動セットアップスクリプト。Python 3、依存ライブラリ、agy CLI、Data ディレクトリ権限を自動検証・導入。何度実行しても安全な冪等設計。
- **[`requirements.txt`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/requirements.txt)**
  Python 依存パッケージ定義 (`websockets`, `edge-tts`, `google-genai`)。

- **[`docs/specification.md`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/docs/specification.md)**
  システムの詳細仕様書。アーキテクチャ図、シーケンス図、通信プロトコル仕様、音声処理仕様を定義。
- **[`docs/Todo.md`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/docs/Todo.md)**
  開発進行状況管理、残課題、今後の拡張ロードマップをまとめたタスクリスト。
- **[`docs/investigation_fvtt_dnd5e_control.md`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/docs/investigation_fvtt_dnd5e_control.md)**
  FVTT (v11〜v14) および D&D5e におけるシーン・マップ・トークン・スキルロール制御 API の技術調査レポート。
- **[`docs/dungeon_generator_spec.md`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/docs/dungeon_generator_spec.md)**
  ダンジョン自動生成ツール (`dungeon_generator.py`) の仕様書。
- **[`docs/dungeon_math_investigation.md`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/docs/dungeon_math_investigation.md)**
  グラフ理論、幾何学、パーコレーション理論等によるダンジョン生成の数学的側面調査と次期仕様化検討書。
- **[`docs/ai_dungeon_flavor_spec.md`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/docs/ai_dungeon_flavor_spec.md)**
  TRPG/DM設計ノウハウ（歴史の地層、生態系、空室の価値、Jaquaysing）と離散グラフ解析に基づくAI階層フレーバー生成仕様書。
- **[`tools/dungeon_generator.py`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/tools/dungeon_generator.py)**
  シード値、サイズ、部屋密度を指定可能なダンジョン自動生成ツール。
- **[`module.json`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/module.json)**
  Foundry VTT のモジュール定義。モジュール ID (`ai-gm-bridge`)、バージョン、FVTT 対応バージョン (v11〜v14)、読み込みスクリプト (`bridge.js`) を指定。
- **[`bridge.js`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/bridge.js)**
  FVTT 内で実行されるクライアント側 JavaScript。
  - GM ユーザー (`game.user.isGM`) のみ WebSocket (`ws://127.0.0.1:8765`) へ接続。
  - `createChatMessage` フックでプレイヤー発言を検知しサーバーへ転送。
  - 受信したアクション (`chat`, `roll`, `play_sound`) を FVTT の API 経由で実行。
- **[`ai_gm_server.py`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/ai_gm_server.py)**
  メインのサーバープロセス。
  - プレイヤーの入力から HTML タグを除去し、Gemini (`gemini-3.8-flash`) へ問い合わせ。
  - 構造化 JSON (`narrative`, `speech`, `dice`) を取得。
  - `edge-tts` で音声ファイル (`voice_<timestamp>.mp3`) を生成し、FVTT へ順次送信。
- **[`tools/test_send.py`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/tools/test_send.py)** / **[`tools/test_voice.py`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/tools/test_voice.py)**
  FVTT との接続や音声再生の単体検証用モックサーバー（手動検証ツール）。

---

## 3. システムアーキテクチャ & 通信プロトコル

### 通信方式
- **プロトコル**: WebSocket (`ws://127.0.0.1:8765`)
- **データ形式**: JSON 文字列

```
+------------------------------------------------------------------+
|                     Foundry VTT (Client)                        |
|                                                                  |
|   Player Message ---> [bridge.js] (createChatMessage hook)       |
|                             |                                    |
|                             | (ws: player_message)               |
|                             v                                    |
|                   [ai_gm_server.py]                              |
|                   (WebSocket Server)                             |
|                    /              \                              |
|                   v                v                             |
|          Google Gemini API       edge-tts                        |
|          (gemini-3.8-flash)      (ja-JP-NanamiNeural)            |
|                   \                /                             |
|                    +-------+------+                              |
|                            |                                     |
|                            | (ws: chat / roll / play_sound)      |
|                            v                                     |
|   FVTT Screen <----- [bridge.js] (ChatMessage / Roll / Audio)    |
+------------------------------------------------------------------+
```

### メッセージフォーマット仕様

#### 1. FVTT → Python サーバー (`player_message`)
```json
{
  "event": "player_message",
  "user": "プレイヤー名",
  "content": "部屋の中を詳しく調べてみます"
}
```

#### 2. Python サーバー → FVTT
- **チャット投稿 (`chat`)**:
  ```json
  {
    "action": "chat",
    "speaker": "AI GM",
    "content": "部屋の中央には古い石造りの祭壇があり、かすかに魔力が漂っています。"
  }
  ```
- **ダイスロール (`roll`)**:
  ```json
  {
    "action": "roll",
    "speaker": "AI GM (判定)",
    "formula": "1d20"
  }
  ```
- **音声再生 (`play_sound`)**:
  ```json
  {
    "action": "play_sound",
    "src": "voice_1728712345.mp3",
    "volume": 1.0
  }
  ```

---

## 4. 開発・実行環境

### 動作前提
- **OS**: Linux (WSL / Ubuntu 等)
- **Python**: 3.12+ (現行環境: 3.14)
- **Foundry VTT**: v11 〜 v14 (モジュール配置先: `~/foundrydata/Data/modules/ai-gm-bridge/`)
- **主要 Python ライブラリ**:
  - `websockets` (16.x+)
  - `google-genai` (2.x+)
  - `edge-tts` (7.x+)

### 必須環境変数
- `GEMINI_API_KEY`: Google Gemini API のアクセスキー
  ```bash
  export GEMINI_API_KEY="your-gemini-api-key"
  ```

### 実行コマンド
```bash
# 環境セットアップ (初回および更新時・何度実行しても安全)
./Setup.sh

# メインサーバー起動
python3 ai_gm_server.py

# 音声単体テスト用
python3 tools/test_voice.py

# チャット・ダイス音テスト用
python3 tools/test_send.py

# 自動テスト (変更時は両方を通すこと)
python3 -m pip install -r requirements-dev.txt
python3 -m pytest
npm test
```

---

## 5. エージェント向け開発ルール & 注意事項

### 1. 二重実行・無限ループの防止
- **GM 限定実行**: `bridge.js` 内で `if (!game.user.isGM) return;` を必須とし、複数クライアント接続による重複実行を防ぐこと。
- **ループ検知**: `createChatMessage` フックでは、`alias.startsWith("AI GM")` および `/` や `[` で始まるコマンド発言を必ず除外すること。

### 2. 音声ファイルパスの取り扱い
- FVTT のオーディオエンジン (`foundry.audio.AudioHelper.play`) は、Foundry の `Data` フォルダ直下 (`~/foundrydata/Data/`) をルートとする相対パスを参照します。
- Python 側で音声を保存する際は `os.path.join(DATA_DIR, file_name)` に出力し、FVTT に渡す `src` には `file_name`（または相対パス）を指定してください。

### 3. Gemini API (google-genai) 利用規約
- 本プロジェクトは新版 SDK `google-genai` (`from google import genai`) を使用しています。レガシーな `google-generativeai` への書き戻しは行わないでください。
- 一時的な 503 エラー (過負荷) 等に備え、指数バックオフによるリトライ処理 (`query_gemini_with_retry`) を維持・適用してください。

### 4. 既存コードおよびドキュメントの維持
- 既存の日本語コメント、Docstring、ログメッセージを尊重し、変更理由がない限り削除しないでください。
- WebSocket メッセージプロトコルを変更する場合は、`scripts/actions.js`・`bridge.js` と `gm_server/protocol.py`・`gm_server/handlers/`（および必要に応じてテストスクリプト）の整合性を必ず同時に担保してください。

### 5. モジュール構成とテスト
- 新しい event は `gm_server/handlers/` にハンドラを追加し `gm_server/router.py` の `HANDLERS` に登録します。新しい action は `scripts/actions.js` の `ACTION_HANDLERS` に追加します。
- FVTT のグローバル (`game`, `ui`, `CONST` など) に触れないロジックは `scripts/lib/` に純粋関数として置き、`tests/js/` にテストを書いてください。
- AI やサーバー由来のテキストを HTML に埋め込むときは必ず `escapeHtml` / `textToHtml` を通してください。
- AI が投稿する `ChatMessage` には `generatedFlags()` を付けてください (ループ検知に使います)。
- `reference/` からの移植は [`docs/reference_porting_review.md`](docs/reference_porting_review.md) のフェーズ順に行います。
