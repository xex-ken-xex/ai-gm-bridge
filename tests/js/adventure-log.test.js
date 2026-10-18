import { test } from "node:test";
import assert from "node:assert/strict";
import {
  appendReplayHtml,
  buildListPageHtml,
  inventoryDiff,
  inventorySnapshot,
  mergeEntries,
  messageToLine,
  normalizeLogMeta,
  planSession,
  replayToHtml,
  sessionPageName,
  transcriptSince,
} from "../../scripts/lib/adventure-log.js";

test("messageToLine: ウィスパーやGeminiちゃんとの相談・コマンドは残さない", () => {
  assert.equal(messageToLine({ alias: "AI GM", content: "秘密", whisper: true }), null);
  assert.equal(messageToLine({ alias: "サポートGM Geminiちゃん", content: "任せて！" }), null);
  assert.equal(messageToLine({ alias: "アリア", content: "/ai 記録を作って" }), null);
  assert.equal(messageToLine({ alias: "アリア", content: "<p> </p>" }), null);
  assert.deepEqual(messageToLine({ alias: "アリア", content: "<p>扉を開ける</p><p>慎重に</p>" }), { speaker: "アリア", text: "扉を開ける / 慎重に" });
});

test("messageToLine: ダイスは式と出目にする", () => {
  assert.deepEqual(
    messageToLine({ alias: "ボルグ", isRoll: true, flavor: "<b>運動</b>判定", rolls: [{ formula: "1d20+3", total: 17 }] }),
    { speaker: "ボルグ", text: "🎲 運動判定 1d20+3 → 17" },
  );
  assert.equal(messageToLine({ alias: "ボルグ", isRoll: true, rolls: [] }), null);
});

test("transcriptSince: 前の記録より後のメッセージを古い順に", () => {
  const lines = transcriptSince([
    { timestamp: 30, alias: "B", content: "三" },
    { timestamp: 10, alias: "A", content: "一" },
    { timestamp: 20, alias: "A", content: "二" },
  ], 10);
  assert.deepEqual(lines.map(l => l.text), ["二", "三"]);
});

test("planSession: 更新は最新の回に書き足し、回が無ければ新しく作る", () => {
  const empty = normalizeLogMeta(null);
  assert.deepEqual(planSession(empty, "update"), { mode: "create", number: 1, since: 0, pageId: null });
  const meta = normalizeLogMeta({ sessions: [{ number: 1, pageId: "p1", since: 0, until: 50 }, { number: 2, pageId: "p2", since: 50, until: 90 }] });
  assert.deepEqual(planSession(meta, "update"), { mode: "update", number: 2, since: 90, pageId: "p2" });
  assert.deepEqual(planSession(meta, "create"), { mode: "create", number: 3, since: 90, pageId: null });
});

test("normalizeLogMeta: 壊れた値でも落ちない", () => {
  const meta = normalizeLogMeta({ sessions: "x", items: [null, { name: "剣" }], inventory: 3 });
  assert.deepEqual(meta.sessions, []);
  assert.deepEqual(meta.items, [{ name: "剣" }]);
  assert.equal(meta.inventory, null);
});

test("inventoryDiff: 増えた所持品だけ。初めての記録と新しい PC は数えない", () => {
  const before = inventorySnapshot([{ id: "a", name: "アリア", items: [{ name: "ロープ", quantity: 1 }, { name: "矢", quantity: 10 }] }]);
  const now = inventorySnapshot([
    { id: "a", name: "アリア", items: [{ name: "ロープ", quantity: 1 }, { name: "矢", quantity: 25 }, { name: "灯台の鍵" }] },
    { id: "b", name: "ボルグ", items: [{ name: "斧", quantity: 1 }] },
  ]);
  assert.deepEqual(inventoryDiff(before, now), [
    { holder: "アリア", name: "矢", quantity: 15 },
    { holder: "アリア", name: "灯台の鍵", quantity: 1 },
  ]);
  assert.deepEqual(inventoryDiff(null, now), []);
});

test("inventorySnapshot: 呪文・特技・クラスの特徴は所持品に数えない", () => {
  const snap = inventorySnapshot([{ id: "a", name: "アリア", items: [
    { name: "ファイアボール", type: "spell" },
    { name: "二刀流", type: "feat" },
    { name: "ウィザード", type: "class" },
    { name: "ロングソード", type: "weapon" },
    { name: "背負い袋", type: "backpack" },
    { name: "治癒のポーション", type: "consumable", quantity: 2 },
  ] }]);
  assert.deepEqual(snap.a.items, { "ロングソード": 1, "背負い袋": 1, "治癒のポーション": 2 });
});

test("mergeEntries: 同じ人は 1 件にまとめ、初めて会った回は残す", () => {
  let people = mergeEntries([], [{ name: "マルタ", note: "宿の女将" }], 1);
  people = mergeEntries(people, [{ name: "マルタ", note: "灯台の秘密を打ち明けた" }, { name: "ゲイル", note: "" }, { name: "" }], 2);
  assert.deepEqual(people, [
    { name: "マルタ", note: "灯台の秘密を打ち明けた", session: 1, lastSession: 2 },
    { name: "ゲイル", note: "", session: 2 },
  ]);
  const items = mergeEntries([{ name: "ポーション", holder: "アリア", note: "", session: 1 }], [{ name: "ポーション", holder: "ボルグ" }], 2, true);
  assert.equal(items.length, 2);
});

test("一覧と本文の HTML は AI のテキストをエスケープする", () => {
  assert.match(buildListPageHtml("people", []), /作り直されます[\s\S]*まだ記録された人物はいません/);
  const html = buildListPageHtml("items", [{ name: "<剣>", holder: "アリア", note: "a&b", session: 2 }]);
  assert.match(html, /<strong>&lt;剣&gt;<\/strong> <span class="ai-gm-log-holder">\(アリア\)<\/span> — a&amp;b <small>第2回<\/small>/);
  assert.equal(replayToHtml("一行目\n\n<script>"), "<p>一行目</p>\n<p>&lt;script&gt;</p>");
  assert.equal(appendReplayHtml("<p>前</p>", "続き"), "<p>前</p>\n<hr>\n<p>続き</p>");
  assert.equal(sessionPageName(3, " "), "第3回 冒険の記録");
});
