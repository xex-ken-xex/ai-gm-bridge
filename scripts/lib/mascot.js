// アシスタント DM マスコット (アギィ) の純粋関数 (Foundry 非依存): 状態の切り替えと吹き出しのタイマー・
// 送受信ごとの台詞・保存位置の読み込み・内蔵 SVG
// GM の画面での状態を game.socket で全員に配る (scripts/mascot.js)。プレイヤーに見せてよい台詞だけを配る

export const MASCOT_NAME = "アギィ (Agy)";
export const MASCOT_STATES = Object.freeze(["idle", "thinking", "speaking", "offline"]);
export const DEFAULT_POSITION = Object.freeze({ left: 110, top: 90 });
// ウィジェットの大きさ (画面の外に出さないための目安)
export const WIDGET_SIZE = Object.freeze({ width: 90, height: 110 });
const MAX_BUBBLE_CHARS = 120;
const MAX_DURATION_MS = 60_000;

/** 状態・台詞・表示時間を検める (ソケットで届いた値もここを通す) */
export function normalizeMascotState({ state, text = "", durationMs = 5000 } = {}) {
  return {
    state: MASCOT_STATES.includes(state) ? state : "idle",
    text: String(text ?? "").replace(/\s+/g, " ").trim().slice(0, MAX_BUBBLE_CHARS),
    durationMs: Math.min(MAX_DURATION_MS, Math.max(500, Number(durationMs) || 5000)),
  };
}

/**
 * localStorage に保存した位置を読む (L10: 壊れた JSON でも落ちない)。画面の外なら内側に戻す
 */
export function parseSavedPosition(raw, viewport = { width: Infinity, height: Infinity }) {
  let saved = null;
  try {
    saved = raw ? JSON.parse(raw) : null;
  } catch (e) {
    saved = null;
  }
  return clampPosition({
    left: Number.isFinite(Number(saved?.left)) ? Number(saved.left) : DEFAULT_POSITION.left,
    top: Number.isFinite(Number(saved?.top)) ? Number(saved.top) : DEFAULT_POSITION.top,
  }, viewport);
}

export function clampPosition({ left, top }, { width = Infinity, height = Infinity } = {}) {
  return {
    left: Math.round(Math.max(0, Math.min(width - WIDGET_SIZE.width, left))),
    top: Math.round(Math.max(0, Math.min(height - WIDGET_SIZE.height, top))),
  };
}

/**
 * FVTT → サーバーへ送った event ごとのマスコットの反応。null なら変えない。
 * public: プレイヤーの画面にも台詞を出してよいか (false なら状態だけを配る)
 */
export function mascotForEvent(payload = {}) {
  switch (payload.event) {
    case "ai_command":
      return { state: "thinking", text: "指示を解析しているよ…", durationMs: 15000, public: false };
    case "player_message":
      return { state: "thinking", text: "AI GM が続きを考えているよ…", durationMs: 15000, public: true };
    case "npc_dialogue_request":
      return { state: "thinking", text: "周りの NPC が反応を考えているよ…", durationMs: 15000, public: true };
    case "npc_turn_request":
      return { state: "thinking", text: `${payload.perception?.npc_name ?? "NPC"}の戦術を考えているよ…`, durationMs: 15000, public: true };
    case "builder_generate_stage":
      return { state: "thinking", text: "シナリオの下書きを考えているよ…", durationMs: 30000, public: false };
    case "builder_build_scene":
      return { state: "thinking", text: "シーンを組み立てているよ…", durationMs: 30000, public: false };
    default:
      return null;
  }
}

/** サーバーから届いた action ごとのマスコットの反応。null なら変えない */
export function mascotForAction(data = {}) {
  switch (data.action) {
    case "chat":
    case "play_sound":
      return { state: "speaking", text: "", durationMs: 4000, public: true };
    case "npc_dialogue_response":
      return { state: "speaking", text: `💬 ${data.result?.primary_npc_name ?? "NPC"}が反応したよ！`, durationMs: 5000, public: true };
    case "execute_npc_turn":
      return { state: "speaking", text: "⚔️ NPC の行動！", durationMs: 5000, public: true };
    case "builder_stage_result":
      return { state: "speaking", text: "下書きができたよ！内容を確認・修正してみてね。", durationMs: 6000, public: false };
    case "create_dungeon_scene":
      return { state: "speaking", text: "シーンができたよ！", durationMs: 5000, public: false };
    case "interactive_card":
      return { state: "speaking", text: "GM に確認したいことがあるよ。", durationMs: 6000, public: false };
    case "progress":
      return { state: "thinking", text: data.message ?? "", durationMs: 15000, public: false };
    case "builder_progress":
      return data.finished
        ? { state: "speaking", text: "シーンの構築が終わったよ！", durationMs: 5000, public: false }
        : { state: "thinking", text: "", durationMs: 30000, public: false };
    default:
      return null;
  }
}

/**
 * 状態と吹き出しの切り替え。表示時間が過ぎたら待機 (idle) に戻す。
 * 新しい状態が来たら前のタイマーを止める (L10: 吹き出しのタイマーが残って新しい台詞を消さない)。
 * 切断 (offline) はつながるまで戻さない
 */
export class MascotController {
  constructor({ render, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
    this.render = render;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.timer = null;
    this.current = { state: "idle", text: "" };
  }

  set(input) {
    const { state, text, durationMs } = normalizeMascotState(input);
    if (this.timer !== null) {
      this.clearTimer(this.timer);
      this.timer = null;
    }
    this.current = { state, text };
    this.render(this.current);
    if (state === "offline" && !text) return this.current;
    this.timer = this.setTimer(() => {
      this.timer = null;
      this.current = { state: state === "offline" ? "offline" : "idle", text: "" };
      this.render(this.current);
    }, durationMs);
    return this.current;
  }

  dispose() {
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
  }
}

/** 画像が設定されていないときの内蔵 SVG (data URI) */
export function mascotSvgDataUri(state) {
  const glow = { thinking: "#f1c40f", speaking: "#2ecc71", offline: "#7f8c8d" }[state] ?? "#9b59b6";
  const eyes = state === "thinking"
    ? "M28,38 L36,38 M48,38 L56,38"
    : state === "offline"
      ? "M28,40 Q32,44 36,40 M48,40 Q52,44 56,40"
      : "M32,36 A3,4 0 1,1 32,44 A3,4 0 1,1 32,36 M52,36 A3,4 0 1,1 52,44 A3,4 0 1,1 52,36";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 84 84">
    <defs><radialGradient id="g" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="${glow}" stop-opacity="0.9"/>
      <stop offset="70%" stop-color="#2c3e50" stop-opacity="0.95"/>
      <stop offset="100%" stop-color="${glow}" stop-opacity="0"/>
    </radialGradient></defs>
    <circle cx="42" cy="44" r="34" fill="url(#g)"/>
    <path d="M16,32 Q42,24 68,32 L46,4 L34,12 Z" fill="#341f97" stroke="#f39c12" stroke-width="2"/>
    <path d="${eyes}" stroke="#fff" fill="none" stroke-width="3" stroke-linecap="round"/>
    ${state === "thinking" ? '<rect x="26" y="54" width="32" height="18" rx="3" fill="#e67e22" stroke="#fff" stroke-width="1.5"/>' : ""}
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}
