// NPC 会話システム (Foundry 依存): 聴き手の収集・発言の振り分け・反応の表示・記憶の更新・技能判定・ブレイン管理画面
//
// - プレイヤーの発言は GM 側の createChatMessage で拾い (H1)、発言者のトークンの近くに NPC がいれば
//   npc_dialogue_request、いなければ player_message の 1 経路だけで送る (H4。bridge.js)
// - 判定のヒントと NPC の内心は GM のクライアントのメモリだけに置き、チャットには GM 宛てのウィスパーでだけ出す (H7)
// - 判定ボタンは誰でも押せる。押した人のキャラクターで振り、その結果のメッセージを GM 側が拾って続きの会話を頼む (M4)
// 純粋関数は scripts/lib/npc-brain.js・npc-listen.js・cards.js (tests/js)

import { escapeHtml } from "./lib/html.js";
import { buildNpcDialogueHtml, buildNpcGmNoteHtml } from "./lib/cards.js";
import { generatedFlags, MODULE_ID } from "./lib/chat-filter.js";
import {
  brainForPrompt,
  brainFromForm,
  clarityLabel,
  decayMemories,
  FORGET_BELOW,
  normalizeBrain,
  rememberDialogue,
  VOICE_CHOICES,
} from "./lib/npc-brain.js";
import {
  checkFormula,
  checkResultContext,
  isConversableNpc,
  selectListeners,
  skillKeyOf,
  speakerTokenId,
} from "./lib/npc-listen.js";
import { findRoomAt, tokenCenter } from "./lib/rooms.js";

const BRAIN_FLAG = "npc_brain";
const CHECK_FLAG = "npc_check";

let send = null;
// 判定の提案 (offer id → 中身)。GM のクライアントのメモリだけに置く。再読み込みすると消える
const offers = new Map();
// actor ごとのブレイン更新の順番待ち (M10: 並行して書くと記憶が消えるため直列にする)
const brainQueues = new Map();

/** bridge.js から WebSocket 送信関数を受け取る */
export function setNpcSender(fn) {
  send = fn;
}

// -------------------------------------------------------------------------
// ブレイン (actor の flag)
// -------------------------------------------------------------------------
function biographyOf(actor) {
  return actor?.system?.details?.biography?.value ?? actor?.system?.biography ?? "";
}

export function getBrain(actor) {
  return normalizeBrain(actor?.getFlag?.(MODULE_ID, BRAIN_FLAG), biographyOf(actor));
}

/** ブレインを読み、change で作り直して 1 回で保存する。同じ actor への更新は順番に行う */
function updateBrain(actor, change) {
  const key = actor.uuid ?? actor.id;
  const run = (brainQueues.get(key) ?? Promise.resolve()).then(async () => {
    const next = change(getBrain(actor));
    await actor.setFlag(MODULE_ID, BRAIN_FLAG, next);
    return next;
  });
  brainQueues.set(key, run.catch(() => null));
  return run;
}

// -------------------------------------------------------------------------
// 聴き手と発言の振り分け
// -------------------------------------------------------------------------
function soundBlocked(scene) {
  const backend = CONFIG?.Canvas?.polygonBackends?.sound;
  // 壁の判定はキャンバスに表示中のシーンでだけできる
  if (!backend || canvas?.scene?.id !== scene.id) return () => false;
  return (_npc, from, to) => {
    try {
      return Boolean(backend.testCollision(from, to, { type: "sound", mode: "any" }));
    } catch (e) {
      return false;
    }
  };
}

function tokenBox(doc) {
  return { id: doc.id, x: doc.x, y: doc.y, width: doc.width, height: doc.height, doc };
}

/** 発言者の声が届く NPC を、AI に渡す形で集める (近い順・最大 4 人) */
export function collectListeners(scene, speakerDoc) {
  if (!scene || !speakerDoc) return [];
  const npcs = scene.tokens.contents
    .filter(t => isConversableNpc({ actorType: t.actor?.type, hasPlayerOwner: t.actor?.hasPlayerOwner, hidden: t.hidden }))
    .map(tokenBox);
  const picked = selectListeners(npcs, tokenBox(speakerDoc), {
    gridSize: scene.grid?.size ?? 100,
    gridDistance: scene.grid?.distance ?? 5,
    blocked: soundBlocked(scene),
  });
  return picked.map(({ npc, distanceFt }) => listenerPayload(npc.doc, distanceFt));
}

function listenerPayload(doc, distanceFt) {
  const brain = getBrain(doc.actor);
  return { id: doc.id, name: doc.name, distance_ft: distanceFt, ...brainForPrompt(brain), voice: brain.voice };
}

function roomOf(scene, doc) {
  const rooms = scene.getFlag?.(MODULE_ID, "rooms");
  if (!rooms?.length || !doc) return null;
  const gridSize = scene.grid?.size ?? 100;
  const c = tokenCenter({ x: doc.x, y: doc.y, width: doc.width, height: doc.height }, gridSize);
  return findRoomAt(rooms, c.x, c.y, { gridSize, offsetX: scene.dimensions?.sceneX ?? 0, offsetY: scene.dimensions?.sceneY ?? 0 });
}

/** メッセージの発言者のトークン (H2)。発言者のトークン → 発言したユーザーのキャラクターのトークンの順 */
function speakerDocOf(message) {
  const scene = game.scenes?.get(message.speaker?.scene) ?? game.scenes?.viewed ?? game.scenes?.active;
  if (!scene) return { scene: null, doc: null };
  const tokens = scene.tokens.contents.map(t => ({ id: t.id, actorId: t.actorId ?? t.actor?.id }));
  const id = speakerTokenId({ speakerTokenId: message.speaker?.token, characterId: message.author?.character?.id }, tokens);
  return { scene, doc: id ? scene.tokens.get(id) : null };
}

function dialogueRequest({ scene, speakerDoc, speakerName, input, listeners, checkResult = null }) {
  const room = roomOf(scene, speakerDoc);
  return {
    event: "npc_dialogue_request",
    speaker_name: speakerName,
    speaker_token_id: speakerDoc?.id ?? null,
    scene_id: scene.id,
    player_input: input,
    // H3: 発言者がいる部屋 (部屋の外なら シーン名)
    room_name: room?.name ?? scene.name,
    room_flavor: room?.flavor ?? "",
    listening_npcs: listeners,
    check_result_context: checkResult,
  };
}

/**
 * プレイヤーの発言を NPC 会話として送る。近くに NPC がいなければ false (呼び出し側が AI GM に送る)
 */
export function sendDialogueIfHeard(message, input) {
  const { scene, doc } = speakerDocOf(message);
  const listeners = collectListeners(scene, doc);
  if (!listeners.length || !send) return false;
  console.log(`AI GM Bridge | NPC 会話: ${doc.name}「${input}」 聴き手: ${listeners.map(l => l.name).join("・")}`);
  return send(dialogueRequest({ scene, speakerDoc: doc, speakerName: doc.name, input, listeners }));
}

// -------------------------------------------------------------------------
// サーバーからの反応 (npc_dialogue_response)
// -------------------------------------------------------------------------
function gmRecipients() {
  return (game.users?.filter?.(u => u.isGM) ?? []).map(u => u.id);
}

export async function handleNpcDialogueResponse(data) {
  const res = data.result ?? {};
  const scene = game.scenes?.get(data.scene_id) ?? game.scenes?.viewed;
  const npcDoc = scene?.tokens?.get(res.primary_npc_id) ?? null;
  const speakerDoc = data.speaker_token_id ? scene?.tokens?.get(data.speaker_token_id) : null;
  const npcName = npcDoc?.name ?? res.primary_npc_name ?? "NPC";

  let brain = null;
  if (npcDoc?.actor) {
    brain = await updateBrain(npcDoc.actor, b => rememberDialogue(b, { trustDelta: res.trust_delta, memory: res.new_memory }));
  }

  let offerId = null;
  if (res.check_offer?.skill && npcDoc) {
    offerId = foundry.utils.randomID();
    offers.set(offerId, {
      skill: res.check_offer.skill,
      dc: Number(res.check_offer.dc) || 13,
      hint: res.check_offer.target_secret_hint ?? "",
      sceneId: scene.id,
      npcTokenId: npcDoc.id,
      speakerTokenId: speakerDoc?.id ?? null,
      used: false,
    });
  }

  await ChatMessage.create({
    speaker: { scene: scene?.id, token: npcDoc?.id, actor: npcDoc?.actor?.id, alias: npcName },
    content: buildNpcDialogueHtml(res, { offerId, actorId: speakerDoc?.actor?.id ?? null }),
    flags: generatedFlags(),
  });

  const note = buildNpcGmNoteHtml(res, { npcName, trust: brain?.trust ?? null });
  if (note) {
    await ChatMessage.create({
      speaker: { alias: "AI GM (NPC メモ)" },
      content: note,
      whisper: gmRecipients(),
      flags: generatedFlags(),
    });
  }

  // 吹き出し (キャンバスに表示中のシーンだけ)
  const bubbles = canvas?.hud?.bubbles;
  if (bubbles && canvas.scene?.id === scene?.id) {
    const text = res.speech || (res.narrative_action ? `${res.narrative_action.slice(0, 40)}…` : "");
    if (npcDoc?.object && text) await bubbles.broadcast(npcDoc.object, text, { emote: !res.speech });
    for (const a of res.ambient_reactions ?? []) {
      const t = scene.tokens.get(a.npc_id);
      if (t?.object && a.silent_emote) await bubbles.broadcast(t.object, a.silent_emote, { emote: true });
    }
  }
}

// -------------------------------------------------------------------------
// 技能判定 (誰でも押せるボタン → 押した人のクライアントで振る → GM が結果を拾う)
// -------------------------------------------------------------------------
/** チャットの「本音を引き出す」ボタン (全ユーザーのクライアントで動く) */
export async function onNpcCheckClick(ev) {
  const btn = ev.target?.closest?.(".ai-npc-check-btn");
  if (!btn || btn.disabled) return;
  ev.preventDefault();
  const fromButton = game.actors?.get(btn.dataset.actorId);
  const actor = fromButton?.isOwner ? fromButton : game.user?.character;
  if (!actor) {
    ui.notifications.warn("AI GM Bridge: 判定するキャラクターがいません (ユーザー設定でキャラクターを割り当ててください)。");
    return;
  }
  btn.disabled = true;
  btn.style.opacity = "0.6";
  const skill = btn.dataset.skill || "説得";
  const rollData = actor.getRollData?.() ?? {};
  const roll = new Roll(checkFormula(skillKeyOf(skill), rollData), rollData);
  await roll.evaluate();
  await roll.toMessage({
    speaker: ChatMessage.getSpeaker({ actor }),
    flavor: `🎲 ${escapeHtml(skill)}判定 (NPC の本音を引き出す)`,
    flags: { [MODULE_ID]: { [CHECK_FLAG]: { offer_id: btn.dataset.offerId } } },
  });
}

/** 判定の結果のメッセージか (GM 側の createChatMessage で使う) */
export function isNpcCheckRoll(message) {
  return Boolean(message.flags?.[MODULE_ID]?.[CHECK_FLAG]?.offer_id);
}

/** 判定の結果を受けて、NPC に続きを反応させる (1 つの提案につき 1 回だけ) */
export async function handleNpcCheckRoll(message) {
  const offerId = message.flags[MODULE_ID][CHECK_FLAG].offer_id;
  const offer = offers.get(offerId);
  if (!offer) {
    ui.notifications.warn("AI GM Bridge: この判定の提案が見つかりません (GM の画面を再読み込みすると消えます)。");
    return;
  }
  if (offer.used) return;
  offer.used = true;
  const total = message.rolls?.[0]?.total;
  if (!Number.isFinite(total)) return;
  const scene = game.scenes?.get(offer.sceneId);
  const npcDoc = scene?.tokens?.get(offer.npcTokenId);
  if (!scene || !npcDoc) return;

  const { doc } = speakerDocOf(message);
  const speakerDoc = doc ?? scene.tokens.get(offer.speakerTokenId) ?? null;
  let listeners = speakerDoc ? collectListeners(scene, speakerDoc) : [];
  // 判定の相手は離れていても必ず聴き手に入れる (先頭)
  listeners = [listenerPayload(npcDoc, listeners.find(l => l.id === npcDoc.id)?.distance_ft ?? 0),
    ...listeners.filter(l => l.id !== npcDoc.id)];
  const speakerName = speakerDoc?.name ?? message.speaker?.alias ?? message.author?.name ?? "PC";
  send?.(dialogueRequest({
    scene,
    speakerDoc,
    speakerName,
    input: `（${offer.skill}判定で${npcDoc.name}の本音を引き出そうとした）`,
    listeners,
    checkResult: checkResultContext({ skill: offer.skill, dc: offer.dc, total, hint: offer.hint }),
  }));
}

// -------------------------------------------------------------------------
// ブレイン管理画面 (/ai NPC)
// -------------------------------------------------------------------------
function npcActors() {
  return (game.actors?.contents ?? []).filter(a => a.type !== "character" && !a.hasPlayerOwner);
}

function brainDialogHtml(actor, brain, isToken) {
  const actorOptions = npcActors()
    .map(a => `<option value="${escapeHtml(a.id)}" ${a.id === actor.id && !isToken ? "selected" : ""}>${escapeHtml(a.name)}</option>`)
    .join("");
  const tokenOption = isToken ? `<option value="" selected>${escapeHtml(actor.name)} (選択中のトークン)</option>` : "";
  const memRows = brain.long_term_memory.map(m => `
    <div class="aib-mem-row">
      <input type="text" class="mem-summary" value="${escapeHtml(m.summary)}">
      <label>重要度 <input type="number" class="mem-imp" min="1" max="5" value="${Number(m.importance)}"></label>
      <label>鮮明度 <input type="number" class="mem-clarity" min="0" max="1" step="0.05" value="${Number(m.clarity.toFixed(2))}"></label>
      <span class="mem-label">${escapeHtml(clarityLabel(m.clarity))}</span>
    </div>`).join("");
  const voiceOptions = VOICE_CHOICES
    .map(v => `<option value="${escapeHtml(v.name)}" ${brain.voice.name === v.name ? "selected" : ""}>${escapeHtml(v.label)}</option>`)
    .join("");
  const shortList = brain.short_term_memory.map(m => `<li>${escapeHtml(m.summary)}</li>`).join("");
  return `
  <div class="aib aib-brain">
    <div class="aib-top">
      <select data-brain="actor">${tokenOption}${actorOptions}</select>
      <button type="button" data-brain-action="decay" title="時間の経過で長期記憶を 1 段階あいまいにします">⏳ 時間を進める (記憶の風化)</button>
    </div>
    <div class="aib-grid aib-settings">
      <label class="aib-field"><span class="aib-label">🤝 PC への信頼度 (-5〜+5)</span><input type="number" data-brain="trust" min="-5" max="5" value="${Number(brain.trust)}"></label>
      <label class="aib-field"><span class="aib-label">🧠 記憶力 (0.3 忘れっぽい〜1.0 忘れない)</span><input type="number" data-brain="memory_retention" min="0.3" max="1" step="0.05" value="${Number(brain.memory_retention)}"></label>
      <label class="aib-field"><span class="aib-label">🗣️ 声 / 高さ / 速さ</span>
        <span class="aib-voice"><select data-brain="voice_name">${voiceOptions}</select>
        <input type="text" data-brain="voice_pitch" value="${escapeHtml(brain.voice.pitch)}" title="声の高さ (例: -10Hz, +8Hz)">
        <input type="text" data-brain="voice_rate" value="${escapeHtml(brain.voice.rate)}" title="話す速さ (例: -15%, +10%)"></span></label>
    </div>
    <label class="aib-field"><span class="aib-label">🎯 いまの目的・行動原理</span><input type="text" data-brain="goal" value="${escapeHtml(brain.goal)}"></label>
    <div class="aib-grid aib-two">
      <label class="aib-field aib-pub"><span class="aib-label">📖 聞かれれば話す知識</span><textarea data-brain="public_known" class="aib-short">${escapeHtml(brain.public_known)}</textarea></label>
      <label class="aib-field aib-dm"><span class="aib-label">🔒 隠している秘密と、打ち明ける条件</span><textarea data-brain="secret_known" class="aib-short">${escapeHtml(brain.secret_known)}</textarea></label>
    </div>
    <label class="aib-field"><span class="aib-label">🚫 知らないこと (AI がメタ知識で答えないように)</span><input type="text" data-brain="ignorance" value="${escapeHtml(brain.ignorance)}"></label>
    <details class="aib-box"><summary>🕘 直近のやり取り (${brain.short_term_memory.length} 件)</summary><ul class="aib-short-mem">${shortList || "<li>まだありません</li>"}</ul></details>
    <div class="aib-field"><span class="aib-label">📚 長期記憶 (鮮明度 0.8 以上 = 鮮明 / 0.4〜0.8 = うろ覚え / 0.4 未満 = ほぼ忘却。${FORGET_BELOW} 未満で忘れる。文を消すと削除)</span>
      <div class="aib-mem-list">${memRows || "<p>長期記憶はまだありません (会話で自動的にたまります)</p>"}</div></div>
    <div class="aib-actions"><button type="button" class="aib-save" data-brain-action="save">💾 この NPC のブレインを保存</button></div>
  </div>`;
}

function readBrainForm(root) {
  const v = key => root.querySelector(`[data-brain="${key}"]`)?.value ?? "";
  return {
    goal: v("goal"), public_known: v("public_known"), secret_known: v("secret_known"), ignorance: v("ignorance"),
    trust: v("trust"), memory_retention: v("memory_retention"),
    voice_name: v("voice_name"), voice_pitch: v("voice_pitch"), voice_rate: v("voice_rate"),
    memories: [...root.querySelectorAll(".aib-mem-row")].map(row => ({
      summary: row.querySelector(".mem-summary")?.value ?? "",
      importance: row.querySelector(".mem-imp")?.value,
      clarity: row.querySelector(".mem-clarity")?.value,
    })),
  };
}

let brainApp = null;

async function renderBrainDialog(title, content) {
  const DialogV2 = foundry?.applications?.api?.DialogV2;
  if (DialogV2) {
    const app = new DialogV2({
      classes: ["ai-gm-builder"],
      window: { title, resizable: true },
      position: { width: 720, height: "auto" },
      content,
      buttons: [{ action: "close", label: "閉じる", default: true }],
    });
    await app.render({ force: true });
    return { app, root: app.element };
  }
  return new Promise(resolve => {
    const app = new Dialog({
      title, content, buttons: { close: { label: "閉じる" } },
      render: html => resolve({ app, root: html instanceof HTMLElement ? html : html[0] }),
    }, { classes: ["dialog", "ai-gm-builder"], width: 720, height: "auto", resizable: true });
    app.render(true);
  });
}

/** NPC のブレイン (目的・知識・秘密・記憶・声) を編集する画面。トークンを選択していればそのトークンの NPC */
export async function openNpcBrainManager(target = null) {
  let actor = target;
  let isToken = false;
  if (!actor) {
    const controlled = canvas?.tokens?.controlled?.[0]?.actor;
    if (controlled && controlled.type !== "character" && !controlled.hasPlayerOwner) {
      actor = controlled;
      isToken = Boolean(controlled.isToken);
    }
  }
  actor ??= npcActors()[0];
  if (!actor) {
    ui.notifications.warn("AI GM Bridge: 編集できる NPC がいません。NPC のアクターを作るか、NPC のトークンを選択してください。");
    return;
  }
  const brain = getBrain(actor);
  await brainApp?.close?.();
  const { app, root } = await renderBrainDialog(`🧠 NPC ブレイン - ${actor.name}`, brainDialogHtml(actor, brain, isToken));
  brainApp = app;
  root.addEventListener("keydown", ev => {
    if (ev.key === "Enter" && ev.target?.tagName === "INPUT") ev.preventDefault();
  });
  root.querySelector('[data-brain="actor"]')?.addEventListener("change", ev => {
    const next = game.actors.get(ev.target.value);
    if (next) openNpcBrainManager(next);
  });
  root.querySelector('[data-brain-action="decay"]')?.addEventListener("click", async () => {
    await updateBrain(actor, b => decayMemories(brainFromForm(b, readBrainForm(root))));
    ui.notifications.info(`${actor.name} の記憶が時間の経過で少しあいまいになりました。`);
    openNpcBrainManager(actor);
  });
  root.querySelector('[data-brain-action="save"]')?.addEventListener("click", async () => {
    await updateBrain(actor, b => brainFromForm(b, readBrainForm(root)));
    ui.notifications.info(`『${actor.name}』のブレインを保存しました。`);
    await brainApp?.close?.();
  });
}
