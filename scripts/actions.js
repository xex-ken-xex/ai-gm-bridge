// サーバー → FVTT の action ごとの処理 (Foundry 依存)
// プロトコルを変更する場合は gm_server/protocol.py も同時に更新すること

import { handleBuilderProgress, handleBuilderStageResult } from "./builder.js";
import { handleNpcDialogueResponse } from "./npc.js";
import { handleExecuteNpcTurn } from "./npc-combat.js";
import { generatedFlags } from "./lib/chat-filter.js";
import { buildChoiceCardHtml, buildSceneSuccessCardHtml } from "./lib/cards.js";
import {
  buildJournalPages,
  buildNoteDocs,
  buildRoomPage,
  buildRoomLights,
  buildRoomSounds,
  buildSceneData,
  buildStartGoalLights,
  buildWallDocs,
  DEFAULT_LEVEL_ID,
  mergeRoomUpdates,
  MODULE_ID,
  roomIdOf,
  sceneSize,
} from "./lib/scene-docs.js";

async function postChat(data) {
  await ChatMessage.create({
    speaker: { alias: data.speaker || "AI GM" },
    content: data.content,
    flags: generatedFlags(),
  });
}

async function rollDice(data) {
  const roll = new Roll(data.formula);
  await roll.evaluate();
  await roll.toMessage({
    speaker: { alias: data.speaker || "AI GM (判定)" },
    flags: generatedFlags(),
  });
}

async function playSound(data) {
  if (game.audio?.context?.state === "suspended") {
    await game.audio.context.resume();
  }
  // 第2引数 true: プレイヤーを含む全クライアントで再生 (2026-10-16 ken 決定)
  await foundry.audio.AudioHelper.play({
    src: data.src,
    volume: data.volume ?? 1.0,
    autoplay: true,
    loop: false,
  }, true);
}

// 待ち時間の長い処理の途中経過。GM の画面にだけ通知し、チャットには残さない
async function showProgress(data) {
  ui.notifications.info(`AI GM Bridge: ${data.message ?? ""}`);
}

async function showInteractiveCard(data) {
  await ChatMessage.create({
    speaker: { alias: data.speaker || "サポートGM Geminiちゃん" },
    content: buildChoiceCardHtml(data),
    whisper: [game.user.id], // GM本人のみにウィスパー表示
    flags: generatedFlags(),
  });
}

/** シーン描画原点オフセット (sceneX, sceneY) を取得 (padding や shiftX/Y に追従) */
function sceneOffset(scene) {
  const dims = scene.dimensions ?? (scene.getDimensions ? scene.getDimensions() : {});
  return { offsetX: dims.sceneX ?? 0, offsetY: dims.sceneY ?? 0 };
}

async function createDungeonScene(data) {
  ui.notifications.info("AI GM Bridge: ダンジョンシーンを構築中...");
  const { width: sceneWidth, height: sceneHeight } = sceneSize(data);

  const newScene = await Scene.create(buildSceneData(data));
  console.log("AI GM Bridge | ダンジョンシーン作成完了 ID:", newScene.id);
  // scene_key: 後から届く update_dungeon_rooms (AI の情景ができた区画) の宛先の目印
  if (data.scene_key) await newScene.setFlag(MODULE_ID, "scene_key", data.scene_key);

  const offset = sceneOffset(newScene);

  // v14 レベル背景画像の明示的確認・更新 (確実な描画反映)
  const firstLevel = newScene.levels?.get?.(DEFAULT_LEVEL_ID) || newScene.levels?.contents?.[0];
  if (firstLevel && firstLevel.background?.src !== data.background_src) {
    await newScene.updateEmbeddedDocuments("Level", [{ _id: firstLevel.id, background: { src: data.background_src } }]);
  }

  // 壁ドキュメントの配置
  const wallDocs = buildWallDocs(data.walls, offset, CONST);
  if (wallDocs.length > 0) {
    await newScene.createEmbeddedDocuments("Wall", wallDocs);
  }

  // スタート/ゴール地点と各部屋 (light_preset) に光源を配置
  const lights = [...buildStartGoalLights(data, offset), ...buildRoomLights(data.rooms, offset)];
  if (lights.length > 0) {
    await newScene.createEmbeddedDocuments("AmbientLight", lights);
  }

  // 部屋ごとの環境音 (ambient_sound_src)
  const sounds = buildRoomSounds(data.rooms, offset, newScene.grid?.distance ?? 5);
  if (sounds.length > 0) {
    await newScene.createEmbeddedDocuments("AmbientSound", sounds);
  }

  // 部屋フレーバーノート用ジャーナル帳の作成 & マップノートピンの配置
  let noteCount = 0;
  let journal = null;
  if (data.rooms?.length > 0) {
    journal = await JournalEntry.create({
      name: `【探索録】${newScene.name}`,
      pages: buildJournalPages(data, CONST),
    });
    if (journal) {
      const createdPages = (journal.pages?.contents ?? []).map(p => ({ id: p.id, name: p.name }));
      const noteDocs = buildNoteDocs(data.rooms, journal.id, createdPages, offset);
      await newScene.createEmbeddedDocuments("Note", noteDocs);
      noteCount = noteDocs.length;
      // 部屋メタデータをシーンのフラグに記録（トークン進入判定用）
      await newScene.setFlag(MODULE_ID, "rooms", data.rooms);
      await newScene.setFlag(MODULE_ID, "journal_id", journal.id);
    }
  }

  // 完了通知カード
  await ChatMessage.create({
    speaker: { alias: "サポートGM Geminiちゃん" },
    content: buildSceneSuccessCardHtml({
      sceneId: newScene.id,
      sceneName: newScene.name,
      data,
      sceneWidth,
      sceneHeight,
      wallCount: newScene.walls?.size ?? wallDocs.length,
      noteCount,
      lightCount: lights.length,
      soundCount: sounds.length,
      journalId: journal?.id,
    }),
    whisper: [game.user.id],
    flags: generatedFlags(),
  });
}

/**
 * 先に作ったダンジョンシーンの区画を、AI の情景ができたものから差し替える。
 * ジャーナルのページ・マップノート・光源・環境音・シーンの rooms フラグを書き換える
 */
async function updateDungeonRooms(data) {
  const scene = game.scenes?.find(s => s.getFlag(MODULE_ID, "scene_key") === data.scene_key);
  if (!scene) {
    console.warn("AI GM Bridge | 区画の差し替え先のシーンが見つかりません:", data.scene_key);
    return;
  }
  const { rooms, changed } = mergeRoomUpdates(scene.getFlag(MODULE_ID, "rooms") ?? [], data.rooms);
  if (changed.length === 0) return;
  const ids = new Set(changed.map(c => c.room.id));
  const offset = sceneOffset(scene);

  // ジャーナルのページとマップノート (区画 id の flag で探し、無ければ差し替え前の名前で探す)
  const journal = game.journal?.get(scene.getFlag(MODULE_ID, "journal_id"));
  const pages = journal?.pages?.contents ?? [];
  const notes = scene.notes?.contents ?? [];
  const pageUpdates = [];
  const noteUpdates = [];
  for (const { before, room } of changed) {
    const page = pages.find(p => roomIdOf(p) === room.id) ?? pages.find(p => p.name === before.name);
    if (page) {
      const pageData = buildRoomPage(room, CONST);
      pageUpdates.push({ _id: page.id, name: pageData.name, text: pageData.text });
    }
    const note = notes.find(n => roomIdOf(n) === room.id) ?? notes.find(n => n.text === before.name);
    if (note) noteUpdates.push({ _id: note.id, text: room.name, texture: { src: room.icon || "icons/svg/book.svg" } });
  }
  if (journal && pageUpdates.length) await journal.updateEmbeddedDocuments("JournalEntryPage", pageUpdates);
  if (noteUpdates.length) await scene.updateEmbeddedDocuments("Note", noteUpdates);

  // 光源と環境音は区画ごとに作り直す (light_preset / ambient_sound_src が変わるため)
  const changedRooms = changed.map(c => c.room);
  const oldLights = (scene.lights?.contents ?? []).filter(l => ids.has(roomIdOf(l))).map(l => l.id);
  if (oldLights.length) await scene.deleteEmbeddedDocuments("AmbientLight", oldLights);
  const lights = buildRoomLights(changedRooms, offset);
  if (lights.length) await scene.createEmbeddedDocuments("AmbientLight", lights);
  const oldSounds = (scene.sounds?.contents ?? []).filter(s => ids.has(roomIdOf(s))).map(s => s.id);
  if (oldSounds.length) await scene.deleteEmbeddedDocuments("AmbientSound", oldSounds);
  const sounds = buildRoomSounds(changedRooms, offset, scene.grid?.distance ?? 5);
  if (sounds.length) await scene.createEmbeddedDocuments("AmbientSound", sounds);

  await scene.setFlag(MODULE_ID, "rooms", rooms);
  console.log(`AI GM Bridge | 『${scene.name}』の区画を ${changed.length} 件差し替えました`);
}

export const ACTION_HANDLERS = {
  chat: postChat,
  roll: rollDice,
  play_sound: playSound,
  interactive_card: showInteractiveCard,
  progress: showProgress,
  create_dungeon_scene: createDungeonScene,
  update_dungeon_rooms: updateDungeonRooms,
  builder_stage_result: handleBuilderStageResult,
  builder_progress: handleBuilderProgress,
  npc_dialogue_response: handleNpcDialogueResponse,
  execute_npc_turn: handleExecuteNpcTurn,
};

export async function handleServerAction(data) {
  console.log("AI GM Bridge | 受信:", data);
  const handler = ACTION_HANDLERS[data?.action];
  if (!handler) {
    console.warn("AI GM Bridge | 未対応の action:", data?.action);
    return;
  }
  await handler(data);
}
