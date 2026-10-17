// アシスタント DM マスコット UI (Foundry 依存): GM・プレイヤー全員の画面に出すミニキャラ
//
// - GM のクライアントでの状態 (待機・考え中・話し中・切断) と台詞を game.socket で全員に配る (module.json の "socket": true)。
//   GM だけの操作・確認 (ビルダーの下書き・/ai の相談・接続の状態など) は状態も台詞も配らない
// - ドラッグで動かせ、位置はブラウザごとに保存する。GM はダブルクリックでシナリオビルダー、右クリックで NPC ブレイン管理を開く
// - 設定「マスコットを表示」(ユーザーごと) で隠せる。「マスコットの画像」(ワールド。状態ごとにも) で画像を差し替えられる
// - Shift + ダブルクリックで既定の位置に戻す
// 純粋関数は scripts/lib/mascot.js (tests/js)。見た目は styles/mascot.css

import { MODULE_ID } from "./lib/chat-filter.js";
import {
  clampPosition,
  DEFAULT_POSITION,
  MASCOT_NAME,
  MascotController,
  mascotForAction,
  mascotForEvent,
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
  offline: { key: "mascotImageOffline", label: "切断" },
});

let widget = null;
let controller = null;
// GM の操作 (ビルダー・ブレイン管理を開く)。bridge.js から受け取る
let gmActions = { openBuilder: null, openBrain: null };

/** 設定 (init フックで呼ぶ) */
export function registerMascotSettings() {
  game.settings.register(MODULE_ID, SHOW_SETTING, {
    name: "マスコットを表示",
    hint: "画面にアシスタント DM のミニキャラ (アギィ) を表示します。この設定はユーザーごとです。",
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
    start = { x: ev.clientX, y: ev.clientY, left: rect.left, top: rect.top };
    el.setPointerCapture?.(ev.pointerId);
  });
  el.addEventListener("pointermove", ev => {
    if (!start) return;
    const pos = clampPosition(
      { left: start.left + ev.clientX - start.x, top: start.top + ev.clientY - start.y },
      { width: window.innerWidth, height: window.innerHeight },
    );
    el.style.left = `${pos.left}px`;
    el.style.top = `${pos.top}px`;
  });
  const end = () => {
    if (!start) return;
    start = null;
    savePosition({ left: parseInt(el.style.left, 10), top: parseInt(el.style.top, 10) });
  };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
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
  widget.querySelector(".ai-mascot-nameplate").title = game.user?.isGM
    ? "ドラッグ: 移動 / ダブルクリック: シナリオビルダー / 右クリック: NPC ブレイン管理 / Shift + ダブルクリック: 位置を戻す"
    : "ドラッグ: 移動 / Shift + ダブルクリック: 位置を戻す";
  document.body.appendChild(widget);
  enableDrag(widget);

  widget.addEventListener("dblclick", ev => {
    // Shift + ダブルクリック: 既定の位置に戻す (誰でも)
    if (ev.shiftKey) {
      resetPosition();
      return;
    }
    if (game.user?.isGM && gmActions.openBuilder) gmActions.openBuilder();
    else controller?.set({ state: "speaking", text: "私は副マスターのアギィ！冒険のサポートはお任せあれ！", durationMs: 4000 });
  });
  widget.addEventListener("contextmenu", ev => {
    if (!game.user?.isGM || !gmActions.openBrain) return;
    ev.preventDefault();
    gmActions.openBrain();
  });

  controller?.dispose();
  controller = new MascotController({ render });
  render(controller.current);
}

/** 既定の位置に戻す (Shift + ダブルクリック) */
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
    setMascotState("offline", "サーバーと通信できないみたい…", 4000, { isPublic: false });
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
  setMascotState("offline", "サーバーにつないでいます… (Python サーバーは起動していますか？)", 6000, { isPublic: false });
}

/** 接続と切断 */
export function mascotOnConnection(open) {
  if (open) setMascotState("idle", "準備完了！ダブルクリックでビルダー、右クリックで NPC ブレイン管理が開くよ！", 5000, { isPublic: false });
  else setMascotState("offline", "サーバーとの接続が切れたみたい…再接続するね。", 5000, { isPublic: false });
}
