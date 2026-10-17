import { test } from "node:test";
import assert from "node:assert/strict";
import {
  clampPosition,
  DEFAULT_POSITION,
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
  assert.deepEqual(parseSavedPosition('{"left":5000,"top":-20}', { width: 1000, height: 800 }), { left: 910, top: 0 });
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
