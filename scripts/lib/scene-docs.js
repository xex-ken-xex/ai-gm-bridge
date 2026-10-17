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
    senseNone: c.EDGE_SENSE_TYPES?.NONE ?? FALLBACK_CONSTS.EDGE_SENSE_TYPES.NONE,
    doorNone: c.WALL_DOOR_TYPES?.NONE ?? FALLBACK_CONSTS.WALL_DOOR_TYPES.NONE,
    door: c.WALL_DOOR_TYPES?.DOOR ?? FALLBACK_CONSTS.WALL_DOOR_TYPES.DOOR,
    secret: c.WALL_DOOR_TYPES?.SECRET ?? FALLBACK_CONSTS.WALL_DOOR_TYPES.SECRET,
    html: c.JOURNAL_ENTRY_PAGE_FORMATS?.HTML ?? FALLBACK_CONSTS.JOURNAL_ENTRY_PAGE_FORMATS.HTML,
  };
}

/**
 * 部屋の light_preset (tools/dungeon_generator.py の LIGHT_PRESET_KEYS) → AmbientLight の config
 * dark は「明かりなし」。アニメーション無しは type: null (L13: "none" というキーは存在しない)
 */
export const LIGHT_PRESETS = {
  torch_warm: { bright: 15, dim: 30, color: "#ff9f43", alpha: 0.45, animation: { type: "torch", speed: 4, intensity: 4 } },
  bonfire_bright: { bright: 25, dim: 50, color: "#ff6b35", alpha: 0.55, animation: { type: "flame", speed: 5, intensity: 5 } },
  magic_blue: { bright: 15, dim: 35, color: "#48dbfb", alpha: 0.4, animation: { type: "pulse", speed: 3, intensity: 3 } },
  acid_green: { bright: 12, dim: 28, color: "#1dd1a1", alpha: 0.4, animation: { type: "fog", speed: 2, intensity: 3 } },
  moonlight_dim: { bright: 0, dim: 40, color: "#c8d6e5", alpha: 0.25, animation: { type: null, speed: 1, intensity: 1 } },
  dark: null,
};

// シーンモードごとの明るさとレベル名 (未知のモードは underground 扱い)
const SCENE_MODE_ENV = {
  underground: { darkness: 0.75, globalLight: false, levelName: "地下1階" },
  ruins: { darkness: 0.35, globalLight: false, levelName: "地上・野外" },
  wilderness: { darkness: 0.35, globalLight: true, levelName: "地上・野外" },
};

function modeEnv(sceneMode) {
  return SCENE_MODE_ENV[sceneMode] || SCENE_MODE_ENV.underground;
}

export function sceneSize(data) {
  const gridSize = data.grid_size || 100;
  return { gridSize, width: data.grid_width * gridSize, height: data.grid_height * gridSize };
}

/** Scene.create に渡すデータ (v11〜v14 互換) */
export function buildSceneData(data) {
  const { gridSize, width, height } = sceneSize(data);
  const env = modeEnv(data.scene_mode);
  return {
    name: data.name || "地下迷宮",
    width,
    height,
    padding: 0,
    // v13 以前: シーン直下の background / v14: levels[].background
    background: { src: data.background_src },
    initialLevel: DEFAULT_LEVEL_ID,
    levels: [{ _id: DEFAULT_LEVEL_ID, name: env.levelName, background: { src: data.background_src } }],
    grid: { size: gridSize, distance: 5, units: "ft" },
    // v12 以降の環境設定 (BaseScene.environment)。v11 互換のためトップレベルも残す
    environment: { darknessLevel: env.darkness, globalLight: { enabled: env.globalLight } },
    darkness: env.darkness,
    globalLight: { enabled: env.globalLight },
    navigation: true,
  };
}

/**
 * サーバーの壁 [x0, y0, x1, y1, type?] をシーン原点オフセット付きの Wall データにする
 * type: normal (既定) / low_wall・rubble (移動のみ遮る。視線・光・音は通す) / door / secret_door
 */
export function buildWallDocs(walls, { offsetX = 0, offsetY = 0 } = {}, consts) {
  if (!Array.isArray(walls)) return [];
  const k = resolveConsts(consts);
  const doorByType = { door: k.door, secret_door: k.secret };
  return walls.map(w => {
    const type = w[4] || "normal";
    const low = type === "low_wall" || type === "rubble";
    const sense = low ? k.senseNone : k.sense;
    return {
      c: [w[0] + offsetX, w[1] + offsetY, w[2] + offsetX, w[3] + offsetY],
      move: k.move,
      sight: sense,
      light: sense,
      sound: sense,
      door: doorByType[type] ?? k.doorNone,
    };
  });
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

/** 部屋ごとの AmbientLight (light_preset 未指定でも安全な休憩所は焚き火) */
export function buildRoomLights(rooms, { offsetX = 0, offsetY = 0 } = {}) {
  const lights = [];
  for (const r of rooms || []) {
    const key = r.light_preset || (r.safe_rest ? "bonfire_bright" : null);
    const preset = key ? LIGHT_PRESETS[key] : null;
    if (!preset) continue;
    lights.push({ x: r.cx + offsetX, y: r.cy + offsetY, config: structuredClone(preset) });
  }
  return lights;
}

/**
 * 部屋ごとの AmbientSound。radius は距離単位 (ft) なので マス数 × grid.distance で求める (M1)
 * gridDistance: 1マスの距離 (buildSceneData では 5ft)
 */
export function buildRoomSounds(rooms, { offsetX = 0, offsetY = 0 } = {}, gridDistance = 5) {
  const sounds = [];
  for (const r of rooms || []) {
    if (!r.ambient_sound_src) continue;
    const cells = Math.max(r.grid_w || 8, r.grid_h || 8);
    sounds.push({
      x: r.cx + offsetX,
      y: r.cy + offsetY,
      radius: cells * gridDistance * 0.85,
      path: r.ambient_sound_src,
      repeat: true,
      volume: 0.65,
      walls: true,
      easing: true,
    });
  }
  return sounds;
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
        ${lore.dm_secrets ? `<div style="margin-bottom: 12px; background: #fff8e7; border-left: 4px solid #f39c12; padding: 6px 10px; color: #7f5200;"><strong>🔒 DM 専用情報:</strong><br>${textToHtml(lore.dm_secrets)}</div>` : ""}

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

/** 部屋の役割。生成器は role と type に同じ値を入れる (旧データは type のみ) (M2) */
export function roomRole(r) {
  return r?.role ?? r?.type ?? null;
}

/** 各部屋の個別フレーバーノートページ */
export function buildRoomPage(r, consts) {
  const { html } = resolveConsts(consts);
  const restBadge = r.safe_rest
    ? `<span style="display:inline-block; background:#27ae60; color:#fff; padding:2px 6px; border-radius:3px; font-size:11px; margin-left:8px;">⛺ 安全な休憩所</span>`
    : (roomRole(r) === "sub_goal" ? `<span style="display:inline-block; background:#8e44ad; color:#fff; padding:2px 6px; border-radius:3px; font-size:11px; margin-left:8px;">🔑 中目標ギミック</span>`
    : (r.is_empty ? `<span style="display:inline-block; background:#7f8c8d; color:#fff; padding:2px 6px; border-radius:3px; font-size:11px; margin-left:8px;">静寂の空室</span>` : ""));

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
        <div style="font-size: 16px; font-weight: bold; color: #e06c4f; border-bottom: 2px solid #e06c4f; margin-bottom: 8px; padding-bottom: 4px;">
          ${escapeHtml(r.name)} ${restBadge}
        </div>
        <p class="ai-gm-room-flavor" style="margin-bottom: 12px; font-style: italic; color: #f3e9d2; background: rgba(30, 24, 20, 0.88); border-left: 4px solid #e06c4f; padding: 8px 10px; border-radius: 4px;">
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
