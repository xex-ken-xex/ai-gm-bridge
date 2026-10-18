import { test } from "node:test";
import assert from "node:assert/strict";
import {
  clampPosition,
  DEFAULT_POSITION,
  isClickMove,
  mascotMenuItems,
  WIDGET_SIZE,
  MascotController,
  mascotForAction,
  mascotForEvent,
  mascotSvgDataUri,
  normalizeMascotState,
  parseSavedPosition,
} from "../../scripts/lib/mascot.js";

function fakeTimers() {
  const timers = new Map();
  let next = 1;
  return {
    timers,
    setTimer: (fn, ms) => { const id = next++; timers.set(id, { fn, ms }); return id; },
    clearTimer: id => timers.delete(id),
    fire() { for (const [id, t] of [...timers]) { timers.delete(id); t.fn(); } },
  };
}

test("parseSavedPosition は壊れた JSON でも既定の位置にし、画面の外なら内側に戻す (L10)", () => {
  assert.deepEqual(parseSavedPosition("{oops"), DEFAULT_POSITION);
  assert.deepEqual(parseSavedPosition(null), DEFAULT_POSITION);
  assert.deepEqual(parseSavedPosition('{"left":"x","top":40}'), { left: DEFAULT_POSITION.left, top: 40 });
  assert.deepEqual(parseSavedPosition('{"left":5000,"top":-20}', { width: 1000, height: 800 }), { left: 1000 - WIDGET_SIZE.width, top: 0 });
  assert.deepEqual(clampPosition({ left: 10.4, top: 20.6 }), { left: 10, top: 21 });
});

test("normalizeMascotState は不明な状態・長い台詞・極端な時間を丸める", () => {
  const v = normalizeMascotState({ state: "dance", text: "あ".repeat(500), durationMs: 1e9 });
  assert.equal(v.state, "idle");
  assert.equal(v.text.length, 120);
  assert.equal(v.durationMs, 60000);
  assert.equal(normalizeMascotState({ state: "thinking", durationMs: "x" }).durationMs, 5000);
});

test("MascotController は前のタイマーを止め、時間が過ぎたら待機に戻す (L10)", () => {
  const t = fakeTimers();
  const shown = [];
  const c = new MascotController({ render: v => shown.push(v), setTimer: t.setTimer, clearTimer: t.clearTimer });
  c.set({ state: "thinking", text: "考え中", durationMs: 10000 });
  c.set({ state: "speaking", text: "できた", durationMs: 3000 });
  assert.equal(t.timers.size, 1); // 古いタイマーは止まっている
  t.fire();
  assert.deepEqual(shown.at(-1), { state: "idle", text: "" });
  assert.deepEqual(shown.map(s => s.text), ["考え中", "できた", ""]);
});

test("MascotController: 切断は台詞が消えても切断のまま", () => {
  const t = fakeTimers();
  const shown = [];
  const c = new MascotController({ render: v => shown.push(v), setTimer: t.setTimer, clearTimer: t.clearTimer });
  c.set({ state: "offline", text: "切れた" });
  t.fire();
  assert.deepEqual(shown.at(-1), { state: "offline", text: "" });
  c.set({ state: "offline" });
  assert.equal(t.timers.size, 0);
});

test("mascotForEvent / mascotForAction: ビルダーや GM 宛てのものはプレイヤーに配らない", () => {
  assert.equal(mascotForEvent({ event: "builder_generate_stage" }).public, false);
  assert.equal(mascotForEvent({ event: "ai_command" }).public, false);
  assert.equal(mascotForEvent({ event: "player_message" }).state, "thinking");
  assert.match(mascotForEvent({ event: "npc_turn_request", perception: { npc_name: "オーク" } }).text, /オーク/);
  assert.equal(mascotForEvent({ event: "room_entered" }).state, "thinking");
  assert.equal(mascotForEvent({ event: "ai_choice" }), null);
  assert.equal(mascotForAction({ action: "progress", message: "秘密の遺跡" }).public, false);
  assert.equal(mascotForAction({ action: "builder_stage_result" }).public, false);
  assert.match(mascotForAction({ action: "npc_dialogue_response", result: { primary_npc_name: "エドガー" } }).text, /エドガー/);
  assert.equal(mascotForAction({ action: "builder_progress", finished: true }).state, "speaking");
  assert.equal(mascotForAction({ action: "unknown" }), null);
});

test("mascotSvgDataUri は状態ごとの SVG を返す", () => {
  assert.ok(mascotSvgDataUri("thinking").startsWith("data:image/svg+xml;utf8,"));
  assert.notEqual(mascotSvgDataUri("thinking"), mascotSvgDataUri("idle"));
});

test("名札とチャットの発言者名は同じキャラクター (サポートGM Geminiちゃん)", async () => {
  const { MASCOT_NAME, MASCOT_TITLE } = await import("../../scripts/lib/mascot.js");
  const { isAiAlias } = await import("../../scripts/lib/chat-filter.js");
  assert.equal(MASCOT_NAME, "Geminiちゃん");
  assert.match(MASCOT_TITLE, /サポートGM/);
  assert.ok(isAiAlias(`サポートGM ${MASCOT_NAME}`));
});

test("内蔵の顔: 考え中は「…」、切断は「z」、話し中だけ口を開ける", () => {
  const svg = s => decodeURIComponent(mascotSvgDataUri(s).split(",")[1]);
  assert.ok(svg("thinking").includes("#f1c40f\"><circle"));
  assert.ok(svg("offline").includes(">z</text>"));
  assert.ok(svg("speaking").includes("#c0392b"));
  assert.ok(!svg("idle").includes("#c0392b"));
});

test("口を開けて話すのはGeminiちゃん自身の発言だけ。ナレーション・NPC の声は聞いている", () => {
  assert.equal(mascotForAction({ action: "chat", speaker: "サポートGM Geminiちゃん" }).state, "speaking");
  assert.equal(mascotForAction({ action: "chat", speaker: "AI GM" }).state, "listening");
  assert.equal(mascotForAction({ action: "chat" }).state, "listening");
  assert.equal(mascotForAction({ action: "play_sound" }).state, "listening");
  assert.equal(mascotForAction({ action: "npc_dialogue_response", result: {} }).state, "listening");
  assert.equal(mascotForAction({ action: "execute_npc_turn" }).state, "listening");
  assert.equal(normalizeMascotState({ state: "listening" }).state, "listening");
});

test("MascotController の既定のタイマーは this を持たずに呼ぶ (ブラウザの Illegal invocation 対策)", () => {
  const realSet = globalThis.setTimeout;
  const realClear = globalThis.clearTimeout;
  // ブラウザの setTimeout と同じく、this が window (globalThis) 以外なら例外にする
  globalThis.setTimeout = function (fn, ms) {
    if (this !== undefined && this !== globalThis) throw new TypeError("Illegal invocation");
    return realSet(fn, ms);
  };
  globalThis.clearTimeout = function (id) {
    if (this !== undefined && this !== globalThis) throw new TypeError("Illegal invocation");
    return realClear(id);
  };
  try {
    const c = new MascotController({ render: () => {} });
    c.set({ state: "thinking", text: "考え中", durationMs: 1000 });
    c.set({ state: "speaking", text: "できた", durationMs: 1000 });
    c.dispose();
  } finally {
    globalThis.setTimeout = realSet;
    globalThis.clearTimeout = realClear;
  }
});

test("isClickMove は少し動いただけならクリック、しきい値を超えたらドラッグ", () => {
  assert.ok(isClickMove({ x: 10, y: 10 }, { x: 13, y: 14 }));
  assert.ok(!isClickMove({ x: 10, y: 10 }, { x: 20, y: 10 }));
  assert.ok(!isClickMove(null, { x: 0, y: 0 }));
});

test("mascotMenuItems は GM だけの項目をプレイヤーに出さず、足された項目はあいさつの後ろに入れる", () => {
  const ids = items => items.map(i => i.id);
  assert.deepEqual(ids(mascotMenuItems({ isGM: true })), ["builder", "brain", "greet", "reset", "hide"]);
  assert.deepEqual(ids(mascotMenuItems({ isGM: false })), ["greet", "reset", "hide"]);
  const extra = [
    { id: "voice-test", icon: "🔊", label: "声のテスト", gmOnly: true },
    { id: "nolabel", label: " " },
    { id: "greet", label: "上書きしようとしても既定が勝つ" },
  ];
  assert.deepEqual(ids(mascotMenuItems({ isGM: true, extra })), ["builder", "brain", "greet", "voice-test", "reset", "hide"]);
  assert.deepEqual(ids(mascotMenuItems({ isGM: false, extra })), ["greet", "reset", "hide"]);
  assert.equal(mascotMenuItems({ isGM: true, extra }).find(i => i.id === "greet").label, "あいさつ");
});

test("MascotController.say は状態を変えずに台詞だけを出し、時間が来たら台詞だけを消す", () => {
  const timers = [];
  const shown = [];
  const c = new MascotController({
    render: v => shown.push({ ...v }),
    setTimer: (fn, ms) => timers.push({ fn, ms, live: true }) - 1,
    clearTimer: id => { if (timers[id]) timers[id].live = false; },
  });
  c.set({ state: "offline" });
  c.say("どうする？", 3000);
  assert.deepEqual(c.current, { state: "offline", text: "どうする？" });
  const fire = t => { t.live = false; t.fn(); };
  timers.filter(t => t.live).forEach(fire);
  assert.deepEqual(c.current, { state: "offline", text: "" });

  // 考え中: 台詞が消えても、状態の戻るタイマーは残る
  c.set({ state: "thinking", text: "", durationMs: 10000 });
  c.say("どうする？", 3000);
  const live = timers.filter(t => t.live);
  assert.equal(live.length, 2);
  fire(live.find(t => t.ms === 3000));
  assert.deepEqual(c.current, { state: "thinking", text: "" });
  fire(live.find(t => t.ms === 10000));
  assert.deepEqual(c.current, { state: "idle", text: "" });
});
