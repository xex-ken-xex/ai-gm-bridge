// NPC 会話の聴き手選び・経路の切り替え・技能判定の純粋関数 (Foundry 非依存)

// 声が届く距離 (フィート)
export const HEARING_RADIUS_FT = 35;
// 1 回の会話で反応を考える NPC の上限 (近い順)
export const MAX_LISTENERS = 4;

/**
 * 会話に反応させる NPC か: NPC (character 以外) で、プレイヤーが所有しておらず、隠れていない (L4: すべて「かつ」)
 */
export function isConversableNpc({ actorType = null, hasPlayerOwner = false, hidden = false } = {}) {
  return Boolean(actorType) && actorType !== "character" && !hasPlayerOwner && !hidden;
}

/** トークン (左上の座標とマス数) の中心 */
function center(t, gridSize) {
  return { x: t.x + ((t.width ?? 1) * gridSize) / 2, y: t.y + ((t.height ?? 1) * gridSize) / 2 };
}

/**
 * 発言者の声が届く NPC を近い順に選ぶ。
 * - 発言者が分からなければ誰も選ばない (L8: 全員を 10ft 扱いにしない。その場合は AI GM に回す)
 * - 発言者自身 (NPC が話した場合) は聴き手に入れない (L8)
 * - blocked(npc) が true の NPC (壁で音が遮られる) は除く
 * npcs / speaker: { id, x, y, width, height } (width / height はマス数)
 */
export function selectListeners(npcs, speaker, {
  gridSize = 100, gridDistance = 5, radiusFt = HEARING_RADIUS_FT, limit = MAX_LISTENERS, blocked = () => false,
} = {}) {
  if (!speaker) return [];
  const from = center(speaker, gridSize);
  const listeners = [];
  for (const npc of npcs || []) {
    if (npc.id === speaker.id) continue;
    const to = center(npc, gridSize);
    const distanceFt = Math.round((Math.hypot(to.x - from.x, to.y - from.y) / gridSize) * gridDistance);
    if (distanceFt > radiusFt) continue;
    if (blocked(npc, from, to)) continue;
    listeners.push({ npc, distanceFt });
  }
  listeners.sort((a, b) => a.distanceFt - b.distanceFt);
  return listeners.slice(0, limit);
}

/** 発言の送り先 (H4: 1 経路だけ)。近くに NPC がいれば NPC 会話、いなければ AI GM */
export function chooseRoute(listeners) {
  return listeners?.length ? "npc_dialogue_request" : "player_message";
}

/**
 * 発言者のトークンを決める (H2: GM が選択中のトークンではなく、メッセージの発言者)。
 * speaker.token があればそれ、無ければ発言したユーザーのキャラクターのトークン (同じシーンの最初の 1 つ)
 * tokens: [{ id, actorId }]
 */
export function speakerTokenId({ speakerTokenId = null, characterId = null } = {}, tokens = []) {
  if (speakerTokenId && tokens.some(t => t.id === speakerTokenId)) return speakerTokenId;
  if (characterId) return tokens.find(t => t.actorId === characterId)?.id ?? null;
  return null;
}

// AI が提案する判定名 → dnd5e の技能キー
export const SKILL_KEYS = Object.freeze({
  説得: "per", 看破: "ins", 威圧: "itm", 捜査: "inv", 知覚: "prc", 隠密: "ste", ペテン: "dec", 芸能: "prf",
  動物使い: "ani", 医術: "med", 宗教: "rel", 歴史: "his", 魔法学: "arc", 自然: "nat", 生存: "sur",
  軽業: "acr", 運動: "ath", 手先の早業: "slt",
});

/** 判定名から dnd5e の技能キーを引く (「説得判定」「説得 (魅力)」なども可)。分からなければ null */
export function skillKeyOf(skillName) {
  const name = String(skillName ?? "").trim();
  if (!name) return null;
  if (Object.values(SKILL_KEYS).includes(name)) return name;
  const hit = Object.keys(SKILL_KEYS).sort((a, b) => b.length - a.length).find(k => name.includes(k));
  return hit ? SKILL_KEYS[hit] : null;
}

/** 判定の式。技能の修正値がロールデータにあれば足す */
export function checkFormula(skillKey, rollData) {
  return skillKey && rollData?.skills?.[skillKey]?.total !== undefined ? `1d20 + @skills.${skillKey}.total` : "1d20";
}

/** 判定結果を NPC 会話のプロンプト用の文にする (成功ならヒントを添える) */
export function checkResultContext({ skill, dc, total, hint = "" }) {
  const success = Number(total) >= Number(dc);
  const head = `${skill}判定 ${success ? "成功" : "失敗"} (出目の合計 ${total} / 目標値 ${dc})`;
  return success && hint ? `${head}。打ち明けてよい内容: ${hint}` : head;
}
