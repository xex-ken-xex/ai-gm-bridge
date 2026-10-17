# AI GM Bridge システム仕様書

## 1. 基本情報

- **システム名称**: AI GM Bridge
- **モジュールID**: `ai-gm-bridge`
- **対象環境**:
  - Foundry Virtual Tabletop (FVTT): v11 〜 v14
  - Python: 3.12+ (現行環境: 3.14)
  - OS: Linux / WSL / macOS
- **目的**: Foundry VTT 上で動作するセッションにおいて、LLM (Google Gemini) を自律型ゲームマスター (AI GM) として組み込み、プレイヤーの発言に応じた情景描写、ダイス判定指示、音声合成による発話演出を自動で行う。

---

## 2. システム構成・アーキテクチャ

本システムは、FVTT 内で動作するクライアントモジュールと、外部で動作する Python WebSocket バックエンドサーバーの 2 層構成です。

```mermaid
flowchart TD
    subgraph FVTT ["Foundry VTT (ブラウザ/Electron)"]
        User["プレイヤー / GM"]
        ChatHook["createChatMessage Hook"]
        Bridge["bridge.js (WS Client)"]
        AudioHelper["foundry.audio.AudioHelper"]
        ChatLog["ChatMessage / Roll"]
    end

    subgraph Backend ["Python サーバー (ai_gm_server.py)"]
        WSServer["WebSocket Server (port 8765)"]
        TextClean["HTMLタグ除去・前処理"]
        GeminiClient["Google Gemini API (gemini-3.8-flash)"]
        TTS["edge-tts (ja-JP-NanamiNeural)"]
    end

    subgraph Storage ["ローカルストレージ"]
        DataDir["~/foundrydata/Data/ (音声MP3保存)"]
    end

    User -->|チャット発言| ChatHook
    ChatHook -->|メッセージ検知| Bridge
    Bridge -->|WebSocket: player_message| WSServer
    WSServer --> TextClean
    TextClean --> GeminiClient
    GeminiClient -->|構造化JSONレスポンス| WSServer
    WSServer -->|音声合成| TTS
    TTS -->|ファイル書き出し| DataDir
    WSServer -->|WS: chat| Bridge
    WSServer -->|WS: roll| Bridge
    WSServer -->|WS: play_sound| Bridge
    Bridge -->|描画・投稿| ChatLog
    Bridge -->|再生指示| AudioHelper
    DataDir -.->|音声ロード| AudioHelper
```

---

## 3. コンポーネント仕様

### 3.1 Foundry VTT モジュール側

| ファイル | 種別 | 説明 |
| :--- | :--- | :--- |
| [`module.json`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/module.json) | マニフェスト | モジュールメタデータ、対応バージョン、エントリーポイント定義 |
| [`bridge.js`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/bridge.js) | ESModule | WebSocket クライアント、FVTT API フック、イベントディスパッチャ |

#### 動作要件・権限設計
- **GM 権限限定実行**:
  - `game.user.isGM` が `true` のクライアントのみ WebSocket 接続を開設。
  - 一般プレイヤーのクライアントがサーバーと通信することによる重複実行や無駄な負荷を防止。
- **再接続機能**:
  - 切断時 (`onclose`) は 5 秒間隔で自動再試行。
- **オーディオコンテキスト自動復帰**:
  - ブラウザの自動再生ポリシーによるサスペンド状態 (`game.audio.context.state === "suspended"`) を検知し、再生直前に `resume()` を実行。

#### フック処理 (`createChatMessage`)
- **発言フィルタリング**:
  - ループ防止: 送信者のエイリアスが `AI GM` で始まる場合は送信除外。
  - コマンド除外: 先頭が `/` (スラッシュコマンド) や `[` (マクロ等) で始まるメッセージは除外。
  - 通常のプレイヤー会話のみを抽出してサーバーに転送。

---

### 3.2 Python バックエンドサーバー側

| ファイル | 役割 |
| :--- | :--- |
| [`ai_gm_server.py`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/ai_gm_server.py) | メイン WebSocket サーバー。Gemini API 呼び出し、edge-tts 音声合成、FVTT へのメッセージ配信 |
| [`tools/test_send.py`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/tools/test_send.py) | チャット・ダイス音送信の単体疎通テスト用スクリプト |
| [`tools/test_voice.py`](file:///home/kenic/foundrydata/Data/modules/ai-gm-bridge/tools/test_voice.py) | 音声生成と FVTT 再生指示の単体疎通テスト用スクリプト |

#### 使用技術・ライブラリ
- **WebSocket**: `websockets` (ポート: `8765`, ホスト: `0.0.0.0`)
- **LLM**: `google-genai` (モデル: `gemini-3.8-flash`)
- **TTS**: `edge-tts` (ボイスモデル: `ja-JP-NanamiNeural`)

#### エラーハンドリング & レジリエンス
- **HTML クリーニング**: FVTT チャットに含まれる `<p>...</p>` などの HTML タグを正規表現で事前除去。
- **API 再試行 (Exponential Backoff)**: Gemini API が 503 過負荷を返した場合、最大 3 回まで指数関数的待機時間（2秒、4秒）を設けて自動再試行。
- **agy の呼び出し**: `agy -p <プロンプト> --output-format json` に、`AI_GM_AGY_MODEL` があれば `--model`、`AI_GM_AGY_EFFORT` があれば `--effort` を付ける。
  agy は同時に複数起動すると応答が返らなくなることがあるため、同時実行は `AI_GM_AGY_CONCURRENCY` (既定 1) 本まで。分割した問い合わせも 1 本ずつ順番に投げる。
  - agy は空の作業フォルダ (`AI_GM_AGY_WORKDIR`、既定 `~/ai-gm-logs/agy-workspace`) で動かす。agy はエージェントなので、モジュールのソースがあるフォルダで動かすとファイルを読み回ることがある。
  - stdin はつながない (確認の入力を待ってサーバーの端末を読みに行かないように)。
  - agy は結果を出し終えたあと、プロセスが終了せずに残ることがある。標準出力に結果の JSON がそろった時点で受け取り、3 秒待っても終わらなければ止める。
  - 応答が止まったときの調査用に、agy 自身の動作ログを `--log-file` で今日のログフォルダ (`agy-<時刻>-<番号>.log`) に残す (`AI_GM_AGY_LOG=0` で止める)。
    実行中は 30 秒ごとにプロセスの状態・CPU 時間・子プロセスと出力の量を、標準エラー出力は届いた行ごとに詳細ログへ書く。打ち切ったときはそれまでの出力も残す。
- **詳細ログ**: `~/ai-gm-logs/<YYYY-MM-DD>/server.log` に書く (`gm_server/logs.py`)。FVTT はモジュールフォルダを Web で配信するため、Data フォルダの外に置く。
  - AI への問い合わせ (agy のコマンド・プロンプト全文) と応答 (全文・所要時間・終了コード) に `[AI #番号]` を付けて突き合わせられるようにする。
  - FVTT との送受信 (4000 文字まで) と、コンソールに出した行もすべて書く。
  - 日付が変わると新しいフォルダに切り替え、今日を含めて 10 日より古い日付フォルダは削除する。1 日分が 20MB を超えたら `server.log.1` … に回す。
  - コンソールとログファイルの各行には「日時 レベル」(INFO / WARNING / ERROR) を付ける。
  - `GEMINI_API_KEY` と、名前に KEY / TOKEN / SECRET / PASSWORD を含む環境変数の値、Google API キーの形の文字列は `***` に伏せる。
  - 環境変数: `AI_GM_LOG_DIR` (出力先)、`AI_GM_LOG_DAYS` (残す日数)、`AI_GM_LOG_MAX_BYTES` (1 ファイルの上限)。
- **再接続をまたぐ送信**: 処理中に依頼元の接続が閉じたら (FVTT の再読み込みなど)、いま繋がっている接続へ送る。
  どこにも繋がっていなければ最大 20 件まで溜め、次の接続時に送る (`progress` は溜めない)。`gm_server/context.py` の `ConnectionHub`。

---

## 4. 通信プロトコル & データフォーマット

### 4.1 FVTT → Python サーバー

#### プレイヤー発言イベント (`player_message`)
プレイヤーが FVTT チャット欄に通常発言を行った際に送信されます。

```json
{
  "event": "player_message",
  "user": "string (発言プレイヤーの表示名)",
  "content": "string (発言テキスト本文)"
}
```

#### GM `/ai` コマンドイベント (`ai_command`)
GM がチャット欄で `/ai <プロンプト>` を実行した際に送信されます。

```json
{
  "event": "ai_command",
  "user": "string (GM表示名)",
  "prompt": "string (AIへの指示内容)"
}
```

#### チャット内ボタン選択イベント (`ai_choice`)
GM が対話型チャットカード上の選択肢ボタンをクリックした際に送信されます。

```json
{
  "event": "ai_choice",
  "user": "string (GM表示名)",
  "value": "string (選択肢の内部識別値。例: 'dungeon_size:small')",
  "label": "string (選択肢の表示名。例: '小 (100x100マス)')"
}
```

#### シナリオビルダー (`builder_generate_stage` / `builder_build_scene`)
`/ai ビルダー` で開く画面から送信します。詳細は [scenario_builder_spec.md](scenario_builder_spec.md)。

```json
{
  "event": "builder_generate_stage",
  "stage": "region | settlement | wilderness | site_lore",
  "user_prompt": "GM 指示",
  "fixed_name": "GM が付けた名前 (無ければ空)",
  "world": { "title": "…", "description": "…", "references": [{ "name": "参照ジャーナル", "text": "…" }] },
  "parents": [{ "category": "region", "name": "…", "public_lore": "…", "dm_secrets": "…", "canon": ["…"], "open_threads": ["…"] }],
  "atlas_index": [{ "category": "settlement", "name": "…", "canon": ["…"] }],
  "actor_names": ["…"],
  "recent_cards": ["region.terrain.salt_flat"],
  "settlement_scale": "(settlement のみ)",
  "site_mode": "(site_lore のみ) ruins | underground", "size_key": "small | medium",
  "actors_context": { "pcs": [{ "name": "…", "info": "…", "bio": "…" }], "npcs": [] }
}
```

`builder_build_scene` は上記の文脈に加えて `scene_mode`, `name`, `public_lore`, `dm_secrets` を送ります。
旅路は `map` (`{"seed": …}`) と `landmarks`、遺跡は `size_key` (small / medium)・`density`・`loop_ratio`・`sub_objectives` を送ります。
サーバーは `create_dungeon_scene` で応答します。

集落と旅路の下書きは、AI の応答が長くタイムアウトしやすいため 2 回に分けて問い合わせます
(集落: 骨組み → 人物と秘密、旅路: 骨組みと地点の一覧 → 地点の描写を 5 地点ずつ。Gemini は並列、agy は 1 本ずつ)。FVTT に返す `builder_stage_result` の形は 1 回のときと同じです。

---

### 4.2 Python サーバー → FVTT

サーバーは Gemini の推論結果および音声合成の完了に応じて、以下のアクションメッセージを順次送信します。

#### 1. チャット描写 (`chat`)
情景描写やセリフを FVTT のチャットログに投稿します。

```json
{
  "action": "chat",
  "speaker": "string (表示名。既定値: 'AI GM')",
  "content": "string (チャット本文。リッチテキスト可)"
}
```

#### 2. ダイスロール (`roll`)
判定が必要な場合にダイスコマンドを実行させます。

```json
{
  "action": "roll",
  "speaker": "string (表示名。既定値: 'AI GM (判定)')",
  "formula": "string (ダイス計算式。例: '1d20', '2d6+3')"
}
```
※ サーバー側で `/r ` や `/roll ` プレフィックスを自動除去して送信します。

#### 3. 音声再生 (`play_sound`)
合成した音声ファイルまたはシステム効果音の再生を指示します。

```json
{
  "action": "play_sound",
  "src": "string (Dataディレクトリからの相対パス。例: 'voice_1728712345.mp3')",
  "volume": "number (音量 0.0〜1.0。既定値: 1.0)"
}
```

#### 4. インタラクティブ選択カード (`interactive_card`)
GM 向けに対話型の選択肢ボタンを含むチャットカードを表示します。

```json
{
  "action": "interactive_card",
  "title": "ダンジョン自動生成",
  "message": "生成するダンジョンの規模を選択してください：",
  "choices": [
    { "label": "小 (100x100マス)", "value": "dungeon_size:small" },
    { "label": "中 (500x500マス)", "value": "dungeon_size:medium" }
  ]
}
```

#### 部屋進入の読み上げと「到達済」の印
PC のトークン (プレイヤー所有、または所有者未設定の `character`) が部屋に入ると、FVTT がチャットに情景を出し `room_entered` をサーバーへ送って読み上げます。
読み上げた後、探索録ジャーナルの部屋ページの情景テキスト先頭に `<到達済>` を付けます。印があるページの部屋は読み上げません。GM が印を消せば、次に入ったときにまた読み上げます。

#### 4-2. 途中経過 (`progress`)
マップ生成・伝承・部屋の情景など待ち時間の長い処理の間、30 秒ごとに送ります。GM の画面に通知 (`ui.notifications.info`) として出し、チャットには残しません。

```json
{ "action": "progress", "message": "[部屋の情景] 完了 1/3 / 経過 30秒 / タイムアウトまで残り 570秒" }
```

待ち処理がタイムアウト (伝承・部屋の情景は agy 300 秒 / Gemini 120 秒、マップ生成 300 秒) を過ぎると、処理は続けたまま GM に `interactive_card` で延長を確認します。選択肢の値は `timeout_extend:<id>` (同じ秒数だけ延長) / `timeout_abort:<id>` (打ち切って既定の内容で続行) です。120 秒回答が無ければ打ち切ります。この回答はサーバーの受信キューを待たずに処理されます。

#### 5. ダンジョンシーン構築 (`create_dungeon_scene`)
生成されたマップ画像とパラメータを元に、FVTT 内部で `Scene.create` を実行させます。
`/ai ダンジョン` のウィザードは「舞台 (`dungeon_mode:<mode>`) → サイズ → 部屋密度 → 周回路」の順に選択カードを出します (野外は部屋密度を飛ばします)。

```json
{
  "action": "create_dungeon_scene",
  "scene_mode": "underground",
  "name": "地下迷宮 (小: 100x100)",
  "background_src": "dungeon_underground_100x100_1728712345.png",
  "grid_width": 100,
  "grid_height": 100,
  "grid_size": 100,
  "start_pos": [45, 60],
  "goal_pos": [80, 20],
  "walls": [[0, 0, 500, 0, "normal"], [500, 0, 500, 200, "door"]],
  "rooms": [{ "id": 1, "name": "部屋 1: …", "role": "start", "type": "start", "light_preset": "torch_warm", "ambient_sound_src": null, "cx": 350, "cy": 450, "grid_w": 4, "grid_h": 4 }]
}
```

- `scene_mode`: `underground` (暗さ 0.75) / `ruins` (暗さ 0.35) / `wilderness` (暗さ 0.35・全体光あり・壁なし)。省略時は `underground`。
- `walls[i][4]` (壁タイプ): `normal` / `door` / `secret_door` / `low_wall`・`rubble` (移動のみ遮り、視線・光・音は通す)。省略時は `normal`。
- `rooms[i].light_preset`: `torch_warm` / `bonfire_bright` / `magic_blue` / `acid_green` / `moonlight_dim` / `dark` / `null`。`null` で安全な休憩所なら焚き火を置きます。
- `rooms[i].ambient_sound_src`: 部屋の中心に置く環境音 (Data フォルダからの相対パス)。半径は部屋の長辺のマス数 × 1 マスの距離 × 0.85。

#### 6. シナリオビルダーの下書き (`builder_stage_result`)

```json
{
  "action": "builder_stage_result",
  "stage": "wilderness",
  "result": { "wilderness_title": "…", "public_lore": {}, "dm_secrets": {}, "landmarks": [{ "id": 0, "title": "…" }], "canon": [], "open_threads": [] },
  "cards": [{ "id": "wilderness.road.pilgrim", "axis": "道の性格", "text": "…" }],
  "seed": 1234,
  "fallback": false,
  "map": { "seed": 1234, "size_key": "small", "density": 0.4, "loop_ratio": 0.3, "points": 9 }
}
```

`map` は旅路のみ。`landmarks[i].id` は地点グラフの 0 始まりの id です。`fallback: true` は AI 応答が使えず骨組みを返したことを示します。
`create_dungeon_scene.lore.dm_secrets` があれば、探索録の伝承ページに DM 専用情報として表示します。

#### 7. シナリオビルダーの進捗 (`builder_progress`)
下書きの作成中、2 秒ごとに送ります。ビルダー画面の「⏳ AI が下書きを作成中です…」の行に、工程・完了数・経過・タイムアウトまでの残り秒を出します
(次の進捗が届くまでは画面側で 1 秒ずつ数え進めます。残りが 0 になったら、延長確認のカードを選ぶよう案内します)。

```json
{ "action": "builder_progress", "stage": "wilderness", "step": 2, "steps": 2, "step_label": "地点の描写",
  "detail": "完了 1/2", "elapsed": 40, "remaining": 260 }
```

---

## 5. シーケンスフロー

```mermaid
sequenceDiagram
    autonumber
    actor Player as プレイヤー
    participant FVTT as FVTT (bridge.js)
    participant Server as ai_gm_server.py
    participant Gemini as Gemini API
    participant TTS as edge-tts

    Player->>FVTT: チャット入力 ("部屋の奥を探索します")
    FVTT->>FVTT: フィルタチェック (GM発言/コマンド除外)
    FVTT->>Server: ws.send({"event": "player_message", "content": ...})
    Server->>Server: clean_html(content)
    Server->>Gemini: generate_content(SYSTEM_INSTRUCTION, content)
    Gemini-->>Server: JSON { narrative, speech, dice }
    
    opt speechが存在する場合
        Server->>TTS: Communicate.save(DATA_DIR/voice_<ts>.mp3)
        TTS-->>Server: 保存完了
    end

    Server->>FVTT: ws.send({"action": "chat", "content": narrative})
    FVTT->>Player: ChatMessage 表示

    opt diceが存在する場合
        Server->>FVTT: ws.send({"action": "roll", "formula": formula})
        FVTT->>Player: Roll 評価・ダイス結果チャット表示
    end

    opt 音声生成が行われた場合
        Server->>FVTT: ws.send({"action": "play_sound", "src": voice_filename})
        FVTT->>Player: AudioHelper.play() 音声再生
    end
```

---

## 6. 音声ファイル管理仕様

1. **保存場所**: `~/foundrydata/Data/` (`DATA_DIR`)
2. **命名規則**: `voice_<UNIX_TIMESTAMP>.mp3`
3. **再生指定**:
   - FVTT の `foundry.audio.AudioHelper.play` は `~/foundrydata/Data/` をルートとする相対 URL を要求するため、`src` にはファイル名（または相対パス）のみを渡す。
4. **クリーンアップ方針**:
   - セッション中に生成された古い一時音声ファイルは、将来的に定期削除またはセッション終了時に削除するメンテナンス処理を導入予定。

---

## 7. AI GM プロンプト & 出力スキーマ仕様

### システムプロンプト
```text
あなたはファンタジーTRPGの熟練ゲームマスター(GM)です。
プレイヤーの宣言を受け、以下のJSONフォーマットのみを出力してください。Markdownのコードブロック(```json)は不要です。

{
  "narrative": "チャット欄に表示する情景描写（セリフ含むリッチテキスト）",
  "speech": "音声読み上げ用テキスト（簡潔に1〜2文。記号や括弧は含めないこと）",
  "dice": "/r 1d20 などのダイスコマンド（判定が必要な場合のみ。不要なら null）"
}
```

### パラメータ設定
- `response_mime_type`: `"application/json"`
- 対象モデル: `gemini-3.8-flash`
