import { test } from "node:test";
import assert from "node:assert/strict";
import {
  availableActions,
  flankCells,
  gridDistance,
  healthStatus,
  isSameTurn,
  moveBudget,
  planSteps,
  sanitizeFormula,
  shouldTakeTurn,
  turnKey,
} from "../../scripts/lib/npc-tactics.js";
import { buildNpcGmNoteHtml, buildNpcTurnGmNoteHtml, buildNpcTurnHtml } from "../../scripts/lib/cards.js";

const at = (col, row) => ({ col, row });

test("shouldTakeTurn は PC・プレイヤー所有・非表示・行動不能の NPC を除く (M6)", () => {
  assert.equal(shouldTakeTurn({ actorType: "npc", hp: 5 }), true);
  assert.equal(shouldTakeTurn({ actorType: "character", hp: 5 }), false);
  assert.equal(shouldTakeTurn({ actorType: "npc", hasPlayerOwner: true }), false);
  assert.equal(shouldTakeTurn({ actorType: "npc", hidden: true }), false);
  assert.equal(shouldTakeTurn({ actorType: "npc", hp: 0 }), false);
  assert.equal(shouldTakeTurn({ actorType: "npc", defeated: true }), false);
  assert.equal(shouldTakeTurn({ actorType: "npc", statuses: new Set(["stunned"]) }), false);
  assert.equal(shouldTakeTurn({ actorType: "npc", hp: null, statuses: new Set(["prone"]) }), true);
  assert.equal(shouldTakeTurn({}), false);
});

test("isSameTurn は手番が進んだ・別のトークン・戦闘が終わったときに false (M6)", () => {
  const req = { id: "c1", round: 2, turn: 3, tokenId: "orc" };
  assert.equal(isSameTurn(req, { ...req, started: true }), true);
  assert.equal(isSameTurn(req, { ...req, started: true, turn: 4 }), false);
  assert.equal(isSameTurn(req, { ...req, started: true, round: 3 }), false);
  assert.equal(isSameTurn(req, { ...req, started: true, tokenId: "goblin" }), false);
  assert.equal(isSameTurn(req, { ...req, started: false }), false);
  assert.equal(isSameTurn(req, null), false);
  assert.equal(turnKey(req), "c1:2:3");
});

test("gridDistance は斜めも 1 マスで数え、大きいトークンは端どうしで測る", () => {
  assert.equal(gridDistance(at(0, 0), at(3, 2)), 3);
  assert.equal(gridDistance(at(0, 0), at(1, 1)), 1);
  const ogre = { col: 0, row: 0, w: 2, h: 2 };
  assert.equal(gridDistance(ogre, at(2, 1)), 1);
  assert.equal(gridDistance(ogre, at(1, 1)), 0);
  assert.equal(gridDistance(ogre, at(4, 0)), 3);
});

test("planSteps: 接近は隣接したら止まり、隣接済みなら動かない (M7)", () => {
  const path = planSteps({ start: at(0, 0), intent: "approach_target", target: at(5, 0), maxSteps: 6 });
  assert.deepEqual(path.map(p => [p.col, p.row]), [[1, 0], [2, 0], [3, 0], [4, 0]]);
  assert.deepEqual(planSteps({ start: at(4, 1), intent: "approach_target", target: at(5, 0) }), []);
  // 移動力が足りなければその分だけ
  assert.equal(planSteps({ start: at(0, 0), intent: "approach_target", target: at(9, 0), maxSteps: 3 }).length, 3);
});

test("planSteps: 壁 (canStep) を避けて回り込む", () => {
  // x = 2 の列は row 0〜2 が壁
  const wall = new Set(["2,0", "2,1", "2,2"]);
  const canStep = (_from, to) => !wall.has(`${to.col},${to.row}`) && to.row >= 0;
  const path = planSteps({ start: at(0, 0), intent: "approach_target", target: at(4, 0), maxSteps: 8, canStep });
  assert.ok(path.every(p => !wall.has(`${p.col},${p.row}`)));
  assert.equal(gridDistance(path.at(-1), at(4, 0)), 1);
});

test("planSteps: 後退は最も近い敵から離れ、シーンの外 (canStep) へは出ない", () => {
  const canStep = (_f, to) => to.col >= 0 && to.row >= 0 && to.col < 6 && to.row < 6;
  const path = planSteps({ start: at(2, 2), intent: "retreat_from_enemies", enemies: [at(3, 2)], maxSteps: 6, canStep });
  assert.ok(path.length >= 2);
  assert.ok(path.every(p => canStep(null, p)));
  assert.ok(gridDistance(path.at(-1), at(3, 2)) > 1);
});

test("planSteps: 遮蔽を探すときは敵から見えないマスに入ったら止まる", () => {
  const hasCover = p => p.col <= 0;
  const path = planSteps({ start: at(2, 0), intent: "seek_cover_or_gimmick", enemies: [at(5, 0)], maxSteps: 6, hasCover });
  assert.equal(path.at(-1).col, 0);
  assert.deepEqual(planSteps({ start: at(0, 0), intent: "seek_cover_or_gimmick", enemies: [at(5, 0)], hasCover }), []);
});

test("planSteps: 挟み撃ちは味方の反対側へ回り込み、味方がいなければ接近と同じ (M7)", () => {
  const target = at(5, 5);
  const ally = at(4, 5);
  assert.deepEqual(flankCells(target, [ally, at(0, 0)]).map(c => [c.col, c.row]), [[6, 5]]);
  const path = planSteps({ start: at(5, 2), intent: "flank_target", target, allies: [ally], maxSteps: 6,
    canStep: (_f, to) => !(to.col === 5 && to.row === 5) && !(to.col === 4 && to.row === 5) });
  assert.deepEqual([path.at(-1).col, path.at(-1).row], [6, 5]);
  const alone = planSteps({ start: at(5, 2), intent: "flank_target", target, maxSteps: 6 });
  assert.equal(gridDistance(alone.at(-1), target), 1);
});

test("planSteps: 構える・意図が分からない・狙いが無いときは動かない", () => {
  assert.deepEqual(planSteps({ start: at(0, 0), intent: "hold_position", target: at(5, 0) }), []);
  assert.deepEqual(planSteps({ start: at(0, 0), intent: "fly", target: at(5, 0) }), []);
  assert.deepEqual(planSteps({ start: at(0, 0), intent: "approach_target" }), []);
});

test("moveBudget は移動速度をマス数にし、上限で抑える", () => {
  assert.equal(moveBudget(30, 5), 6);
  assert.equal(moveBudget(25, 5), 5);
  assert.equal(moveBudget(500, 5), 12);
  assert.equal(moveBudget(undefined, 5), 6);
});

test("sanitizeFormula はダイス式だけを通す (M5)", () => {
  assert.equal(sanitizeFormula("1d20+5"), "1d20+5");
  assert.equal(sanitizeFormula("2d6 + (1d4)"), "2d6 + (1d4)");
  for (const bad of [null, "", "7", "1d20+@abilities.str.mod", "(1d6", "1d6)", "1d6;alert(1)", "<img>"]) {
    assert.equal(sanitizeFormula(bad), null, String(bad));
  }
});

test("availableActions は武器・特技・呪文だけを 8 件まで", () => {
  const items = [{ name: "剣", type: "weapon" }, { name: "鎧", type: "equipment" }, { name: "火球", type: "spell" },
    ...Array.from({ length: 10 }, (_, i) => ({ name: `特技${i}`, type: "feat" }))];
  const got = availableActions(items);
  assert.equal(got.length, 8);
  assert.deepEqual(got.slice(0, 2), [{ name: "剣", type: "weapon" }, { name: "火球", type: "spell" }]);
});

test("healthStatus", () => {
  assert.equal(healthStatus(10, 10), "健在・余裕あり");
  assert.equal(healthStatus(5, 10), "負傷・消耗");
  assert.equal(healthStatus(1, 10), "瀕死・危機的");
  assert.equal(healthStatus(0, 10), "倒れている");
});

test("NPC の手番のカードはエスケープし、内心を入れない (GM メモにだけ出す)", () => {
  const decision = { emote: "<img src=x onerror=alert(1)>", speech: "死ね<b>", action_name: "斧<script>",
    internal_thought: "秘密の作戦", move_intent: "flank_target" };
  const card = buildNpcTurnHtml(decision);
  assert.ok(!card.includes("<img") && !card.includes("<script>") && !card.includes("秘密の作戦"));
  assert.ok(card.includes("&lt;img"));
  const note = buildNpcTurnGmNoteHtml(decision, { npcName: "オーク", targetName: "アリア<", notes: ["移動できない"] });
  assert.ok(note.includes("秘密の作戦") && note.includes("挟み撃ち") && note.includes("アリア&lt;") && note.includes("移動できない"));
  assert.equal(buildNpcTurnGmNoteHtml({}), "");
});

test("会話の GM メモに移動の結果を添える", () => {
  const note = buildNpcGmNoteHtml({ physical_action: "leave_scene" }, { movement: "トークンを非表示にしました" });
  assert.ok(note.includes("この場を立ち去る") && note.includes("非表示"));
});
