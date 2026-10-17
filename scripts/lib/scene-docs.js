// create_dungeon_scene の受信データから FVTT ドキュメントのデータを組み立てる (純粋関数)
// FVTT の CONST は引数 consts で受け取る (テスト時は既定値を使用)

import { escapeHtml, textToHtml } from "./html.js";

export const DEFAULT_LEVEL_ID = "defaultLevel0000";

/** v14.368 の CONST 値 (CONST が無い環境でのフォールバック) */
export const FALLBACK_CONSTS = {
  WALL_MOVEMENT_TYPES: { NONE: 0, NORMAL: 20 },
  EDGE_SENSE_TYPES: { NONE: 0, LIMITED: 10, NORMAL: 20 },
  WALL_DOOR_TYPES: { NONE: 0, DOOR: 1, SECRET: 2 },
  JOURNAL_ENTRY_PAGE_FORMATS: { HTML: 1, MARKDOWN: 2 },
};

function resolveConsts(consts) {
  const c = consts || {};
  return {
    move: c.WALL_MOVEMENT_TYPES?.NORMAL ?? FALLBACK_CONSTS.WALL_MOVEMENT_TYPES.NORMAL,
    sense: c.EDGE_SENSE_TYPES?.NORMAL ?? FALLBACK_CONSTS.EDGE_SENSE_TYPES.NORMAL,
    html: c.JOURNAL_ENTRY_PAGE_FORMATS?.HTML ?? FALLBACK_CONSTS.JOURNAL_ENTRY_PAGE_FORMATS.HTML,
  };
}

export function sceneSize(data) {
  const gridSize = data.grid_size || 100;
  return { gridSize, width: data.grid_width * gridSize, height: data.grid_height * gridSize };
}

/** Scene.create に渡すデータ (v11〜v14 互換) */
export function buildSceneData(data) {
  const { gridSize, width, height } = sceneSize(data);
  return {
    name: data.name || "地下迷宮",
    width,
    height,
    padding: 0,
    // v13 以前: シーン直下の background / v14: levels[].background
    background: { src: data.background_src },
    initialLevel: DEFAULT_LEVEL_ID,
    levels: [{ _id: DEFAULT_LEVEL_ID, name: "地下1階", background: { src: data.background_src } }],
    grid: { size: gridSize, distance: 5, units: "ft" },
    // v12 以降の環境設定 (BaseScene.environment)。v11 互換のためトップレベルも残す
    environment: { darknessLevel: 0.75, globalLight: { enabled: false } },
    darkness: 0.75,
    globalLight: { enabled: false },
    navigation: true,
  };
}

/** サーバーの壁 [x0, y0, x1, y1] をシーン原点オフセット付きの Wall データにする */
export function buildWallDocs(walls, { offsetX = 0, offsetY = 0 } = {}, consts) {
  if (!Array.isArray(walls)) return [];
  const { move, sense } = resolveConsts(consts);
  return walls.map(w => ({
    c: [w[0] + offsetX, w[1] + offsetY, w[2] + offsetX, w[3] + offsetY],
    move,
    sight: sense,
    light: sense,
    sound: sense,
  }));
}

function cellCenter(pos, gridSize, offsetX, offsetY) {
  return { x: pos[0] * gridSize + gridSize / 2 + offsetX, y: pos[1] * gridSize + gridSize / 2 + offsetY };
}

/** スタート地点 (緑光) とゴール地点 (赤光) の AmbientLight */
export function buildStartGoalLights(data, { offsetX = 0, offsetY = 0 } = {}) {
  const gridSize = data.grid_size || 100;
  const lights = [];
  if (Array.isArray(data.start_pos)) {
    lights.push({
      ...cellCenter(data.start_pos, gridSize, offsetX, offsetY),
      config: { bright: 20, dim: 40, color: "#2ecc71", alpha: 0.45, animation: { type: "pulse", speed: 3, intensity: 3 } },
    });
  }
  if (Array.isArray(data.goal_pos)) {
    lights.push({
      ...cellCenter(data.goal_pos, gridSize, offsetX, offsetY),
      config: { bright: 20, dim: 40, color: "#e74c3c", alpha: 0.45, animation: { type: "torch", speed: 4, intensity: 4 } },
    });
  }
  return lights;
}

/** フロア伝承・生態系ページ (Tier 2 メゾ伝承) */
export function buildLorePage(lore, consts) {
  const { html } = resolveConsts(consts);
  const factionsHtml = Array.isArray(lore.factions)
    ? lore.factions.map(f => `<li style="margin-bottom: 4px;"><strong>${escapeHtml(f?.name)}</strong> (${escapeHtml(f?.territory)}): <em>${escapeHtml(f?.status)}</em></li>`).join("")
    : `<li>${escapeHtml(lore.factions)}</li>`;
  const amb = lore.floor_ambience || {};

  return {
    name: `【フロア伝承・生態系】${lore.floor_title || "概要"}`,
    type: "text",
    title: { show: true, level: 1 },
    text: {
      content: `
      <div class="ai-gm-floor-lore" style="font-family: inherit; line-height: 1.6; font-size: 14px;">
        <h2 style="color: #782e22; border-bottom: 2px solid #782e22; padding-bottom: 4px; margin-top: 0;">
          🏰 ${escapeHtml(lore.dungeon_name || "ダンジョン概要")}
        </h2>
        <p style="margin-bottom: 10px;"><strong>【歴史の地層】:</strong> ${textToHtml(lore.history_summary)}</p>
        <p style="margin-bottom: 12px;"><strong>【フロアの成り立ち】:</strong> ${textToHtml(lore.floor_origin)}</p>

        <h3 style="color: #2c3e50; border-bottom: 1px solid #ddd; padding-bottom: 2px; margin-top: 14px;">⚔️ 勢力関係・縄張り (Factions)</h3>
        <ul style="padding-left: 20px; margin-bottom: 12px;">${factionsHtml}</ul>

        <h3 style="color: #2c3e50; border-bottom: 1px solid #ddd; padding-bottom: 2px; margin-top: 14px;">🌫️ 環境の五感 (Sensory Ambience)</h3>
        <ul style="padding-left: 20px; margin-bottom: 12px;">
          <li><strong>視覚:</strong> ${escapeHtml(amb.visual)}</li>
          <li><strong>聴覚:</strong> ${escapeHtml(amb.auditory)}</li>
          <li><strong>嗅覚:</strong> ${escapeHtml(amb.olfactory)}</li>
          <li><strong>空気:</strong> ${escapeHtml(amb.air)}</li>
        </ul>
      </div>
      `,
      format: html,
    },
  };
}

/** 各部屋の個別フレーバーノートページ */
export function buildRoomPage(r, consts) {
  const { html } = resolveConsts(consts);
  const restBadge = r.safe_rest
    ? `<span style="display:inline-block; background:#27ae60; color:#fff; padding:2px 6px; border-radius:3px; font-size:11px; margin-left:8px;">⛺ 安全な休憩所</span>`
    : (r.is_empty ? `<span style="display:inline-block; background:#7f8c8d; color:#fff; padding:2px 6px; border-radius:3px; font-size:11px; margin-left:8px;">静寂の空室</span>` : "");

  const gmNoteHtml = r.gm_notes ? `
    <div style="margin-top: 10px; background: #fff8e7; border-left: 4px solid #f39c12; padding: 6px 10px; font-size: 12px; color: #7f5200; border-radius: 2px;">
      <strong>GM専用メモ:</strong> ${textToHtml(r.gm_notes)}
    </div>
  ` : "";

  return {
    name: r.name,
    type: "text",
    title: { show: true, level: 1 },
    text: {
      content: `
      <div class="ai-gm-room-note" style="font-family: inherit; line-height: 1.6; font-size: 14px;">
        <div style="font-size: 16px; font-weight: bold; color: #782e22; border-bottom: 2px solid #782e22; margin-bottom: 8px; padding-bottom: 4px;">
          ${escapeHtml(r.name)} ${restBadge}
        </div>
        <p style="margin-bottom: 12px; font-style: italic; color: #222;">
          ${textToHtml(r.flavor)}
        </p>
        ${gmNoteHtml}
        <div style="margin-top: 10px; font-size: 12px; color: #555; background: #f0f0f0; padding: 6px 10px; border-radius: 4px;">
          📍 マス目範囲: X: ${r.grid_x}〜${r.grid_x + r.grid_w}, Y: ${r.grid_y}〜${r.grid_y + r.grid_h} (${r.grid_w}x${r.grid_h} マス)
        </div>
      </div>
      `,
      format: html,
    },
  };
}

export function buildJournalPages(data, consts) {
  const pages = [];
  if (data.lore) pages.push(buildLorePage(data.lore, consts));
  for (const r of data.rooms || []) pages.push(buildRoomPage(r, consts));
  return pages;
}

/**
 * マップノートピン。ページはページ名で対応付ける (作成後の並び順に依存しない)
 * createdPages: [{ id, name }]
 */
export function buildNoteDocs(rooms, journalId, createdPages, { offsetX = 0, offsetY = 0 } = {}) {
  const pageIdByName = new Map((createdPages || []).map(p => [p.name, p.id]));
  return (rooms || []).map(r => ({
    entryId: journalId,
    pageId: pageIdByName.get(r.name) ?? null,
    x: r.cx + offsetX,
    y: r.cy + offsetY,
    text: r.name,
    texture: { src: r.icon || "icons/svg/book.svg" },
    iconSize: 36,
    fontSize: 16,
  }));
}
