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
├── AGENTS.md            # 本ドキュメント (AI エージェント向け仕様・規約)
├── module.json          # Foundry VTT モジュール定義ファイル
├── bridge.js            # FVTT 側 ESModule スクリプト (WebSocket クライアント)
├── ai_gm_server.py      # メインバックエンド (WebSocket サーバー + Gemini + edge-tts)
├── tools/
│   ├── test_send.py     # チャット & ダイス音送信の単体疎通テスト用スクリプト
│   ├── test_voice.py    # 音声生成 & 再生指示の単体疎通テスト用スクリプト
│   └── dungeon_generator.py # ダンジョン自動生成ツール (テキスト/グリッド出力)
└── docs/
    ├── specification.md # 詳細仕様書 (アーキテクチャ・プロトコル・シーケンス)
    ├── Todo.md          # 開発タスクリスト & ロードマップ
    ├── investigation_fvtt_dnd5e_control.md # FVTT & D&D5e 制御仕様調査レポート (シーン・マップ・トークン・ロール)
    ├── dungeon_generator_spec.md # ダンジョン自動生成ツール仕様書
    └── dungeon_math_investigation.md # ダンジョン生成の数学的モデリング調査・仕様化検討レポート
```

### 各ファイルの役割

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
# メインサーバー起動
python3 ai_gm_server.py

# 音声単体テスト用
python3 tools/test_voice.py

# チャット・ダイス音テスト用
python3 tools/test_send.py
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
- WebSocket メッセージプロトコルを変更する場合は、`bridge.js` と `ai_gm_server.py`（および必要に応じてテストスクリプト）の整合性を必ず同時に担保してください。
