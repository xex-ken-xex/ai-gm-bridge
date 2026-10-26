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
- **送り先の切り替え (NPC 会話)**: 発言者のトークン (`message.speaker.token`、無ければ発言ユーザーのキャラクターのトークン) から
  35ft 以内で、壁で音が遮られていない NPC (character 以外・プレイヤー非所有・非表示でない) がいれば `npc_dialogue_request`、
  いなければ `player_message` を送る。どちらか 1 経路だけ (二重応答を防ぐ)。発言者のトークンが分からないときは `player_message`。

#### アシスタント DM マスコット (`scripts/mascot.js`)
- GM・プレイヤー全員の画面に、ドラッグで動かせるミニキャラ「Geminiちゃん」(肩書き: 星詠みのサポートGM) を出す。`/ai` の返事の発言者名「サポートGM Geminiちゃん」と同じキャラクター (`config.SPEAKER_SUPPORT`。ループ検知の前方一致 `サポートGM` はそのまま)。
- 内蔵の SVG は状態ごとに顔を描き分ける: 待機 = 笑み、考え中 = 見上げる目と「…」、話し中 = 開いた口、聞いている = 横を向いた目と閉じた口、切断 = 閉じた目と「z」。
- 役 (思考・挙動・セリフ) をそろえる: `/ai` の返事 (`SUPPORT_GM_PERSONA`) もマスコットの台詞も、一人称「私」・「〜だよ」の口調の「星詠みのサポートGM」。世話焼きの張り切り屋 (「大丈夫？手伝おうか？」「任せて！」) で、ちょっとおっちょこちょい (ドジったら「ごめんね」と素直に謝る)。ドジなのは態度と言い回しだけで、答えの中身は的確・簡潔。位置はブラウザごとに `localStorage` (`ai-gm-mascot-pos`) に保存し、読めなければ既定の位置。
- 状態は 待機 (idle) / 考え中 (thinking) / 話し中 (speaking) / 聞いている (listening) / 切断 (offline)。
  「話し中」(口を開ける) はGeminiちゃん自身の発言 (発言者名が「サポートGM」で始まる `chat`・GM への確認・下書き・`speaker: "support"` の `play_sound`) だけ。AI GM のナレーション・NPC の声と反応・NPC の手番は「聞いている」。サーバーへの送信・サーバーからの受信・接続と切断で切り替わり、表示時間が過ぎたら待機に戻る (切断は再接続まで戻らない)。
- GM の画面での状態を `game.socket` (`module.ai-gm-bridge`、`sync_mascot`) で全員に配る。`module.json` の `"socket": true` が必要 (変更後は Foundry の再起動が要る)。
  ビルダーの下書き・途中経過・`/ai` の相談・GM への確認・接続の状態など、GM だけの操作は状態も台詞も配らない。受信側は GM からのものだけを受け付け、台詞は `textContent` で表示する。
- GM の画面では、サーバーにつながるまで「切断」で表示する (Python サーバーを起動していないと気づけるように)。
- クリック (左でも右でも。ドラッグとは 5px 以上動いたかで分ける) でメニューを出す。GM は「シナリオビルダー」「NPC ブレイン管理」「冒険記録を更新」(サーバー接続時)、全員が「あいさつ」「位置を戻す」(保存した位置も消す)「マスコットを隠す」(設定「マスコットを表示」を切る)。
  メニューの外を押すか Esc で閉じる。ほかの機能は `registerMascotMenuItem({ id, icon, label, gmOnly, onSelect })` で項目を足せる (あいさつの後ろに入る)。
- サーバーと繋ぐ GM のメニューには「⚙️ AI の設定」も出る (`registerAiSettingsMenu`)。押すと `ai_settings_request` を送り、返ってきた `ai_settings` で設定画面を開く。画面では接続先 (名前・種類・モデル・URL・API キーの環境変数の名前・待ち上限・同時実行) と作業ごとの接続先 (試す順に「→」区切り) を書き換え、「保存する」で `ai_settings_save` を送る。API キーは「設定済み / 未設定」だけを表示し、値は画面にもやり取りにも出さない。
- サーバーと繋ぐ GM のメニューには「声のテスト (ナレーター)」「声のテスト (Geminiちゃん)」も出る (`registerVoiceTestMenu`)。押すと `voice_test` を送り、見本のセリフが GM の画面でだけ鳴る (`play_sound` の `gm_only`)。どのエンジン (Irodori-TTS / edge-tts) で鳴ったか、Irodori で作れなかった理由、使った声 (参照音声・説明文・種) は GM の画面の通知 (`progress`) に出る。テストのときは、Irodori に失敗したあとの edge-tts だけの間 (`AI_GM_IRODORI_RETRY_AFTER`) を待たずにもう一度 Irodori を試す。
- ウィンドウ (シート・ビルダー・ジャーナル) より下、キャンバスと画面の枠より上に出す (`z-index: 70`)。
- 設定: 「マスコットを表示」(ユーザーごと)、「マスコットの画像」と状態ごとの「マスコットの画像 (考え中 / 話し中 / 聞いている / 切断)」(ワールド。空欄なら共通の画像、それも空なら内蔵の SVG)。

#### フック処理 (`combatTurnChange` / `updateCombat`)
- **NPC の自律戦闘**: 戦闘トラッカーで手番が NPC (character 以外・プレイヤー非所有・非表示でない・HP 0 や戦闘不能・行動不能の状態でない) に
  回ったら、その NPC の知覚を集めて `npc_turn_request` を送る。同じ手番には 1 回だけ (v12 以降の `combatTurnChange` と v11 の `updateCombat` の両方で拾う)。
- 設定「NPC の自律戦闘」(ワールド設定、既定は有効) で止められる。
- AI が動かしている NPC のアイテムのカードなどは、発言として `player_message` / `npc_dialogue_request` に回さない。

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
- **TTS**: `edge-tts` (ボイスモデル: `ja-JP-NanamiNeural`)。`AI_GM_TTS_ENGINE=irodori` で Irodori-TTS-Server (OpenAI 互換 API) に切り替えられる。Irodori で作れないときは edge-tts で作り直し、その後 `AI_GM_IRODORI_RETRY_AFTER` 秒は edge-tts だけを使う (切り替わった最初の 1 回だけ GM の画面に `progress` で知らせる)。起動時に `/health` で繋がるかを確かめて表示し、繋がれば裏で短いあいさつを 1 回作らせてモデルを読み込ませる (音声は捨てる)。繋がらなければ最初の読み上げから短い待ち上限にする。入れ方は [`irodori_tts_setup.md`](irodori_tts_setup.md)

#### AI の接続先の振り分け
作業ごとに、どの AI に頼むかを設定ファイルで決められます (`gm_server/ai_routing.py`・接続先は `gm_server/ai_providers.py`)。

- **設定ファイル**: `~/.config/ai-gm-bridge/ai_providers.json` (`AI_GM_AI_CONFIG` で変更)。`ai_providers.sample.json` (例と手順を `_` で始まる項目に書いてある。`_` で始まる項目は読み込むときに無視する) を写して作るか、FVTT の「⚙️ AI の設定」画面で保存して作る。モジュールのフォルダ (`Data/modules/ai-gm-bridge/`) は Foundry がブラウザに配信するため、そこに置いた `ai_providers.json` は読まずに警告する。無ければ今までどおり (Gemini のキーがあれば Gemini → agy、サポートGM の相談とスキル判定は agy)。
- **接続先 (`providers`)**: 名前を付けて並べる。`type` は次のどれか。共通で `timeout` (秒) と `concurrency` (同時に投げる数) を書ける。

  | `type` | 内容 | 主な項目 |
  | :--- | :--- | :--- |
  | `gemini` | Gemini API | `model`、`api_key_env` (キーを入れた環境変数の名前。既定 `GEMINI_API_KEY`) |
  | `agy` | agy CLI (今までの呼び方) | モデル・effort・同時実行数は `AI_GM_AGY_*` の環境変数で決まる |
  | `claude_code` | Claude Code CLI (`claude -p --output-format json --tools "" --strict-mcp-config`。プロンプトは標準入力。プロンプトにプレイヤーの発言が入るため、道具と MCP サーバーは持たせない) | `model` (`sonnet` など)、`command` (既定 `claude`)、`extra_args`、`workdir` (既定 `~/ai-gm-logs/claude-workspace`)。会話は `--resume` で続ける。打ち切りのときは `claude` が起こした子プロセスごと止める。先に `claude` を一度起動してログインしておく (API キーなら `ANTHROPIC_API_KEY`) |
  | `openai` | OpenAI 互換の Chat Completions API (LM Studio・Ollama・OpenAI など) | `base_url` (既定 `http://localhost:1234/v1`)、`model`、`api_key_env` (要るときだけ)、`temperature`、`json_response_format` (`true` で `response_format: json_object` を付ける)。会話はサーバーの中で直近 20 件まで覚える |

- **作業ごとの接続先 (`tasks`)**: 作業の名前に接続先の名前 (並べるとフォールバックの順) を書く。書いていない作業は `default` を使う。キーが無いなど使えない接続先は飛ばす。

  | 作業 | 内容 |
  | :--- | :--- |
  | `ai_gm` | AI GM の語り (プレイヤーの発言への応答) |
  | `support_gm` | サポートGM Geminiちゃん への相談 |
  | `skill_router` | `/ai` のスキル判定 |
  | `builder` | シナリオビルダー (各段階と、区画・地点の描写) |
  | `dungeon` | ダンジョン生成 (伝承・部屋の情景) |
  | `npc_dialogue` | NPC の会話 |
  | `npc_turn` | NPC の戦闘の手番 |
  | `adventure_log` | 冒険記録 |

- **API キー**: 設定ファイルには環境変数の名前 (`api_key_env`) を書き、値は書かない。設定ファイルに `api_key` を直接書いても使わず、起動時に警告する (その値はログでも伏せる)。起動時の表示と `python3 -m gm_server.ai_routing` (いまの振り分けを表示) では「設定済み / 未設定」だけを出す。
- 分割した問い合わせ (部屋の情景など) の同時実行数と 1 回の待ち上限は、その作業で最初に試す接続先の `concurrency` / `timeout` を使う。

#### エラーハンドリング & レジリエンス
- **HTML クリーニング**: FVTT チャットに含まれる `<p>...</p>` などの HTML タグを正規表現で事前除去。
- **API 再試行 (Exponential Backoff)**: Gemini API が 503 過負荷を返した場合、最大 3 回まで指数関数的待機時間（2秒、4秒）を設けて自動再試行。
- **agy の呼び出し**: `agy -p <プロンプト> --output-format json` に、`--model` (`AI_GM_AGY_MODEL`、既定 `gemini-3.8-flash-medium`。空文字にすると付けない)、`AI_GM_AGY_EFFORT` があれば `--effort` を付ける。
  agy は同時に複数起動すると応答が返らなくなることがあるため、同時実行は `AI_GM_AGY_CONCURRENCY` (既定 1) 本まで。分割した問い合わせも 1 本ずつ順番に投げる。
  - agy は空の作業フォルダ (`AI_GM_AGY_WORKDIR`、既定 `~/ai-gm-logs/agy-workspace`) で動かす。agy はエージェントなので、モジュールのソースがあるフォルダで動かすとファイルを読み回ることがある。
  - stdin はつながない (確認の入力を待ってサーバーの端末を読みに行かないように)。
  - agy は結果を出し終えたあと、プロセスが終了せずに残ることがある。標準出力に結果の JSON がそろった時点で受け取り、3 秒待っても終わらなければ止める。
  - agy は新しいプロセスグループで起動し、止めるとき (打ち切り・時間切れ・結果のあと終わらないとき) は agy が起こした子プロセスもまとめて止める。
  - 応答が止まったときの調査用に、agy 自身の動作ログを `--log-file` で今日のログフォルダ (`agy-<時刻>-<番号>.log`) に残す (`AI_GM_AGY_LOG=0` で止める)。
    実行中は 30 秒ごとにプロセスの状態・CPU 時間・子プロセスと出力の量を、標準エラー出力は届いた行ごとに詳細ログへ書く。打ち切ったときはそれまでの出力も残す。
- **詳細ログ**: 下の「ログ」を参照。
- **再接続をまたぐ送信**: 処理中に依頼元の接続が閉じたら (FVTT の再読み込みなど)、いま繋がっている接続へ送る。
  どこにも繋がっていなければ最大 20 件まで溜め、次の接続時に送る (`progress` は溜めない)。`gm_server/context.py` の `ConnectionHub`。

#### ログ

サーバーの動きはコンソールと詳細ログファイルの両方に出します (`gm_server/logs.py`・`gm_server/progress.py`)。

**置き場所と残し方**

```text
~/ai-gm-logs/                      # AI_GM_LOG_DIR。FVTT はモジュールフォルダを Web で配信するため Data フォルダの外に置く
├── 2026-10-17/                    # 日付フォルダ。日付が変わると新しいフォルダに切り替える
│   ├── server.log                 # 詳細ログ (20MB を超えたら server.log.1 … server.log.20 に回す)
│   └── agy-002510-0001.log        # agy 自身の動作ログ (agy-<時刻>-<番号>.log。AI_GM_AGY_LOG=0 で出さない)
└── agy-workspace/                 # agy を動かす空のフォルダ (ログではない)
```

- 今日を含めて 10 日 (`AI_GM_LOG_DAYS`) より古い日付フォルダは、起動時と日付が変わったときに削除する。
- 各行に「日時 レベル」(INFO / WARNING / ERROR) を付ける。コンソールも同じ形。
- `GEMINI_API_KEY` と、名前に KEY / TOKEN / SECRET / PASSWORD を含む環境変数の値、Google API キーの形の文字列は `***` に伏せる (ファイルにもコンソールにも出さない)。
  - 形で分かるキー (Google の `AIza…`、OpenAI / Anthropic の `sk-…`・`sk-ant-…`・`sk-proj-…`) と、`key=…`・`Authorization: Bearer …`・`x-api-key: …`も伏せる。
  - JSON・YAML・`.env`・設定ファイルの「名前: 値」/「名前=値」で、名前が認証の語 (`api_key`・`access_key`・`secret_key`・`private_key`・`client_secret`・`auth_token`・`access_token`・`refresh_token`・`password`) で終わるもの (`anthropic_api_key`・`db_password` のような接頭辞つきも) の値も伏せる。値は `"…"`・`'…'`・引用符なしのどれでもよい。数字や `true` / `null` は残す。シナリオの `"ancient_secret"`・`"size_key"` や `max_tokens`・`token_count` は伏せない。
  - 設定ファイルなど環境変数以外から読んだキーは `logs.register_secret` で登録すると伏せる。
  - agy 自身の動作ログ (`agy-*.log`) はサーバーを通らずに書かれるため、agy が終わるたび (打ち切りでも) と、サーバーの起動時に、ログフォルダの `agy-*.log` を書き直して伏せる。

**グループ分け (全体 → 工程 → 細かい作業)**

時間のかかる処理は 3 段に分けて出します。

| 印 | 段 | 例 |
| :--- | :--- | :--- |
| `■` | 全体の処理の始まりと終わり | `■ シナリオビルダー: 地下迷宮『塔』のシーンを構築します (AI、small、seed=8)` / `■ … シーン構築を終えました (95秒)` |
| `  ▶` | 全体の中の工程 | `  ▶ [1/2 マップ生成] マップを作りました (24 区画、壁 310 本、画像 …)` / `  ▶ [ビルダー: 集落 1/2 骨組み] AI に問い合わせます (プロンプト 5200 文字、上限 300秒)` |
| `    ・` | 工程の中の細かい作業 | `    ・[区画の情景] 区画 3・5・8・12 の情景を作成中 (12 区画中 4 区画済み)` / `    ・区画 3・5・8・12 の情景をシーンに反映しました (8/12 区画)` |

待ちの長い工程の間は、コンソールの最後の行を 1 秒ごとに書き換えて `[ラベル] いまの作業 / 経過 / タイムアウトまで残り` を出します (詳細ログには書かない)。
FVTT のビルダー画面にも同じ内容を 2 行で出します (`builder_progress`。1 行目が全体、2 行目がいまの作業)。

**詳細ログだけに書くもの**

- AI への問い合わせ: エンジン・モデル・agy のコマンド・プロンプト全文。`[AI #番号]` を付け、応答と突き合わせられるようにする。
- AI の応答: 全文・所要時間・終了コード。失敗・打ち切りのときはそれまでの出力。
- agy の様子: 標準エラー出力の各行と、30 秒ごとのプロセスの状態 (CPU 時間・子プロセス・出力の量)。結果を出したのに終わらないときは「終了しないため止めました」。
- FVTT との送受信 (「受信」「送信」。4000 文字まで。`builder_progress` と `progress` は書かない)。

**困ったときの見方**

1. コンソールで `■` の行を探し、どの処理で止まったかを見る。
2. その下の `▶` と `・` で、どの工程・どの区画までできたかを見る。
3. AI の問題なら、`server.log` で同じ `[AI #番号]` の問い合わせと応答を見る。agy なら同じ時刻の `agy-*.log` も見る。

#### 環境変数

すべて省略できます (既定値で動きます)。定義は `gm_server/config.py`。サーバーを起動する端末で `export 名前=値` してから `python3 ai_gm_server.py` を実行します。

| 環境変数 | 既定値 | 内容 |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | (なし) | Gemini API のキー。設定すると Gemini を直接使い、無ければ agy CLI を使う。ログには `***` で伏せて書く |
| `AI_GM_GEMINI_MODEL` | `gemini-3.8-flash` | Gemini を直接使うときのモデル |
| `AI_GM_AI_CONFIG` | `~/.config/ai-gm-bridge/ai_providers.json` | AI の接続先の振り分け設定ファイル (無ければ既定の振り分け) |
| `AI_GM_GEMINI_TIMEOUT` | `120` | Gemini の 1 回の問い合わせの待ち上限 (秒) |
| `AI_GM_LLM_CONCURRENCY` | `3` | Gemini に同時に投げる問い合わせの数 (部屋の情景などの分割分) |
| `AI_GM_AGY_MODEL` | `gemini-3.8-flash-medium` | agy に `--model` で渡すモデル (`agy models` で一覧)。空文字にすると `--model` を付けず agy 自身の既定を使う |
| `AI_GM_AGY_EFFORT` | (なし) | agy に `--effort` で渡す推論の深さ (low / medium / high / xhigh / max) |
| `AI_GM_AGY_TIMEOUT` | `300` | agy の 1 回の問い合わせの待ち上限 (秒)。過ぎると延長の確認カードを出す |
| `AI_GM_AGY_CONCURRENCY` | `1` | agy を同時に動かす数。2 以上にすると応答が返らなくなることがある |
| `AI_GM_AGY_WORKDIR` | `~/ai-gm-logs/agy-workspace` | agy を動かす空のフォルダ |
| `AI_GM_AGY_LOG` | `1` | `0` で agy 自身の動作ログ (`agy-<時刻>-<番号>.log`) を残さない |
| `AI_GM_MAPGEN_TIMEOUT` | `300` | マップ生成の待ち上限 (秒) |
| `AI_GM_DATA_DIR` | `~/foundrydata/Data` | FVTT の Data フォルダ。マップ画像と音声をここに書き出す |
| `AI_GM_TTS_ENGINE` | `edge` | 音声エンジン。`edge` (edge-tts) か `irodori` (Irodori-TTS-Server。繋がらないときは edge-tts に戻す) |
| `AI_GM_IRODORI_URL` | `http://127.0.0.1:8088` | Irodori-TTS-Server の URL |
| `AI_GM_IRODORI_MODEL` | `irodori-tts` | `/v1/audio/speech` に渡すモデル名 (サーバーの `IRODORI_MODEL_NAME`) |
| `AI_GM_IRODORI_VOICE` | `none` | 参照音声の ID (サーバーの voices フォルダのファイル名)。`none` なら声の説明文だけで作る |
| `AI_GM_IRODORI_API_KEY` | (なし) | Irodori-TTS-Server を `IRODORI_API_KEY` 付きで起動したときの同じ値。ログには `***` で伏せて書く |
| `AI_GM_IRODORI_TIMEOUT` | `20` | Irodori の 1 回の音声生成の待ち上限 (秒)。描写のチャットは音声ができてから送るため短めにする |
| `AI_GM_IRODORI_FIRST_TIMEOUT` | `180` | 最初の 1 回の待ち上限 (秒)。最初の音声生成でモデルを読み込むため長めにする。起動時に裏であいさつを 1 回作らせてこの待ちを使い切るので、実際の読み上げは最初から `AI_GM_IRODORI_TIMEOUT` になる |
| `AI_GM_IRODORI_HEALTH_TIMEOUT` | `5` | 起動時に `/health` を確かめるときの待ち上限 (秒) |
| `AI_GM_IRODORI_RETRY_AFTER` | `60` | Irodori に失敗したあと edge-tts だけを使う時間 (秒) |
| `AI_GM_IRODORI_CAPTION_FEMALE` | 落ち着いた大人の女性の声。… | 女性の NPC (ボイスが Keita 以外) の声の説明文 (VoiceDesign)。`AI_GM_IRODORI_CAPTION_NARRATOR` が空ならナレーターもこの声 |
| `AI_GM_IRODORI_CAPTION_NARRATOR` | (`AI_GM_IRODORI_CAPTION_FEMALE` と同じ) | ナレーターの声の説明文。設定するとナレーターだけ女性の NPC と別の声にできる |
| `AI_GM_IRODORI_CAPTION_SUPPORT` | 明るく元気な若い女性の声。… | サポート GM (Geminiちゃん) の声の説明文。ナレーターの声の設定には影響されない |
| `AI_GM_IRODORI_CAPTION_MALE` | 落ち着いた大人の男性の声。… | 男性の NPC (ボイスが Keita) の声の説明文 |
| `AI_GM_IRODORI_SEED` | `1234` | 声の揺れを抑える乱数の種。ナレーターはこの値、NPC はこの値とボイス名・声の高さ・説明文から決めた値。空にすると毎回変わる |
| `AI_GM_IRODORI_NARRATOR_VOICE` | (なし) | ナレーターの参照音声の ID (Irodori-TTS-Server の voices フォルダのファイル名)。設定するとナレーターはこの声だけで作る (説明文は付けない) |
| `AI_GM_HOST` | `127.0.0.1` | WebSocket サーバーの待ち受けアドレス。bridge.js は `ws://127.0.0.1:8765` に繋ぐので、LAN に開く必要はない (認証が無いため `0.0.0.0` にしない。Origin の確認はブラウザにしか効かず、ブラウザ以外のプログラムは Origin を偽れる) |
| `AI_GM_ALLOWED_ORIGINS` | (なし) | 接続を受けるページ (Origin) をカンマ区切りで足す。`localhost`・ループバックの IP のページと、Origin の無い接続 (ブラウザ以外) はいつでも受ける。ほかは接続直後に 1008 で閉じる |
| `AI_GM_PORT` | `8765` | WebSocket サーバーのポート |
| `AI_GM_LOG_DIR` | `~/ai-gm-logs` | 詳細ログの出力先 (日付フォルダを作る) |
| `AI_GM_LOG_DAYS` | `10` | 詳細ログを残す日数 (今日を含む) |
| `AI_GM_LOG_MAX_BYTES` | `20971520` (20MB) | 1 日分のログファイルの上限。超えたら `server.log.1` … に回す |

#### ナレーターの声を好みの声にする (Irodori-TTS)

ナレーター (話し手を指定しない描写の読み上げ) の声は、次の優先順で決まる。上ほど声が安定する。

1. `AI_GM_IRODORI_NARRATOR_VOICE` があれば、その参照音声だけで作る (説明文は付けない。食い違うと声が不安定になるため)。
2. なければ `AI_GM_IRODORI_CAPTION_NARRATOR` の説明文で作る。
3. それも空なら `AI_GM_IRODORI_CAPTION_FEMALE` の説明文で作る (女性の NPC と同じ声になる)。

乱数の種は、どの場合も `AI_GM_IRODORI_SEED` を使う。説明文が同じでも種が違えば別の声になり、同じ種でも文の長さが違えば声は少し揺れる。

好みの声にする手順 (コマンドは [`irodori_tts_setup.md`](irodori_tts_setup.md) の「6. ナレーターの声を好みの声にする」):

1. **声の説明文を決める**: 「若い男性の声。低めで落ち着いた、ささやくような語り口。」のように、性別・年齢・高さ・話し方を書く。Windows の PowerShell から Irodori-TTS-Server に直接頼んで聴き比べ、気に入った文を `AI_GM_IRODORI_CAPTION_NARRATOR` に入れる。
2. **種を選ぶ**: 同じ説明文で種だけを変えた音声を何本か作って聴き比べ、気に入った番号を `AI_GM_IRODORI_SEED` に入れる。
3. **参照音声にする** (いちばん確実): 気に入った 1 本 (または自分で録った声、数秒〜2 分) を Irodori-TTS-Server の `voices` フォルダに `narrator.mp3` などの名前で置き、`AI_GM_IRODORI_NARRATOR_VOICE=narrator` にする。`voices` フォルダは置いた時点で使われる (Irodori の再起動は不要)。

環境変数を変えたら AI GM のサーバーを再起動する。名前が `voices` フォルダのファイルと合わないと Irodori が失敗し edge-tts で読み上げるため、起動時のあいさつ (モデルの読み込み) に失敗したときのログに `AI_GM_IRODORI_NARRATOR_VOICE` の名前を確かめるよう書く。NPC と Geminiちゃん (シーン完成などの報告) の声はこれらの設定の影響を受けない (`AI_GM_IRODORI_SEED` を変えたときだけ種も変わる)。Geminiちゃんは `AI_GM_IRODORI_CAPTION_SUPPORT` の説明文で話す (edge-tts のときは `ja-JP-NanamiNeural`)。

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

#### NPC 会話イベント (`npc_dialogue_request`)
発言者の声が届く NPC (近い順に最大 4 人) がいるときに、`player_message` の代わりに送ります。NPC の知識・秘密・記憶は actor の flag `npc_brain` (`/ai NPC` の画面で編集)。

```json
{
  "event": "npc_dialogue_request",
  "speaker_name": "アリア", "speaker_token_id": "トークン id", "scene_id": "シーン id",
  "player_input": "帳簿を見せてくれ",
  "room_name": "発言者がいる部屋 (部屋の外ならシーン名)", "room_flavor": "部屋の情景",
  "check_result_context": null,
  "listening_npcs": [{
    "id": "トークン id", "name": "エドガー", "distance_ft": 5,
    "goal": "…", "trust": 0, "public_known": "…", "secret_known": "…", "ignorance": "…",
    "short_term_memory": ["…"], "long_term_memory": ["[うろ覚え] (重要度:4) …"],
    "voice": { "name": "ja-JP-KeitaNeural", "rate": "+0%", "pitch": "+0Hz" }
  }]
}
```

判定ボタンで振った結果を受けたときは、`check_result_context` に「説得判定 成功 (出目の合計 16 / 目標値 14)。打ち明けてよい内容: …」を入れて同じ形で送ります
(ヒントと目標値は GM のクライアントが覚えている値を使い、プレイヤー側の値は使いません)。

#### NPC の手番イベント (`npc_turn_request`)
戦闘で NPC の手番になったときに送ります。`combat` は頼んだときの手番で、サーバーはそのまま `execute_npc_turn` に付けて返します。

```json
{
  "event": "npc_turn_request", "scene_id": "戦闘のシーン id",
  "combat": { "id": "戦闘 id", "round": 2, "turn": 3 },
  "perception": {
    "token_id": "手番の NPC のトークン id", "npc_name": "オークの戦士", "npc_role": "humanoid",
    "npc_bio": "伝記 (HTML を除いて 300 文字まで)", "npc_goal": "npc_brain の目的",
    "hp_current": 4, "hp_max": 15, "health_status": "健在・余裕あり | 負傷・消耗 | 瀕死・危機的 | 倒れている",
    "move_speed": 30, "available_actions": [{ "name": "グレートアックス", "type": "weapon | feat | spell" }],
    "room_name": "NPC がいる部屋 (部屋の外ならシーン名)", "room_flavor": "…", "room_gm_notes": "…",
    "perceived_targets": [{ "token_id": "…", "name": "アリア", "distance_ft": 5, "adjacent": true,
                            "has_los": true, "health_status": "…" }],
    "voice": { "name": "ja-JP-KeitaNeural", "rate": "+0%", "pitch": "+0Hz" }
  }
}
```

- 距離は 5e の数え方 (斜めも 1 マス) で、大きいトークンは端どうし。`has_los` は戦闘のシーンをキャンバスに表示しているときだけ判定し、それ以外は `null`。

#### GM `/ai` コマンドイベント (`ai_command`)
GM がチャット欄で `/ai <プロンプト>` を実行した際に送信されます。

```json
{
  "event": "ai_command",
  "user": "string (GM表示名)",
  "prompt": "string (AIへの指示内容)"
}
```

サーバーは「判定プロンプト (`prompts/skill_router_prompt.txt`) + スキル一覧 (`gm_server/skills.py`) + GM の文」を agy に 1 回だけ送り、返ってきた JSON で振り分けます (スキル判定)。

| type | サーバーの動作 |
| :--- | :--- |
| `skill` | そのスキルを実行する。ダンジョン生成スキルはウィザードの選択カード (`scene_mode` が読み取れたらサイズから)、地域作成・町生成・旅路作成・シナリオ作成スキルは `open_builder`、冒険記録スキルは `adventure_log_collect`、ヘルプはスキル一覧のチャット。準備中のスキル (NPC作成スキル) は準備中の案内と、agy が書いた文章での手伝い |
| `clarify` | 候補を `interactive_card` で聞き返す。ボタンの値は `ai_skill:<カード番号>:<選択肢の番号>` で、`ai_choice` で戻るとそのスキルを実行する。最新でないカードのボタンは「古い選択肢」と知らせるだけ |
| `answer` | 回答をサポートGM のチャットに出す |

- 決まった言い方 (`/ai ダンジョン生成`・`/ai 記録を作って` など、キーワード判定の冒険記録・ダンジョン生成に当たる文) は agy を通さずにすぐ動かします。
- 判定の問い合わせは `AI_GM_ROUTER_TIMEOUT` (既定 45 秒) で打ち切り、応答が無かったことをチャットで知らせます。
- JSON として読めない応答は、そのまま回答として表示します (途中で切れた JSON なら `text` の中身だけ)。一覧に無いスキルは回答扱いにします。
- `/ai` だけならスキル一覧を出します (agy は呼びません)。`/ai ビルダー` と `/ai NPC` は今までどおり FVTT 側だけで画面を開きます。

#### 声のテストイベント (`voice_test`)
GM がマスコットのメニューで「声のテスト」を押したときに送ります。サーバーは見本のセリフを今の音声エンジンで読み上げ、`gm_only` 付きの `play_sound` と、結果を書いた `progress` を返します。

```json
{
  "event": "voice_test",
  "voice": "narrator | support (ナレーターか Geminiちゃんの声。既定値: narrator)"
}
```

#### AI の設定画面 (`ai_settings_request` / `ai_settings_save`)
GM がマスコットのメニューで「⚙️ AI の設定」を押すと `ai_settings_request` を送ります。サーバーは今の振り分けを `ai_settings` で返します。
「保存する」を押すと `ai_settings_save` を送ります。

```json
{
  "event": "ai_settings_save",
  "providers": { "名前": { "type": "gemini | agy | claude_code | openai", "model": "…", "base_url": "…", "api_key_env": "環境変数の名前", "timeout": "秒", "concurrency": "本数", "json_response_format": true } },
  "tasks": { "default": ["名前", "…"], "npc_dialogue": ["…"] }
}
```

- サーバーは中身を検めてから設定ファイル (`AI_GM_AI_CONFIG`) に書き (ファイルの権限は 600)、読み直して `ai_settings` を返す。おかしければ書かずに理由を `message` に入れて返す (`ok: false`)。
- 種類ごとに書ける項目だけを残す (agy は `timeout` だけ)。画面に出さない項目 (`extra_args` など) と、ファイルの `_` で始まる説明はそのまま残す。
- `api_key` (キーの値) は受け取っても書かない。`api_key_env` は環境変数の名前らしいもの (大文字・数字・`_` だけ、40 文字まで、数字は 4 文字まで、`_` を含まないなら 20 文字未満) だけを受け付け、キーの値らしいものは断る (断った値はメッセージにもログにも出さない)。
- 画面の欄には設定ファイルに書いてある値だけを入れ、今の実際の値 (環境変数の既定など) は「〜 (既定)」と薄く見せる。そのまま保存しても既定値はファイルに書かれない。
- サーバーは LAN からも繋げるため、画面からは localhost 以外の URL の OpenAI 互換の接続先を作れない (キーの持ち出しや、プレイヤーの発言を外へ送って返事で NPC を操ることを防ぐ)。自分の PC とみなすのは `localhost` とループバックの IP アドレス (`127.0.0.1`・`::1` など) だけ。設定ファイルに書いてあった URL・環境変数の名前のままなら、ほかの欄は画面で変えられる。クラウドの OpenAI 互換 API や別の PC の LM Studio は設定ファイルに直接書く。
- 保存すると、相談の会話 (会話 ID) は最初からになる。

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
  "site_mode": "(site_lore のみ) ruins | underground", "size_key": "small | medium | large",
  "actors_context": { "pcs": [{ "name": "…", "info": "…", "bio": "…" }], "npcs": [] }
}
```

`builder_build_scene` は上記の文脈に加えて `scene_mode`, `name`, `public_lore`, `dm_secrets` を送ります。
旅路は `map` (`{"seed": …}`) と `landmarks`、遺跡は `size_key` (small 20x20 / medium 50x50 (既定) / large 100x100)・`density`・`loop_ratio`・`sub_objectives` を送ります。
サーバーは `create_dungeon_scene` で応答します。
遺跡は、ジェネレータ (`tools/dungeon_generator.py`) で地図・壁・区画を作った時点で仮の区画名のまま `scene_key` 付きの `create_dungeon_scene` を送り、
AI が区画の情景を書き終えたものから `update_dungeon_rooms` で差し替えます (AI の応答を待たずにシーンを開けます)。

`builder_build_scene` の `use_ai` (bool、既定 true) が false なら「スクリプト」で構築します。AI には問い合わせず、ジェネレータの地図と仮の区画名のままシーンを作って終えます
(旅路は下書きのランドマークも使いません。区画の文面は DM が探索録に書きます)。

`builder_generate_stage` と `builder_build_scene` は `auto_extend` (bool。ビルダー画面の「⏱️ タイムアウトしたら自動で延長する」、既定 false) を送ります。
true のときはタイムアウトしても GM に確認せず、同じ秒数だけ自動で延長します (最大 5 回。それ以降は通常どおりカードで確認します)。

#### 冒険記録 (`adventure_log_request`)
`/ai 記録を作って` (新しい回) / `/ai 記録を更新して` (最新の回に書き足す) に対してサーバーが `adventure_log_collect` を返すと、FVTT (GM) が材料を集めて送ります。
マスコットのメニュー「冒険記録を更新」からも送ります (回が無ければ新しく作ります)。

- `transcript`: 前の記録より後のチャット (古い順の `{speaker, text}`)。ウィスパー・サポートGM との相談・`/` のコマンドは除き、ダイスは「🎲 式 → 出目」にします。新しいものが無ければ送らず、GM に「まだない」と伝えます。
- `inventory_changes`: プレイヤー所有の character の所持品が、前の記録から増えた分 (`{holder, name, quantity}`。初めての記録では空)。数えるのは物品 (weapon / equipment / consumable / tool / loot / container) だけで、呪文・特技・クラスの特徴は数えません。
- `place_hints`: その間に発言があったシーンのうち、ナビに出しているものの表示名 (`navName`、無ければ名前)。サーバーに送れなかったときは GM に通知します。`previous_replay`: 更新のときの最新の回の本文 (HTML)。

```json
{ "event": "adventure_log_request", "mode": "create", "session_number": 2,
  "transcript": [{ "speaker": "アリア", "text": "宿の女将に話を聞こう" }], "pcs": ["アリア"],
  "place_hints": ["霧の港町"], "inventory_changes": [{ "holder": "アリア", "name": "灯台の鍵", "quantity": 1 }],
  "previous_replay": "" }
```

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
  "volume": "number (音量 0.0〜1.0。既定値: 1.0)",
  "subtitle": "string (任意。読み上げた文)",
  "gm_only": "boolean (任意。true なら GM のクライアントでだけ鳴らす。声のテストなど)",
  "speaker": "string (任意。'support' ならGeminiちゃん自身の声で、マスコットが「話し中」になる。無ければナレーションか NPC)"
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
    { "label": "小 (20x20マス)", "value": "dungeon_size:small" },
    { "label": "中 (50x50マス)", "value": "dungeon_size:medium" },
    { "label": "大 (100x100マス)", "value": "dungeon_size:large" },
    { "label": "巨大 (200x200マス)", "value": "dungeon_size:xlarge" }
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
`/ai ダンジョン` のウィザードは「舞台 (`dungeon_mode:<mode>`) → サイズ → 部屋密度 → 周回路」の順に選択カードを出します (野外は部屋密度を飛ばします)。サイズは小 20x20・中 50x50・大 100x100・巨大 200x200 マスです (野外は 9 地点前後が入るよう中から選びます)。

```json
{
  "action": "create_dungeon_scene",
  "scene_mode": "underground",
  "name": "地下迷宮 (大: 100x100)",
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
- `scene_key` (任意): 後から届く `update_dungeon_rooms` の宛先の目印。シーンの flag (`scene_key`, `journal_id`) に記録します。
  区画の光源・環境音・ノート・探索録のページには flag `room_id` (区画の `id`) を付けます。

#### 5-2. 区画の差し替え (`update_dungeon_rooms`)

```json
{ "action": "update_dungeon_rooms", "scene_key": "builder-1234-ab12cd34",
  "rooms": [{ "id": 3, "name": "区画 3: 崩れた礼拝堂", "flavor": "…", "gm_notes": "…", "icon": "icons/svg/temple.svg", "light_preset": "magic_blue", "cx": 350, "cy": 450 }] }
```

`scene_key` の flag を持つシーンを探し、`rooms[i].id` が同じ区画について、探索録のページ (名前と本文)・マップノート (名前とアイコン)・光源と環境音 (作り直し)・シーンの `rooms` flag を書き換えます。
シーンが見つからなければ何もしません (コンソールに警告)。

#### 5-3. NPC 会話の反応 (`npc_dialogue_response`)

```json
{
  "action": "npc_dialogue_response", "speaker_token_id": "…", "scene_id": "…",
  "result": {
    "primary_npc_id": "トークン id", "primary_npc_name": "エドガー",
    "internal_thought": "GM だけが読む内心", "narrative_action": "地の文", "speech": "セリフ (かぎ括弧なし)",
    "ambient_reactions": [{ "npc_id": "…", "npc_name": "…", "silent_emote": "…" }],
    "physical_action": "stay | approach_speaker | step_back | leave_scene | turn_hostile",
    "trust_delta": -2〜2, "new_memory": { "summary": "…", "importance": 1〜5 } | null,
    "check_offer": { "skill": "説得", "dc": 5〜30, "target_secret_hint": "GM だけが読む" } | null,
    "fallback": false
  }
}
```

- サーバーは AI の応答を検める: `primary_npc_id` / `ambient_reactions` は聴き手の id だけ、数値は範囲内に丸め、読めなければ最も近い NPC の既定の反応 (`fallback: true`)。
- FVTT は、NPC を発言者にした小説風カード (地の文・セリフ・脇役の仕草・判定ボタン) を全員に、内心・信頼度・動きたがっている行動・判定成功時のヒントを GM にだけウィスパーで出し、吹き出しを表示する。
  NPC の記憶 (`new_memory`) と信頼度 (`trust_delta`) を `npc_brain` に書く。
  `physical_action` は実行し、結果を GM へのメモに添える: 近づく (2 マス) / 下がる (1 マス) は壁を避けてマス単位で動かし、立ち去るはトークンを非表示に、敵対するはトークンの立場を「敵対」にする。
- セリフは NPC の声色 (`voice`) で読み上げ、`play_sound` (字幕付き) を続けて送る。

**NPC の記憶と忘却** (`scripts/lib/npc-brain.js`)
- 短期記憶は最新 5 件。あふれた古いものは鮮明度 0.9 で長期記憶へ移す。重要度 4 以上はすぐ長期記憶にも入れる (二重には移さない)。
- 「⏳ 時間を進める」で長期記憶を 1 段階風化させる: 鮮明度 × (1 − (5 − 重要度) × (1 − 記憶力) × 0.35)。重要度 5 は忘れない。0.15 未満で忘れる。
- AI には鮮明度を [鮮明] (0.8 以上) / [うろ覚え] (0.4 以上) / [ほぼ忘却] の印で渡す。

#### 5-3-2. AI の設定 (`ai_settings`)
設定画面の中身です。API キーの値は含めません。

```json
{
  "action": "ai_settings",
  "config_path": "~/.config/ai-gm-bridge/ai_providers.json",
  "config_exists": "boolean (false なら既定の振り分け)",
  "providers": [{ "name": "gemini", "type": "gemini", "type_label": "Gemini API", "model": "…", "api_key_env": "GEMINI_API_KEY", "key_status": "設定済み | 未設定 | 不要", "available": true, "timeout": 120, "concurrency": 3 }],
  "tasks": { "default": ["gemini", "agy"], "npc_dialogue": [] },
  "task_labels": { "default": "書いていない作業 (default)", "npc_dialogue": "NPC の会話" },
  "type_labels": { "gemini": "Gemini API" },
  "editable_fields": { "agy": ["timeout"] },
  "message": "保存しました … / 保存できませんでした: …",
  "ok": true
}
```

#### 5-4. NPC の手番の行動 (`execute_npc_turn`)

```json
{
  "action": "execute_npc_turn", "token_id": "…", "scene_id": "…",
  "combat": { "id": "…", "round": 2, "turn": 3 },
  "decision": {
    "internal_thought": "GM だけが読む内心",
    "move_intent": "approach_target | flank_target | retreat_from_enemies | seek_cover_or_gimmick | hold_position",
    "target_token_id": "perceived_targets の token_id | null",
    "action_type": "attack | skill_or_spell | interact_environment | dodge_or_flee | parley",
    "action_name": "available_actions の名前 | null", "dice_formula": "1d20+5 | null",
    "emote": "行動の描写", "speech": "セリフか咆哮 (かぎ括弧なし)", "fallback": false
  }
}
```

- サーバーは AI の応答を検める: 狙いは知覚した PC の id だけ、`action_name` は一覧の名前だけ (大文字小文字は無視)、`dice_formula` は数字・`d`・`k`/`h`/`l`・四則演算・括弧だけの式。
  接近・挟み撃ちで狙いが無ければ、倒れていない最寄りの PC。読めなければ最寄りの PC に詰め寄る既定の行動 (`fallback: true`)。
- FVTT は届いたときに `combat` の手番 (id・round・turn と手番のトークン) がまだ同じか確かめ、進んでいたら実行しない。シーンは `combat.scene` (表示中・アクティブのシーンとは限らない)。
- **移動**: `move_intent` に沿って、1 手番の移動力 (`移動速度 ÷ マスの距離`、最大 12 マス) の分だけマス単位で動かす (`scripts/lib/npc-tactics.js` の `planSteps`)。
  - 歩いて行けるマスを幅優先で調べ (斜めも 1 マス。壁・他のトークン・シーンの外は通らない)、評価が最も良いマスへの最短の道を進む。いまの位置より良いマスが無ければ動かない。
  - 接近: 狙いに隣接するまで (隣接済みなら動かない)。挟み撃ち: 狙いに隣接している味方 (同じ立場の NPC) の反対側のマス。味方がいなければ接近と同じ。接近と挟み撃ちは 30 マス先まで道を探して回り込む。
  - 後退: 最も近い敵 (倒れていない PC) から離れる。遮蔽: 敵の誰からも視線が通らないマスに入ったら止まる。どちらも 1 手番で行けるマスだけ。
  - 壁はキャンバスに表示中のシーンでしか判定できないため、戦闘のシーンを表示していなければ動かさず、GM へのメモにその旨を出す。
- **行動**: `action_name` のアイテムがあれば NPC のカード (描写・セリフ) を出してから `item.use()`。使用に失敗したら `dice_formula` (無ければ `1d20`) で代用する。
  アイテムが無く `dice_formula` があれば、カードを flavor に入れたダイスのメッセージ 1 通にする。どちらも無ければカードだけ。式は FVTT 側でも `Roll.validate` で確かめる。
- 吹き出し (セリフ、無ければ描写) を表示し、内心・移動の意図・狙い・実行できなかったことを GM にだけウィスパーで出す。セリフは NPC の声色で読み上げる (`play_sound`)。

#### シナリオビルダーを開く (`open_builder`)
スキル判定で地域作成・町生成・旅路作成・シナリオ作成スキルになったときに送ります。FVTT はビルダーを `tab` のタブで開き、GM 指示欄に `user_prompt` を入れます (下書きの作成は GM がボタンを押してから)。

```json
{
  "action": "open_builder",
  "tab": "region | settlement | wilderness | site",
  "user_prompt": "string (GM の要望の要約)"
}
```

#### 6. シナリオビルダーの下書き (`builder_stage_result`)

```json
{
  "action": "builder_stage_result",
  "stage": "wilderness",
  "result": { "wilderness_title": "…", "public_lore": {}, "dm_secrets": {}, "landmarks": [{ "id": 0, "title": "…" }], "canon": [], "open_threads": [] },
  "cards": [{ "id": "wilderness.road.pilgrim", "axis": "道の性格", "text": "…" }],
  "seed": 1234,
  "fallback": false,
  "map": { "seed": 1234, "size_key": "large", "density": 0.4, "loop_ratio": 0.3, "points": 9 }
}
```

`map` は旅路のみ。`landmarks[i].id` は地点グラフの 0 始まりの id です。`fallback: true` は AI 応答が使えず骨組みを返したことを示します。
`create_dungeon_scene.lore.dm_secrets` があれば、探索録の伝承ページに DM 専用情報として表示します。

#### 7. シナリオビルダーの進捗 (`builder_progress`)
下書きの作成中とシーン構築中に、2 秒ごとに送ります。ビルダー画面の「⏳ AI が下書きを作成中です…」(`kind: "draft"`) /
「⏳ シーンを構築中です…」(`kind: "build"`) の行に、工程・完了数・経過・タイムアウトまでの残り秒を出します
(次の進捗が届くまでは画面側で 1 秒ずつ数え進めます。残りが 0 になったら、延長確認のカードを選ぶよう案内します。自動延長のときは「自動で延長します」)。
作業中は「AI で下書き」「シーン構築」のボタンを押せなくします。
シーン構築の最後 (失敗しても) に `finished: true` を送り、画面の「構築中」の行を消します。下書きは `builder_stage_result` が届いた時点で消します。

```json
{ "action": "builder_progress", "kind": "build", "finished": false, "stage": "site_lore", "step": 2, "steps": 2,
  "step_label": "区画の情景", "detail": "完了 1/2", "elapsed": 40, "remaining": 260 }
```

#### 8. 冒険記録 (`adventure_log_collect` / `adventure_log_write`)
`adventure_log_collect` (`{"action": "adventure_log_collect", "mode": "create" | "update"}`) は、FVTT に `adventure_log_request` の材料を集めてもらう指示です。
`adventure_log_write` は AI (`prompts/adventure_log_prompt.txt`) が書いたリプレイ風の本文と一覧です。
AI の応答が読めなければ `fallback: true` で、チャットの終わりの方の抜粋と所持品の差分だけを返します。所持品の差分は AI が書き漏らしても `items` に入ります。

```json
{ "action": "adventure_log_write", "mode": "create", "session_number": 2, "title": "霧の港と灯台の噂",
  "replay": "一行は霧の港町にたどり着いた。\n女将マルタ「北の灯台に近づいちゃいけないよ」",
  "items": [{ "name": "灯台の鍵", "holder": "アリア", "note": "女将から預かった" }],
  "people": [{ "name": "女将マルタ", "note": "宿の女将。灯台を恐れている" }],
  "places": [{ "name": "霧の港町", "note": "最初に着いた町" }], "fallback": false }
```

FVTT は「📖 冒険日誌」ジャーナル (プレイヤーも閲覧可) に書きます (`scripts/adventure-log.js`)。

- 回ごとのページ「第N回 見出し」: 新しい回は本文のページを作り、更新は最新の回の本文の後に `<hr>` を挟んで書き足します (GM が書き換えた本文は残ります)。
- 一覧のページ「🎒 取得物」「🤝 知り合った人々」「🗺️ 訪れた場所」: 人物・場所は名前、取得物は名前と持ち主で 1 件にまとめ、初めて記録した回を添えます。一覧は記録のたびに flag から作り直すので、ページの先頭にその旨を書きます。
- ジャーナルの flag `adventure_log` に、回の区切り (`sessions[].since` / `until`: どのメッセージまで読んだか)・一覧・PC の所持品の控えを保存します。
- 書き終えたら、Geminiちゃんが GM 宛てに「冒険日誌を開く」ボタン付きで知らせます。

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
