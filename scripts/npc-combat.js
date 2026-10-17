// NPC 自律戦闘 (Foundry 依存): 手番を迎えた NPC の知覚を集めて npc_turn_request を送り、
// 返ってきた execute_npc_turn で移動・吹き出し・行動 (アイテムの使用かダイス) を実行する
//
// - M3: シーンは game.scenes.active ではなく combat.scene / tokenDoc.parent を使う
// - M5: アイテムの使用に失敗したらダイスで代用する。AI のダイス式は検めてから振る。振るときはカードを flavor に入れて 1 通にする
// - M6: 頼んだときの手番 (combat の id / round / turn と手番のトークン) を付けて送り、届いたときに手番が進んでいたら実行しない。
//   行動不能・非表示の NPC には頼まない
// 純粋関数は scripts/lib/npc-tactics.js・cards.js (tests/js)

import { buildNpcTurnGmNoteHtml, buildNpcTurnHtml } from "./lib/cards.js";
import { generatedFlags, MODULE_ID } from "./lib/chat-filter.js";
import {
  availableActions,
  gridDistance,
  healthStatus,
  isIncapacitated,
  isSameTurn,
  moveBudget,
  sanitizeFormula,
  shouldTakeTurn,
  turnKey,
} from "./lib/npc-tactics.js";
import { getBrain, roomOf } from "./npc.js";
import { cellOf, gridOf, hasLineOfSight, moveTokenByIntent } from "./npc-move.js";

export const AUTO_COMBAT_SETTING = "npcAutoCombat";

let send = null;
// 頼んだ手番の目印 (同じ手番に二度頼まない。combatTurnChange と updateCombat の両方で呼ばれるため)
const requestedTurns = new Set();
// 行動中の NPC の actor id (アイテムのカードを AI GM やほかの NPC に発言として転送しないため)
const actingActors = new Set();

/** bridge.js から WebSocket 送信関数を受け取る */
export function setCombatSender(fn) {
  send = fn;
}

/** 設定画面の「NPC の自律戦闘」 (init フックで呼ぶ) */
export function registerNpcCombatSettings() {
  game.settings.register(MODULE_ID, AUTO_COMBAT_SETTING, {
    name: "NPC の自律戦闘",
    hint: "戦闘トラッカーで NPC の手番になったとき、AI が行動 (移動・攻撃・セリフ) を決めて実行します。",
    scope: "world",
    config: true,
    type: Boolean,
    default: true,
  });
}

function autoCombatEnabled() {
  try {
    return game.settings.get(MODULE_ID, AUTO_COMBAT_SETTING) !== false;
  } catch (e) {
    return true;
  }
}

/** 行動中の NPC が出したメッセージか (bridge.js の createChatMessage で転送しないために使う) */
export function isNpcTurnMessage(message) {
  const actorId = message.speaker?.actor;
  return Boolean(actorId && actingActors.has(actorId));
}

function isPc(doc) {
  return doc.actor?.type === "character" || Boolean(doc.actor?.hasPlayerOwner);
}

function hpOf(actor) {
  const hp = actor?.system?.attributes?.hp ?? {};
  return { value: hp.value ?? null, max: hp.max ?? null };
}

function turnState(doc, combatant = null) {
  return {
    actorType: doc.actor?.type ?? null,
    hasPlayerOwner: Boolean(doc.actor?.hasPlayerOwner),
    hidden: Boolean(doc.hidden || combatant?.hidden),
    defeated: Boolean(combatant?.isDefeated),
    hp: hpOf(doc.actor).value,
    statuses: doc.actor?.statuses ?? [],
  };
}

function isDown(doc) {
  return isIncapacitated({ hp: hpOf(doc.actor).value, statuses: doc.actor?.statuses ?? [] });
}

/** NPC から見える PC (同じシーンの、隠れていない PC のトークン) */
function opponentsOf(scene, doc) {
  return scene.tokens.contents.filter(t => t.id !== doc.id && !t.hidden && isPc(t));
}

/** 挟み撃ちの味方 (同じ立場の、隠れていない NPC) */
function alliesOf(scene, doc) {
  return scene.tokens.contents.filter(t => t.id !== doc.id && !t.hidden && !isPc(t) && t.disposition === doc.disposition && !isDown(t));
}

function biographyText(actor) {
  const raw = actor?.system?.details?.biography?.value ?? actor?.system?.biography ?? "";
  return String(raw).replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 300);
}

/** 手番の NPC の知覚 (AI に渡す) */
export function buildPerception(doc) {
  const scene = doc.parent;
  const actor = doc.actor;
  const g = gridOf(scene);
  const here = cellOf(doc, g);
  const hp = hpOf(actor);
  const brain = getBrain(actor);
  const room = roomOf(scene, doc);
  const targets = opponentsOf(scene, doc).map(pc => {
    const squares = gridDistance(here, cellOf(pc, g));
    const pcHp = hpOf(pc.actor);
    return {
      token_id: pc.id,
      name: pc.name,
      distance_ft: squares * g.distance,
      adjacent: squares <= 1,
      has_los: hasLineOfSight(scene, doc, pc),
      health_status: isDown(pc) ? "倒れている" : healthStatus(pcHp.value ?? 1, pcHp.max ?? 1),
    };
  });
  return {
    token_id: doc.id,
    npc_name: doc.name,
    npc_role: actor?.system?.details?.type?.value ?? actor?.type ?? "NPC",
    npc_bio: biographyText(actor),
    npc_goal: brain.goal,
    hp_current: hp.value ?? 10,
    hp_max: hp.max ?? 10,
    health_status: healthStatus(hp.value ?? 10, hp.max ?? 10),
    move_speed: actor?.system?.attributes?.movement?.walk ?? 30,
    available_actions: availableActions(actor?.items?.contents ?? []),
    room_name: room?.name ?? scene.name,
    room_flavor: room?.flavor ?? "",
    room_gm_notes: room?.gm_notes ?? "",
    perceived_targets: targets,
    voice: brain.voice,
  };
}

/** 手番が変わったとき (combatTurnChange / updateCombat)。NPC の手番なら AI に行動を頼む */
export function onCombatTurn(combat) {
  if (!send || !combat?.started || !autoCombatEnabled()) return;
  const combatant = combat.combatant;
  const doc = combatant?.token;
  if (!doc?.parent || !doc.actor) return;
  if (!shouldTakeTurn(turnState(doc, combatant))) return;
  const turn = { id: combat.id, round: combat.round, turn: combat.turn };
  const key = turnKey(turn);
  if (requestedTurns.has(key)) return;
  requestedTurns.add(key);
  if (requestedTurns.size > 200) requestedTurns.delete(requestedTurns.values().next().value);

  console.log(`AI GM Bridge | NPC の手番: ${doc.name} (ラウンド ${combat.round})。AI に行動を頼みます`);
  // M3: シーンは手番のトークンのシーン
  if (!send({ event: "npc_turn_request", scene_id: doc.parent.id, combat: turn, perception: buildPerception(doc) })) {
    requestedTurns.delete(key);
    return;
  }
  ui.notifications.info(`AI GM Bridge: ${doc.name}の戦術を考えています…`);
}

function gmRecipients() {
  return (game.users?.filter?.(u => u.isGM) ?? []).map(u => u.id);
}

function validFormula(formula) {
  const f = sanitizeFormula(formula);
  if (!f) return null;
  try {
    return typeof Roll.validate !== "function" || Roll.validate(f) ? f : null;
  } catch (e) {
    return null;
  }
}

/** 行動の実行 (アイテム → ダイス → カードだけ)。notes に GM 向けの注意を足す */
async function performAction(scene, doc, decision, notes) {
  const actor = doc.actor;
  const speaker = { scene: scene.id, token: doc.id, actor: actor?.id, alias: doc.name };
  const card = buildNpcTurnHtml(decision);
  const item = decision.action_name ? actor?.items?.getName?.(decision.action_name) : null;
  let cardPosted = false;
  let formula = validFormula(decision.dice_formula);

  if (item && typeof item.use === "function") {
    await ChatMessage.create({ speaker, content: card, flags: generatedFlags() });
    cardPosted = true;
    try {
      await item.use();
      return;
    } catch (e) {
      console.warn(`AI GM Bridge | 「${item.name}」の使用に失敗しました`, e);
      notes.push(`「${item.name}」を使えなかったため、ダイスで代用しました`);
      formula ??= "1d20";
    }
  } else if (decision.action_name) {
    notes.push(`「${decision.action_name}」がアイテムに見つからないため、${formula ? "ダイス式で振りました" : "描写だけにしました"}`);
  }
  if (decision.dice_formula && !formula) notes.push(`AI のダイス式「${decision.dice_formula}」は振れない式のため振りませんでした`);

  if (formula) {
    const rollData = actor?.getRollData?.() ?? {};
    const roll = new Roll(formula, rollData);
    await roll.evaluate();
    const label = decision.action_name ? `🎲 ${decision.action_name}` : "🎲 判定";
    await roll.toMessage({ speaker, flavor: cardPosted ? label : card, flags: generatedFlags() });
    return;
  }
  if (!cardPosted) await ChatMessage.create({ speaker, content: card, flags: generatedFlags() });
}

/** サーバーからの execute_npc_turn */
export async function handleExecuteNpcTurn(data) {
  const decision = data.decision ?? {};
  const combat = game.combats?.get(data.combat?.id);
  const current = combat
    ? { id: combat.id, started: combat.started, round: combat.round, turn: combat.turn, tokenId: combat.combatant?.tokenId }
    : null;
  if (!isSameTurn({ ...data.combat, tokenId: data.token_id }, current)) {
    console.log("AI GM Bridge | NPC の行動が届く前に手番が進んだため、実行しません", data.combat);
    ui.notifications.info("AI GM Bridge: 手番が進んだため、NPC の古い行動は取りやめました。");
    return;
  }
  // M3: 戦闘のシーン (表示中・アクティブのシーンとは限らない)
  const scene = combat.scene ?? game.scenes?.get(data.scene_id);
  const doc = scene?.tokens?.get(data.token_id);
  if (!doc?.actor || !shouldTakeTurn(turnState(doc, combat.combatant))) return;

  const actorId = doc.actor.id;
  actingActors.add(actorId);
  const notes = [];
  try {
    const target = decision.target_token_id ? scene.tokens.get(decision.target_token_id) : null;
    const enemies = opponentsOf(scene, doc).filter(t => !isDown(t));
    const { skipped } = await moveTokenByIntent(scene, doc, {
      intent: decision.move_intent,
      target,
      enemies: enemies.length ? enemies : target ? [target] : [],
      allies: alliesOf(scene, doc),
      maxSteps: moveBudget(doc.actor.system?.attributes?.movement?.walk, scene.grid?.distance),
    });
    if (skipped) notes.push(skipped);

    const bubbles = canvas?.hud?.bubbles;
    if (bubbles && doc.object && canvas.scene?.id === scene.id && (decision.speech || decision.emote)) {
      await bubbles.broadcast(doc.object, decision.speech || decision.emote, { emote: !decision.speech });
    }

    await performAction(scene, doc, decision, notes);
  } finally {
    actingActors.delete(actorId);
  }

  const note = buildNpcTurnGmNoteHtml(decision, {
    npcName: doc.name,
    targetName: decision.target_token_id ? scene.tokens.get(decision.target_token_id)?.name ?? null : null,
    notes,
  });
  if (note) {
    await ChatMessage.create({ speaker: { alias: "AI GM (NPC メモ)" }, content: note, whisper: gmRecipients(), flags: generatedFlags() });
  }
}

/** 手番の変化を拾うフック (サーバー連携を担当する GM のクライアントだけで登録する) */
export function registerNpcCombatHooks() {
  // v12 以降は combatTurnChange、v11 は updateCombat。両方で呼ばれても turnKey で 1 回にする
  Hooks.on("combatTurnChange", combat => onCombatTurn(combat));
  Hooks.on("updateCombat", (combat, change) => {
    if (change?.turn === undefined && change?.round === undefined) return;
    onCombatTurn(combat);
  });
}
