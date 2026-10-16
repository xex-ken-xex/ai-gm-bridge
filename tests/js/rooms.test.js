import { test } from "node:test";
import assert from "node:assert/strict";
import { findRoomAt, RoomVisitTracker, tokenCenter } from "../../scripts/lib/rooms.js";

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
