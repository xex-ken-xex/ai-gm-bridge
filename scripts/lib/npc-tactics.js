// NPC 自律戦闘の純粋関数 (Foundry 非依存): 手番に動かす NPC の判定・手番の照合・移動の計画・ダイス式の検査
// 座標はすべてマス単位 ({ col, row } = トークン左上のマス)。壁・他のトークン・シーンの外は canStep で呼び出し側が判定する

export const MOVE_INTENTS = Object.freeze([
  "approach_target", "flank_target", "retreat_from_enemies", "seek_cover_or_gimmick", "hold_position",
]);
// 1 手番で動けるマス数の上限 (移動速度が大きくても AI の計画が暴れないように)
export const MAX_MOVE_STEPS = 12;
// 行動できない状態 (dnd5e の状態異常 id)
export const INCAPACITATED_STATUSES = Object.freeze([
  "dead", "defeated", "unconscious", "incapacitated", "paralyzed", "petrified", "stunned",
]);
// 会話の physical_action → 移動 (Phase 3 で GM メモにだけ出していたもの)
export const DIALOGUE_MOVES = Object.freeze({
  approach_speaker: { intent: "approach_target", steps: 2 },
  step_back: { intent: "retreat_from_enemies", steps: 1 },
});

const STEP_DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]];

export function healthStatus(hp, max) {
  const ratio = Number(max) > 0 ? Number(hp) / Number(max) : 1;
  return ratio > 0.7 ? "健在・余裕あり" : ratio > 0.3 ? "負傷・消耗" : ratio > 0 ? "瀕死・危機的" : "倒れている";
}

/** 行動できない (戦闘不能・HP 0・行動不能の状態異常) か */
export function isIncapacitated({ hp = null, defeated = false, statuses = [] } = {}) {
  if (defeated) return true;
  if (hp !== null && hp !== undefined && Number(hp) <= 0) return true;
  return [...(statuses ?? [])].some(s => INCAPACITATED_STATUSES.includes(s));
}

/**
 * 手番に AI が動かす NPC か (M6): PC ではなく、プレイヤーが所有しておらず、隠れておらず、行動できる
 */
export function shouldTakeTurn({ actorType = null, hasPlayerOwner = false, hidden = false, ...state } = {}) {
  if (!actorType || actorType === "character" || hasPlayerOwner || hidden) return false;
  return !isIncapacitated(state);
}

/** 手番の目印 (同じ手番に二度頼まないため) */
export function turnKey({ id, round, turn }) {
  return `${id}:${round}:${turn}`;
}

/**
 * AI の応答が届いたとき、まだ同じ手番か (M6: 待っている間に手番が進んでいたら古い行動は実行しない)
 * requested: 頼んだときの { id, round, turn, tokenId }、current: いまの戦闘の { id, started, round, turn, tokenId }
 */
export function isSameTurn(requested, current) {
  if (!requested || !current?.started) return false;
  return requested.id === current.id && Number(requested.round) === Number(current.round)
    && Number(requested.turn) === Number(current.turn) && requested.tokenId === current.tokenId;
}

/** 移動速度 (ft) から 1 手番で動けるマス数 */
export function moveBudget(speedFt, gridDistance = 5) {
  const squares = Math.floor((Number(speedFt) || 30) / (Number(gridDistance) || 5));
  return Math.max(0, Math.min(MAX_MOVE_STEPS, squares));
}

/**
 * 5e の数え方 (斜めも 1 マス) の距離。w / h (マス数、既定 1) があれば大きいトークンの端どうしで測る
 * (0 = 重なっている、1 = 隣接)
 */
export function gridDistance(a, b) {
  const gap = (p, pw, q, qw) => Math.max(0, q - (p + pw - 1), p - (q + qw - 1));
  return Math.max(gap(a.col, a.w ?? 1, b.col, b.w ?? 1), gap(a.row, a.h ?? 1, b.row, b.h ?? 1));
}

function euclid(a, b) {
  return Math.hypot(a.col + (a.w ?? 1) / 2 - b.col - (b.w ?? 1) / 2, a.row + (a.h ?? 1) / 2 - b.row - (b.h ?? 1) / 2);
}

function nearestDistance(p, enemies) {
  let best = { grid: Infinity, euclid: Infinity };
  for (const e of enemies) {
    const g = gridDistance(p, e);
    const d = euclid(p, e);
    if (g < best.grid || (g === best.grid && d < best.euclid)) best = { grid: g, euclid: d };
  }
  return best;
}

/** 挟み撃ちの位置: 対象に隣接している味方の、対象をはさんだ反対側のマス */
export function flankCells(target, allies = []) {
  return allies
    .filter(a => gridDistance(a, target) === 1)
    .map(a => ({ col: target.col * 2 - a.col, row: target.row * 2 - a.row, w: 1, h: 1 }));
}

/** 意図ごとの評価 (小さいほど良い)。null なら動かない */
function scorer(intent, { target, enemies, allies, hasCover }) {
  const toward = goal => p => {
    const g = gridDistance(p, goal);
    return g === 0 ? 0 : g * 1000 + euclid(p, goal);
  };
  const adjacentTo = t => p => {
    const g = gridDistance(p, t);
    // 隣接したらそれ以上は動かない (斜め隣から真横へ回り込むような無駄な移動をしない: M7)
    return g === 1 ? 0 : g === 0 ? 1e9 : (g - 1) * 1000 + euclid(p, t);
  };
  const away = list => p => {
    const near = nearestDistance(p, list);
    return -(near.grid * 1000 + near.euclid);
  };
  if (intent === "approach_target") return target ? adjacentTo(target) : null;
  if (intent === "flank_target") {
    if (!target) return null;
    const cells = flankCells(target, allies);
    // 隣接する味方がいなければ接近と同じ
    if (!cells.length) return adjacentTo(target);
    return p => Math.min(...cells.map(c => toward(c)(p)));
  }
  const threats = enemies?.length ? enemies : target ? [target] : [];
  if (intent === "retreat_from_enemies") return threats.length ? away(threats) : null;
  if (intent === "seek_cover_or_gimmick") {
    if (!threats.length) return null;
    const far = away(threats);
    // 遮蔽 (敵から見えないマス) に入ったらそこで止まる
    return p => (hasCover?.(p) ? -1e12 : far(p));
  }
  return null;
}

// 接近・挟み撃ちで壁を回り込む道を探す範囲 (マス数) と、調べるマスの上限
export const SEARCH_DEPTH = 30;
const SEARCH_NODES = 1500;

/**
 * 移動の計画 (M7)。いまの位置から歩いて行けるマスを幅優先で調べ (斜めも 1 マス。壁は canStep で回り込む)、
 * 評価が最も良いマスへの最短の道のうち、移動力の分だけ進む。いまの位置より良いマスが無ければ動かない
 * (いまの位置も候補に入るので、隣接済みなら動かない)。
 * 接近・挟み撃ちは遠くのマスまで道を探し (1 手番で届かなくても近づく)、後退・遮蔽は 1 手番で行けるマスだけを選ぶ。
 * canStep(from, to): 壁・他のトークン・シーンの外なら false
 * 戻り値: 通るマスの列 (start は含まない)
 */
export function planSteps({
  start, intent, target = null, enemies = [], allies = [], maxSteps = 6, canStep = () => true, hasCover = null,
}) {
  const score = scorer(intent, { target, enemies, allies, hasCover });
  const steps = Math.min(maxSteps, MAX_MOVE_STEPS);
  if (!score || !start || steps <= 0) return [];
  const size = { w: start.w ?? 1, h: start.h ?? 1 };
  const origin = { col: start.col, row: start.row, ...size };
  const depthLimit = intent === "approach_target" || intent === "flank_target" ? Math.max(steps, SEARCH_DEPTH) : steps;
  const key = c => `${c.col},${c.row}`;
  const prev = new Map([[key(origin), null]]);
  let best = { cell: origin, score: score(origin), depth: 0 };
  let frontier = [origin];
  for (let depth = 1; depth <= depthLimit && frontier.length && prev.size < SEARCH_NODES; depth++) {
    const next = [];
    for (const cur of frontier) {
      for (const [dc, dr] of STEP_DIRS) {
        const cell = { col: cur.col + dc, row: cur.row + dr, ...size };
        const k = key(cell);
        if (prev.has(k) || !canStep(cur, cell)) continue;
        prev.set(k, cur);
        next.push(cell);
        const s = score(cell);
        if (s < best.score) best = { cell, score: s, depth };
      }
    }
    frontier = next;
  }
  if (best.cell === origin) return [];
  const route = [];
  for (let c = best.cell; c && c !== origin; c = prev.get(key(c))) route.unshift(c);
  return route.slice(0, steps);
}

// ダイス式として受け付ける文字 (AI が書いた式をそのまま Roll に渡さない: M5)
const FORMULA_RE = /^[0-9dkhl+\-*/() ]{1,60}$/i;

/** AI のダイス式を検める。式として読めなければ null */
export function sanitizeFormula(formula) {
  const f = String(formula ?? "").trim();
  if (!f || !FORMULA_RE.test(f) || !/\d*d\d+/i.test(f)) return null;
  let depth = 0;
  for (const ch of f) {
    depth += ch === "(" ? 1 : ch === ")" ? -1 : 0;
    if (depth < 0) return null;
  }
  return depth === 0 ? f : null;
}

/** AI に渡す行動の一覧 (武器・特技・呪文。最大 8 件) */
export function availableActions(items = []) {
  return items
    .filter(i => ["weapon", "feat", "spell"].includes(i?.type) && i.name)
    .slice(0, 8)
    .map(i => ({ name: String(i.name), type: i.type }));
}

export const MOVE_INTENT_LABELS = Object.freeze({
  approach_target: "対象に接近",
  flank_target: "挟み撃ちの位置へ",
  retreat_from_enemies: "敵から後退",
  seek_cover_or_gimmick: "遮蔽・ギミックへ",
  hold_position: "その場で構える",
});
