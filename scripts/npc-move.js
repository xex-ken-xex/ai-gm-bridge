// NPC のトークンの移動 (Foundry 依存): 壁・他のトークン・シーンの外を避けて 1 マスずつ動かす
// 移動の計画そのものは scripts/lib/npc-tactics.js の planSteps (tests/js)
//
// M3: 対象のシーンは呼び出し側が tokenDoc.parent / combat.scene で渡す。壁の判定はキャンバスに表示中のシーンでしかできないため、
// 表示していないシーンでは動かさない (壁を無視して通り抜けさせない)

import { DIALOGUE_MOVES, planSteps } from "./lib/npc-tactics.js";

// 1 マス進むごとの待ち時間 (トークンのアニメーションが追いつくように)
const STEP_DELAY_MS = 180;

/** シーンのマスの大きさと描画原点 */
export function gridOf(scene) {
  const dims = scene.dimensions ?? (scene.getDimensions ? scene.getDimensions() : {});
  const size = scene.grid?.size ?? 100;
  return {
    size,
    distance: scene.grid?.distance ?? 5,
    offsetX: dims.sceneX ?? 0,
    offsetY: dims.sceneY ?? 0,
    cols: Math.floor((dims.sceneWidth ?? scene.width ?? 0) / size),
    rows: Math.floor((dims.sceneHeight ?? scene.height ?? 0) / size),
  };
}

/** トークンのいるマス (左上) と大きさ */
export function cellOf(doc, g) {
  return {
    col: Math.round((doc.x - g.offsetX) / g.size),
    row: Math.round((doc.y - g.offsetY) / g.size),
    w: Math.max(1, Math.round(doc.width ?? 1)),
    h: Math.max(1, Math.round(doc.height ?? 1)),
  };
}

function cellCenter(cell, g) {
  return { x: g.offsetX + (cell.col + (cell.w ?? 1) / 2) * g.size, y: g.offsetY + (cell.row + (cell.h ?? 1) / 2) * g.size };
}

/** 壁の判定ができるシーンか (キャンバスに表示中で、マス目がある) */
export function canTestWalls(scene) {
  return canvas?.scene?.id === scene?.id && scene?.grid?.type !== 0;
}

/** 壁にぶつかるか (type: move / sight / sound)。判定できなければ false */
export function collides(type, from, to) {
  const backend = CONFIG?.Canvas?.polygonBackends?.[type];
  if (!backend) return false;
  try {
    return Boolean(backend.testCollision(from, to, { type, mode: "any" }));
  } catch (e) {
    return false;
  }
}

/** 2 つのトークンの間に視線が通るか (判定できないシーンでは null) */
export function hasLineOfSight(scene, a, b) {
  if (!canTestWalls(scene)) return null;
  const g = gridOf(scene);
  return !collides("sight", cellCenter(cellOf(a, g), g), cellCenter(cellOf(b, g), g));
}

function overlaps(a, b) {
  return a.col < b.col + b.w && b.col < a.col + a.w && a.row < b.row + b.h && b.row < a.row + a.h;
}

function makeCanStep(scene, doc, g) {
  const others = scene.tokens.contents.filter(t => t.id !== doc.id).map(t => cellOf(t, g));
  return (from, to) => {
    if (to.col < 0 || to.row < 0 || (g.cols && to.col + to.w > g.cols) || (g.rows && to.row + to.h > g.rows)) return false;
    if (others.some(o => overlaps(to, o))) return false;
    return !collides("move", cellCenter(from, g), cellCenter(to, g));
  };
}

function makeHasCover(enemies, g) {
  return p => enemies.length > 0 && enemies.every(e => collides("sight", cellCenter(e, g), cellCenter(p, g)));
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/**
 * 意図に沿ってトークンを動かす。戻り値 { steps, skipped } (skipped: 動かせなかった理由。GM メモに出す)
 * target / enemies / allies: TokenDocument
 */
export async function moveTokenByIntent(scene, doc, { intent, target = null, enemies = [], allies = [], maxSteps = 6 }) {
  if (!intent || intent === "hold_position" || maxSteps <= 0) return { steps: 0, skipped: null };
  if (!canTestWalls(scene)) {
    return { steps: 0, skipped: "シーンがキャンバスに表示されていない (またはマス目が無い) ため、壁を確かめられず移動しませんでした" };
  }
  const g = gridOf(scene);
  const enemyCells = enemies.map(e => cellOf(e, g));
  const path = planSteps({
    start: cellOf(doc, g),
    intent,
    target: target ? cellOf(target, g) : null,
    enemies: enemyCells,
    allies: allies.map(a => cellOf(a, g)),
    maxSteps,
    canStep: makeCanStep(scene, doc, g),
    hasCover: makeHasCover(enemyCells, g),
  });
  for (const cell of path) {
    await doc.update({ x: g.offsetX + cell.col * g.size, y: g.offsetY + cell.row * g.size });
    await sleep(STEP_DELAY_MS);
  }
  return { steps: path.length, skipped: null };
}

/**
 * NPC 会話の physical_action を実行する (Phase 3 では GM メモにだけ出していたもの)。
 * 近づく / 下がる はマス単位で動かし、立ち去るは非表示 (L9: alpha を下げるだけでは残る)、敵対はトークンの立場を敵対にする。
 * 戻り値: GM メモに足す文 (何もしなければ null)
 */
export async function applyDialogueMovement(scene, npcDoc, speakerDoc, physicalAction) {
  if (!scene || !npcDoc || !physicalAction || physicalAction === "stay") return null;
  if (physicalAction === "leave_scene") {
    await npcDoc.update({ hidden: true });
    return "トークンを非表示にしました (立ち去った)";
  }
  if (physicalAction === "turn_hostile") {
    const hostile = CONST?.TOKEN_DISPOSITIONS?.HOSTILE ?? -1;
    if (npcDoc.disposition !== hostile) await npcDoc.update({ disposition: hostile });
    return "トークンの立場を「敵対」にしました";
  }
  const move = DIALOGUE_MOVES[physicalAction];
  if (!move || !speakerDoc) return null;
  const { steps, skipped } = await moveTokenByIntent(scene, npcDoc, {
    intent: move.intent, target: speakerDoc, enemies: [speakerDoc], maxSteps: move.steps,
  });
  return skipped ?? (steps ? `${steps} マス動きました` : null);
}
