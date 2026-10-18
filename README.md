# AI GM Bridge (`ai-gm-bridge`)

Foundry Virtual Tabletop (FVTT) とローカル Python サーバーを WebSocket で接続し、AI をサポートGM（アシスタント）として活用するためのモジュールです。

---

## 主な機能（現在できること）

### 1. ダンジョン・マップの対話型自動生成
- チャット欄で `/ai ダンジョン生成` を入力すると、対話カード形式で **規模（小/中/大/特大）**、**部屋密度（標準/密集/ギッシリ）**、**周回路（袋小路のみ/標準/迷路多め）** を選択できます。
- パラメータに基づき、迷宮マップ画像 (PNG) と視界・衝突を遮断する壁 (Wall) を自動配置した新シーンが即座に構築されます。
- スタート地点（緑の微光）および最深部ゴール（赤の松明）の光源が自動設置されます。

### 2. 世界観と構造に基づく伝承・部屋フレーバーの自動補完
- ワールド設定（`world.json` の説明欄）や GM が入力したテーマ（例: `/ai 氷結した古代神殿のダンジョン`）を自動で読み込みます。
- **フロア伝承・生態系**: ダンジョンの歴史、現在巣食う勢力、五感環境（光・音・匂い・空気感）を生成し、フロア設定録ジャーナル (`JournalEntry`) として記録します。
- **部屋ごとの役割と空室**: 部屋の接続数（袋小路・通過路・ハブ交差点）を数学的に把握し、全体の約 25〜35% を安全な休憩所・空室として配分。全部屋に情景描写、GM専用メモ、対応する SVG アイコンを付与してマップピン (`Note`) を配置します。

### 3. トークン部屋進入時の情景表示 & 音声ナレーション
- プレイヤーやトークンが未探索の部屋に足を踏み入れると、チャット欄に情景描写カードが自動表示されます。
- 同時に `edge-tts`（日本語音声 `ja-JP-NanamiNeural`）により、五感に訴える情景ナレーションが自動読み上げされます。
- `AI_GM_TTS_ENGINE=irodori` にすると、声の説明文から声を作る Irodori-TTS で読み上げます（入れ方は [`docs/irodori_tts_setup.md`](docs/irodori_tts_setup.md)。繋がらないときは edge-tts に戻ります）。

### 4. サポートGMとの相談・NPCセリフ提案
- チャット欄で `/ai [相談・指示]` と入力することで、NPC のセリフ提案やロールプレイの相談、D&D 5e のルール確認などをサポートGM Geminiちゃん と行えます。
- （ダンジョン構築を意図しない一般的な質問は、自動的に相談モードとして処理されます）

### 5. プレイヤー発言への自動GM演出
- プレイヤーの通常発言に対し、AI GM が状況描写や NPC セリフを返し、必要に応じてダイスロールや音声再生を行います。

---

## 動作要件

- **OS**: Linux (Ubuntu / WSL2 など)
- **Foundry VTT**: v11 〜 v14（v14 マルチレベル背景に対応）
- **Python**: 3.10 以上 (Python 3.12 / 3.14 検証済)
- **agy (Antigravity CLI)**: サポートGM機能の実行に必須

---

## セットアップ手順

### 1. 環境セットアップ
リポジトリ直下の `Setup.sh` を実行します（何度実行しても安全な冪等スクリプトです）。

```bash
./Setup.sh
```

- Python パッケージ (`websockets`, `edge-tts`, `google-genai`) の導入確認
- `agy` CLI の検出・検証
- Data ディレクトリの書き込み権限確認
- 実行権限の付与

### 2. サーバーの起動
Python サーバーを起動し、WebSocket 待機状態にします。

```bash
python3 ai_gm_server.py
```

#### 対話式の起動スクリプト (`start_server.sh`)

どのコードでサーバーを動かすかを選んでから起動します。

```bash
./start_server.sh
```

```
  0) 最新の main を取ってきて起動 (Enter でもこれ)
  1) claude/xxxx      ← 検証したい PR のブランチ (新しい順に最大 10 件)
       10/18 06:59  コミットの説明
  ...
  c) 今のブランチのまま起動 (git を触らない)
  q) やめる
```

- 番号を入れると、`git fetch` のあとそのブランチをリモートと同じ内容にしてから起動します。
- 編集中のファイルがあるときは切り替えずに止まります (`c` なら今のまま起動できます)。
- 手元のブランチにリモートに無いコミットがあるときは、捨ててよいか確認します。
- コードが切り替わったら、FVTT を開いているブラウザを再読み込み (F5) してください。`module.json` が変わったときは F5 では反映されないので、FVTT の「セットアップに戻る」からワールドを起動し直してください (スクリプトが案内します)。`requirements.txt` が変わっていればライブラリも自動で更新します。

Windows のデスクトップから起動するときは、リポジトリにある `start-ai-gm.bat` をデスクトップにコピーしてください。ダブルクリックで上のメニューが開きます。中の `FVTT` は `wsl -l -v` に出る WSL のディストリビューション名なので、違う名前で入れている場合は書き換えてください。`bash -lic` の `-i` は、`~/.bashrc` の `GEMINI_API_KEY` を読み込むために付けています。

バックグラウンド常駐起動する場合:
```bash
nohup python3 ai_gm_server.py > ai_gm_server.log 2>&1 &
```

### 3. Foundry VTT 側の有効化
1. Foundry VTT をブラウザで開き、GM としてログインします。
2. ゲーム設定 > **「モジュールの管理」** で **「AI GM Bridge」** を有効化します。
3. GM ログイン時に自動でローカルサーバー (`ws://127.0.0.1:8765`) へ接続されます。

---

## チャットコマンド一覧

| コマンド | 説明 |
| :--- | :--- |
| `/ai ダンジョン生成` | 対話型選択カードを表示し、ダンジョンシーンを自動作成します。 |
| `/ai [テーマ]のダンジョン` | 指定したテーマ（例: `/ai 水没したドワーフ要塞`）を最優先してダンジョンを生成します。 |
| `/ai [相談・指示]` | NPC のセリフや行動、ルール相談などをサポートGMに問い合わせます。 |

---

## 自動テスト

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest   # サーバー側
npm test            # FVTT 側の純粋関数 (Node 18+)
```

---

## ディレクトリ構成

```text
ai-gm-bridge/
├── Setup.sh                  # 冪等セットアップスクリプト
├── start_server.sh           # 対話式のサーバー起動スクリプト (main / ブランチを選んで起動)
├── start-ai-gm.bat           # Windows から start_server.sh を開くバッチ (デスクトップにコピーして使う)
├── README.md                 # 本ドキュメント
├── AGENTS.md                 # エージェント・開発者向け詳細仕様書
├── module.json               # FVTT モジュール定義ファイル
├── bridge.js                 # FVTT クライアント側エントリーポイント
├── scripts/                  # FVTT クライアント側の実装 (actions / net / lib)
├── ai_gm_server.py           # WebSocket サーバー起動スクリプト
├── gm_server/                # サーバー実装 (LLM・音声・ハンドラ・ルーター)
├── tests/                    # 自動テスト (python: pytest / js: node:test)
├── requirements.txt          # Python 依存ライブラリ一覧
├── prompts/                  # プロンプトテンプレート (キャッシュ保持)
│   ├── dungeon_lore_prompt.txt   # ダンジョン伝承・生態系生成用
│   └── dungeon_rooms_prompt.txt  # 部屋トポロジー・情景補完用
├── tools/
│   ├── dungeon_generator.py  # 幾何学・壁・グラフ生成ツール
│   ├── prompt_manager.py     # プロンプト補完・ワールド情報取得
│   ├── test_voice.py         # 音声疎通テストスクリプト
│   └── test_send.py          # チャット・ダイス疎通テストスクリプト
└── docs/                     # 詳細設計・調査仕様書
    ├── specification.md      # システム全体設計書
    ├── Todo.md               # 開発タスクリスト
    ├── ai_dungeon_flavor_spec.md # AI 階層フレーバー生成仕様
    ├── reference_porting_review.md # reference/ 移植レビュー & 移植計画
    ├── dungeon_math_investigation.md # グラフ・数学モデリング調査
    └── investigation_fvtt_dnd5e_control.md # FVTT制御仕様調査
```
