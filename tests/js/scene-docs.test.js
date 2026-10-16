import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildJournalPages,
  buildNoteDocs,
  buildSceneData,
  buildStartGoalLights,
  buildWallDocs,
} from "../../scripts/lib/scene-docs.js";

const data = {
  name: "黒水城塞",
  background_src: "dungeon_100x100_1.png",
  grid_width: 100,
  grid_height: 80,
  grid_size: 100,
  start_pos: [2, 3],
  goal_pos: [90, 70],
  walls: [[0, 0, 500, 0], [500, 0, 500, 300]],
  lore: { dungeon_name: "<script>x</script>", floor_title: "第1層", factions: [{ name: "ゴブリン", territory: "門", status: "警戒" }] },
  rooms: [
    { id: 1, name: "部屋 1: 入口", grid_x: 1, grid_y: 2, grid_w: 4, grid_h: 4, cx: 350, cy: 450, flavor: "冷たい<風>", gm_notes: "罠\nあり", icon: "icons/svg/door.svg", safe_rest: true },
    { id: 2, name: "部屋 2: 宝物庫", grid_x: 9, grid_y: 9, grid_w: 3, grid_h: 3, cx: 1050, cy: 1050, flavor: "金貨" },
  ],
};

test("buildSceneData はサイズ・レベル背景・v12+ の environment を設定する", () => {
  const s = buildSceneData(data);
  assert.equal(s.width, 10000);
  assert.equal(s.height, 8000);
  assert.equal(s.levels[0].background.src, data.background_src);
  assert.equal(s.initialLevel, s.levels[0]._id);
  assert.deepEqual(s.environment, { darknessLevel: 0.75, globalLight: { enabled: false } });
});

test("buildWallDocs はオフセットを加算し CONST を使う", () => {
  const walls = buildWallDocs(data.walls, { offsetX: 10, offsetY: 20 }, { WALL_MOVEMENT_TYPES: { NORMAL: 99 }, EDGE_SENSE_TYPES: { NORMAL: 77 } });
  assert.deepEqual(walls[0].c, [10, 20, 510, 20]);
  assert.equal(walls[0].move, 99);
  assert.equal(walls[0].sight, 77);
  assert.equal(buildWallDocs(undefined).length, 0);
  assert.equal(buildWallDocs(data.walls)[0].move, 20);
});

test("buildStartGoalLights はセル中心に光源を置く", () => {
  const [start, goal] = buildStartGoalLights(data, { offsetX: 0, offsetY: 0 });
  assert.deepEqual([start.x, start.y], [250, 350]);
  assert.deepEqual([goal.x, goal.y], [9050, 7050]);
  assert.equal(buildStartGoalLights({ grid_size: 100 }).length, 0);
});

test("buildJournalPages は AI テキストをエスケープする", () => {
  const pages = buildJournalPages(data);
  assert.equal(pages.length, 3);
  assert.ok(!pages[0].text.content.includes("<script>"));
  assert.ok(pages[0].text.content.includes("&lt;script&gt;"));
  assert.ok(pages[1].text.content.includes("冷たい&lt;風&gt;"));
  assert.ok(pages[1].text.content.includes("罠<br>あり"));
  assert.ok(pages[1].text.content.includes("安全な休憩所"));
  assert.equal(pages[1].text.format, 1);
});

test("buildNoteDocs はページ名でページを対応付ける (並び順に依存しない)", () => {
  const created = [
    { id: "p2", name: "部屋 2: 宝物庫" },
    { id: "lore", name: "【フロア伝承・生態系】第1層" },
    { id: "p1", name: "部屋 1: 入口" },
  ];
  const notes = buildNoteDocs(data.rooms, "j1", created, { offsetX: 5, offsetY: 5 });
  assert.equal(notes[0].pageId, "p1");
  assert.equal(notes[1].pageId, "p2");
  assert.deepEqual([notes[0].x, notes[0].y], [355, 455]);
  assert.equal(notes[1].texture.src, "icons/svg/book.svg");
});
