# AI GM Bridge - 開発 Todo リスト

本ドキュメントは、`ai-gm-bridge` の実装状況、残課題、および今後のロードマップを管理する Todo リストです。

---

## 進行状況サマリー

| フェーズ | 概要 | 進捗状況 |
| :--- | :--- | :--- |
| **Phase 1** | コア通信・Gemini連携・基本音声再生 | **完了** (v1.0.0 相当) |
| **Phase 2** | **FVTT × agy サポートGM連携 & ダンジョンシーン自動構築** | **完了** (v1.1.0 相当) |
| **Phase 3** | FVTT インゲーム操作連携 (トークン/フォグ/D&D5eロール) | **調査完了・次期対応** |
| **Phase 4** | モジュール設定 UI・システム特化ルール | **企画中** |

---

## Phase 2: FVTT × agy サポートGM連携 & ダンジョンシーン自動構築

人間GMが FVTT 画面内（Ubuntu ターミナル操作不要）で `/ai` コマンドによりサポートGM (agy) へ指示を出し、チャット上の選択ボタンでパラメータを対話決定してダンジョンシーンを自動作成する機能。

- [x] **1. FVTT `/ai` スラッシュコマンド検知 (`bridge.js`)**
  - GM 発言から `/ai ` で始まるプロンプトを検知し、一般プレイヤーから隔離して WebSocket で `event: "ai_command"` をサーバーへ転送。
- [x] **2. チャット内インタラクティブ選択ボタンの実装 (`bridge.js`)**
  - サポートGM からの `action: "interactive_card"` を受け取り、選択肢ボタン付きの HTML カードをチャットログに描画。
  - `renderChatMessage` フックでボタンクリックを検知し、ワンクリックで選択内容 (`event: "ai_choice"`) をサーバーへ即時返送。
- [x] **3. `agy` (Antigravity Agent) サブプロセス連携ハンドラ (`ai_gm_server.py`)**
  - `agy -p "..." --conversation <id> --output-format json` を非同期サブプロセスで実行し、マルチターンの会話コンテキストを維持。
  - 通常のプレイヤー発言（Gemini + 音声）と GM 専用コマンド（agy サポートGM）のルーティング分離。
- [x] **4. サポートGM プロンプト & ツール実行パイプライン**
  - ダンジョン生成要求に対して、サイズ（小/中/大/特大）、部屋の密度（標準/密集/ギッシリ）、ループ率（袋小路のみ/標準/迷路多め）の対話型選択カードを段階生成。
  - パラメータ確定時に `tools/dungeon_generator.py` を呼び出してグリッドデータ（壁・床・スタート・ゴール）を生成。部屋の密度設定を部屋数・床面積・配置アルゴリズムに正確に反映。
- [x] **5. FVTT シーン自動構築 API 実装 (`bridge.js` & `ai_gm_server.py`)**
  - 生成されたダンジョンデータから、FVTT の `Scene.create` を呼び出して新マップシーンを自動構築。
  - v14 マルチレベル対応 (`levels` コレクションの `defaultLevel0000` 背景画像 `background.src`) によりマップ PNG 画像を正確に描画。
  - スタート地点 (緑光) / ゴール地点 (赤松明) の AmbientLight を自動配置。
  - チャットカードで「全員で移動」「GMのみ確認」ボタンを提供。
- [x] **6. 壁 (Wall) セグメント自動抽出 & 衝突・視界遮断の実装 (`dungeon_generator.py` & `bridge.js`)**
  - ダンジョンの床と壁セルの境界線を検出し、同一直線上の隣接辺を自動結合して最適な線分リストを抽出（100x100マスで約120本）。
  - `Scene.create` 時に `walls` ドキュメントを一括登録し、プレイヤー/トークンの壁すり抜け防止および動的視界 (Fog of War) の完全遮断を実現。
- [x] **7. 冪等セットアップスクリプトの実装 (`Setup.sh` & `requirements.txt`)**
  - 依存ライブラリ (`websockets`, `edge-tts`, `google-genai`) の自動検証・導入、agy 検出、権限設定をワンコマンド化。何度実行しても安全な冪等設計。
- [x] **8. 各部屋のフレーバーノート自動生成 & トークン進入時TTS読み上げ (`dungeon_generator.py`, `bridge.js`, `ai_gm_server.py`)**
  - ダンジョン生成時に全部屋へ固有のタイトル・情景描写・SVGアイコンを自動付与。
  - FVTT にジャーナル (`JournalEntry`) とマップノート (`Note`) を全室の中央に自動配置。
  - トークン移動フック (`updateToken`) により部屋進入を検知し、チャット描写投稿と `edge-tts` による自動音声ナレーションをシームレスに連動。
- [x] **9. 熟練DMノウハウに基づくAI 3層階層フレーバー補完システム (`ai_dungeon_flavor_spec.md`)**
  - [x] TRPG/DM設計原則（歴史の地層、ダンジョン生態系、空室の演出価値、Jaquaysing トポロジー）の調査・仕様書策定。
  - [x] `world.json` / `game.world.description`（「ゴブリンキングとブラックドラゴンの地下城塞」）の自動読み取りと世界観プロンプト補完。
  - [x] `dungeon_generator.py` に部屋グラフのトポロジー解析（次数 Degree、深さ Depth、閉路フラグ InCycle、面積）出力機能を追加。
  - [x] `tools/prompt_manager.py` による高速プロンプト補完（キー部屋優先選定・キャッシュ保持・応答速度最適化）。
  - [x] `ai_gm_server.py` にリアルタイム進行通知チャットおよび Agy 連携補完パイプラインを実装。
  - [x] `bridge.js` にフロア伝承・生態系ジャーナル、ロール別SVGアイコン、GM専用メモ、休息所バッジ、ワンクリック「設定録を開く」ボタンを統合。

---

## 1. コア機能 & 通信 (Networking & Foundation)

- [x] WebSocket による双方向通信の実装 (`bridge.js` ⇄ `ai_gm_server.py`)
- [x] GM クライアントのみ WebSocket 接続を開設するガード実装 (`game.user.isGM`)
- [x] チャット無限ループ防止（`AI GM` 発言および `/` コマンドの除外）
- [x] 切断時の自動再接続機能（5秒インターバル）
- [ ] WebSocket 接続状態の FVTT 画面上インジケーター（アイコン表示など）
- [ ] 複数プレイヤーの同時発言に対するリクエストキュー / バッファリング処理
- [ ] ローカル以外のネットワーク接続を想定した認証・トークン検証機構

---

## 2. AI GM 思考・対話エンジン (AI & Reasoning)

- [x] Google Gemini API (`gemini-3.8-flash`) 連携
- [x] 新版 SDK (`google-genai`) への移行完了
- [x] 構造化 JSON 出力制御 (`narrative`, `speech`, `dice`)
- [x] サーバー混雑 (503) に対する指数バックオフ自動リトライ (`query_gemini_with_retry`)
- [ ] **会話履歴 (マルチターン対話) の保持**
  - セッション全体の文脈を踏まえた GM 描写の実現
- [ ] **シナリオ情報・事前設定の注入**
  - シナリオテキスト、NPC 設定、ダンジョン情報のプロンプト読み込み
- [ ] **システム特化型ルール判定**
  - クトゥルフ神話TRPG (CoC 6版 / 7版) の技能判定・SANチェック
  - D&D 5e / ソード・ワールド2.5 等の目標値比較と成否判定
- [ ] **PC キャラクター情報の連携**
  - アクターシートの能力値・ステータスをコンテキストに含める

---

## 3. 音声・演出 (Audio & TTS)

- [x] `edge-tts` による日本語音声合成 (`ja-JP-NanamiNeural`)
- [x] FVTT `foundry.audio.AudioHelper.play` による自動音声再生
- [x] ブラウザの AudioContext サスペンド自動復帰処理
- [ ] **複数キャラクターの声色切り替え**
  - GM ナレーション、男性 NPC、女性 NPC での音声モデル動的変更
- [ ] **生成音声ファイルのクリーンアップ**
  - セッション終了時または起動時に古い `voice_*.mp3` を自動削除
- [ ] **BGM / 環境音の連動操作**
  - シーンの雰囲気に合わせた FVTT プレイリスト (Playlist) の自動再生・切替指示
- [ ] **ローカル TTS (Irodori-TTS) への切り替え**
  - Edge TTS は外部仕様変更で 403 になるリスクがあるため、ローカル GPU (RTX 4070 Ti) で動く日本語 TTS を導入する
  - 候補: [Irodori-TTS](https://github.com/Aratako/Irodori-TTS) v4.1-Small (MIT・約766M・絵文字で囁き/笑い等を指定可)。1回の生成は30秒までなので長文は分割
  - [ ] [Irodori-TTS-Server](https://github.com/Aratako/Irodori-TTS-Server) (OpenAI 互換 API) を WSL に導入する手順を `docs/` に追加 (uv / CUDA extra / 起動・常駐方法)
  - [ ] `gm_server/tts.py` に TTS バックエンド切り替え設定を追加 (`edge` / `irodori`、環境変数でサーバー URL・声を指定。接続できない場合は edge-tts にフォールバック)
  - [ ] `Setup.sh` / README / AGENTS.md の音声まわりの記述を更新

---

## 4. FVTT インゲーム自動化 (In-Game Controls)
> ※ 技術調査完了: 詳細は [`docs/investigation_fvtt_dnd5e_control.md`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/docs/investigation_fvtt_dnd5e_control.md) を参照

- [x] チャットメッセージ自動投稿 (`chat`)
- [x] ダイスロールの自動実行・結果投稿 (`roll`)
- [x] **インゲーム制御 API の技術調査** (シーン・マップ・トークン・D&D5eスキルロール)
- [ ] **シーン操作の実装**
  - 「部屋を移動する」宣言に応じたシーン（マップ）の自動切り替え (`change_scene`)
- [ ] **トークン / マップ操作の実装**
  - 敵トークンのスポーン（出現）、HP 増減、状態異常アイコン付与 (`spawn_token`, `move_token`)
  - マップ上の明かり (Light) やフォグ・オブ・ウォーの解除、暗さ・天候変更 (`update_map`)
- [ ] **ダイス結果に応じた分岐描写**
  - D&D5e スキルロール要求 (`request_skill`) および結果フック (`dnd5e.rollSkill`) による成否フィードバック連動

---

## 5. UI / 設定・運用 (Settings & UX)

- [ ] FVTT モジュール設定画面 (Settings Configuration)
  - WebSocket サーバーのアドレス/ポート設定
  - API キー入力欄（ブラウザ側または .env 連携）
  - デフォルト音声モデル・音量設定
- [ ] **GM 承認モード (Human-in-the-loop)**
  - AI GM の回答を即時投稿せず、GM が確認・修正してから投稿できるプレビュー UI
- [ ] **ログ・デバッグ機能**
  - 対話ログ・ダイス履歴のエクスポート機能 (Markdown / JSON)
- [x] 自動テスト (pytest / node:test) の整備（`tests/`。※`tools/test_*.py` は手動疎通検証用）
- [x] CI の整備（`.github/workflows/test.yml`。main は PR 必須・`test` 必須で保護）
- [ ] **reference/ 統合版の移植**（[`reference_porting_review.md`](reference_porting_review.md) の Phase 1〜6）
