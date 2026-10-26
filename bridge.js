// AI GM Bridge - FVTT 側エントリーポイント
// 実装は scripts/ 以下に機能単位で分割しています:
//   scripts/lib/*        Foundry 非依存の純粋関数 (tests/js でテスト)
//   scripts/net/*        Python サーバーとの WebSocket 接続
//   scripts/actions.js   サーバーからの action 処理

import { handleServerAction } from "./scripts/actions.js";
import { collectAdventureLog, setLogSender } from "./scripts/adventure-log.js";
import { registerAiSettingsMenu } from "./scripts/ai-settings.js";
import { openScenarioBuilder, setBuilderSender } from "./scripts/builder.js";
import { buildRoomNarrationHtml } from "./scripts/lib/cards.js";
import { generatedFlags, MODULE_ID, playerMessageToForward } from "./scripts/lib/chat-filter.js";
import { AI_COMMAND_RGX, cleanCommandPrompt, isBrainCommand, isBuilderCommand, parseAiCommand } from "./scripts/lib/commands.js";
import {
  findRoomAt,
  isRoomReached,
  markRoomReached,
  RoomVisitTracker,
  shouldNarrateToken,
  tokenCenter,
} from "./scripts/lib/rooms.js";
import { BridgeConnection } from "./scripts/net/connection.js";
import {
  initMascot,
  listenMascotSocket,
  mascotOnAction,
  mascotOnConnecting,
  mascotOnConnection,
  mascotOnSend,
  registerMascotMenuItem,
  registerMascotSettings,
  registerVoiceTestMenu,
} from "./scripts/mascot.js";
import {
  handleNpcCheckRoll,
  isNpcCheckRoll,
  onNpcCheckClick,
  openNpcBrainManager,
  sendDialogueIfHeard,
  setNpcSender,
} from "./scripts/npc.js";
import {
  isNpcTurnMessage,
  registerNpcCombatHooks,
  registerNpcCombatSettings,
  setCombatSender,
} from "./scripts/npc-combat.js";

let connection = null;

let mascotWarned = false;

/** マスコットの表示で失敗しても、サーバーとの接続やチャットの処理は止めない (画面への通知は最初の 1 回だけ) */
function safely(label, fn) {
  try {
    return fn();
  } catch (e) {
    console.warn(`AI GM Bridge | マスコットの${label}に失敗しました (接続には影響しません)`, e);
    if (!mascotWarned) {
      mascotWarned = true;
      ui.notifications?.warn("AI GM Bridge: マスコットを表示できませんでした。サーバーとの連携はそのまま使えます (詳細はコンソール)。");
    }
    return undefined;
  }
}

/** サーバーへ送る (マスコットの「考え中」や「切断」も切り替える) */
function sendToServer(payload) {
  const ok = Boolean(connection?.send(payload));
  safely("更新", () => mascotOnSend(payload, ok));
  return ok;
}

/** 複数 GM がログインしていても 1 人だけがサーバー連携を担当する */
function isPrimaryGM() {
  if (!game.user?.isGM) return false;
  const activeGM = game.users?.activeGM;
  return activeGM ? activeGM.isSelf : true;
}

function sendAiCommand(prompt) {
  // `/ai ビルダー` はシナリオビルダー画面を開くだけ (サーバーへは送らない)
  if (isBuilderCommand(prompt)) {
    openScenarioBuilder();
    return;
  }
  // `/ai NPC` は NPC のブレイン管理画面を開くだけ
  if (isBrainCommand(prompt)) {
    openNpcBrainManager();
    return;
  }
  if (!connection) {
    ui.notifications.warn("AI GM Bridge: Python サーバー (8765) に接続されていません。");
    return;
  }
  console.log("AI GM Bridge | GMの/aiコマンドを検知、送信中:", prompt);
  if (sendToServer({ event: "ai_command", user: game.user.name || "GM", prompt })) {
    ui.notifications.info("サポートGM Geminiちゃん に指示を送信中...");
  }
}

// 1. FVTT スラッシュコマンド登録 (/ai, /agy)
function registerAiCommands() {
  // v13 以降は foundry.applications.sidebar.tabs.ChatLog (グローバルの ChatLog は非推奨で、v15 で消える)
  const ChatLogClass = globalThis.foundry?.applications?.sidebar?.tabs?.ChatLog
    ?? (typeof ChatLog === "undefined" ? null : ChatLog);
  if (!ChatLogClass?.CHAT_COMMANDS) return;
  const aiCmd = {
    rgx: AI_COMMAND_RGX,
    isRoll: false,
    isMultiline: false,
    fn: function(command, match) {
      if (game.user && !game.user.isGM) return false;
      sendAiCommand(cleanCommandPrompt(match[1]));
      return false; // メッセージ生成を防止
    }
  };
  ChatLogClass.CHAT_COMMANDS.ai = aiCmd;
  ChatLogClass.CHAT_COMMANDS.agy = aiCmd;
}

// モジュール評価時および init フックで即座にコマンド登録
registerAiCommands();
Hooks.once("init", registerAiCommands);
Hooks.once("init", registerNpcCombatSettings);
Hooks.once("init", registerMascotSettings);

// FVTTのチャット入力直接インターセプト (/ai, /agy スラッシュコマンド)
Hooks.on("chatMessage", (chatLog, messageText) => {
  if (game.user && !game.user.isGM) return;
  const prompt = parseAiCommand(messageText);
  if (prompt !== null) {
    sendAiCommand(prompt);
    return false; // FVTT標準の「有効なコマンドではありません」エラーを防止し入力を消費
  }
});

// チャット内ボタン (選択肢・シーン移動・設定録) のクリックをイベント委譲で処理
// (renderChatMessage / renderChatMessageHTML の世代差に依存しない)
function onChatButtonClick(ev) {
  if (!game.user?.isGM) return;
  const btn = ev.target?.closest?.(".ai-gm-choice-btn, .ai-gm-scene-jump-btn, .ai-gm-scene-view-btn, .ai-gm-journal-open-btn");
  if (!btn) return;
  ev.preventDefault();

  if (btn.classList.contains("ai-gm-choice-btn")) {
    if (btn.disabled) return;
    const value = btn.getAttribute("data-choice-value");
    const label = btn.getAttribute("data-choice-label") || btn.textContent.trim();
    // 兄弟ボタンの無効化
    btn.closest(".ai-gm-choice-container")?.querySelectorAll(".ai-gm-choice-btn").forEach(b => {
      b.disabled = true;
      b.style.opacity = "0.5";
    });
    btn.style.borderColor = "#2ecc71";
    btn.style.fontWeight = "bold";
    btn.style.opacity = "1.0";
    ui.notifications.info(`サポートGMへ回答: ${label}`);
    sendToServer({ event: "ai_choice", user: game.user.name, value, label });
    return;
  }

  if (btn.classList.contains("ai-gm-journal-open-btn")) {
    game.journal.get(btn.getAttribute("data-journal-id"))?.sheet.render(true);
    return;
  }

  const targetScene = game.scenes.get(btn.getAttribute("data-scene-id"));
  if (!targetScene) return;
  if (btn.classList.contains("ai-gm-scene-jump-btn")) {
    targetScene.activate().then(() => ui.notifications.info(`シーン「${targetScene.name}」へ移動しました`));
  } else {
    targetScene.view().then(() => ui.notifications.info(`シーン「${targetScene.name}」をGM画面に表示しました`));
  }
}

/** 部屋のマップノートから探索録ジャーナルのページを引く (無ければ null) */
function findRoomPage(scene, room) {
  const note = scene.notes?.find?.(n => n.text === room.name && n.entryId);
  if (!note) return null;
  const entry = game.journal?.get(note.entryId);
  return (note.pageId ? entry?.pages?.get(note.pageId) : null)
    ?? entry?.pages?.find?.(p => p.name === room.name)
    ?? null;
}

Hooks.once("ready", async function() {
  console.log("AI GM Bridge | === チャット双方向 & サポートGM Geminiちゃん モジュール ロード完了 ===");
  // NPC 会話の「本音を引き出す」判定ボタンはプレイヤーも押す (押した人のキャラクターで振る)
  document.addEventListener("click", onNpcCheckClick);
  // アシスタント DM マスコットは全員の画面に出す。GM の画面での状態が game.socket で配られる
  safely("表示", () => initMascot(game.user.isGM ? { openBuilder: openScenarioBuilder, openBrain: () => openNpcBrainManager() } : {}));
  safely("同期", listenMascotSocket);
  if (!game.user.isGM) return;

  registerAiCommands();
  document.addEventListener("click", onChatButtonClick);

  if (!isPrimaryGM()) {
    console.log("AI GM Bridge | 他の GM がサーバー連携を担当しているため接続しません");
    return;
  }

  connection = new BridgeConnection({
    onMessage: data => {
      safely("更新", () => mascotOnAction(data));
      return handleServerAction(data);
    },
    onStatus: open => safely("更新", () => mascotOnConnection(open)),
    notify: {
      info: msg => ui.notifications.info(msg),
      warn: msg => ui.notifications.warn(msg),
    },
  });
  // つながるまでは GM の画面のマスコットを「切断」にしておく (サーバーを起動していないと気づけるように)
  safely("更新", mascotOnConnecting);
  connection.connect();
  setBuilderSender(sendToServer);
  setNpcSender(sendToServer);
  safely("メニュー", () => registerVoiceTestMenu(sendToServer));
  // Geminiちゃんのメニューから AI の設定 (作業ごとの接続先) を開く
  safely("メニュー", () => registerAiSettingsMenu(sendToServer));
  setCombatSender(sendToServer);
  setLogSender(sendToServer);
  // Geminiちゃんのメニューから冒険日誌を更新する (最新の回に書き足す。回が無ければ作る)
  safely("メニュー", () => registerMascotMenuItem({
    id: "adventure-log",
    icon: "📖",
    label: "冒険記録を更新",
    gmOnly: true,
    onSelect: () => collectAdventureLog("update"),
  }));
  // 戦闘トラッカーで NPC の手番になったら AI に行動を頼む (設定「NPC の自律戦闘」で切り替え)
  registerNpcCombatHooks();

  // FVTTのチャット投稿を検知してPythonへ転送するフック (通常発言)
  Hooks.on("createChatMessage", (message) => {
    const prompt = parseAiCommand(message.content || "");
    if (prompt !== null) {
      // 万が一チャットメッセージとしてすり抜けて保存された場合のフォールバック
      try { message.delete(); } catch (e) {}
      sendAiCommand(prompt);
      return;
    }

    // NPC 会話の判定ボタンで振った結果 → NPC に続きを反応させる
    if (isNpcCheckRoll(message)) {
      if (connection.isOpen) handleNpcCheckRoll(message);
      return;
    }

    // AI が動かしている NPC のアイテムのカードなどは発言として転送しない
    if (isNpcTurnMessage(message)) return;

    const content = playerMessageToForward({
      alias: message.speaker?.alias,
      content: message.content,
      isRoll: message.isRoll,
      flags: message.flags,
    });
    if (content === null || !connection.isOpen) return;
    // H4: 発言者の声が届く NPC がいれば NPC 会話、いなければ AI GM。どちらか 1 経路だけで送る
    if (sendDialogueIfHeard(message, content)) return;
    console.log("AI GM Bridge | プレイヤー発言を検知、送信中:", content);
    sendToServer({ event: "player_message", user: message.author?.name || "Player", content });
  });

  // プレイヤー/トークンの部屋進入検知 & フレーバー自動通知 & 音声読み上げ
  const visits = new RoomVisitTracker();
  Hooks.on("updateToken", async (tokenDoc, change) => {
    if (change.x === undefined && change.y === undefined) return;
    // NPC や非表示トークン (AI の NPC 移動など) ではナレーションしない。PC は所有者未設定でも対象
    if (!shouldNarrateToken({
      hidden: tokenDoc.hidden,
      hasPlayerOwner: tokenDoc.actor?.hasPlayerOwner,
      actorType: tokenDoc.actor?.type,
    })) return;

    const scene = tokenDoc.parent;
    const rooms = scene?.getFlag?.(MODULE_ID, "rooms");
    if (!rooms?.length) return;

    const gridSize = scene.grid?.size ?? 100;
    const center = tokenCenter({
      x: change.x ?? tokenDoc.x,
      y: change.y ?? tokenDoc.y,
      width: tokenDoc.width,
      height: tokenDoc.height,
    }, gridSize);
    const currentRoom = findRoomAt(rooms, center.x, center.y, {
      gridSize,
      offsetX: scene.dimensions?.sceneX ?? 0,
      offsetY: scene.dimensions?.sceneY ?? 0,
    });
    if (!currentRoom) {
      // 通路に出たら記録を消す (同じ部屋に入り直したとき、印が消されていればまた読み上げる)
      visits.changedRoom(tokenDoc.id, null);
      return;
    }

    // 部屋ページ (探索録) の情景テキスト先頭に <到達済> があれば読み上げない。読み上げたら印を付ける
    const page = findRoomPage(scene, currentRoom);
    if (page) {
      if (!visits.changedRoom(tokenDoc.id, currentRoom.id)) return;
      if (isRoomReached(page.text?.content)) return;
      if (!visits.begin(scene.id, currentRoom.id)) return;
    } else if (!visits.enter(scene.id, tokenDoc.id, currentRoom.id)) {
      return;
    }

    try {
      console.log(`AI GM Bridge | トークン「${tokenDoc.name}」が「${currentRoom.name}」に進入！`);
      await ChatMessage.create({
        speaker: { alias: "AI GM (情景描写)" },
        content: buildRoomNarrationHtml(currentRoom),
        flags: generatedFlags(),
      });

      if (connection.isOpen) {
        sendToServer({
          event: "room_entered",
          token_name: tokenDoc.name,
          room_name: currentRoom.name,
          flavor: currentRoom.flavor,
        });
      }

      if (page) {
        await page.update({ "text.content": markRoomReached(page.text?.content) });
      }
    } finally {
      visits.finish(scene.id, currentRoom.id);
    }
  });
});
