// アシスタント DM マスコット UI (Foundry 依存): GM・プレイヤー全員の画面に出すミニキャラ「Geminiちゃん」
// (チャットの発言者名「サポートGM Geminiちゃん」と同じキャラクター)
//
// - GM のクライアントでの状態 (待機・考え中・話し中・切断) と台詞を game.socket で全員に配る (module.json の "socket": true)。
//   GM だけの操作・確認 (ビルダーの下書き・/ai の相談・接続の状態など) は状態も台詞も配らない
// - ドラッグで動かせ、位置はブラウザごとに保存する
// - クリック (左でも右でも) でメニューを出す: GM はシナリオビルダー・NPC ブレイン管理、全員があいさつ・位置を戻す・隠す。
//   ほかの機能は registerMascotMenuItem で項目を足せる (声のテストなど)
// - 設定「マスコットを表示」(ユーザーごと) で隠せる。「マスコットの画像」(ワールド。状態ごとにも) で画像を差し替えられる
// 純粋関数は scripts/lib/mascot.js (tests/js)。見た目は styles/mascot.css

import { MODULE_ID } from "./lib/chat-filter.js";
import {
  clampPosition,
  DEFAULT_POSITION,
  isClickMove,
  MASCOT_NAME,
  MASCOT_TITLE,
  MascotController,
  mascotForAction,
  mascotForEvent,
  mascotMenuItems,
  mascotSvgDataUri,
  normalizeMascotState,
  parseSavedPosition,
} from "./lib/mascot.js";

const SOCKET = `module.${MODULE_ID}`;
const POSITION_KEY = "ai-gm-mascot-pos";
const SHOW_SETTING = "showMascot";
const IMAGE_SETTING = "mascotImage";
// 状態ごとの画像 (空欄なら「マスコットの画像」、それも空なら内蔵の SVG)
const STATE_IMAGE_SETTINGS = Object.freeze({
  thinking: { key: "mascotImageThinking", label: "考え中" },
  speaking: { key: "mascotImageSpeaking", label: "話し中" },
  listening: { key: "mascotImageListening", label: "聞いている" },
  offline: { key: "mascotImageOffline", label: "切断" },
});

let widget = null;
let menu = null;
let controller = null;
// GM の操作 (ビルダー・ブレイン管理を開く)。bridge.js から受け取る
let gmActions = { openBuilder: null, openBrain: null };
// ほかの機能が足したメニューの項目 (id → { icon, label, gmOnly, onSelect })
const extraMenuItems = new Map();

/**
 * マスコットのメニューに項目を足す (同じ id なら置き換える)。
 * 例: registerMascotMenuItem({ id: "voice-test", icon: "🔊", label: "声のテスト", gmOnly: true, onSelect: () => ... })
 */
export function registerMascotMenuItem({ id, icon = "✨", label, gmOnly = false, onSelect }) {
  if (!id || !label || typeof onSelect !== "function") return;
  extraMenuItems.set(id, { id, icon, label, gmOnly, onSelect });
}

/** 設定 (init フックで呼ぶ) */
export function registerMascotSettings() {
  game.settings.register(MODULE_ID, SHOW_SETTING, {
    name: "マスコットを表示",
    hint: "画面にアシスタント DM のミニキャラ (Geminiちゃん) を表示します。この設定はユーザーごとです。",
    scope: "client",
    config: true,
    type: Boolean,
    default: true,
    onChange: value => (value ? initMascot() : removeMascot()),
  });
  game.settings.register(MODULE_ID, IMAGE_SETTING, {
    name: "マスコットの画像",
    hint: "ミニキャラの画像 (透過 PNG / WebP など)。空欄なら内蔵のイラストを使います。",
    scope: "world",
    config: true,
    type: String,
    default: "",
    filePicker: "image",
    onChange: () => controller && render(controller.current),
  });
  for (const { key, label } of Object.values(STATE_IMAGE_SETTINGS)) {
    game.settings.register(MODULE_ID, key, {
      name: `マスコットの画像 (${label})`,
      hint: `「${label}」のときの画像。空欄なら「マスコットの画像」を使います。`,
      scope: "world",
      config: true,
      type: String,
      default: "",
      filePicker: "image",
      onChange: () => controller && render(controller.current),
    });
  }
}

/** 状態の画像: 状態ごとの画像 → 共通の画像 → 内蔵の SVG */
function imageFor(state) {
  const own = STATE_IMAGE_SETTINGS[state] ? setting(STATE_IMAGE_SETTINGS[state].key, "") : "";
  return own || setting(IMAGE_SETTING, "") || mascotSvgDataUri(state);
}

function setting(key, fallback) {
  try {
    return game.settings.get(MODULE_ID, key);
  } catch (e) {
    return fallback;
  }
}

function readPosition() {
  let raw = null;
  try {
    raw = localStorage.getItem(POSITION_KEY);
  } catch (e) {
    raw = null;
  }
  return parseSavedPosition(raw, { width: window.innerWidth, height: window.innerHeight });
}

function savePosition(pos) {
  try {
    localStorage.setItem(POSITION_KEY, JSON.stringify(pos));
  } catch (e) {
    // 保存できなくても動かせる (次回は既定の位置)
  }
}

function render({ state, text }) {
  if (!widget) return;
  widget.dataset.state = state;
  const image = imageFor(state);
  widget.querySelector(".ai-mascot-avatar").style.backgroundImage = `url("${String(image).replace(/["\\]/g, "\\$&")}")`;
  const bubble = widget.querySelector(".ai-mascot-bubble");
  bubble.textContent = text;
  bubble.classList.toggle("show", Boolean(text));
}

function enableDrag(el) {
  let start = null;
  el.addEventListener("pointerdown", ev => {
    if (ev.button !== 0) return;
    const rect = el.getBoundingClientRect();
    start = { x: ev.clientX, y: ev.clientY, left: rect.left, top: rect.top, moved: false };
    el.setPointerCapture?.(ev.pointerId);
  });
  el.addEventListener("pointermove", ev => {
    if (!start) return;
    if (!start.moved && isClickMove(start, { x: ev.clientX, y: ev.clientY })) return;
    if (!start.moved) closeMenu();
    start.moved = true;
    const pos = clampPosition(
      { left: start.left + ev.clientX - start.x, top: start.top + ev.clientY - start.y },
      { width: window.innerWidth, height: window.innerHeight },
    );
    el.style.left = `${pos.left}px`;
    el.style.top = `${pos.top}px`;
  });
  el.addEventListener("pointerup", () => {
    if (!start) return;
    const moved = start.moved;
    start = null;
    // 動かさずに離したらクリック: メニューを開く (開いていれば閉じる)
    if (moved) savePosition({ left: parseInt(el.style.left, 10), top: parseInt(el.style.top, 10) });
    else toggleMenu();
  });
  el.addEventListener("pointercancel", () => {
    if (start?.moved) savePosition({ left: parseInt(el.style.left, 10), top: parseInt(el.style.top, 10) });
    start = null;
  });
}

// 既定の項目の動作 (足された項目と同じく onSelect の形。項目の並びとラベルは scripts/lib/mascot.js)
const BASE_MENU_ACTIONS = Object.freeze({
  builder: () => gmActions.openBuilder?.(),
  brain: () => gmActions.openBrain?.(),
  // 自分の画面だけで話す (全員には配らない)
  greet: () => controller?.set({ state: "speaking", text: `私は${MASCOT_TITLE}、${MASCOT_NAME}だよ！困ったことがあったら手伝うね、任せて！`, durationMs: 4000 }),
  reset: () => resetPosition(),
  hide: () => {
    ui.notifications?.info(`${MASCOT_NAME}を隠したよ。設定「マスコットを表示」でまた呼んでね！`);
    return game.settings.set(MODULE_ID, SHOW_SETTING, false);
  },
});

/** メニューの項目を選んだとき */
function selectMenuItem(id) {
  closeMenu();
  const onSelect = BASE_MENU_ACTIONS[id] ?? extraMenuItems.get(id)?.onSelect;
  return onSelect?.();
}

function toggleMenu() {
  if (menu) closeMenu();
  else openMenu();
}

function openMenu() {
  if (!widget) return;
  closeMenu();
  const items = mascotMenuItems({ isGM: Boolean(game.user?.isGM), extra: [...extraMenuItems.values()] });
  menu = document.createElement("div");
  menu.id = "ai-gm-mascot-menu";
  menu.setAttribute("role", "menu");
  for (const item of items) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("role", "menuitem");
    btn.dataset.id = item.id;
    btn.textContent = `${item.icon} ${item.label}`;
    btn.addEventListener("click", ev => {
      ev.stopPropagation();
      try {
        const done = selectMenuItem(item.id);
        done?.catch?.(e => console.warn("AI GM Bridge | マスコットのメニューで失敗しました", e));
      } catch (e) {
        console.warn("AI GM Bridge | マスコットのメニューで失敗しました", e);
      }
    });
    menu.appendChild(btn);
  }
  document.body.appendChild(menu);
  placeMenu();
  // キーボードでも選べるように最初の項目へ。吹き出しで一言 (自分の画面だけ)
  menu.querySelector("button")?.focus();
  controller?.set({ state: "idle", text: "どうする？任せて！", durationMs: 3000 });
  // メニューの外を押す・Esc で閉じる
  setTimeout(() => {
    document.addEventListener("pointerdown", onOutsidePointer, true);
    document.addEventListener("keydown", onMenuKey, true);
  }, 0);
}

/** マスコットの横 (右に入らなければ左) に出す */
function placeMenu() {
  if (!menu || !widget) return;
  const w = widget.getBoundingClientRect();
  const m = menu.getBoundingClientRect();
  const right = w.right + 6;
  const left = right + m.width > window.innerWidth ? Math.max(0, w.left - m.width - 6) : right;
  const top = Math.max(0, Math.min(window.innerHeight - m.height, w.top));
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;
}

function onOutsidePointer(ev) {
  if (menu?.contains(ev.target) || widget?.contains(ev.target)) return;
  closeMenu();
}

function onMenuKey(ev) {
  if (ev.key === "Escape") return closeMenu();
  if (!menu || !["ArrowDown", "ArrowUp"].includes(ev.key)) return;
  const buttons = [...menu.querySelectorAll("button")];
  const at = buttons.indexOf(document.activeElement);
  const next = (at + (ev.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
  buttons[next]?.focus();
  ev.preventDefault();
}

function closeMenu() {
  document.removeEventListener("pointerdown", onOutsidePointer, true);
  document.removeEventListener("keydown", onMenuKey, true);
  menu?.remove();
  menu = null;
}

/** マスコットを表示する (ready で全員が呼ぶ。設定で隠しているときは何もしない) */
export function initMascot({ openBuilder = null, openBrain = null } = {}) {
  if (openBuilder || openBrain) gmActions = { openBuilder, openBrain };
  if (widget || !setting(SHOW_SETTING, true)) return;
  const pos = readPosition();
  widget = document.createElement("div");
  widget.id = "ai-gm-mascot-widget";
  widget.style.left = `${pos.left}px`;
  widget.style.top = `${pos.top}px`;
  widget.innerHTML = `
    <div class="ai-mascot-bubble"></div>
    <div class="ai-mascot-avatar"></div>
    <div class="ai-mascot-nameplate"><span class="ai-mascot-dot"></span><span class="ai-mascot-name"></span></div>`;
  widget.querySelector(".ai-mascot-name").textContent = MASCOT_NAME;
  widget.title = `${MASCOT_TITLE} ${MASCOT_NAME}\nクリック: メニュー / ドラッグ: 移動`;
  document.body.appendChild(widget);
  enableDrag(widget);

  // 右クリックでも同じメニューを出す (ブラウザのメニューは出さない)
  widget.addEventListener("contextmenu", ev => {
    ev.preventDefault();
    openMenu();
  });

  controller?.dispose();
  controller = new MascotController({ render });
  render(controller.current);
}

/** 既定の位置に戻す (メニューの「位置を戻す」) */
export function resetPosition() {
  if (!widget) return;
  const pos = clampPosition(DEFAULT_POSITION, { width: window.innerWidth, height: window.innerHeight });
  widget.style.left = `${pos.left}px`;
  widget.style.top = `${pos.top}px`;
  try {
    localStorage.removeItem(POSITION_KEY);
  } catch (e) {
    // 消せなくても表示は戻っている
  }
}

export function removeMascot() {
  closeMenu();
  controller?.dispose();
  widget?.remove();
  widget = null;
}

/**
 * マスコットの状態を変える。GM が呼んだときは全員の画面にも配る
 * (isPublic が false のもの = GM だけの操作・確認は、状態も台詞も GM の画面にだけ出す)
 */
export function setMascotState(state, text = "", durationMs = 5000, { isPublic = true, broadcast = true } = {}) {
  const value = normalizeMascotState({ state, text, durationMs });
  controller?.set(value);
  if (broadcast && isPublic && game.user?.isGM && game.socket) {
    game.socket.emit(SOCKET, { action: "sync_mascot", ...value });
  }
}

/** ほかの人の画面からの同期 (ready で全員が登録する)。GM が送ったものだけを受け付ける */
export function listenMascotSocket() {
  game.socket?.on(SOCKET, (data, senderId) => {
    if (data?.action !== "sync_mascot") return;
    const sender = senderId ? game.users?.get(senderId) : null;
    if (sender && !sender.isGM) return;
    controller?.set(normalizeMascotState(data));
  });
}

function apply(reaction) {
  if (reaction) setMascotState(reaction.state, reaction.text, reaction.durationMs, { isPublic: reaction.public });
}

/** サーバーへ送ったとき (bridge.js の送信関数から呼ぶ) */
export function mascotOnSend(payload, ok) {
  if (!ok) {
    setMascotState("offline", "あれれ、サーバーと通信できないみたい…", 4000, { isPublic: false });
    return;
  }
  apply(mascotForEvent(payload));
}

/** サーバーから届いたとき (bridge.js の受信処理から呼ぶ) */
export function mascotOnAction(data) {
  apply(mascotForAction(data));
}

/** サーバーにつなぐ前 (GM の画面だけ)。つながるまで「切断」のままにする */
export function mascotOnConnecting() {
  setMascotState("offline", "サーバーにつないでいるよ…つながらないときは Python サーバーを起動してね！", 6000, { isPublic: false });
}

/** 接続と切断 */
export function mascotOnConnection(open) {
  if (open) setMascotState("idle", "準備完了！困ったら手伝うよ。私をクリックするとメニューが出るよ！", 5000, { isPublic: false });
  else setMascotState("offline", "あっ、サーバーとの接続が切れちゃった…ごめんね、つなぎ直すね。", 5000, { isPublic: false });
}
