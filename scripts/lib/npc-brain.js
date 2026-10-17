// NPC の「ブレイン」(目的・知識の境界・信頼度・記憶と忘却・声色) の純粋関数 (Foundry 非依存)
// 保存先は actor の flag `npc_brain` (scripts/npc.js)。ここでは値の組み立てと計算だけを行い、元の値は書き換えない

export const SHORT_TERM_LIMIT = 5;
export const TRUST_LIMIT = 5;
export const IMPORTANCE = Object.freeze({ min: 1, max: 5, default: 3 });
// 鮮明度がこれ未満になった長期記憶は忘れる (一覧から消す)
export const FORGET_BELOW = 0.15;
// 短期記憶から押し出されて長期記憶に移るときの鮮明度
export const PROMOTED_CLARITY = 0.9;
export const DEFAULT_VOICE = Object.freeze({ name: "ja-JP-KeitaNeural", rate: "+0%", pitch: "+0Hz" });
export const VOICE_CHOICES = Object.freeze([
  { name: "ja-JP-KeitaNeural", label: "男性 (Keita)" },
  { name: "ja-JP-NanamiNeural", label: "女性 (Nanami)" },
]);

export const DEFAULT_NPC_BRAIN = Object.freeze({
  goal: "自身の生活と安全を守りつつ、周囲の動向を探る",
  public_known: "この周辺の一般的な地理と、表向きの噂話",
  secret_known: "",
  ignorance: "他地域の政治、専門的な古代魔術の知識、自分が立ち会っていない出来事",
  trust: 0,
  // 1.0 = 忘れない、0.8 = 標準、0.5 = 忘れっぽい
  memory_retention: 0.8,
  voice: DEFAULT_VOICE,
  short_term_memory: [],
  long_term_memory: [],
});

function clamp(value, low, high, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(high, Math.max(low, n));
}

/** 重要度を 1〜5 の整数にする (無効な値は 3) */
export function clampImportance(value) {
  return Math.round(clamp(value, IMPORTANCE.min, IMPORTANCE.max, IMPORTANCE.default));
}

export function clampTrust(value) {
  return Math.round(clamp(value, -TRUST_LIMIT, TRUST_LIMIT, 0));
}

function normalizeMemory(m, { long = false, now = 0 } = {}) {
  const summary = String(m?.summary ?? "").trim();
  if (!summary) return null;
  const item = { summary, importance: clampImportance(m.importance), timestamp: Number(m.timestamp) || now };
  if (long) item.clarity = clamp(m.clarity, 0, 1, 1);
  else if (m.promoted) item.promoted = true;
  return item;
}

/**
 * 保存済みの値を既定値で補ってブレインにする。保存が無ければ、伝記 (HTML を除いた文) を表向きの知識にする
 */
export function normalizeBrain(saved, biographyText = "") {
  const base = structuredClone(DEFAULT_NPC_BRAIN);
  if (!saved || typeof saved !== "object") {
    const bio = String(biographyText || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    if (bio) base.public_known = bio.slice(0, 250);
    return base;
  }
  const text = key => (typeof saved[key] === "string" ? saved[key] : base[key]);
  return {
    goal: text("goal"),
    public_known: text("public_known"),
    secret_known: text("secret_known"),
    ignorance: text("ignorance"),
    trust: clampTrust(saved.trust ?? 0),
    memory_retention: clamp(saved.memory_retention, 0.3, 1, base.memory_retention),
    voice: { ...base.voice, ...(saved.voice && typeof saved.voice === "object" ? saved.voice : {}) },
    short_term_memory: (saved.short_term_memory ?? []).map(m => normalizeMemory(m)).filter(Boolean),
    long_term_memory: (saved.long_term_memory ?? []).map(m => normalizeMemory(m, { long: true })).filter(Boolean),
  };
}

/** 短期記憶が上限を超えたら古いものから長期記憶へ移す (重要で既に長期記憶にあるものは移さない) */
export function promoteShortTerm(brain) {
  const short = [...brain.short_term_memory];
  const long = [...brain.long_term_memory];
  while (short.length > SHORT_TERM_LIMIT) {
    const old = short.shift();
    if (old.promoted) continue;
    long.push({ summary: old.summary, importance: old.importance, clarity: PROMOTED_CLARITY, timestamp: old.timestamp });
  }
  return { ...brain, short_term_memory: short, long_term_memory: long };
}

/**
 * 会話のあとの記憶の更新 (信頼度の増減と新しい記憶)。
 * 重要度 4 以上はすぐ長期記憶にも入れ、短期記憶の側には promoted の印を付けて二重に移さない (M8)
 */
export function rememberDialogue(brain, { trustDelta = 0, memory = null, now = Date.now() } = {}) {
  let next = { ...brain, trust: clampTrust((brain.trust ?? 0) + (Number(trustDelta) || 0)) };
  const summary = String(memory?.summary ?? "").trim();
  if (summary) {
    const importance = clampImportance(memory.importance);
    const important = importance >= 4;
    next.short_term_memory = [...next.short_term_memory, { summary, importance, timestamp: now, ...(important ? { promoted: true } : {}) }];
    if (important) {
      next.long_term_memory = [...next.long_term_memory, { summary, importance, clarity: 1, timestamp: now }];
    }
  }
  return promoteShortTerm(next);
}

/**
 * 時間経過で長期記憶を 1 段階風化させる。重要度 5 は忘れない。
 * 鮮明度は丸めずに保つ (丸めると減衰が小さいとき永久に変わらない: M9)。FORGET_BELOW 未満になったら忘れる
 */
export function decayMemories(brain) {
  const retention = clamp(brain.memory_retention, 0.3, 1, DEFAULT_NPC_BRAIN.memory_retention);
  const long = brain.long_term_memory
    .map(m => {
      if (m.importance >= IMPORTANCE.max) return { ...m, clarity: 1 };
      const weight = 1 - (IMPORTANCE.max - m.importance) * (1 - retention) * 0.35;
      return { ...m, clarity: (m.clarity ?? 1) * weight };
    })
    .filter(m => m.clarity >= FORGET_BELOW);
  return { ...brain, long_term_memory: long };
}

export function clarityLabel(clarity) {
  const c = clarity ?? 1;
  return c >= 0.8 ? "[鮮明]" : c >= 0.4 ? "[うろ覚え]" : "[ほぼ忘却]";
}

/** AI に渡す形 (npc_dialogue_request の listening_npcs の 1 人分。id・name・distance_ft・voice は呼び出し側で足す) */
export function brainForPrompt(brain) {
  return {
    goal: brain.goal,
    trust: brain.trust,
    public_known: brain.public_known,
    secret_known: brain.secret_known,
    ignorance: brain.ignorance,
    short_term_memory: brain.short_term_memory.map(m => m.summary),
    long_term_memory: brain.long_term_memory.map(m => `${clarityLabel(m.clarity)} (重要度:${m.importance}) ${m.summary}`),
  };
}

/**
 * ブレイン管理画面の入力値を保存用のブレインにする。
 * 長期記憶は画面の行の順に対応させ、文が変わっていない記憶は元の時刻を残す (L6)
 */
export function brainFromForm(prev, form, now = Date.now()) {
  const memories = (form.memories ?? []).map((row, i) => {
    const summary = String(row.summary ?? "").trim();
    if (!summary) return null;
    const old = prev.long_term_memory[i];
    return {
      summary,
      importance: clampImportance(row.importance),
      clarity: clamp(row.clarity, 0, 1, 1),
      timestamp: old && old.summary === summary ? old.timestamp : now,
    };
  }).filter(Boolean);
  return {
    ...prev,
    goal: String(form.goal ?? ""),
    public_known: String(form.public_known ?? ""),
    secret_known: String(form.secret_known ?? ""),
    ignorance: String(form.ignorance ?? ""),
    trust: clampTrust(form.trust),
    memory_retention: clamp(form.memory_retention, 0.3, 1, DEFAULT_NPC_BRAIN.memory_retention),
    voice: {
      name: VOICE_CHOICES.some(v => v.name === form.voice_name) ? form.voice_name : DEFAULT_VOICE.name,
      rate: String(form.voice_rate || DEFAULT_VOICE.rate),
      pitch: String(form.voice_pitch || DEFAULT_VOICE.pitch),
    },
    long_term_memory: memories,
  };
}
