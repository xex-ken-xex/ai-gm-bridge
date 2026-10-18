// アシスタント DM マスコット (Geminiちゃん) の純粋関数 (Foundry 非依存): 状態の切り替えと吹き出しのタイマー・
// 送受信ごとの台詞・保存位置の読み込み・内蔵 SVG
// GM の画面での状態を game.socket で全員に配る (scripts/mascot.js)。プレイヤーに見せてよいものだけを配る

// 呼び名は 1 つにそろえる: チャットの発言者名は「サポートGM Geminiちゃん」(gm_server/config.py の SPEAKER_SUPPORT)、名札は「Geminiちゃん」
export const MASCOT_NAME = "Geminiちゃん";
export const MASCOT_TITLE = "星詠みのサポートGM";
// listening: ほかの役 (AI GM のナレーション・NPC) が話している間。Geminiちゃんは口を動かさずに聞いている
export const MASCOT_STATES = Object.freeze(["idle", "thinking", "speaking", "listening", "offline"]);
// Geminiちゃん自身の発言の発言者名 (gm_server/config.py の SPEAKER_SUPPORT。前方一致)
export const SUPPORT_SPEAKER_PREFIX = "サポートGM";
export const DEFAULT_POSITION = Object.freeze({ left: 110, top: 90 });
// ウィジェットの大きさ (画面の外に出さないための目安)
export const WIDGET_SIZE = Object.freeze({ width: 100, height: 110 });
// これより動いたらドラッグ、動かなければクリック (メニューを開く)
export const CLICK_MOVE_PX = 5;
const MAX_BUBBLE_CHARS = 120;
const MAX_DURATION_MS = 60_000;

/** 押してから離すまでの移動がしきい値以内ならクリック (ドラッグではない) */
export function isClickMove(start, end, threshold = CLICK_MOVE_PX) {
  if (!start || !end) return false;
  return Math.hypot(end.x - start.x, end.y - start.y) <= threshold;
}

// 左クリック (右クリックも) で出すメニューの既定の項目。gmOnly はプレイヤーの画面には出さない
export const BASE_MENU_ITEMS = Object.freeze([
  { id: "builder", icon: "🏰", label: "シナリオビルダー", gmOnly: true },
  { id: "brain", icon: "🧠", label: "NPC ブレイン管理", gmOnly: true },
  { id: "greet", icon: "👋", label: "あいさつ", gmOnly: false },
  { id: "reset", icon: "↩️", label: "位置を戻す", gmOnly: false },
  { id: "hide", icon: "🙈", label: "マスコットを隠す", gmOnly: false },
]);

/**
 * メニューに出す項目。extra (ほかの機能が足す項目。声のテストなど) はあいさつの後ろに入れる。
 * 同じ id は先に来たものを使い、ラベルの無い項目は捨てる
 */
export function mascotMenuItems({ isGM = false, extra = [] } = {}) {
  const valid = item => item && typeof item.id === "string" && item.id && String(item.label ?? "").trim();
  const base = BASE_MENU_ITEMS.map(i => ({ ...i }));
  const at = base.findIndex(i => i.id === "greet") + 1;
  const extras = (extra ?? []).filter(valid).map(i => ({
    id: i.id, icon: String(i.icon ?? "✨"), label: String(i.label).trim(), gmOnly: Boolean(i.gmOnly),
  }));
  const seen = new Set();
  return [...base.slice(0, at), ...extras, ...base.slice(at)]
    .filter(i => (isGM || !i.gmOnly) && !seen.has(i.id) && seen.add(i.id));
}

/** 声のテストのメニュー項目 (GM だけ)。voice はサーバーの voice_test に渡す声の種類 */
export const VOICE_TEST_ITEMS = Object.freeze([
  Object.freeze({ id: "voice-test-narrator", icon: "🔊", label: "声のテスト (ナレーター)", gmOnly: true, voice: "narrator" }),
  Object.freeze({ id: "voice-test-support", icon: "🎤", label: "声のテスト (Geminiちゃん)", gmOnly: true, voice: "support" }),
]);

/** 声のテストでサーバーへ送る内容 */
export function voiceTestPayload(voice) {
  return { event: "voice_test", voice: voice === "support" ? "support" : "narrator" };
}

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
 * public: プレイヤーの画面にも配ってよいか (false なら GM の画面にだけ出し、状態も配らない)
 */
export function mascotForEvent(payload = {}) {
  switch (payload.event) {
    case "ai_command":
      return { state: "thinking", text: "任せて！指示を読んでいるよ…", durationMs: 15000, public: false };
    case "room_entered":
      return { state: "thinking", text: "部屋の様子を語る準備をしているよ…", durationMs: 8000, public: true };
    case "player_message":
      return { state: "thinking", text: "AI GM が続きを考えているよ…", durationMs: 15000, public: true };
    case "npc_dialogue_request":
      return { state: "thinking", text: "周りの NPC が反応を考えているよ…", durationMs: 15000, public: true };
    case "npc_turn_request":
      return { state: "thinking", text: `${payload.perception?.npc_name ?? "NPC"}の戦術を考えているよ…`, durationMs: 15000, public: true };
    case "builder_generate_stage":
      return { state: "thinking", text: "任せて！シナリオの下書きを考えるね…", durationMs: 30000, public: false };
    case "builder_build_scene":
      return { state: "thinking", text: "よーし、シーンを組み立てるよ…", durationMs: 30000, public: false };
    case "voice_test":
      return { state: "thinking", text: "声を作っているよ…", durationMs: 15000, public: false };
    default:
      return null;
  }
}

/** サーバーから届いた action ごとのマスコットの反応。null なら変えない */
export function mascotForAction(data = {}) {
  switch (data.action) {
    case "chat":
      // 「話し中」(口を開ける) はGeminiちゃん自身の発言だけ。AI GM のナレーションなどは聞いている
      return String(data.speaker ?? "").startsWith(SUPPORT_SPEAKER_PREFIX)
        ? { state: "speaking", text: "", durationMs: 4000, public: true }
        : { state: "listening", text: "", durationMs: 6000, public: true };
    case "play_sound":
      // speaker が support ならGeminiちゃん自身の声 (話し中)。それ以外はナレーションか NPC (聞いている)。
      // GM にだけ鳴らす音 (声のテスト) は配らない
      return data.speaker === "support"
        ? { state: "speaking", text: "", durationMs: 6000, public: !data.gm_only }
        : { state: "listening", text: "", durationMs: 8000, public: !data.gm_only };
    case "npc_dialogue_response":
      return { state: "listening", text: `👀 ${data.result?.primary_npc_name ?? "NPC"}が答えるみたい…`, durationMs: 5000, public: true };
    case "execute_npc_turn":
      return { state: "listening", text: "⚔️ NPC の手番だよ！", durationMs: 5000, public: true };
    case "builder_stage_result":
      return { state: "speaking", text: "下書きができたよ！見落としがあるかもだから、確認・修正してね。", durationMs: 6000, public: false };
    case "create_dungeon_scene":
      return { state: "speaking", text: "シーンができたよ！", durationMs: 5000, public: false };
    case "interactive_card":
      return { state: "speaking", text: "ねえ、ちょっと確認させて！", durationMs: 6000, public: false };
    case "progress":
      return { state: "thinking", text: data.message ?? "", durationMs: 15000, public: false };
    case "builder_progress":
      return data.finished
        ? { state: "speaking", text: "シーンの構築が終わったよ！うまくできたかな？", durationMs: 5000, public: false }
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
  // setTimeout をそのまま this.setTimer に入れて呼ぶと、ブラウザでは this が window でないため
  // 「Illegal invocation」になる (Node では起きない)。関数で包んで呼ぶ
  constructor({ render, setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = id => clearTimeout(id) } = {}) {
    this.render = render;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.timer = null;
    this.bubbleTimer = null;
    this.current = { state: "idle", text: "" };
  }

  set(input) {
    const { state, text, durationMs } = normalizeMascotState(input);
    this.clearBubbleTimer();
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

  /**
   * 状態はそのままで、吹き出しの台詞だけを出す (メニューを開いたときの一言など)。
   * 切断・考え中の表示を消さない。時間が来たら台詞だけを消す (状態の戻るタイマーには触らない)
   */
  say(text, durationMs = 3000) {
    const value = normalizeMascotState({ state: this.current.state, text, durationMs });
    this.clearBubbleTimer();
    this.current = { state: this.current.state, text: value.text };
    this.render(this.current);
    this.bubbleTimer = this.setTimer(() => {
      this.bubbleTimer = null;
      if (this.current.text !== value.text) return;
      this.current = { state: this.current.state, text: "" };
      this.render(this.current);
    }, value.durationMs);
    return this.current;
  }

  clearBubbleTimer() {
    if (this.bubbleTimer !== null) this.clearTimer(this.bubbleTimer);
    this.bubbleTimer = null;
  }

  dispose() {
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    this.clearBubbleTimer();
  }
}

// 内蔵 SVG の顔 (状態ごと)。目・口・飾り (考え中の「…」、切断の「z」) を描き分ける
const FACES = Object.freeze({
  // 待機: 丸い目と小さな笑み
  idle: {
    eyes: '<ellipse cx="32" cy="40" rx="3" ry="4" fill="#fff"/><ellipse cx="52" cy="40" rx="3" ry="4" fill="#fff"/>',
    mouth: '<path d="M37,52 Q42,56 47,52" stroke="#fff" fill="none" stroke-width="2.5" stroke-linecap="round"/>',
    extra: "",
  },
  // 考え中: 右上を見上げる目・すぼめた口・頭の上の「…」
  thinking: {
    eyes: '<circle cx="32" cy="40" r="4.5" fill="#fff"/><circle cx="52" cy="40" r="4.5" fill="#fff"/>'
      + '<circle cx="33.5" cy="37.5" r="2" fill="#2c3e50"/><circle cx="53.5" cy="37.5" r="2" fill="#2c3e50"/>',
    mouth: '<circle cx="44" cy="54" r="2" fill="#fff"/>',
    extra: '<g fill="#f1c40f"><circle cx="62" cy="16" r="2.6"/><circle cx="70" cy="11" r="3.2"/><circle cx="79" cy="6" r="3.8"/></g>',
  },
  // 話し中: 開いた口
  speaking: {
    eyes: '<path d="M28,41 Q32,36 36,41 M48,41 Q52,36 56,41" stroke="#fff" fill="none" stroke-width="3" stroke-linecap="round"/>',
    mouth: '<ellipse cx="42" cy="54" rx="5" ry="4" fill="#fff"/><ellipse cx="42" cy="55.5" rx="3" ry="2" fill="#c0392b"/>',
    extra: "",
  },
  // 聞いている: 横を向いた目・閉じた口 (ほかの役が話している間)
  listening: {
    eyes: '<circle cx="32" cy="40" r="4.5" fill="#fff"/><circle cx="52" cy="40" r="4.5" fill="#fff"/>'
      + '<circle cx="30" cy="41" r="2" fill="#2c3e50"/><circle cx="50" cy="41" r="2" fill="#2c3e50"/>',
    mouth: '<path d="M38,54 Q42,55.5 46,54" stroke="#fff" fill="none" stroke-width="2.5" stroke-linecap="round"/>',
    extra: "",
  },
  // 切断: 閉じた目と「z」(眠っている)
  offline: {
    eyes: '<path d="M28,41 L36,41 M48,41 L56,41" stroke="#fff" fill="none" stroke-width="3" stroke-linecap="round"/>',
    mouth: '<path d="M39,54 L45,54" stroke="#fff" fill="none" stroke-width="2.5" stroke-linecap="round"/>',
    extra: '<g fill="#ecf0f1" font-family="sans-serif" font-weight="bold"><text x="62" y="20" font-size="10">z</text><text x="70" y="12" font-size="13">z</text></g>',
  },
});

/** 画像が設定されていないときの内蔵 SVG (data URI) */
export function mascotSvgDataUri(state) {
  const glow = { thinking: "#f1c40f", speaking: "#2ecc71", listening: "#3498db", offline: "#7f8c8d" }[state] ?? "#9b59b6";
  const face = FACES[state] ?? FACES.idle;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 84 84">
    <defs><radialGradient id="g" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="${glow}" stop-opacity="0.9"/>
      <stop offset="70%" stop-color="#2c3e50" stop-opacity="0.95"/>
      <stop offset="100%" stop-color="${glow}" stop-opacity="0"/>
    </radialGradient></defs>
    <circle cx="42" cy="44" r="34" fill="url(#g)"/>
    <path d="M16,32 Q42,24 68,32 L46,4 L34,12 Z" fill="#341f97" stroke="#f39c12" stroke-width="2"/>
    ${face.eyes}${face.mouth}${face.extra}
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}
