import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildJournalPages,
  buildNoteDocs,
  buildRoomLights,
  buildRoomSounds,
  buildSceneData,
  buildStartGoalLights,
  buildWallDocs,
  LIGHT_PRESETS,
  mergeRoomUpdates,
  roomIdOf,
  roomRole,
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

test("buildSceneData はシーンモードで明るさとレベル名を切り替える", () => {
  assert.equal(buildSceneData(data).levels[0].name, "地下1階");
  const ruins = buildSceneData({ ...data, scene_mode: "ruins" });
  assert.equal(ruins.darkness, 0.35);
  assert.equal(ruins.environment.globalLight.enabled, false);
  assert.equal(ruins.levels[0].name, "地上・野外");
  const wild = buildSceneData({ ...data, scene_mode: "wilderness" });
  assert.deepEqual(wild.environment, { darknessLevel: 0.35, globalLight: { enabled: true } });
  assert.equal(wild.globalLight.enabled, true);
  assert.equal(buildSceneData({ ...data, scene_mode: "moon" }).darkness, 0.75);
});

test("buildWallDocs は壁タイプを扉・隠し扉・低い壁に変換する", () => {
  const walls = [
    [0, 0, 100, 0],
    [0, 0, 100, 0, "normal"],
    [0, 0, 100, 0, "door"],
    [0, 0, 100, 0, "secret_door"],
    [0, 0, 100, 0, "low_wall"],
    [0, 0, 100, 0, "rubble"],
  ];
  const docs = buildWallDocs(walls);
  assert.deepEqual(docs.map(d => d.door), [0, 0, 1, 2, 0, 0]);
  assert.deepEqual(docs.map(d => d.sight), [20, 20, 20, 20, 0, 0]);
  assert.deepEqual(docs.map(d => d.light), [20, 20, 20, 20, 0, 0]);
  assert.ok(docs.every(d => d.move === 20));
  const custom = buildWallDocs([[0, 0, 1, 0, "secret_door"]], {}, { WALL_DOOR_TYPES: { NONE: 0, DOOR: 5, SECRET: 6 } });
  assert.equal(custom[0].door, 6);
});

test("buildRoomLights は light_preset と安全な休憩所から光源を作る", () => {
  const rooms = [
    { cx: 10, cy: 20, light_preset: "magic_blue" },
    { cx: 30, cy: 40, safe_rest: true },
    { cx: 50, cy: 60, light_preset: "dark", safe_rest: true },
    { cx: 70, cy: 80, light_preset: "unknown" },
    { cx: 90, cy: 99 },
  ];
  const lights = buildRoomLights(rooms, { offsetX: 1, offsetY: 2 });
  assert.equal(lights.length, 2);
  assert.deepEqual([lights[0].x, lights[0].y], [11, 22]);
  assert.equal(lights[0].config.color, LIGHT_PRESETS.magic_blue.color);
  assert.equal(lights[1].config.animation.type, "flame");
  // 生成した config を書き換えてもプリセットは変わらない
  lights[0].config.bright = 999;
  assert.equal(LIGHT_PRESETS.magic_blue.bright, 15);
  assert.deepEqual(buildRoomLights(undefined), []);
});

test("LIGHT_PRESETS はアニメーション無しを null で表す (L13)", () => {
  assert.equal(LIGHT_PRESETS.moonlight_dim.animation.type, null);
  assert.ok(Object.values(LIGHT_PRESETS).every(p => p === null || p.animation.type !== "none"));
});

test("buildRoomSounds の radius は距離単位 (マス数 × grid.distance) (M1)", () => {
  const rooms = [
    { cx: 100, cy: 200, grid_w: 4, grid_h: 6, ambient_sound_src: "sounds/drips.ogg" },
    { cx: 0, cy: 0, grid_w: 4, grid_h: 4 },
  ];
  const [sound, ...rest] = buildRoomSounds(rooms, { offsetX: 5, offsetY: 5 });
  assert.equal(rest.length, 0);
  assert.equal(sound.radius, 6 * 5 * 0.85);
  assert.deepEqual([sound.x, sound.y, sound.path], [105, 205, "sounds/drips.ogg"]);
  assert.equal(buildRoomSounds(rooms, {}, 10)[0].radius, 6 * 10 * 0.85);
});

test("roomRole は role を優先し、無ければ type を使う (M2)", () => {
  assert.equal(roomRole({ role: "sub_goal", type: "room" }), "sub_goal");
  assert.equal(roomRole({ type: "start" }), "start");
  assert.equal(roomRole({}), null);
  const pages = buildJournalPages({ rooms: [{ ...data.rooms[1], type: "sub_goal" }] });
  assert.ok(pages[0].text.content.includes("中目標ギミック"));
});

test("部屋ページの情景テキストはダークテーマでも読める明るい文字色と暗い背景", () => {
  const html = buildJournalPages({ rooms: [data.rooms[1]] })[0].text.content;
  const style = html.match(/class="ai-gm-room-flavor" style="([^"]*)"/)[1];
  assert.ok(style.includes("color: #f3e9d2"));
  assert.ok(style.includes("background: rgba(30, 24, 20"));
  assert.ok(!html.includes("color: #222"));
});

test("区画の光源・環境音・ノート・ページには区画 id の flag が付く (後から差し替える目印)", () => {
  const room = { id: 3, name: "区画3", cx: 10, cy: 20, light_preset: "magic_blue", ambient_sound_src: "a.ogg", grid_w: 2, grid_h: 2 };
  assert.equal(roomIdOf(buildRoomLights([room])[0]), 3);
  assert.equal(roomIdOf(buildRoomSounds([room])[0]), 3);
  assert.equal(roomIdOf(buildNoteDocs([room], "j", [])[0]), 3);
  assert.equal(roomIdOf(buildJournalPages({ rooms: [room] })[0]), 3);
  // id の無い区画には flag を付けない
  assert.equal(buildRoomLights([{ ...room, id: undefined }])[0].flags, undefined);
  assert.equal(roomIdOf(null), null);
});

test("mergeRoomUpdates は id で上書きし、並び順を保ち、知らない id は無視する", () => {
  const rooms = [{ id: 1, name: "区画1", cx: 1 }, { id: 2, name: "区画2", cx: 2 }];
  const { rooms: merged, changed } = mergeRoomUpdates(rooms, [{ id: 2, name: "崩れた礼拝堂", flavor: "…" }, { id: 9, name: "x" }]);
  assert.deepEqual(merged.map(r => r.name), ["区画1", "崩れた礼拝堂"]);
  assert.equal(merged[1].cx, 2);
  assert.equal(changed.length, 1);
  assert.equal(changed[0].before.name, "区画2");
  assert.equal(rooms[1].name, "区画2");
  assert.deepEqual(mergeRoomUpdates(undefined, undefined), { rooms: [], changed: [] });
});
