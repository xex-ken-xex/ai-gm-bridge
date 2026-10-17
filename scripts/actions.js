// サーバー → FVTT の action ごとの処理 (Foundry 依存)
// プロトコルを変更する場合は gm_server/protocol.py も同時に更新すること

import { generatedFlags } from "./lib/chat-filter.js";
import { buildChoiceCardHtml, buildSceneSuccessCardHtml } from "./lib/cards.js";
import {
  buildJournalPages,
  buildNoteDocs,
  buildSceneData,
  buildStartGoalLights,
  buildWallDocs,
  DEFAULT_LEVEL_ID,
  sceneSize,
} from "./lib/scene-docs.js";

const MODULE_ID = "ai-gm-bridge";

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

async function showInteractiveCard(data) {
  await ChatMessage.create({
    speaker: { alias: data.speaker || "サポートGM (agy)" },
    content: buildChoiceCardHtml(data),
    whisper: [game.user.id], // GM本人のみにウィスパー表示
    flags: generatedFlags(),
  });
}

async function createDungeonScene(data) {
  ui.notifications.info("AI GM Bridge: ダンジョンシーンを構築中...");
  const { width: sceneWidth, height: sceneHeight } = sceneSize(data);

  const newScene = await Scene.create(buildSceneData(data));
  console.log("AI GM Bridge | ダンジョンシーン作成完了 ID:", newScene.id);

  // シーン描画原点オフセット (sceneX, sceneY) を取得 (padding や shiftX/Y に追従)
  const dims = newScene.dimensions ?? (newScene.getDimensions ? newScene.getDimensions() : {});
  const offset = { offsetX: dims.sceneX ?? 0, offsetY: dims.sceneY ?? 0 };

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

  // スタート地点とゴール地点に光源を配置
  const lights = buildStartGoalLights(data, offset);
  if (lights.length > 0) {
    await newScene.createEmbeddedDocuments("AmbientLight", lights);
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
    }
  }

  // 完了通知カード
  await ChatMessage.create({
    speaker: { alias: "サポートGM (agy)" },
    content: buildSceneSuccessCardHtml({
      sceneId: newScene.id,
      sceneName: newScene.name,
      data,
      sceneWidth,
      sceneHeight,
      wallCount: newScene.walls?.size ?? wallDocs.length,
      noteCount,
      journalId: journal?.id,
    }),
    whisper: [game.user.id],
    flags: generatedFlags(),
  });
}

export const ACTION_HANDLERS = {
  chat: postChat,
  roll: rollDice,
  play_sound: playSound,
  interactive_card: showInteractiveCard,
  create_dungeon_scene: createDungeonScene,
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
