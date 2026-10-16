# Reference 実装 移植レビュー & 移植計画

`reference/tools/`（統合版の「そうなったらいいな」コード）を本番コードへ移植するにあたり、誤っている箇所・直すべき箇所を洗い出し、移植の順番を決めたものです。
FVTT API との互換性は [`reference/foundry_vtt_api_spec.md`](../reference/foundry_vtt_api_spec.md) §6 にまとまっているので、ここではそれ以外を扱います。

- 対象: `reference/tools/ai_gm_server.py` (805 行)・`reference/tools/prompt_manager.py` (658 行)・`reference/tools/bridge.js` (2,395 行)・`reference/prompts/*.txt`
- 比較元: 本番の `ai_gm_server.py`・`bridge.js`・`tools/*.py`
- 状態の凡例: ✅ 本番で修正済み (Phase 0) / 🔜 その機能を移植するときに直す / 📝 判断が必要

---

## 1. 本番コードの構成 (Phase 0 で分割済み)

### Python サーバー (`gm_server/`)

| モジュール | 役割 | テスト |
|---|---|---|
| `config.py` | 環境変数・定数 (`AI_GM_DATA_DIR`, `AI_GM_HOST`, `AI_GM_PORT`, `AI_GM_GEMINI_MODEL`) | — |
| `text.py` | HTML 除去・ダイス式抽出・TTS 話速/音高の正規化 (純粋関数) | `test_text.py` |
| `protocol.py` | FVTT へ送る action メッセージの組み立て | `test_handlers.py` |
| `tts.py` | edge-tts 音声合成 (`SpeechSynthesizer`、声色指定対応) | `test_handlers.py` |
| `llm.py` | Gemini (リトライ付き) / agy CLI 問い合わせ (`LLMService`) | `test_llm.py` |
| `dungeon_scene.py` | ダンジョン生成ウィザード・シーン構築命令 | `test_handlers.py` |
| `handlers/*.py` | event ごとの処理 (`player_message`, `ai_command`, `ai_choice`, `room_entered`) | `test_handlers.py` |
| `router.py` | event 名 → ハンドラの振り分け表 | `test_handlers.py` |
| `app.py` | WebSocket サーバー本体 | 手動 (起動スモーク) |

`ai_gm_server.py` は起動スクリプトだけになりました (`python3 ai_gm_server.py` はそのまま使えます)。
新しい event を足すときは `handlers/` にファイルを追加し、`router.HANDLERS` に登録します。

### FVTT モジュール (`bridge.js` + `scripts/`)

| ファイル | 役割 | 区分 | テスト |
|---|---|---|---|
| `bridge.js` | エントリーポイント (フック登録のみ) | Foundry | — |
| `scripts/actions.js` | action ごとの処理 (チャット・ロール・音声・カード・シーン構築) | Foundry | — |
| `scripts/net/connection.js` | WebSocket 接続・自動再接続・受信の直列処理 | 注入式 | `connection.test.js` |
| `scripts/lib/html.js` | `escapeHtml` ほか | 純粋 | `html.test.js` |
| `scripts/lib/commands.js` | `/ai` `/agy` の解析 | 純粋 | `commands.test.js` |
| `scripts/lib/chat-filter.js` | 転送判定・AI 生成フラグ (ループ防止) | 純粋 | `chat-filter.test.js` |
| `scripts/lib/rooms.js` | 部屋進入判定・訪問記録 | 純粋 | `rooms.test.js` |
| `scripts/lib/scene-docs.js` | Scene / Wall / Light / Journal / Note のデータ組み立て | 純粋 | `scene-docs.test.js` |
| `scripts/lib/cards.js` | チャットカード HTML | 純粋 | `cards.test.js` |

### テストの実行

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest          # Python (tests/python)
npm test                   # FVTT 側の純粋関数 (tests/js, Node 18+ の node:test。依存パッケージなし)
```

---

## 2. Python 側の問題点

### 2.1 本番コードで見つかり、Phase 0 で直したもの

| # | 箇所 | 問題 | 状態 |
|---|---|---|---|
| P1 | `ai_gm_server.py` `dungeon_loop:` 分岐 | パーツが 3 個以下だと `loop_ratio` が未定義のまま使われ `UnboundLocalError` | ✅ `parse_dungeon_choice` で既定値を補う |
| P2 | `query_gemini_with_retry` | 同期 SDK をそのまま呼んでおりイベントループが止まる (応答待ちの間、他の受信を処理できない) | ✅ `asyncio.to_thread` で実行 |
| P3 | 同上 | 「指数バックオフ」と書きつつ 2→4 秒の線形。503 以外 (429 など) は再試行しない | ✅ 2→4→8 秒、429/UNAVAILABLE/RESOURCE_EXHAUSTED も対象 |
| P4 | 音声ファイル名 | `voice_<秒>.mp3` のため、同じ秒に 2 件生成すると上書きされる | ✅ `voice_<ミリ秒>_<連番>.mp3` |
| P5 | `generate_and_create_dungeon_scene` | 幾何生成と PNG 書き出し (特大で数十秒) がイベントループを止める | ✅ 別スレッドで実行 |
| P6 | `tools/dungeon_generator.py` `get_room_flavor_notes` | `cf_map.get(i) or cf_map.get(i + 1)` のため、AI が一部の部屋だけ返すと隣の部屋のフレーバーが混入する | ✅ id の基準 (0/1 始まり) を応答全体で 1 回だけ判定 |
| P7 | `tools/prompt_manager.py` `extract_json_from_response` | 前置き付きの `[{...}]` (要素 1 件) が dict として返る | ✅ 出現順に `raw_decode` |
| P8 | `SYSTEM_INSTRUCTION` | 「あなたファンタジー…」(助詞抜け) | ✅ |
| P9 | `call_agy` | agy 未インストール時に `FileNotFoundError` が上がり、ハンドラごと落ちる | ✅ 空応答として扱い、チャットに警告 |
| P10 | `dice.replace("/r ", "")` | 式の途中にある `/r ` まで消える | ✅ 先頭のみ除去 |
| P11 | ダンジョンの伝承・部屋フレーバー | agy 固定。Gemini キーがあっても使わない | ✅ Reference の `query_llm_json` を移植 (Gemini 優先 → agy) |
| P12 | 受信ループ | 不正 JSON・未対応 event の扱いが暗黙 | ✅ `router.dispatch` でログを出して無視 |

### 2.2 Reference を移植するときに直すもの

| # | 重要度 | 箇所 (reference) | 問題 | 修正方針 |
|---|---|---|---|---|
| R1 | 高 | `ai_gm_server.py` 324, 472 | `DungeonGenerator(scene_mode=...)` を呼ぶが、本番の生成器に `scene_mode` 引数は無く `TypeError` で落ちる。壁タイプ (`door` 等) を返す `extract_walls` も無い | 🔜 生成器に scene_mode・壁タイプを実装してから builder を移植 |
| R2 | 高 | `ai_gm_server.py` 771, 781, 421 | 発言者名を `アギィ (AI GM)` `アギィ (Agy)` に変更しているが、本番 bridge のループ検知は `AI GM` / `サポートGM` の前方一致。そのまま統合すると AI の発言が再送されて無限ループ | ✅ bridge 側は AI 生成フラグ (`flags["ai-gm-bridge"].generated`) でも判定するようにした。名前変更は 📝 |
| R3 | 高 | `ai_gm_server.py` 194, 215 | モデルが `gemini-2.5-flash` にハードコード (本番は `gemini-3.8-flash`) | ✅ `AI_GM_GEMINI_MODEL` に一本化 |
| R4 | 中 | `prompt_manager.py` `build_settlement_stage_prompt` ほか | `region_ctx.get("public_lore")` は dict だが `str.format` にそのまま渡すため Python の repr (`{'climate': ...}`) がプロンプトに入る | 🔜 `json.dumps(..., ensure_ascii=False)` で渡す |
| R5 | 中 | `prompt_manager.py` `build_site_rooms_stage_prompt` | `neighbors` だけ +1 して id は 0 始まりのまま。AI から見ると隣接番号と id がずれる | 🔜 両方 0 始まりに統一 (本番の生成器は `neighbors` を出力するようにした) |
| R6 | 中 | `ai_gm_server.py` 全体 | 1 メッセージずつ `await` するため、シーン構築中は NPC 手番・会話がすべて止まる | 🔜 NPC 系 event はタスク化 (`asyncio.create_task`) し、シーン構築だけ直列にする |
| R7 | 中 | `ai_gm_server.py` 57-61 | `agy_session` (会話 ID・ワイルダネス生成器) がモジュールグローバルで、接続をまたいで共有・リロードで消える | 🔜 `SessionState` に移す |
| R8 | 中 | `ai_gm_server.py` 716 | agy の応答を `\n→<br>` 変換だけしてチャットへ流す (HTML 注入) | 🔜 `html.escape` してから `<br>` 化 |
| R9 | 低 | `prompt_manager.py` `load_cached_prompt` | 内蔵テンプレートを使ったときはキャッシュしない。ファイルとの差分に気付きにくい | 🔜 |
| R10 | 低 | `ai_gm_server.py` 351 | `size_label` に `xlarge` が無い | 🔜 `SIZE_PRESETS` を共用 |
| R11 | 低 | `tools/dungeon_generator.py` `get_room_topologies` | `in_cycle` は「追加辺の端点」かどうかで、実際に閉路上にあるかではない | 🔜 橋 (bridge) 判定で計算し直す |

---

## 3. FVTT 側 (`bridge.js`) の問題点

### 3.1 本番コードで見つかり、Phase 0 で直したもの

| # | 問題 | 状態 |
|---|---|---|
| B1 | AI・サーバー由来のテキスト (選択肢ラベル、部屋名、フレーバー、伝承) をエスケープせずに HTML に埋め込んでいる | ✅ `escapeHtml` / `textToHtml` を通す |
| B2 | ループ検知が発言者名 (alias) だけ | ✅ AI が作るメッセージに `flags["ai-gm-bridge"].generated` を付け、それでも判定 |
| B3 | 受信処理が直列化されておらず、シーン構築中に次の action が割り込む | ✅ `BridgeConnection` の受信キュー |
| B4 | GM が複数いると全員が WebSocket 接続・転送する | ✅ `game.users.activeGM` だけが接続 |
| B5 | ボタン処理が `renderChatMessage` (v14 で非推奨) 依存で、描画ごとに listener を付けていた | ✅ `document` へのイベント委譲 |
| B6 | Scene の暗さを v11 形式 (`darkness`) だけで指定 | ✅ `environment.darknessLevel` も指定 |
| B7 | マップノートとジャーナルページを配列の添字で対応付け | ✅ ページ名で対応付け |
| B8 | 部屋進入ナレーションが NPC や非表示トークンの移動にも反応 | ✅ プレイヤー所有かつ表示中のトークンだけ |
| B9 | 壁を省略したとき (大きいマップ) に成功カードが「0本」と出るだけ | ✅ 省略した旨を表示 |

### 3.2 Reference を移植するときに直すもの

重要度「高」は移植前に必ず直すものです。行番号は `reference/tools/bridge.js`。

| # | 重要度 | 行 | 問題 | 修正方針 |
|---|---|---|---|---|
| H1 | 高 | 1750-1785 | NPC 会話を `chatMessage` フックで拾っているが、このフックは発言者本人のクライアントでしか呼ばれない。条件が `isGM` なので、プレイヤーの発言は NPC に一度も届かない | 🔜 GM 側の `createChatMessage` で検知し、`activeGM` だけが処理 |
| H2 | 高 | 1764, 1203 | 発言者トークンを「GM が選択中のトークン」や「シーンの最初の PC」から取っている | 🔜 `message.speaker.token` (無ければ `author.character`) |
| H3 | 高 | 1769 | `currentRoom = rooms[0]` 固定。常に部屋 1 の情景がプロンプトに入る | 🔜 `scripts/lib/rooms.js` の `findRoomAt` で発言者の位置から判定 |
| H4 | 高 | 本番 514-541 と連動 | 統合すると GM の発言が `npc_dialogue_request` と `player_message` の両方で送られ、AI が二重応答 | 🔜 経路を 1 本にする (§5-3 で決定: 近くに NPC がいれば NPC 会話、いなければ AI GM) |
| H5 | 高 | 2061, 2079 | 生成器が `light_preset` / `ambient_sound_src` を部屋データにコピーしないため、部屋ごとの照明と環境音が一切作られない | 🔜 生成器の結果に追加 |
| H6 | 高 | 1290-1326, 1640-1649, 1960-1975, 2104-2147 ほか | AI テキストのエスケープ漏れ (プレイヤー発言経由のプロンプトインジェクションで `<img onerror>` が全員の画面で動く) | 🔜 `scripts/lib/cards.js` 方式で移植 (本番分は ✅) |
| H7 | 高 | 1310 | DM 秘密のヒントを公開カードの `data-hint` 属性に出している (プレイヤーが開発者ツールで読める) | 🔜 GM 側のメモリか GM 専用 flag に保存 |
| M1 | 中 | 2083 | AmbientSound の `radius` は距離単位 (ft) なのにピクセル値を入れている (音が非常に遠くまで届く) | 🔜 `マス数 × grid.distance` |
| M2 | 中 | 2128 | `r.role === "sub_goal"` は常に false (生成器は `type` キーに入れる) | 🔜 `r.type` |
| M3 | 中 | 1196, 1255, 1630, 2329 | 対象シーンを `game.scenes.active` で取るが、衝突判定は GM が表示中のシーンの壁を使う | 🔜 `combat.scene` / `tokenDoc.parent` を引き回す |
| M4 | 中 | 2287-2338 | 技能判定ボタンがプレイヤーには効かず、GM が押しても `1d20+3` 固定 | 🔜 socket で GM に依頼し、dnd5e の `actor.rollSkill` を使う |
| M5 | 中 | 1655-1663 | `item.use()` の失敗時に Roll へフォールバックしない (§6 #13 の記述は誤り)。カードが二重投稿。AI の `dice_formula` を検証しない | 🔜 catch で Roll、`Roll.validate` で検証 |
| M6 | 中 | 2341-2357 | AI 応答待ちの間に手番が進んでも古い行動を実行。行動不能・非表示の NPC も対象 | 🔜 `combat_id/round/turn` を付けて受信時に照合、`combatTurnChange` へ移行 |
| M7 | 中 | 1590-1622 | 移動探索に現在地が候補として無く、隣接済みでも 3 マス動く。`flank` は接近と同じ。シーン外判定なし | 🔜 純粋関数 `planSteps` に切り出してテスト |
| M8 | 中 | 1181-1187 + 1144-1151 | 重要度 4 以上の記憶が長期記憶に二重登録される | 🔜 `scripts/lib/memory.js` でテスト付きで実装 |
| M9 | 中 | 1156-1157 | 鮮明度を毎回 0.01 単位に丸めるため、減衰量が 0.005 未満だと永久に変化しない。下限 0.1 の直後に 0.15 未満を削除しており下限が無意味 | 🔜 内部値は丸めない |
| M10 | 中 | 1176-1191 | `setFlag` を連続 2 回、読み書きに排他なし。並行応答で記憶が消える | 🔜 1 回の更新にまとめ、actor ごとに直列化 |
| M12 | 中 | 1993-1996 | medium 以上のシーンが 50,000〜200,000px になる (grid 100px 固定) | 📝 grid_size 可変化かサイズ上限 |
| M14 | 中 | 2240-2283 | ボタンの押下状態が DOM にしか残らず、再描画で戻る | 🔜 `message.setFlag` で保存 (本番もまだ未対応) |
| L1 | 低 | 1736 | `/["'“”」]+\$/` が `$` 文字にマッチ (本番版は修正済み) | ✅ 本番版を採用 |
| L2 | 低 | 1721-1758 | `/ai` を 2 系統で登録し、正規表現の判定がずれている | 🔜 一本化 |
| L3 | 低 | 1761 | `[` 始まりの発言を除外していない (AGENTS.md 違反) | ✅ `chat-filter.js` を共用 |
| L4 | 低 | 1352 | NPC 抽出条件が `||` でプレイヤー所有 NPC も含む | 🔜 `&&` |
| L5 | 低 | 1356-1430 ほか | ブレイン GUI の `value="${...}"` 等がエスケープされていない | 🔜 |
| L6 | 低 | 1471-1477 | 保存のたびに全長期記憶の timestamp を上書き | 🔜 |
| L7 | 低 | 1148 / 1158 | 重要度の既定値が 2 と 3 で不一致、範囲チェックなし | 🔜 定数化 + clamp(1,5) |
| L8 | 低 | 1210 | 発言者不在時に全 NPC を 10ft 扱い。発言者 NPC 自身も聴き手に入る | 🔜 |
| L9 | 低 | 1275 | `leave_scene` が `alpha: 0.3` だけでトークンが残る | 🔜 `hidden: true` |
| L10 | 低 | 106, 276 | `localStorage` の JSON.parse 例外未処理、吹き出しタイマー未解除 | 🔜 |
| L11 | 低 | 1854, 1033 | ランドマークを `window` に置いておりリロードで消える | 🔜 journal flag を参照 |
| L13 | 低 | 56-57 | 照明アニメーション `type: "none"` は存在しないキー (⚠️ 未確認) | 🔜 `null` |

### 3.3 プロトコルの不整合 (reference の bridge ⇄ server)

| 項目 | 内容 | 方針 |
|---|---|---|
| `interactive_card` | reference サーバーは一度も送らない | 本番のダンジョンウィザードで使用中のため維持 |
| `player_message` | reference クライアントは送らない (本番は送る) | H4 で経路を決める |
| `ai_command.actors_context` / `user` | サーバーが読まない | 移植時にプロンプトへ組み込む |
| `room_entered.token_name` | サーバーが読まない (ログのみ) | 維持 |
| `execute_npc_turn.thought` | サーバー側は `internal_thought`。`action_type` はクライアント未使用 | 名前を統一 |
| `create_dungeon_scene.density` | クライアント未使用 | 削除候補 |
| `size_key: "xlarge"` | サーバーは対応、GUI に選択肢なし | どちらかに揃える |

---

## 4. 移植の順番

1 フェーズ = 1 PR。各フェーズで、対応する問題を直し、純粋関数部分にテストを付けてから移植します。

| Phase | 内容 | 主な対象 | 前提 |
|---|---|---|---|
| **0 (完了)** | モジュール分割・テスト基盤・本番の不具合修正・Reference のユーティリティ移植 (TTS 声色・JSON 取得) | §2.1, §3.1 | — |
| 1 | 生成器の拡張: `scene_mode` (underground / ruins / wilderness)・壁タイプ (扉・隠し扉・低い壁)・照明プリセット・部屋データへの `light_preset` / `ambient_sound_src` | R1, R5, R11, H5, M1, M2, L13 | — |
| 2 | 4 段階シナリオビルダー (サーバー: `builder_generate_stage` / `builder_build_scene`、FVTT: アトラスジャーナル、DialogV2 化) | R4, R9, R10, M12, L11 | Phase 1 |
| 3 | NPC 会話システム (聴取範囲・記憶と忘却) | H1-H4, H7, M4, M8-M10, L4-L8 | — |
| 4 | NPC 自律戦闘 | M3, M5-M7 | Phase 3 の知覚処理 |
| 5 | 集落 BGM (別 WSL オーディオ生成) と環境音 | — | Phase 1 |
| 6 | アシスタント DM マスコット UI (`game.socket`、`module.json` に `"socket": true`) | L10 | — |

## 5. 判断事項

決定済み (2026-10-16 ken):

1. **音声はプレイヤーにも流す**: `AudioHelper.play(..., true)` で全クライアントに配信します (Phase 0 で反映)。
3. **NPC 会話と通常の AI GM 応答の切り替え** (H4): 発言者の聴取範囲内に NPC がいれば NPC 会話 (`npc_dialogue_request`)、いなければ AI GM (`player_message`) の 1 経路だけで送ります。Phase 3 で実装します。

未決定 (📝):

2. **発言者名を「アギィ」に変えるか** (R2): 変える場合も、ループ検知はフラグ方式にしたので安全です。
4. **大きいマップの扱い** (M12)。
5. **サーバーの待ち受けアドレス**: 既定が `0.0.0.0` で認証なしのため、LAN 内の誰でも接続できます。WSL の localhost 転送で問題が無ければ `AI_GM_HOST=127.0.0.1` を推奨します (既定値は互換性のため据え置き)。
