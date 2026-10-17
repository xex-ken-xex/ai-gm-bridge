import { test } from "node:test";
import assert from "node:assert/strict";
import {
  findRoomAt,
  isRoomReached,
  markRoomReached,
  REACHED_MARK,
  RoomVisitTracker,
  shouldNarrateToken,
  tokenCenter,
} from "../../scripts/lib/rooms.js";
import { buildRoomPage } from "../../scripts/lib/scene-docs.js";

const rooms = [
  { id: 1, name: "部屋 1", grid_x: 2, grid_y: 2, grid_w: 4, grid_h: 3 },
  { id: 2, name: "部屋 2", grid_x: 10, grid_y: 10, grid_w: 5, grid_h: 5 },
];

test("findRoomAt はグリッド矩形内の部屋を返す", () => {
  assert.equal(findRoomAt(rooms, 250, 250)?.id, 1);
  assert.equal(findRoomAt(rooms, 1200, 1200)?.id, 2);
  assert.equal(findRoomAt(rooms, 50, 50), null);
  assert.equal(findRoomAt(null, 0, 0), null);
});

test("findRoomAt はシーン原点オフセットを考慮する", () => {
  assert.equal(findRoomAt(rooms, 250, 250, { offsetX: 1000, offsetY: 1000 }), null);
  assert.equal(findRoomAt(rooms, 1250, 1250, { offsetX: 1000, offsetY: 1000 })?.id, 1);
});

test("tokenCenter はトークンの中心を返す", () => {
  assert.deepEqual(tokenCenter({ x: 200, y: 300, width: 2, height: 1 }, 100), { x: 300, y: 350 });
});

test("RoomVisitTracker は同じ部屋・訪問済みの部屋を再通知しない", () => {
  const t = new RoomVisitTracker();
  assert.equal(t.enter("s", "tokA", 1), true);
  assert.equal(t.enter("s", "tokA", 1), false);
  assert.equal(t.enter("s", "tokB", 1), false);
  assert.equal(t.enter("s", "tokA", 2), true);
  assert.equal(t.enter("other-scene", "tokA", 1), true);
});

test("shouldNarrateToken はプレイヤー所有か PC のトークンだけを対象にする", () => {
  assert.equal(shouldNarrateToken({ hasPlayerOwner: true, actorType: "npc" }), true);
  // GM が 1 人で動かす所有者未設定の PC
  assert.equal(shouldNarrateToken({ hasPlayerOwner: false, actorType: "character" }), true);
  assert.equal(shouldNarrateToken({ hasPlayerOwner: false, actorType: "npc" }), false);
  assert.equal(shouldNarrateToken({ hidden: true, hasPlayerOwner: true, actorType: "character" }), false);
  assert.equal(shouldNarrateToken({}), false);
});

const page = () => buildRoomPage({ name: "部屋 1: 入口", flavor: "冷たい風", grid_x: 0, grid_y: 0, grid_w: 2, grid_h: 2 }).text.content;

test("markRoomReached は情景テキストの先頭に <到達済> を付け、isRoomReached で判定できる", () => {
  const html = page();
  assert.equal(isRoomReached(html), false);
  const marked = markRoomReached(html);
  assert.ok(marked.includes('class="ai-gm-room-flavor" style="margin-bottom: 12px; font-style: italic; color: #222;">&lt;到達済&gt; '));
  assert.equal(isRoomReached(marked), true);
  // 2 回付けない
  assert.equal(markRoomReached(marked), marked);
});

test("isRoomReached は GM が手で書いた印 (エディタで class が消えた段落) も認識する", () => {
  assert.equal(isRoomReached("<h1>部屋</h1><p>&lt;到達済&gt;&nbsp;冷たい風</p>"), true);
  assert.equal(isRoomReached("<p><em>&lt;到達済&gt;</em> 冷たい風</p>"), true);
  assert.equal(isRoomReached(`<p>${REACHED_MARK}</p>`), true);
  // 段落の途中にあるだけなら未到達 (GM が印を消して文中に言及した等)
  assert.equal(isRoomReached("<p>冷たい風。&lt;到達済&gt;</p>"), false);
  assert.equal(isRoomReached(""), false);
  assert.equal(isRoomReached(undefined), false);
});

test("markRoomReached は情景段落が無いページでは先頭に印の段落を足す", () => {
  const marked = markRoomReached("<p>GM が書き換えた本文</p>");
  assert.equal(marked, "<p>&lt;到達済&gt;</p><p>GM が書き換えた本文</p>");
  assert.equal(isRoomReached(marked), true);
});

test("RoomVisitTracker.changedRoom と begin/finish", () => {
  const t = new RoomVisitTracker();
  assert.equal(t.changedRoom("tokA", 1), true);
  assert.equal(t.changedRoom("tokA", 1), false);
  // 別トークンは訪問済みでも true (到達済みはページの印で判定する)
  assert.equal(t.changedRoom("tokB", 1), true);
  assert.equal(t.begin("s", 1), true);
  assert.equal(t.begin("s", 1), false);
  t.finish("s", 1);
  assert.equal(t.begin("s", 1), true);
});
