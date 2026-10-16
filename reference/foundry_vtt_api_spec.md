# Foundry VTT API 連携仕様書 (Reference 実装 × FVTT v14 API)

本書は `reference/` フォルダに収録された統合版リファレンス実装（`reference/tools/bridge.js`・`reference/tools/ai_gm_server.py`・`reference/tools/prompt_manager.py`・`reference/prompts/*.txt`）を解析し、それらが利用している **Foundry Virtual Tabletop 公式 API (https://foundryvtt.com/api/)** の仕様と突き合わせて整理したものです。

- 参照した API ドキュメント: **Foundry VTT API Documentation – Version 14 (14.368 Stable)**
- 調査日: 2026-10-16
- 対象読者: `bridge.js` / `ai_gm_server.py` を改修する開発者・AI エージェント

> 記法: 「✅ API 文書で確認」は v14 API ドキュメント上で直接確認した事項、「⚠️ 推定」は API ドキュメントに明記がなく、コードや一般的な FVTT の仕組みから推定した事項です。

---

## 1. Reference フォルダの構成

```text
reference/
├── foundry_vtt_api_spec.md      # 本書
├── tools/
│   ├── bridge.js                # FVTT クライアント側 統合スクリプト (約 2,400 行, v12〜v14 対象)
│   ├── ai_gm_server.py          # WebSocket サーバー (Gemini + edge-tts + 3D 環境音)
│   └── prompt_manager.py        # プロンプト構築・world.json 読み取り・JSON 抽出
└── prompts/
    ├── 1_region_prompt.txt      # Stage 1: 地域
    ├── 2_settlement_prompt.txt  # Stage 2: 集落・くらし
    ├── 3_wilderness_prompt.txt  # Stage 3: 周辺・旅路 (ワイルダネス)
    ├── 4a_site_lore_prompt.txt  # Stage 4-A: 廃墟・迷宮の伝承
    ├── 4b_site_rooms_prompt.txt # Stage 4-B: 区画フレーバー・照明・環境音
    ├── npc_dialogue_prompt.txt  # NPC 小説風会話
    └── npc_tactics_prompt.txt   # NPC 自律戦闘
```

リポジトリ直下の `bridge.js` (約 600 行) は旧来の最小実装であり、`reference/tools/bridge.js` はそれを大幅に拡張した統合版です。両者は内容が異なります。

### 1.1 `reference/tools/bridge.js` のセクション構成

| # | セクション | 主に使う FVTT API |
|---|---|---|
| 0 | 基本設定・照明プリセット (`LIGHT_PRESETS`)・NPC ブレイン初期値 | — |
| 1 | アシスタント DM マスコット UI「アギィ」 | `game.socket.emit/on` |
| 2 | 4 階層アトラス・ジャーナル管理 | `Folder.create`, `JournalEntry.create`, `createEmbeddedDocuments("JournalEntryPage")`, `setFlag/getFlag` |
| 3 | 集落 BGM プレイリスト | `Playlist.create`, `PlaylistSound`, `playlist.playSound/stopAll` |
| 4 | シナリオビルダー GUI (4 タブ) | `Dialog` (V1) |
| 5 | NPC 会話システム (聴取範囲・記憶と忘却) | `scene.tokens`, `CONFIG.Canvas.polygonBackends.sound.testCollision`, `canvas.hud.bubbles.broadcast`, `actor.setFlag` |
| 6 | NPC / モンスター自律戦闘 | `polygonBackends.sight/move.testCollision`, `TokenDocument#update`, `Item#use` (dnd5e), `Roll` |
| 7 | スラッシュコマンド `/ai`, `/agy` | `ChatLog.CHAT_COMMANDS`, `chatMessage` フック |
| 8 | WebSocket ハンドラ & シーン自動構築 | `Scene.create`, `Level`, `Wall`, `AmbientLight`, `AmbientSound`, `Note`, `ChatMessage.create`, `AudioHelper.play` |
| 9 | UI イベント監視・手番検知・部屋進入検知 | `renderChatMessage`, `updateCombat`, `updateToken` フック, `Scene#activate/view` |

---

## 2. システム全体像

```
┌──────────────── Foundry VTT (GM クライアント) ────────────────┐
│ bridge.js                                                      │
│  ├ /ai, /agy コマンド ─────────────┐                           │
│  ├ 通常発言 (chatMessage) ─────────┤                           │
│  ├ ビルダー GUI (4 タブ) ──────────┤ ws://127.0.0.1:8765       │
│  ├ updateCombat (NPC 手番) ────────┤ JSON {event: ...}  ──────►│──┐
│  └ updateToken (部屋進入) ─────────┘                           │  │
│                                                                │  │
│  ◄──── JSON {action: ...} ── chat / roll / play_sound /        │  │
│        create_dungeon_scene / execute_npc_turn / ...           │  │
│                                                                │  │
│  game.socket "module.ai-gm-bridge" ──► 全プレイヤー (マスコット同期)│  │
└────────────────────────────────────────────────────────────────┘  │
                                                                     ▼
                         ai_gm_server.py (websockets, max_size 15MB)
                          ├ Gemini (google-genai, gemini-2.5-flash) / agy CLI
                          ├ edge-tts → ~/foundrydata/Data/voice_*.mp3
                          ├ DungeonGenerator → ~/foundrydata/Data/map_*.png
                          └ 3D 環境音生成 (別 WSL, 任意)
```

- WebSocket 接続は **GM クライアントのみ** (`if (!game.user.isGM) return;`) が確立します。切断時は 5 秒後に自動再接続。
- プレイヤークライアントは WebSocket に接続せず、`game.socket` 経由でマスコット状態のみ受け取ります。
- 生成ファイル (音声・マップ画像) は FVTT の `Data` ルート直下に書き出し、`src` には Data ルートからの相対パス (ファイル名) を渡します。

---

## 3. WebSocket プロトコル仕様 (Reference 実装)

### 3.1 FVTT → サーバー (`event`)

| event | 送信元 | 主なフィールド | サーバーの応答 action |
|---|---|---|---|
| `builder_generate_stage` | ビルダー GUI | `stage` (`region` / `settlement` / `wilderness` / `site_lore`), `user_prompt`, `settlement_scale`, `region_context`, `settlement_context`, `wilderness_context`, `site_mode`, `size_key`, `actors_context` | `builder_stage_result` |
| `builder_generate_bgm` | ビルダー GUI (集落保存時) | `settlement_name`, `joy_bgm_en`, `sorrow_bgm_en` | `chat`, `register_settlement_playlist` |
| `builder_build_scene` | ビルダー GUI | `scene_mode` (`ruins` / `underground` / `wilderness`), `name`, `size_key` (`small`/`medium`/`large`/`xlarge`), `density`, `loop_ratio`, `user_prompt`, `site_public_lore`, `site_dm_secrets` (ワイルダネス時は `public_lore`, `dm_secrets`, `landmarks`) | `create_dungeon_scene`, `play_sound` |
| `npc_turn_request` | `updateCombat` フック | `perception` (§5.4) | `execute_npc_turn`, `play_sound` |
| `npc_dialogue_request` | `chatMessage` フック / 判定ボタン | `speaker_name`, `speaker_token_id`, `player_input`, `room_name`, `room_flavor`, `listening_npcs[]`, `check_result_context` | `npc_dialogue_response`, `play_sound` |
| `ai_command` | `/ai`, `/agy` | `user`, `prompt`, `actors_context {pcs[], npcs[]}` | `chat` |
| `ai_choice` | 選択カードのボタン | `user`, `value`, `label` (`dungeon_loop:<size>:<density>:<loop>` を解釈) | `create_dungeon_scene` 等 |
| `room_entered` | `updateToken` フック | `token_name`, `room_name`, `flavor` | `play_sound` |
| `player_message` | (旧仕様互換) | `user`, `content` | `chat`, `roll`, `play_sound` |

### 3.2 サーバー → FVTT (`action`)

| action | 主なフィールド | FVTT 側の処理 (使用 API) |
|---|---|---|
| `builder_stage_result` | `stage`, `result` | ビルダー GUI の入力欄 (DOM) へ下書きを反映 |
| `register_settlement_playlist` | `settlement_name`, `joy_src`, `sorrow_src` | `Playlist.create` / `PlaylistSound` 差し替え → GM 宛 whisper カード |
| `execute_npc_turn` | `token_id`, `move_intent`, `target_token_id`, `action_type`, `action_name`, `dice_formula`, `emote`, `speech`, `thought` | 移動 → 吹き出し → チャット → `Item#use` または `Roll` |
| `npc_dialogue_response` | `speaker_token_id`, `result {primary_npc_id, primary_npc_name, narrative_action, speech, ambient_reactions[], physical_action, trust_delta, new_memory, check_offer}` | 記憶更新 (`actor.setFlag`)・移動・吹き出し・小説風チャットカード |
| `chat` | `speaker`, `content` (HTML) | `ChatMessage.create({speaker:{alias}, content})` |
| `roll` | `speaker`, `formula` | `new Roll(formula).evaluate()` → `toMessage()` |
| `play_sound` | `src`, `volume`, `subtitle` | `foundry.audio.AudioHelper.play(...)` |
| `interactive_card` | `title`, `message`, `choices[{label, value}]`, `speaker` | ボタン付きチャットカード (GM whisper) |
| `create_dungeon_scene` | §4.1 参照 | シーン・壁・照明・環境音・ジャーナル・ノートの一括生成 |

### 3.3 クライアント間 Socket (`game.socket`)

- チャンネル名: `module.ai-gm-bridge`
- ペイロード: `{ action: "sync_mascot", state, bubbleText, durationMs }`
- GM が `setMascotState(..., broadcast=true)` を呼ぶと `game.socket.emit` で全クライアントへ配信し、各クライアントは `game.socket.on` で受けて描画のみ行います (再送しない)。

---

## 4. シーン自動構築 (`create_dungeon_scene`) 詳細

### 4.1 受信ペイロード

```json
{
  "action": "create_dungeon_scene",
  "scene_mode": "underground | ruins | wilderness",
  "name": "地下迷宮",
  "background_src": "map_underground_100x100_1760000000.png",
  "grid_width": 100, "grid_height": 100, "grid_size": 100,
  "room_count": 12,
  "lore": { "dungeon_name": "", "floor_title": "", "history_summary": "", "floor_origin": "", "dm_secrets": "" },
  "rooms": [{
    "name": "朽ちた武器庫", "cx": 1250, "cy": 850,
    "grid_x": 10, "grid_y": 6, "grid_w": 5, "grid_h": 5,
    "flavor": "…", "gm_notes": "…", "role": "sub_goal", "safe_rest": false,
    "light_preset": "torch_warm", "ambient_sound_src": "room_3_….ogg", "icon": "icons/svg/sword.svg"
  }],
  "start_pos": [2, 3], "goal_pos": [90, 88],
  "walls": [[x1, y1, x2, y2, "normal | low_wall | rubble | door | secret_door"]]
}
```

- `walls` と `rooms[].cx/cy` はシーンのパディングを含まない「マップ画像上のピクセル座標」です。FVTT 側で `scene.dimensions.sceneX/sceneY` を加算してキャンバス座標に変換します (Reference 実装は `padding: 0` なのでオフセットは 0)。
- `start_pos` / `goal_pos` はグリッド座標。`* grid_size + grid_size/2` でセル中心に変換します。
- 壁はサーバー側で「マップ幅 ≤ 1000 かつ 3,500 本以下」の場合のみ送信されます (ワイルダネスは壁なし)。

### 4.2 生成手順と使用 API

| 手順 | 呼び出し | v14 API 上の根拠 |
|---|---|---|
| 1. シーン作成 | `Scene.create({name, width, height, padding:0, initialLevel, levels:[{_id, name, background:{src}}], grid:{size, distance:5, units:"ft"}, darkness, globalLight, navigation:true})` | ✅ `BaseScene` スキーマに `levels` (EmbeddedCollection), `initialLevel`, `grid`, `environment`, `padding`, `navigation` がある |
| 2. Level 背景の補正 | `scene.updateEmbeddedDocuments("Level", [{_id, background:{src}}])` | ✅ `BaseLevel` に `background`, `foreground`, `elevation`, `fog`, `textures`, `visibility` |
| 3. 壁 | `scene.createEmbeddedDocuments("Wall", [{c, move, sight, light, sound, door}])` | ✅ `BaseWall`: `c, move, sight, light, sound, door, ds, dir, threshold, animation, doorSound, levels` |
| 4. 照明 | `createEmbeddedDocuments("AmbientLight", [{x, y, config:{bright, dim, color, alpha, animation}}])` | ✅ `BaseAmbientLight`: `x, y, elevation, rotation, walls, vision, config, hidden, levels, locked, name` |
| 5. 環境音 | `createEmbeddedDocuments("AmbientSound", [{x, y, radius, path, repeat, volume, walls, easing}])` | ✅ `BaseAmbientSound`: `x, y, elevation, radius, path, repeat, volume, walls, easing, darkness, effects, hidden, levels` |
| 6. ジャーナル | `JournalEntry.create({name, folder, ownership, pages:[{name, type:"text", title, text:{content, format:1}, ownership}]})` | ✅ `JOURNAL_ENTRY_PAGE_FORMATS.HTML = 1` |
| 7. マップノート | `createEmbeddedDocuments("Note", [{entryId, pageId, x, y, text, texture:{src}, iconSize, fontSize}])` | ✅ `BaseNote`: `entryId, pageId, x, y, elevation, texture, iconSize, text, fontFamily, fontSize, textAnchor, textColor, global, levels` |
| 8. 部屋データ保存 | `scene.setFlag("ai-gm-bridge", "rooms", rooms)` | フラグは部屋進入検知・NPC 知覚で再利用 |
| 9. 完了カード | `ChatMessage.create({..., whisper:[game.user.id]})` + 「全員で移動」(`scene.activate()`) / 「GM のみ確認」(`scene.view()`) | ✅ `Scene#activate(options)`, `Scene#view(options)` |

### 4.3 壁タイプ変換表

| サーバー側 type | `move` | `sight` / `light` | `sound` | `door` |
|---|---|---|---|---|
| `normal` | NORMAL (20) | NORMAL (20) | NORMAL (20) | NONE (0) |
| `low_wall`, `rubble` | NORMAL (20) | NONE (0) | NORMAL (20) | NONE (0) |
| `door` | NORMAL (20) | NORMAL (20) | NORMAL (20) | DOOR (1) |
| `secret_door` | NORMAL (20) | NORMAL (20) | NORMAL (20) | SECRET (2) |

### 4.4 照明プリセット (`LIGHT_PRESETS`)

| キー | bright / dim | color | animation.type |
|---|---|---|---|
| `torch_warm` | 15 / 30 | `#ff9f43` | `torch` |
| `bonfire_bright` | 25 / 50 | `#ff6b35` | `flame` |
| `magic_blue` | 15 / 35 | `#48dbfb` | `pulse` |
| `acid_green` | 12 / 28 | `#1dd1a1` | `fog` |
| `moonlight_dim` | 0 / 40 | `#c8d6e5` | `none` |
| `dark` | (照明なし) | — | — |

`safe_rest: true` の部屋はプリセット未指定時に `bonfire_bright` を使用。開始地点は緑 (`pulse`)、ゴールは赤 (`torch`) の照明を自動配置します。

---

## 5. 各機能の FVTT API 利用仕様

### 5.1 チャット・ダイス・音声

| 機能 | 呼び出し | v14 API 仕様 |
|---|---|---|
| チャット投稿 | `ChatMessage.create({speaker:{alias, token, actor}, content, whisper})` | ✅ スキーマ: `author, content, speaker, whisper, blind, rolls, sound, flavor, style, type, title, emote, system, flags`。`ChatMessage.getSpeaker({actor, token, scene, alias})` で正規の speaker を生成可能 |
| ダイス | `new Roll(formula)` → `await roll.evaluate()` → `roll.toMessage({speaker, flavor})` | ✅ `evaluate(options?): Promise<Roll>`、`toMessage(messageData?, {create?, messageMode?})`。v14 では公開範囲指定は `rollMode` ではなく **`messageMode`** |
| 音声再生 | `foundry.audio.AudioHelper.play({src, volume, autoplay:true, loop:false}, false)` | ✅ `static play(data:{src, volume?, autoplay?, loop?, channel?}, socketOptions?: boolean \| {recipients:string[]})`。`channel` 既定値は `"interface"` |
| オーディオ再開 | `game.audio.context.resume()` | ✅ `AudioHelper#context` は後方互換用 (music コンテキストを指す) |

### 5.2 ジャーナル (4 階層アトラス)

- フォルダ構造: `【AI World Atlas】` / `1_地域 (Regions)`・`2_集落・街 (Settlements)`・`3_周辺・旅路 (Wilderness)`・`4_遺跡・迷宮 (Sites)`
- 1 エントリ = 2 ページ構成
  - `📖 プレイヤー公開情報` … `ownership.default = OBSERVER (2)`
  - `🔒 DM専用情報` … `ownership.default = NONE (0)`
- 既存エントリは全ページ削除 → 再作成で上書き。メタ情報は `flags["ai-gm-bridge"].atlas_meta = {categoryKey, updatedAt, ...}`。
- プロンプト用抽出 (`extractJournalContextForPrompt`) はページ名に「DM」を含むか `ownership.default === 0` のページを DM 秘匿として分離し、HTML を除去してサーバーへ送ります。
- ✅ `DOCUMENT_OWNERSHIP_LEVELS`: `INHERIT -1, NONE 0, LIMITED 1, OBSERVER 2, OWNER 3`

### 5.3 集落 BGM プレイリスト

- フォルダ `【AI World BGM】` 配下に `🏘️ <集落名>` のプレイリストを作成 (`mode: PLAYLIST_MODES.DISABLED`)。
- サウンド: `☀️ 【楽しみ・日常】`, `🕯️ 【悲しみ・憂い】` (`repeat: true, volume: 0.5, channel: "music"`)。
- 切り替え: 同フォルダの再生中プレイリストを `stopAll()` → `playlist.playSound(sound)`。
- ✅ `Playlist#playSound(sound): Promise<Playlist>`, `stopSound`, `playAll`, `stopAll`, `playNext`。`PLAYLIST_MODES`: `DISABLED -1, SEQUENTIAL 0, SHUFFLE 1, SIMULTANEOUS 2`。

### 5.4 NPC 自律戦闘

1. `updateCombat` で手番が NPC (`actor.type !== "character"` かつ `!hasPlayerOwner`) に移ったら `extractNpcPerception()` で知覚データを作成し `npc_turn_request` を送信。
2. 知覚データ: `token_id, npc_name, npc_role, npc_bio, hp_current, hp_max, health_status, move_speed, available_actions, room_name, room_flavor, room_gm_notes, perceived_targets[{token_id, name, distance_ft, has_los, adjacent}]`
   - 視線判定: `CONFIG.Canvas.polygonBackends.sight.testCollision(origin, dest, {type:"sight", mode:"any"})`
   - HP 等は dnd5e の `actor.system.attributes.hp/movement` を参照 (システム依存)。
3. 応答 `execute_npc_turn` の `move_intent` (`approach_target` / `flank_target` / `retreat_from_enemies` / `seek_cover_or_gimmick` / `hold_position`) に従い、8 方向・最大 3 歩の貪欲探索で移動先を決定。各歩で `polygonBackends.move.testCollision` と他トークン占有をチェックし、最後に `tokenDoc.update({x, y})`。
4. `canvas.hud.bubbles.broadcast(token, speech, {emote})` で吹き出し、チャットに行動描写。
5. `action_name` に一致するアイテムがあれば `item.use()` (dnd5e の Activity 実行)、なければ `dice_formula` を `Roll` で振る。

✅ `PointSourcePolygon.testCollision(origin, destination, config)`: `mode` が `"any"` → boolean、`"all"` → 交点配列、`"closest"` → 交点 or null。origin の高度は `config.source` があればその elevation、なければ 0。
✅ `ChatBubbles#broadcast(token: TokenDocument | Token, message, options)` は TokenDocument も受け付ける (`say` は Token のみ)。

### 5.5 NPC 会話システム

- 通常発言時 (`chatMessage` フック, `/` 始まり以外) に、選択中トークン (なければ PC トークン) から **35 ft 以内** かつ `polygonBackends.sound.testCollision` で遮られない NPC を最大 4 体抽出。
- NPC ごとの「ブレイン」は `actor.flags["ai-gm-bridge"].npc_brain` に保存:
  `goal, public_known, secret_known, ignorance, trust (-5〜+5), memory_retention, voice{name, rate, pitch}, short_term_memory[], long_term_memory[{summary, importance, clarity, timestamp}]`
- 記憶アルゴリズム: 短期記憶は 5 件を超えると長期へ移動 (clarity 0.9)。重要度 4 以上は即長期化。忘却ステップでは `weight = 1 - (5 - importance) × (1 - retention) × 0.35` を掛け、clarity < 0.15 で消去。重要度 5 は常に 1.0。
- 応答の `physical_action`: `stay` / `approach_speaker` / `step_back` / `leave_scene` (alpha 0.3) / `turn_hostile` (通知のみ)。
- `check_offer` があれば技能判定ボタンを表示。押下時 `1d20+3` を DC と比較し、結果を `check_result_context` に入れて再度 `npc_dialogue_request` を送信。

### 5.6 部屋進入ナレーション

- `updateToken` フックで `x`/`y` 変化を検出 → `scene.getFlag("ai-gm-bridge", "rooms")` の矩形 (`grid_x, grid_y, grid_w, grid_h`) と照合。
- 直前の部屋と異なる場合のみ `room_entered` を送信 (トークン ID ごとに `Map` で記憶)。サーバーは `「<部屋名>。<flavor>」` を TTS 化して `play_sound` を返します。

### 5.7 スラッシュコマンド

- `ChatLog.CHAT_COMMANDS.ai` / `.agy` に `{rgx, isRoll:false, isMultiline:false, fn}` を登録し、加えて `chatMessage` フックでも `/ai` を判定する二重登録方式。
- 引数なし / `gui` / `ビルド` / `シナリオ` → ビルダー GUI、`brain` / `npc` / `記憶` → NPC ブレイン管理 GUI、それ以外 → `ai_command` 送信。
- ✅ v14 で `ChatLog.CHAT_COMMANDS` (静的レジストリ) と `ChatLog.parse(message)` は存在。ただしエントリの形 (`rgx`, `fn` 等) は公開ドキュメントに記載がなく、⚠️ 内部仕様扱いです。

---

## 6. v14 API との互換性チェック結果

Reference 実装を v14.368 の API ドキュメントと照合した結果です。優先度の高い順に並べています。

| # | 対象 | 現状 (Reference) | v14 API 仕様 | 推奨対応 |
|---|---|---|---|---|
| 1 | 音声の配信範囲 | `AudioHelper.play({...}, false)` | ✅ 第 2 引数 `false` は **他クライアントへ送らない**。`true` で全員、`{recipients:[userId]}` で指定ユーザー | プレイヤーにも TTS を聞かせるなら `true` に変更。GM 専用ナレーションのみ `false` を維持 |
| 2 | チャット描画フック | `Hooks.on("renderChatMessage", ...)` | ✅ v14 の hookEvents には `renderChatMessageHTML` のみ掲載 (HTMLElement を渡す) | `renderChatMessageHTML` へ移行。Reference は `html instanceof HTMLElement` 分岐を既に持つので引数処理はそのまま使える |
| 3 | シーン環境設定 | `Scene.create({darkness, globalLight:{enabled}, background:{src}})` | ✅ `BaseScene` スキーマに `darkness` / `globalLight` / `background` は無い。環境は `environment` (`darknessLevel`, `globalLight`, `cycle`, `base`, `dark`)、背景は `levels[].background` | `environment: { darknessLevel: 0.75, globalLight: { enabled: false } }` 形式で指定。トップレベル `darkness` 等が互換レイヤーで移行されるかは ⚠️ 未確認 |
| 4 | ダイアログ | `new Dialog(...)` (ビルダー / ブレイン GUI) | ✅ `foundry.appv1.api.Dialog` は **v13 から非推奨** | `foundry.applications.api.DialogV2` (`wait`, `prompt`, `confirm`, `input`) か `ApplicationV2` へ移行 |
| 5 | モジュール Socket | `game.socket.emit("module.ai-gm-bridge", ...)` | ⚠️ モジュールの socket チャンネルはマニフェストで `"socket": true` を宣言する必要がある (API ページには記載なし、マニフェスト仕様) | ルート `module.json` に `"socket": true` が無いため、統合版を導入する際に追加する |
| 6 | Roll の公開範囲 | `roll.toMessage({speaker, flavor})` | ✅ v14 の `toMessage` オプションは `{create, messageMode}`。`rollMode` は旧名 (`_mapLegacyRollMode` は v14 で非推奨) | シークレットロールを実装する場合は `messageMode` を使用 |
| 7 | Level 対応 | 壁・照明・環境音・ノートに level 指定なし | ✅ `Wall` / `AmbientLight` / `AmbientSound` / `Note` に `levels` (SceneLevelsSetField) が追加 | 単一 Level なら省略可。多層ダンジョン化する場合は各 placeable に Level ID を指定 |
| 8 | 衝突判定の高度 | `testCollision(origin, dest, {type, mode:"any"})` | ✅ origin 高度は `config.source` が無ければ 0 | 多層シーンでは高度・Level を考慮した判定が必要 |
| 9 | トークン移動 | `tokenDoc.update({x, y})` | ✅ 引き続き有効 (戻り値以外は `move()` と同等)。v14 には `TokenDocument#move(waypoints, options): Promise<boolean>` と `preMoveToken` / `moveToken` フックがある | 経路移動アニメーションや移動完了待ちが必要なら `move()` を使用。部屋進入検知も `moveToken` フックが候補 |
| 10 | 手番検知 | `Hooks.on("updateCombat", ...)` で `turn`/`round` 差分を確認 | ✅ v14 には `combatTurnChange` フックがある | 手番変化専用の `combatTurnChange` に置き換えると判定が簡潔になる |
| 11 | プレイリスト定数のフォールバック | `CONST.PLAYLIST_MODES?.DISABLED ?? 0` | ✅ `DISABLED = -1`、`0` は `SEQUENTIAL` | フォールバック値を `-1` に修正 (CONST が存在する通常環境では影響なし) |
| 12 | 扉タイプ | `door: isDoor ? 1 : (isSecret ? 2 : 0)` | ✅ `WALL_DOOR_TYPES`: `NONE 0, DOOR 1, SECRET 2` (値は一致) | 可読性のため `CONST.WALL_DOOR_TYPES.*` を使用 |
| 13 | `Item#use()` | NPC 行動でアイテム実行 | ⚠️ コア API ではなく dnd5e システムの API | dnd5e のバージョン差 (v4 以降の Activity 化) に注意。失敗時は `Roll` へフォールバック済み |

### 6.1 定数リファレンス (v14.368)

| 定数 | 値 |
|---|---|
| `CONST.EDGE_SENSE_TYPES` | `NONE 0, LIMITED 10, NORMAL 20, PROXIMITY 30, DISTANCE 40` |
| `CONST.WALL_MOVEMENT_TYPES` | `NONE 0, NORMAL 20` |
| `CONST.WALL_DOOR_TYPES` | `NONE 0, DOOR 1, SECRET 2` |
| `CONST.DOCUMENT_OWNERSHIP_LEVELS` | `INHERIT -1, NONE 0, LIMITED 1, OBSERVER 2, OWNER 3` |
| `CONST.PLAYLIST_MODES` | `DISABLED -1, SEQUENTIAL 0, SHUFFLE 1, SIMULTANEOUS 2` |
| `CONST.JOURNAL_ENTRY_PAGE_FORMATS` | `HTML 1, MARKDOWN 2` |

`WALL_SENSE_TYPES` は v14 の CONST 一覧に存在しません (壁の感覚制限は `EDGE_SENSE_TYPES` を使用)。

---

## 7. 主要フックとライフサイクル (v14)

| タイミング | フック | Reference での用途 |
|---|---|---|
| 起動 | `init` → `i18nInit` → `setup` → `ready` | `init` でチャットコマンド登録、`ready` でマスコット UI・Socket 受信・WebSocket 接続 |
| チャット入力 | `chatMessage` (ChatLog) | `/ai` 判定、NPC 会話への転送。`false` を返して既定処理を止める |
| チャット描画 | `renderChatMessageHTML` (v14) / `renderChatMessage` (旧) | 選択肢・シーン移動・ジャーナル展開ボタンのイベント付与 |
| ドキュメント更新 | `update<DocumentName>` (汎用 `updateDocument`) | `updateToken` (部屋進入)、`updateCombat` (手番) |
| トークン移動 | `preMoveToken`, `moveToken`, `stopToken` 等 | (未使用・移行候補) |
| 戦闘 | `combatStart`, `combatRound`, `combatTurn`, `combatTurnChange` | (未使用・移行候補) |

---

## 8. 実装上の注意事項

1. **GM 限定実行**: WebSocket 接続・ドキュメント作成・Socket 送信は GM クライアントのみで行う。プレイヤー側は表示のみ。
2. **ループ防止**: AI 由来のチャット (`speaker.alias` が `AI GM` / `アギィ` 等) と `/`・`[` 始まりの発言はサーバーに転送しない。
3. **ファイルパス**: 音声・画像は `~/foundrydata/Data/` 直下に保存し、FVTT にはファイル名 (Data ルート相対パス) を渡す。
4. **座標系**: サーバーはパディングなしの画像座標で送り、FVTT 側で `scene.dimensions.sceneX/sceneY` を加算する。
5. **大量ドキュメント生成**: `createEmbeddedDocuments` は配列で一括呼び出しする (壁 3,500 本上限はサーバー側で制御)。
6. **プロトコル変更時**: `reference/tools/bridge.js` と `reference/tools/ai_gm_server.py` の `event` / `action` 名とフィールドを同時に更新する。
7. **システム依存**: `actor.system.*`、`Item#use()` は dnd5e 依存。他システムでは null 安全なフォールバックが必要 (Reference は `?.` と既定値で対応済み)。

---

## 9. 参照した API ドキュメント

- [API トップ (Version 14.368)](https://foundryvtt.com/api/)
- [hookEvents](https://foundryvtt.com/api/modules/hookEvents.html)
- [Scene](https://foundryvtt.com/api/classes/foundry.documents.Scene.html) / [BaseScene](https://foundryvtt.com/api/classes/foundry.documents.BaseScene.html) / [BaseLevel](https://foundryvtt.com/api/classes/foundry.documents.BaseLevel.html)
- [BaseWall](https://foundryvtt.com/api/classes/foundry.documents.BaseWall.html) / [BaseAmbientLight](https://foundryvtt.com/api/classes/foundry.documents.BaseAmbientLight.html) / [BaseAmbientSound](https://foundryvtt.com/api/classes/foundry.documents.BaseAmbientSound.html) / [BaseNote](https://foundryvtt.com/api/classes/foundry.documents.BaseNote.html)
- [ChatMessage](https://foundryvtt.com/api/classes/foundry.documents.ChatMessage.html) / [Roll](https://foundryvtt.com/api/classes/foundry.dice.Roll.html) / [AudioHelper](https://foundryvtt.com/api/classes/foundry.audio.AudioHelper.html)
- [Playlist](https://foundryvtt.com/api/classes/foundry.documents.Playlist.html) / [TokenDocument](https://foundryvtt.com/api/classes/foundry.documents.TokenDocument.html)
- [ChatBubbles](https://foundryvtt.com/api/classes/foundry.canvas.animation.ChatBubbles.html) / [ClockwiseSweepPolygon](https://foundryvtt.com/api/classes/foundry.canvas.geometry.ClockwiseSweepPolygon.html)
- [ChatLog](https://foundryvtt.com/api/classes/foundry.applications.sidebar.tabs.ChatLog.html) / [Dialog (V1)](https://foundryvtt.com/api/classes/foundry.appv1.api.Dialog.html) / [DialogV2](https://foundryvtt.com/api/classes/foundry.applications.api.DialogV2.html) / [Game](https://foundryvtt.com/api/classes/foundry.Game.html)
- [CONST 一覧](https://foundryvtt.com/api/modules/CONST.html) / [SceneEnvironmentData (v13)](https://foundryvtt.com/api/v13/interfaces/foundry.documents.types.SceneEnvironmentData.html)

関連ドキュメント: [`docs/specification.md`](../docs/specification.md)（旧最小実装の仕様）、[`docs/investigation_fvtt_dnd5e_control.md`](../docs/investigation_fvtt_dnd5e_control.md)（D&D5e 制御 API 調査）
