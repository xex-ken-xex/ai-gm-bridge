/**
 * AI GM Bridge - Ultimate Unified Client Script for Foundry VTT (v12 - v14)
 * =========================================================================
 * 【収録セクション一覧】
 * 0. 基本設定・照明プリセット・NPCブレイン初期スキーマ定義
 * 1. アシスタントDM マスコットUI「アギィ」(PL/GM全画面同期・ドラッグ移動・状態アニメ)
 * 2. 4階層アトラス・ジャーナル管理 (PL公開ページ / DM秘匿ページの分離保存とプロンプト抽出)
 * 3. 集落BGM プレイリスト自動登録 & ワンクリック排他切り替え制御
 * 4. 専用シナリオビルダーGUI (4タブ: 1.地域 ➔ 2.集落・くらし ➔ 3.周辺・旅路 ➔ 4.廃墟・迷宮)
 * 5. NPC会話システム (聴取範囲判定・記憶と忘却アルゴリズム・小説風地の文描画・NPCブレイン管理GUI)
 * 6. NPC / モンスター自律戦闘エンジン (知覚データ抽出 ➔ AI戦術判断 ➔ 壁回避移動・吹き出し・ロール)
 * 7. スラッシュコマンド (/ai, /agy) & 通常チャット発言のNPC聴取インターセプト
 * 8. WebSocket 通信ハンドラ & シーン自動構築 (地下迷宮 / 野外廃墟[風見が丘型] / ワイルダネス)
 * 9. グローバルUIイベント監視 & トークン部屋進入検知 (room_entered 自動TTSナレーション)
 */

let ws = null;
let builderDialogInstance = null;
let brainDialogInstance = null;
const MODULE_ID = "ai-gm-bridge";

// =========================================================================
// 0. 基本設定・照明プリセット・NPCブレイン初期スキーマ定義
// =========================================================================
const ASSISTANT_DM_CONFIG = {
  name: "アギィ (Agy)",
  title: "星詠みの副マスター",
  // Stable Diffusion等で生成した透過WebPを使う場合はパスを指定（空欄なら内蔵SVGアニメを使用）
  sprites: {
    idle: "",     // 例: "modules/ai-gm-bridge/assets/mascot_idle.webp"
    thinking: "", // 例: "modules/ai-gm-bridge/assets/mascot_thinking.webp"
    speaking: "", // 例: "modules/ai-gm-bridge/assets/mascot_speaking.webp"
    offline: ""   // 例: "modules/ai-gm-bridge/assets/mascot_offline.webp"
  }
};

// Stage 4-B プロンプトの light_preset を FVTT の AmbientLight 設定へ変換する辞書
const LIGHT_PRESETS = {
  torch_warm: {
    bright: 15, dim: 30, color: "#ff9f43", alpha: 0.45,
    animation: { type: "torch", speed: 4, intensity: 4 }
  },
  bonfire_bright: {
    bright: 25, dim: 50, color: "#ff6b35", alpha: 0.55,
    animation: { type: "flame", speed: 5, intensity: 5 }
  },
  magic_blue: {
    bright: 15, dim: 35, color: "#48dbfb", alpha: 0.4,
    animation: { type: "pulse", speed: 3, intensity: 3 }
  },
  acid_green: {
    bright: 12, dim: 28, color: "#1dd1a1", alpha: 0.4,
    animation: { type: "fog", speed: 2, intensity: 3 }
  },
  moonlight_dim: {
    bright: 0, dim: 40, color: "#c8d6e5", alpha: 0.25,
    animation: { type: "none", speed: 1, intensity: 1 }
  },
  dark: null
};

// NPC個体別の知識境界・記憶・声色の初期構造
const DEFAULT_NPC_BRAIN = {
  goal: "自身の生活と安全を守りつつ、周囲の動向を探る",
  public_known: "この周辺の一般的な地理と、表向きの噂話",
  secret_known: "（未設定：集落やダンジョンのDM秘匿情報に準ずる）",
  ignorance: "他地域の政治、専門的な古代魔術の知識、自分が立ち会っていない出来事",
  trust: 0,
  memory_retention: 0.8, // 1.0=不滅, 0.8=標準, 0.5=忘れっぽい(老衰・泥酔等)
  voice: {
    name: "ja-JP-KeitaNeural", // ja-JP-NanamiNeural (女性) / ja-JP-KeitaNeural (男性)
    rate: "+0%",               // -20% 〜 +20%
    pitch: "+0Hz"              // -15Hz 〜 +15Hz
  },
  short_term_memory: [],
  long_term_memory: []
};

// =========================================================================
// 1. アシスタントDM マスコットUI (PL / GM 全員に表示・ドラッグ＆Socket同期)
// =========================================================================
function getFallbackSvgDataUri(state) {
  const glowColor = state === "thinking" ? "#f1c40f" : (state === "speaking" ? "#2ecc71" : (state === "offline" ? "#7f8c8d" : "#9b59b6"));
  const eyeExpr = state === "thinking"
    ? "M28,38 L36,38 M48,38 L56,38"
    : (state === "offline" ? "M28,40 Q32,44 36,40 M48,40 Q52,44 56,40" : "M32,36 A3,4 0 1,1 32,44 A3,4 0 1,1 32,36 M52,36 A3,4 0 1,1 52,44 A3,4 0 1,1 52,36");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 84 84">
    <defs>
      <radialGradient id="g" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stop-color="${glowColor}" stop-opacity="0.9"/>
        <stop offset="70%" stop-color="#2c3e50" stop-opacity="0.95"/>
        <stop offset="100%" stop-color="${glowColor}" stop-opacity="0"/>
      </radialGradient>
    </defs>
    <circle cx="42" cy="44" r="34" fill="url(#g)"/>
    <path d="M16,32 Q42,24 68,32 L46,4 L34,12 Z" fill="#341f97" stroke="#f39c12" stroke-width="2"/>
    <path d="${eyeExpr}" stroke="#fff" fill="none" stroke-width="3" stroke-linecap="round"/>
    ${state === "thinking" ? '<rect x="26" y="54" width="32" height="18" rx="3" fill="#e67e22" stroke="#fff" stroke-width="1.5"/>' : ''}
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

function initAssistantMascotUI() {
  if (document.getElementById("ai-gm-mascot-widget")) return;

  const savedPos = JSON.parse(localStorage.getItem("ai-gm-mascot-pos") || '{"top": 90, "left": 110}');

  const style = document.createElement("style");
  style.textContent = `
    #ai-gm-mascot-widget {
      position: fixed;
      top: ${savedPos.top}px;
      left: ${savedPos.left}px;
      z-index: 9999;
      display: flex;
      flex-direction: column;
      align-items: center;
      user-select: none;
      cursor: grab;
    }
    #ai-gm-mascot-widget:active { cursor: grabbing; }

    @keyframes ai-mascot-float {
      0%, 100% { transform: translateY(0px); }
      50% { transform: translateY(-7px); }
    }
    @keyframes ai-mascot-talk {
      0%, 100% { transform: scale(1, 1) translateY(0); }
      50% { transform: scale(1.07, 0.93) translateY(-9px); }
    }
    @keyframes ai-mascot-think {
      0%, 100% { filter: drop-shadow(0 0 6px #9b59b6); transform: translateY(0px) rotate(-3deg); }
      50% { filter: drop-shadow(0 0 18px #f1c40f) brightness(1.15); transform: translateY(-6px) rotate(3deg); }
    }

    .ai-mascot-avatar {
      width: 80px;
      height: 80px;
      background-size: contain;
      background-repeat: no-repeat;
      background-position: center;
      animation: ai-mascot-float 3s ease-in-out infinite;
      filter: drop-shadow(0 4px 8px rgba(0,0,0,0.65));
    }
    #ai-gm-mascot-widget[data-state="thinking"] .ai-mascot-avatar {
      animation: ai-mascot-think 1.2s ease-in-out infinite;
    }
    #ai-gm-mascot-widget[data-state="speaking"] .ai-mascot-avatar {
      animation: ai-mascot-talk 0.35s ease-in-out infinite;
      filter: drop-shadow(0 0 14px #2ecc71);
    }
    #ai-gm-mascot-widget[data-state="offline"] .ai-mascot-avatar {
      opacity: 0.6;
      filter: grayscale(0.7);
    }

    .ai-mascot-nameplate {
      margin-top: 2px;
      background: rgba(20, 20, 28, 0.9);
      border: 1px solid #e67e22;
      color: #f39c12;
      font-size: 11px;
      font-weight: bold;
      padding: 2px 8px;
      border-radius: 10px;
      box-shadow: 0 2px 5px rgba(0,0,0,0.6);
      display: flex;
      align-items: center;
      gap: 5px;
    }
    .ai-mascot-dot {
      width: 7px; height: 7px; border-radius: 50%; background: #2ecc71;
    }
    #ai-gm-mascot-widget[data-state="thinking"] .ai-mascot-dot { background: #f1c40f; }
    #ai-gm-mascot-widget[data-state="offline"] .ai-mascot-dot { background: #7f8c8d; }

    .ai-mascot-bubble {
      position: absolute;
      bottom: 106%;
      left: 50%;
      transform: translateX(-50%);
      width: 230px;
      background: rgba(255, 252, 240, 0.96);
      color: #1e1e24;
      border: 2px solid #782e22;
      border-radius: 8px;
      padding: 6px 10px;
      font-size: 12px;
      font-weight: bold;
      line-height: 1.4;
      box-shadow: 0 4px 12px rgba(0,0,0,0.45);
      pointer-events: none;
      opacity: 0;
      transition: opacity 0.25s ease;
      text-align: center;
    }
    .ai-mascot-bubble.show { opacity: 1; }
  `;
  document.head.appendChild(style);

  const initialImg = ASSISTANT_DM_CONFIG.sprites.idle || getFallbackSvgDataUri("idle");
  const widget = document.createElement("div");
  widget.id = "ai-gm-mascot-widget";
  widget.setAttribute("data-state", "idle");
  widget.innerHTML = `
    <div class="ai-mascot-bubble" id="ai-mascot-bubble"></div>
    <div class="ai-mascot-avatar" id="ai-mascot-avatar" style="background-image: url('${initialImg}');"></div>
    <div class="ai-mascot-nameplate" title="ドラッグ:移動 / 左ダブルクリック:シナリオビルダー起動 / 右クリック:NPCブレイン管理">
      <span class="ai-mascot-dot"></span>
      <span>${ASSISTANT_DM_CONFIG.name}</span>
    </div>
  `;
  document.body.appendChild(widget);

  // ドラッグ移動制御
  let isDragging = false, startX = 0, startY = 0, origLeft = 0, origTop = 0;
  widget.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    isDragging = true;
    startX = e.clientX;
    startY = e.clientY;
    const rect = widget.getBoundingClientRect();
    origLeft = rect.left;
    origTop = rect.top;
  });

  window.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    const newLeft = Math.max(0, Math.min(window.innerWidth - 90, origLeft + (e.clientX - startX)));
    const newTop = Math.max(0, Math.min(window.innerHeight - 100, origTop + (e.clientY - startY)));
    widget.style.left = `${newLeft}px`;
    widget.style.top = `${newTop}px`;
  });

  window.addEventListener("mouseup", () => {
    if (!isDragging) return;
    isDragging = false;
    localStorage.setItem("ai-gm-mascot-pos", JSON.stringify({
      left: parseInt(widget.style.left, 10),
      top: parseInt(widget.style.top, 10)
    }));
  });

  // 左ダブルクリック: 4段階シナリオビルダーGUIを起動
  widget.addEventListener("dblclick", () => {
    if (game.user?.isGM) {
      openScenarioBuilderGUI();
    } else {
      setMascotState("speaking", "私は副マスターのアギィ！冒険のサポートはお任せあれ！", 4000, false);
    }
  });

  // 右クリック: NPCブレイン＆記憶・声色管理GUIを起動
  widget.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (game.user?.isGM) {
      openNpcBrainManagerGUI();
    }
  });
}

function setMascotState(state, bubbleText = "", durationMs = 5000, broadcast = true) {
  const widget = document.getElementById("ai-gm-mascot-widget");
  const avatar = document.getElementById("ai-mascot-avatar");
  const bubble = document.getElementById("ai-mascot-bubble");
  if (!widget || !avatar) return;

  widget.setAttribute("data-state", state);
  const spriteUrl = ASSISTANT_DM_CONFIG.sprites[state] || getFallbackSvgDataUri(state);
  avatar.style.backgroundImage = `url('${spriteUrl}')`;

  if (bubble) {
    if (bubbleText) {
      bubble.textContent = bubbleText;
      bubble.classList.add("show");
      setTimeout(() => bubble.classList.remove("show"), durationMs);
    } else {
      bubble.classList.remove("show");
    }
  }

  // GM側の状態変化を全プレイヤーの画面へSocket配信
  if (broadcast && game.user?.isGM && game.socket) {
    game.socket.emit(`module.${MODULE_ID}`, {
      action: "sync_mascot",
      state: state,
      bubbleText: bubbleText,
      durationMs: durationMs
    });
  }
}

// =========================================================================
// 2. 4階層アトラス・ジャーナル管理 (PL公開ページ / DM秘匿ページの分離保存と抽出)
// =========================================================================
const ATLAS_CATEGORIES = {
  region: "1_地域 (Regions)",
  settlement: "2_集落・街 (Settlements)",
  wilderness: "3_周辺・旅路 (Wilderness)",
  site: "4_遺跡・迷宮 (Sites)"
};

async function getOrCreateAtlasFolder(folderName, parentId = null) {
  let folder = game.folders.find(f => f.type === "JournalEntry" && f.name === folderName && (f.folder?.id ?? null) === parentId);
  if (!folder) {
    folder = await Folder.create({
      name: folderName,
      type: "JournalEntry",
      folder: parentId,
      color: "#782e22"
    });
  }
  return folder;
}

async function saveAtlasJournal({ categoryKey, title, publicHtml, dmHtml, metaData = {} }) {
  const rootFolder = await getOrCreateAtlasFolder("【AI World Atlas】");
  const subFolderName = ATLAS_CATEGORIES[categoryKey] || categoryKey;
  const subFolder = await getOrCreateAtlasFolder(subFolderName, rootFolder.id);

  let entry = game.journal.find(j => j.name === title && j.folder?.id === subFolder.id);

  const pagesData = [
    {
      name: "📖 プレイヤー公開情報 (風土・くらし・名物)",
      type: "text",
      title: { show: true, level: 1 },
      text: { content: publicHtml, format: 1 },
      ownership: { default: CONST.DOCUMENT_OWNERSHIP_LEVELS?.OBSERVER ?? 2 }
    },
    {
      name: "🔒 DM専用情報 (悲しみの真相・陰謀・目標構造)",
      type: "text",
      title: { show: true, level: 1 },
      text: { content: dmHtml, format: 1 },
      ownership: { default: CONST.DOCUMENT_OWNERSHIP_LEVELS?.NONE ?? 0 }
    }
  ];

  const flagData = {
    categoryKey,
    updatedAt: Date.now(),
    ...metaData
  };

  if (entry) {
    const existingIds = entry.pages.contents.map(p => p.id);
    if (existingIds.length > 0) {
      await entry.deleteEmbeddedDocuments("JournalEntryPage", existingIds);
    }
    await entry.createEmbeddedDocuments("JournalEntryPage", pagesData);
    await entry.setFlag(MODULE_ID, "atlas_meta", flagData);
  } else {
    entry = await JournalEntry.create({
      name: title,
      folder: subFolder.id,
      pages: pagesData,
      ownership: { default: CONST.DOCUMENT_OWNERSHIP_LEVELS?.OBSERVER ?? 2 },
      flags: { [MODULE_ID]: { atlas_meta: flagData } }
    });
  }
  ui.notifications.info(`ジャーナル『${title}』を【${subFolderName}】に保存しました。`);
  return entry;
}

function getAtlasJournalsByCategory(categoryKey) {
  if (!game.journal) return [];
  const subFolderName = ATLAS_CATEGORIES[categoryKey];
  return game.journal.contents.filter(j => {
    const meta = j.getFlag(MODULE_ID, "atlas_meta");
    if (meta && meta.categoryKey === categoryKey) return true;
    return j.folder?.name === subFolderName;
  });
}

function extractJournalContextForPrompt(journalId) {
  if (!journalId || !game.journal) return { name: "", public_lore: "", dm_secrets: "", meta: {} };
  const entry = game.journal.get(journalId);
  if (!entry) return { name: "", public_lore: "", dm_secrets: "", meta: {} };

  const stripHtml = (html) => (html || "")
    .replace(/<div class="ai-gm-bgm-bar"[\s\S]*?<\/div>/gi, "")
    .replace(/<br\s*[\/]?>/gi, "\n")
    .replace(/<\/p>|<\/li>|<\/h[1-6]>|<\/div>/gi, "\n")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  let publicText = "";
  let dmText = "";

  for (const page of entry.pages.contents) {
    const raw = page.text?.content || "";
    if (page.name.includes("DM") || page.ownership?.default === 0) {
      dmText += stripHtml(raw) + "\n";
    } else {
      publicText += stripHtml(raw) + "\n";
    }
  }

  return {
    id: entry.id,
    name: entry.name,
    meta: entry.getFlag(MODULE_ID, "atlas_meta") || {},
    public_lore: publicText.trim(),
    dm_secrets: dmText.trim()
  };
}

function collectSelectedActorsContext(selectedIds = []) {
  if (!game.actors) return { pcs: [], npcs: [] };
  const stripHtml = (html) => (html || "").replace(/<[^>]*>/g, " ").replace(/&nbsp;/gi, " ").replace(/\s+/g, " ").trim();

  const pcs = [];
  const npcs = [];
  const useFilter = selectedIds && selectedIds.length > 0;

  for (const actor of game.actors.contents) {
    if (useFilter && !selectedIds.includes(actor.id)) continue;
    const rawBio = actor.system?.details?.biography?.value || actor.system?.biography || "";
    const bio = stripHtml(rawBio).slice(0, 350);

    if (actor.type === "character" || actor.hasPlayerOwner) {
      const race = actor.system?.details?.race?.name || actor.system?.details?.race || "";
      const bg = actor.system?.details?.background?.name || actor.system?.details?.background || "";
      pcs.push({ name: actor.name, info: `${race} ${bg}`.trim(), bio: bio || "冒険者" });
    } else if (actor.type === "npc" && (useFilter || bio.length > 10)) {
      npcs.push({ name: actor.name, role: actor.system?.details?.type?.value || "NPC", bio: bio });
    }
  }
  return { pcs, npcs };
}

// =========================================================================
// 3. 集落BGM プレイリスト自動登録 & ワンクリック排他切り替え制御
// =========================================================================
async function getOrCreateBgmFolder() {
  let folder = game.folders.find(f => f.type === "Playlist" && f.name === "【AI World BGM】");
  if (!folder) {
    folder = await Folder.create({
      name: "【AI World BGM】",
      type: "Playlist",
      color: "#8e44ad"
    });
  }
  return folder;
}

async function createOrUpdateSettlementPlaylist({ settlementName, joySrc, sorrowSrc }) {
  const folder = await getOrCreateBgmFolder();
  const playlistName = `🏘️ ${settlementName}`;

  let playlist = game.playlists.find(p => p.name === playlistName && p.folder?.id === folder.id);

  const soundsData = [];
  if (joySrc) {
    soundsData.push({
      name: `☀️ 【楽しみ・日常】${settlementName}`,
      path: joySrc,
      repeat: true,
      volume: 0.5,
      channel: "music",
      description: "集落のくらし・市場・酒場・団欒のテーマ"
    });
  }
  if (sorrowSrc) {
    soundsData.push({
      name: `🕯️ 【悲しみ・憂い】${settlementName}`,
      path: sorrowSrc,
      repeat: true,
      volume: 0.5,
      channel: "music",
      description: "集落の悲しみ・夜の静寂・不穏な噂のテーマ"
    });
  }

  if (soundsData.length === 0) return null;

  if (playlist) {
    const oldIds = playlist.sounds.contents.map(s => s.id);
    if (oldIds.length > 0) {
      await playlist.deleteEmbeddedDocuments("PlaylistSound", oldIds);
    }
    await playlist.createEmbeddedDocuments("PlaylistSound", soundsData);
  } else {
    playlist = await Playlist.create({
      name: playlistName,
      folder: folder.id,
      mode: CONST.PLAYLIST_MODES?.DISABLED ?? 0,
      sounds: soundsData,
      flags: { [MODULE_ID]: { settlementName } }
    });
  }

  ui.notifications.info(`プレイリスト『${playlistName}』にBGMを登録しました！`);
  return playlist;
}

async function switchSettlementBgm(settlementName, mood) {
  const playlistName = `🏘️ ${settlementName}`;
  const playlist = game.playlists.find(p => p.name === playlistName || p.getFlag(MODULE_ID, "settlementName") === settlementName);

  if (!playlist) {
    ui.notifications.warn(`『${settlementName}』のプレイリストがまだ生成されていません。`);
    return;
  }

  for (const p of game.playlists.contents) {
    if (p.playing && (p.folder?.name === "【AI World BGM】" || p.id === playlist.id)) {
      await p.stopAll();
    }
  }

  if (mood === "stop") {
    setMascotState("idle", "🔇 BGMを停止したよ。", 3000);
    return;
  }

  const keyword = mood === "joy" ? "楽しみ" : "悲しみ";
  const targetSound = playlist.sounds.contents.find(s => s.name.includes(keyword));

  if (!targetSound) {
    ui.notifications.warn(`該当するBGMトラック（${keyword}）が見つかりません。`);
    return;
  }

  if (typeof playlist.playSound === "function") {
    await playlist.playSound(targetSound);
  } else {
    await playlist.updateEmbeddedDocuments("PlaylistSound", [{ _id: targetSound.id, playing: true }]);
  }

  const moodLabel = mood === "joy" ? "☀️ 楽しみ・日常のテーマ" : "🕯️ 悲しみ・憂いのテーマ";
  setMascotState("speaking", `🎵 ${settlementName}：${moodLabel} を再生中！`, 4500);
}

function buildBgmControlBarHtml(settlementName) {
  return `
  <div class="ai-gm-bgm-bar" style="display:flex; align-items:center; gap:6px; background:#1e1e26; border:1px solid #8e44ad; padding:6px 10px; border-radius:5px; margin:8px 0;">
    <span style="font-size:12px; font-weight:bold; color:#d2b4de; margin-right:auto;">🎵 集落BGM切替:</span>
    <button type="button" class="ai-bgm-switch-btn" data-settlement="${settlementName}" data-mood="joy"
            style="background:#27ae60; color:#fff; border:none; padding:4px 8px; border-radius:4px; cursor:pointer; font-size:12px; font-weight:bold;">
      ☀️ 楽しみ・日常
    </button>
    <button type="button" class="ai-bgm-switch-btn" data-settlement="${settlementName}" data-mood="sorrow"
            style="background:#2c3e50; color:#ecf0f1; border:1px solid #7f8c8d; padding:4px 8px; border-radius:4px; cursor:pointer; font-size:12px; font-weight:bold;">
      🕯️ 悲しみ・夜
    </button>
    <button type="button" class="ai-bgm-switch-btn" data-settlement="${settlementName}" data-mood="stop"
            style="background:#c0392b; color:#fff; border:none; padding:4px 8px; border-radius:4px; cursor:pointer; font-size:12px;">
      ⏹️ 停止
    </button>
  </div>`;
}

// =========================================================================
// 4. 専用シナリオビルダーGUI (4タブ: 地域 ➔ 集落 ➔ ワイルダネス ➔ 廃墟/迷宮)
// =========================================================================
function openScenarioBuilderGUI(activeTab = "tab-region") {
  const regionJournals = getAtlasJournalsByCategory("region");
  const settlementJournals = getAtlasJournalsByCategory("settlement");
  const wildernessJournals = getAtlasJournalsByCategory("wilderness");

  const regionOptions = regionJournals.length > 0
    ? regionJournals.map(j => `<option value="${j.id}">${j.name}</option>`).join("")
    : `<option value="">（地域ジャーナル未作成 - ワールド基本設定を使用）</option>`;

  const settlementOptions = settlementJournals.length > 0
    ? settlementJournals.map(j => `<option value="${j.id}">${j.name}</option>`).join("")
    : `<option value="">（集落ジャーナル未作成）</option>`;

  const wildernessOptions = wildernessJournals.length > 0
    ? `<option value="">（指定なし）</option>` + wildernessJournals.map(j => `<option value="${j.id}">${j.name}</option>`).join("")
    : `<option value="">（ワイルダネス未作成）</option>`;

  const allActors = game.actors ? game.actors.contents : [];
  const pcActors = allActors.filter(a => a.type === "character" || a.hasPlayerOwner);
  const npcActors = allActors.filter(a => a.type === "npc").slice(0, 12);

  const actorCheckboxesHtml = [
    ...pcActors.map(a => `<label style="margin-right:8px; font-size:12px;"><input type="checkbox" class="gui-actor-check" value="${a.id}" checked/> 👤${a.name}(PC)</label>`),
    ...npcActors.map(a => `<label style="margin-right:8px; font-size:12px;"><input type="checkbox" class="gui-actor-check" value="${a.id}"/> 🎭${a.name}(NPC)</label>`)
  ].join("");

  const htmlContent = `
  <style>
    .ai-gui-root { font-family: inherit; color: #f0f0e0; background: #1a1a20; padding: 10px; border-radius: 6px; }
    .ai-gui-tabs { display: flex; gap: 4px; border-bottom: 2px solid #e67e22; margin-bottom: 10px; }
    .ai-gui-tab { flex: 1; padding: 7px 4px; background: #2a2a32; color: #bbb; border: none; border-radius: 4px 4px 0 0; cursor: pointer; font-weight: bold; font-size: 12px; }
    .ai-gui-tab.active { background: #e67e22; color: #111; }
    .ai-gui-panel { display: none; flex-direction: column; gap: 8px; max-height: 560px; overflow-y: auto; padding-right: 4px; }
    .ai-gui-panel.active { display: flex; }
    .ai-gui-row { display: flex; gap: 8px; }
    .ai-gui-row > div { flex: 1; }
    .ai-gui-field { display: flex; flex-direction: column; gap: 3px; }
    .ai-gui-field label { font-size: 12px; color: #f39c12; font-weight: bold; }
    .ai-gui-field input[type="text"], .ai-gui-field textarea, .ai-gui-field select {
      background: #101014; color: #f0f0f0; border: 1px solid #444; border-radius: 4px; padding: 6px; font-size: 12px; font-family: inherit;
    }
    .ai-gui-split { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .ai-gui-box-pub { border-left: 3px solid #2ecc71; padding-left: 6px; }
    .ai-gui-box-dm { border-left: 3px solid #e74c3c; padding-left: 6px; }
    .ai-gui-actions { display: flex; gap: 6px; margin-top: 6px; padding-top: 8px; border-top: 1px solid #333; }
    .ai-btn-gen { flex: 1; background: #8e44ad; color: #fff; border: none; padding: 8px; border-radius: 4px; cursor: pointer; font-weight: bold; }
    .ai-btn-save { flex: 1; background: #2980b9; color: #fff; border: none; padding: 8px; border-radius: 4px; cursor: pointer; font-weight: bold; }
    .ai-btn-build { flex: 1.5; background: #27ae60; color: #fff; border: none; padding: 8px; border-radius: 4px; cursor: pointer; font-weight: bold; }
  </style>

  <div class="ai-gui-root">
    <div class="ai-gui-tabs">
      <button type="button" class="ai-gui-tab ${activeTab === 'tab-region' ? 'active' : ''}" data-target="tab-region">🌍 1.地域ビルド</button>
      <button type="button" class="ai-gui-tab ${activeTab === 'tab-settlement' ? 'active' : ''}" data-target="tab-settlement">🏘️ 2.集落・くらし</button>
      <button type="button" class="ai-gui-tab ${activeTab === 'tab-wilderness' ? 'active' : ''}" data-target="tab-wilderness">🌲 3.周辺・旅路</button>
      <button type="button" class="ai-gui-tab ${activeTab === 'tab-site' ? 'active' : ''}" data-target="tab-site">🏰 4.廃墟・迷宮</button>
    </div>

    <!-- ================= TAB 1: 地域ビルド ================= -->
    <div id="tab-region" class="ai-gui-panel ${activeTab === 'tab-region' ? 'active' : ''}">
      <div class="ai-gui-row">
        <div class="ai-gui-field">
          <label>地域名称（保存時のジャーナル名）</label>
          <input type="text" id="reg-name" placeholder="地域の名称を入力、またはAI生成..." />
        </div>
        <div class="ai-gui-field">
          <label>GMからの地域テーマ指示</label>
          <input type="text" id="reg-prompt" placeholder="気候・地形・歴史の特徴などを指定..." />
        </div>
      </div>
      <div class="ai-gui-split">
        <div class="ai-gui-field ai-gui-box-pub">
          <label>📖 PL公開情報（気候・地形・天然資源・表の歴史）</label>
          <textarea id="reg-public" rows="9" placeholder="AIで下書き生成後、ここで自由に加筆・修正できます..."></textarea>
        </div>
        <div class="ai-gui-field ai-gui-box-dm">
          <label>🔒 DM秘匿情報（見えざる脅威・勢力対立・古代の秘密）</label>
          <textarea id="reg-dm" rows="9" placeholder="DM専用ページに保存される裏設定..."></textarea>
        </div>
      </div>
      <div class="ai-gui-field">
        <label>🔊 地域環境音プロンプト (RTX 4070Ti / Stable Audio 用英語プロンプト)</label>
        <input type="text" id="reg-audio" placeholder="Ambient wind, nature sounds, seamless loop..." />
      </div>
      <div class="ai-gui-actions">
        <button type="button" class="ai-btn-gen" id="btn-gen-region">🤖 Stage 1: 地域設定をAI生成</button>
        <button type="button" class="ai-btn-save" id="btn-save-region">💾 PL/DM分離ジャーナルに保存</button>
      </div>
    </div>

    <!-- ================= TAB 2: 集落・街ビルド ================= -->
    <div id="tab-settlement" class="ai-gui-panel ${activeTab === 'tab-settlement' ? 'active' : ''}">
      <div class="ai-gui-row">
        <div class="ai-gui-field">
          <label>親となる地域ジャーナル（資源・気候を継承）</label>
          <select id="town-parent-region">${regionOptions}</select>
        </div>
        <div class="ai-gui-field">
          <label>集落の規模</label>
          <select id="town-scale">
            <option value="辺境の開拓村・小村">辺境の開拓村・小村</option>
            <option value="街道の宿場町・交易町">街道の宿場町・交易町</option>
            <option value="鉱山街・職人街">鉱山街・職人街</option>
            <option value="城塞都市・港町">城塞都市・港町</option>
          </select>
        </div>
      </div>
      <div class="ai-gui-row">
        <div class="ai-gui-field">
          <label>集落・街の名称</label>
          <input type="text" id="town-name" placeholder="集落・街の名称..." />
        </div>
        <div class="ai-gui-field">
          <label>GMからの集落テーマ指示</label>
          <input type="text" id="town-prompt" placeholder="生業や街の雰囲気を指定（空欄なら地域資源から自動導出）" />
        </div>
      </div>
      <div class="ai-gui-split">
        <div class="ai-gui-field ai-gui-box-pub">
          <label>📖 PL公開（建築・一次/二次産業・末端の手仕事・くらし・楽しみ・悲しみ）</label>
          <textarea id="town-public" rows="9" placeholder="建築、作物/資源、主要工芸品、末端の手仕事（生活財）、日々の楽しみ、影を落とす悲しみ..."></textarea>
        </div>
        <div class="ai-gui-field ai-gui-box-dm">
          <label>🔒 DM秘匿（悲しみの真相・手仕事の探索フック・NPCの秘密）</label>
          <textarea id="town-dm" rows="9" placeholder="集落の悲しみの真因、末端の手仕事がワイルダネスや廃墟でどう発見されるか..."></textarea>
        </div>
      </div>
      <div class="ai-gui-row">
        <div class="ai-gui-field">
          <label>🎵 『楽しみ・日常』BGM英語プロンプト</label>
          <input type="text" id="town-audio-joy" placeholder="Warm acoustic folk music, lively atmosphere..." />
        </div>
        <div class="ai-gui-field">
          <label>🕯️ 『悲しみ・夜』BGM英語プロンプト</label>
          <input type="text" id="town-audio-sorrow" placeholder="Melancholic strings, quiet night atmosphere..." />
        </div>
      </div>
      <div class="ai-gui-actions">
        <button type="button" class="ai-btn-gen" id="btn-gen-town">🤖 Stage 2: 産業・くらし・悲しみをAI生成</button>
        <button type="button" class="ai-btn-save" id="btn-save-town">💾 ジャーナル保存 & BGMプレイリスト生成</button>
      </div>
    </div>

    <!-- ================= TAB 3: 周辺・ワイルダネス ================= -->
    <div id="tab-wilderness" class="ai-gui-panel ${activeTab === 'tab-wilderness' ? 'active' : ''}">
      <div class="ai-gui-row">
        <div class="ai-gui-field">
          <label>親地域ジャーナル</label>
          <select id="wild-parent-region">${regionOptions}</select>
        </div>
        <div class="ai-gui-field">
          <label>出発地の集落ジャーナル（手仕事・悲しみを継承）</label>
          <select id="wild-parent-town">${settlementOptions}</select>
        </div>
      </div>
      <div class="ai-gui-row">
        <div class="ai-gui-field">
          <label>旅路エリア名称</label>
          <input type="text" id="wild-name" placeholder="街道・旅路エリアの名称..." />
        </div>
        <div class="ai-gui-field">
          <label>GM追加指示</label>
          <input type="text" id="wild-prompt" placeholder="道中の地形や中継地のイメージ..." />
        </div>
      </div>
      <div class="ai-gui-split">
        <div class="ai-gui-field ai-gui-box-pub">
          <label>📖 PL公開（街道の景観・天候表）</label>
          <textarea id="wild-public" rows="7" placeholder="旅路の全体像と天候の移り変わり..."></textarea>
        </div>
        <div class="ai-gui-field ai-gui-box-dm">
          <label>🔒 DM秘匿（尾行する脅威・迂回路の秘密・ランドマーク一覧）</label>
          <textarea id="wild-dm" rows="7" placeholder="斥候の襲撃条件や道中に残された集落の生活財の痕跡..."></textarea>
        </div>
      </div>
      <div class="ai-gui-actions">
        <button type="button" class="ai-btn-gen" id="btn-gen-wild">🤖 Stage 3: 旅路データをAI生成</button>
        <button type="button" class="ai-btn-save" id="btn-save-wild">💾 ジャーナルに保存</button>
        <button type="button" class="ai-btn-build" id="btn-build-wild">🌲 ワイルダネス(旅路)シーンを構築</button>
      </div>
    </div>

    <!-- ================= TAB 4: 廃墟・迷宮ビルド ================= -->
    <div id="tab-site" class="ai-gui-panel ${activeTab === 'tab-site' ? 'active' : ''}">
      <div class="ai-gui-row">
        <div class="ai-gui-field">
          <label>文脈を引き継ぐ集落（産業・手仕事・悲しみ）</label>
          <select id="site-parent-town">${settlementOptions}</select>
        </div>
        <div class="ai-gui-field">
          <label>道中（ワイルダネス）ジャーナル</label>
          <select id="site-parent-wild">${wildernessOptions}</select>
        </div>
        <div class="ai-gui-field">
          <label>舞台の空間タイプ</label>
          <select id="site-mode">
            <option value="ruins">⛰️ 野外の廃墟・古戦場 (風見が丘型 / 視線が通る崩落壁)</option>
            <option value="underground">🕳️ 地下迷宮・城塞 (完全閉鎖空間)</option>
          </select>
        </div>
      </div>

      <div class="ai-gui-field">
        <label>🎯 物語・中目標に絡めるPC / NPC（チェックしたActorのBiographyをプロンプトへ注入）</label>
        <div style="background:#101014; padding:5px 8px; border-radius:4px; border:1px solid #333; max-height:48px; overflow-y:auto;">
          ${actorCheckboxesHtml || "Actorが登録されていません"}
        </div>
      </div>

      <div class="ai-gui-row">
        <div class="ai-gui-field">
          <label>廃墟・迷宮の名称</label>
          <input type="text" id="site-name" placeholder="廃墟・ダンジョンの名称..." />
        </div>
        <div class="ai-gui-field">
          <label>GMからの追加テーマ・ギミック指示</label>
          <input type="text" id="site-prompt" placeholder="特定のシチュエーションやギミックの要望..." />
        </div>
      </div>

      <div class="ai-gui-row">
        <div class="ai-gui-field">
          <label>マップ規模</label>
          <select id="site-size">
            <option value="small">小 (100x100マス)</option>
            <option value="medium">中 (500x500マス)</option>
            <option value="large">大 (1000x1000マス)</option>
          </select>
        </div>
        <div class="ai-gui-field">
          <label>部屋密度: <span id="lbl-site-density">0.5</span></label>
          <input type="range" id="site-density" min="0.2" max="1.0" step="0.1" value="0.5" />
        </div>
        <div class="ai-gui-field">
          <label>周回路(ループ率): <span id="lbl-site-loop">0.25</span></label>
          <input type="range" id="site-loop" min="0.0" max="0.5" step="0.05" value="0.25" />
        </div>
      </div>

      <div class="ai-gui-split">
        <div class="ai-gui-field ai-gui-box-pub">
          <label>📖 PL公開（外観・歴史・分かりやすい大目標・PC導入・五感）</label>
          <textarea id="site-public" rows="7" placeholder="Stage 4-Aで生成される【分かりやすい大目標】と五感アンビエンス..."></textarea>
        </div>
        <div class="ai-gui-field ai-gui-box-dm">
          <label>🔒 DM秘匿（袋小路の中目標・スカッとする逆転劇・勢力）</label>
          <textarea id="site-dm" rows="7" placeholder="Stage 4-Aで生成される【中目標】と【スカッとする逆転劇(カタルシス)】。ここを書き換えてからシーン構築すると部屋配置に100%反映されます！"></textarea>
        </div>
      </div>

      <div class="ai-gui-actions">
        <button type="button" class="ai-btn-gen" id="btn-gen-site-lore">🤖 Stage 4-A: 大目標・中目標・逆転劇を下書き生成</button>
        <button type="button" class="ai-btn-save" id="btn-save-site-lore">💾 ジャーナル保存</button>
        <button type="button" class="ai-btn-build" id="btn-build-site-scene">🏰 Stage 4-B: この内容で全室配置＆シーン一括構築</button>
      </div>
    </div>
  </div>
  `;

  if (builderDialogInstance) {
    builderDialogInstance.close();
  }

  builderDialogInstance = new Dialog({
    title: `🧙‍♂️ AI World & Scenario Builder - ${ASSISTANT_DM_CONFIG.name}`,
    content: htmlContent,
    buttons: {},
    render: (html) => {
      const root = (html instanceof HTMLElement) ? html : (html[0] || html);
      bindBuilderGuiEvents(root);
    }
  }, { width: 760, height: "auto", resizable: true });

  builderDialogInstance.render(true);
}

function formatRegionDraftToText(data) {
  const pub = data.public_lore || {};
  const dm = data.dm_secrets || {};
  const resList = Array.isArray(pub.natural_resources)
    ? pub.natural_resources.map(r => `・${r.name} (${r.location}): ${r.usage}`).join("\n")
    : "";

  const publicText = [
    `【気候と地形】\n${pub.climate_and_terrain || ""}`,
    `【天然資源の分布】\n${resList}`,
    `【歴史と遺構】\n${pub.history_and_ruins || ""}`,
    `【旅人の第一印象】\n${pub.travel_impression || ""}`
  ].join("\n\n");

  const dmText = [
    `【地域を蝕む見えざる脅威】\n${dm.hidden_threat || ""}`,
    `【勢力間の緊張関係】\n${dm.faction_tensions || ""}`,
    `【古代の秘密】\n${dm.ancient_secret || ""}`
  ].join("\n\n");

  return { publicText, dmText };
}

function formatSettlementDraftToText(data) {
  const pub = data.public_lore || {};
  const dm = data.dm_secrets || {};
  const craft = pub.micro_craft || {};
  const npcs = Array.isArray(pub.key_npcs)
    ? pub.key_npcs.map(n => `・${n.name} (${n.role}): ${n.personality}`).join("\n")
    : "";

  const publicText = [
    `【建築と街並み】\n${pub.architecture_and_scenery || ""}`,
    `【一次産業（原材料の生産・採取）】\n${pub.primary_industries || ""}`,
    `【二次産業（加工・主要交易品）】\n${pub.secondary_industries || ""}`,
    `【末端の手仕事・生活財】: 『${craft.item_name || "生活工芸品"}』\n${craft.description || ""}`,
    `【日々のくらしと地元の料理】\n${pub.daily_life_and_food || ""}`,
    `【人々の楽しみ】\n${pub.local_joys || ""}`,
    `【影を落とす悲しみ・街の憂い】\n${pub.visible_sorrows || ""}`,
    `【主要NPC】\n${npcs}`
  ].join("\n\n");

  const dmText = [
    `【悲しみの真相と周辺領域への繋がり】\n${dm.sorrow_truth || ""}`,
    `【末端の手仕事（${craft.item_name || "生活財"}）の探索フック】\n${dm.micro_craft_hook || ""}`,
    `【NPCの隠された秘密】\n${dm.npc_hidden_agenda || ""}`
  ].join("\n\n");

  return { publicText, dmText };
}

function formatSiteDraftToText(data) {
  const pub = data.public_lore || {};
  const dm = data.dm_secrets || {};
  const amb = pub.sensory_ambience || {};
  const subs = Array.isArray(dm.sub_objectives)
    ? dm.sub_objectives.map((s, i) => `${i + 1}. 【${s.title}】 (${s.target_topology})\n   ・文脈: ${s.connection_to_town_or_pc}\n   ・達成効果: ${s.effect}`).join("\n")
    : "";
  const facs = Array.isArray(dm.factions)
    ? dm.factions.map(f => `・${f.name} (${f.territory}): ${f.status}`).join("\n")
    : "";

  const publicText = [
    `【ミッション型】: ${data.mission_archetype || ""}`,
    `【分かりやすい大目標】\n${pub.clear_main_goal || ""}`,
    `【PC・集落との導入フック】\n${pub.pc_narrative_hook || ""}`,
    `【外観と歴史】\n${pub.exterior_and_history || ""}`,
    `【五感アンビエンス】\n・視覚: ${amb.visual || ""}\n・聴覚: ${amb.auditory || ""}\n・嗅覚: ${amb.olfactory || ""}\n・空気: ${amb.air || ""}`
  ].join("\n\n");

  const dmText = [
    `【真相と現在の危機】\n${dm.true_situation || ""}`,
    `【階層化された中目標（袋小路・ハブ配置）】\n${subs}`,
    `【スカッとする逆転劇（カタルシス演出）】\n${dm.catharsis_payoff || ""}`,
    `【支配勢力と縄張り】\n${facs}`
  ].join("\n\n");

  return { publicText, dmText };
}

function textToHtmlParagraphs(text) {
  return (text || "")
    .split("\n\n")
    .map(block => `<p style="margin-bottom:10px; line-height:1.6;">${block.replace(/\n/g, "<br>")}</p>`)
    .join("");
}

function bindBuilderGuiEvents(root) {
  root.querySelectorAll(".ai-gui-tab").forEach(tabBtn => {
    tabBtn.addEventListener("click", () => {
      root.querySelectorAll(".ai-gui-tab, .ai-gui-panel").forEach(el => el.classList.remove("active"));
      tabBtn.classList.add("active");
      root.querySelector(`#${tabBtn.dataset.target}`)?.classList.add("active");
    });
  });

  const denSlider = root.querySelector("#site-density");
  const loopSlider = root.querySelector("#site-loop");
  denSlider?.addEventListener("input", (e) => { root.querySelector("#lbl-site-density").textContent = e.target.value; });
  loopSlider?.addEventListener("input", (e) => { root.querySelector("#lbl-site-loop").textContent = e.target.value; });

  // Stage 1: 地域
  root.querySelector("#btn-gen-region")?.addEventListener("click", () => {
    const userPrompt = root.querySelector("#reg-prompt")?.value || "";
    setMascotState("thinking", "地域の気候・天然資源・歴史を推論しているよ…", 15000);
    sendWsPayload({ event: "builder_generate_stage", stage: "region", user_prompt: userPrompt });
  });

  root.querySelector("#btn-save-region")?.addEventListener("click", async () => {
    const title = root.querySelector("#reg-name")?.value?.trim() || "新規地域";
    const pubText = root.querySelector("#reg-public")?.value || "";
    const dmText = root.querySelector("#reg-dm")?.value || "";
    const audioPrompt = root.querySelector("#reg-audio")?.value || "";
    await saveAtlasJournal({
      categoryKey: "region",
      title,
      publicHtml: textToHtmlParagraphs(pubText),
      dmHtml: textToHtmlParagraphs(dmText),
      metaData: { audio_prompt_en: audioPrompt }
    });
    openScenarioBuilderGUI("tab-settlement");
  });

  // Stage 2: 集落・くらし (保存時にBGMプレイリスト生成も連動)
  root.querySelector("#btn-gen-town")?.addEventListener("click", () => {
    const parentRegId = root.querySelector("#town-parent-region")?.value || "";
    const regionCtx = extractJournalContextForPrompt(parentRegId);
    const scale = root.querySelector("#town-scale")?.value || "辺境の村";
    const userPrompt = root.querySelector("#town-prompt")?.value || "";

    setMascotState("thinking", "地域の資源から、集落の産業・手仕事・人々の楽しみと悲しみを紡いでいるよ…", 20000);
    sendWsPayload({
      event: "builder_generate_stage",
      stage: "settlement",
      settlement_scale: scale,
      user_prompt: userPrompt,
      region_context: regionCtx
    });
  });

  root.querySelector("#btn-save-town")?.addEventListener("click", async () => {
    const title = root.querySelector("#town-name")?.value?.trim() || "新規集落";
    const pubText = root.querySelector("#town-public")?.value || "";
    const dmText = root.querySelector("#town-dm")?.value || "";
    const parentRegId = root.querySelector("#town-parent-region")?.value || "";
    const joyAudio = root.querySelector("#town-audio-joy")?.value?.trim() || "";
    const sorrowAudio = root.querySelector("#town-audio-sorrow")?.value?.trim() || "";

    const bgmBarHtml = buildBgmControlBarHtml(title);

    await saveAtlasJournal({
      categoryKey: "settlement",
      title,
      publicHtml: textToHtmlParagraphs(pubText),
      dmHtml: bgmBarHtml + textToHtmlParagraphs(dmText),
      metaData: { parent_region_id: parentRegId, joy_bgm_en: joyAudio, sorrow_bgm_en: sorrowAudio }
    });

    if (joyAudio || sorrowAudio) {
      setMascotState("thinking", `『${title}』の楽しみBGMと悲しみBGMを作曲しているよ…♪`, 25000);
      sendWsPayload({
        event: "builder_generate_bgm",
        settlement_name: title,
        joy_bgm_en: joyAudio,
        sorrow_bgm_en: sorrowAudio
      });
    }

    openScenarioBuilderGUI("tab-wilderness");
  });

  // Stage 3: ワイルダネス
  root.querySelector("#btn-gen-wild")?.addEventListener("click", () => {
    const regCtx = extractJournalContextForPrompt(root.querySelector("#wild-parent-region")?.value);
    const townCtx = extractJournalContextForPrompt(root.querySelector("#wild-parent-town")?.value);
    const userPrompt = root.querySelector("#wild-prompt")?.value || "";

    setMascotState("thinking", "集落から目的地へ向かう街道・獣道・野営地のネットワークを設計中…", 20000);
    sendWsPayload({
      event: "builder_generate_stage",
      stage: "wilderness",
      user_prompt: userPrompt,
      region_context: regCtx,
      settlement_context: townCtx
    });
  });

  root.querySelector("#btn-save-wild")?.addEventListener("click", async () => {
    const title = root.querySelector("#wild-name")?.value?.trim() || "新規ワイルダネス";
    const pubText = root.querySelector("#wild-public")?.value || "";
    const dmText = root.querySelector("#wild-dm")?.value || "";
    await saveAtlasJournal({
      categoryKey: "wilderness",
      title,
      publicHtml: textToHtmlParagraphs(pubText),
      dmHtml: textToHtmlParagraphs(dmText),
      metaData: { raw_landmarks: window._lastWildernessLandmarks || [] }
    });
  });

  root.querySelector("#btn-build-wild")?.addEventListener("click", () => {
    const title = root.querySelector("#wild-name")?.value?.trim() || "旅路エリア";
    const pubText = root.querySelector("#wild-public")?.value || "";
    const dmText = root.querySelector("#wild-dm")?.value || "";
    setMascotState("thinking", "ワイルダネス(旅路)の広域マップとランドマークを構築しているよ！", 25000);
    sendWsPayload({
      event: "builder_build_scene",
      scene_mode: "wilderness",
      name: title,
      public_lore: pubText,
      dm_secrets: dmText,
      landmarks: window._lastWildernessLandmarks || []
    });
  });

  // Stage 4: 廃墟・迷宮
  root.querySelector("#btn-gen-site-lore")?.addEventListener("click", () => {
    const townCtx = extractJournalContextForPrompt(root.querySelector("#site-parent-town")?.value);
    const wildCtx = extractJournalContextForPrompt(root.querySelector("#site-parent-wild")?.value);
    const siteMode = root.querySelector("#site-mode")?.value || "ruins";
    const sizeKey = root.querySelector("#site-size")?.value || "small";
    const userPrompt = root.querySelector("#site-prompt")?.value || "";

    const checkedActorIds = Array.from(root.querySelectorAll(".gui-actor-check:checked")).map(cb => cb.value);
    const actorsCtx = collectSelectedActorsContext(checkedActorIds);

    setMascotState("thinking", "PCの背景と集落の悲しみを結ぶ『大目標・中目標・スカッとする逆転劇』を設計中…", 20000);
    sendWsPayload({
      event: "builder_generate_stage",
      stage: "site_lore",
      site_mode: siteMode,
      size_key: sizeKey,
      user_prompt: userPrompt,
      settlement_context: townCtx,
      wilderness_context: wildCtx,
      actors_context: actorsCtx
    });
  });

  root.querySelector("#btn-save-site-lore")?.addEventListener("click", async () => {
    const title = root.querySelector("#site-name")?.value?.trim() || "新規廃墟・迷宮";
    const pubText = root.querySelector("#site-public")?.value || "";
    const dmText = root.querySelector("#site-dm")?.value || "";
    await saveAtlasJournal({
      categoryKey: "site",
      title,
      publicHtml: textToHtmlParagraphs(pubText),
      dmHtml: textToHtmlParagraphs(dmText),
      metaData: { site_mode: root.querySelector("#site-mode")?.value || "ruins" }
    });
  });

  root.querySelector("#btn-build-site-scene")?.addEventListener("click", async () => {
    const title = root.querySelector("#site-name")?.value?.trim() || "古塔の廃墟";
    const pubText = root.querySelector("#site-public")?.value || "";
    const dmText = root.querySelector("#site-dm")?.value || "";
    const siteMode = root.querySelector("#site-mode")?.value || "ruins";
    const sizeKey = root.querySelector("#site-size")?.value || "small";
    const density = parseFloat(root.querySelector("#site-density")?.value || "0.5");
    const loopRatio = parseFloat(root.querySelector("#site-loop")?.value || "0.25");
    const userPrompt = root.querySelector("#site-prompt")?.value || "";

    await saveAtlasJournal({
      categoryKey: "site",
      title,
      publicHtml: textToHtmlParagraphs(pubText),
      dmHtml: textToHtmlParagraphs(dmText),
      metaData: { site_mode: siteMode }
    });

    setMascotState("thinking", `『${title}』のマップ・視線が通る崩落壁・全部屋の情景と環境音を構築中…！`, 35000);
    sendWsPayload({
      event: "builder_build_scene",
      scene_mode: siteMode,
      name: title,
      size_key: sizeKey,
      density: density,
      loop_ratio: loopRatio,
      user_prompt: userPrompt,
      site_public_lore: pubText,
      site_dm_secrets: dmText
    });
  });
}

// =========================================================================
// 5. NPC会話システム & 記憶・忘却管理 & NPCブレイン管理GUI
// =========================================================================
function getNpcBrain(actor) {
  if (!actor) return foundry.utils.deepClone(DEFAULT_NPC_BRAIN);
  const saved = actor.getFlag(MODULE_ID, "npc_brain");
  if (saved) {
    return foundry.utils.mergeObject(foundry.utils.deepClone(DEFAULT_NPC_BRAIN), saved);
  }
  const rawBio = actor.system?.details?.biography?.value || actor.system?.biography || "";
  const cleanBio = rawBio.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
  const initial = foundry.utils.deepClone(DEFAULT_NPC_BRAIN);
  if (cleanBio) {
    initial.public_known = cleanBio.slice(0, 250);
  }
  return initial;
}

async function decayNpcMemories(actor, forceStep = false) {
  if (!actor) return;
  const brain = getNpcBrain(actor);
  const retention = parseFloat(brain.memory_retention ?? 0.8);

  while (brain.short_term_memory.length > 5) {
    const oldMem = brain.short_term_memory.shift();
    brain.long_term_memory.push({
      summary: oldMem.summary,
      importance: oldMem.importance || 2,
      clarity: 0.9,
      timestamp: oldMem.timestamp || Date.now()
    });
  }

  if (forceStep) {
    brain.long_term_memory = brain.long_term_memory.map(m => {
      if ((m.importance || 3) >= 5) return { ...m, clarity: 1.0 };
      const weight = 1.0 - ((5 - (m.importance || 3)) * (1.0 - retention) * 0.35);
      const nextClarity = Math.max(0.1, Math.round((m.clarity ?? 1.0) * weight * 100) / 100);
      return { ...m, clarity: nextClarity };
    }).filter(m => m.clarity >= 0.15);
  }

  await actor.setFlag(MODULE_ID, "npc_brain", brain);
  return brain;
}

async function updateNpcMemoryAfterDialogue(tokenDoc, trustDelta, newMemObj) {
  const actor = tokenDoc?.actor;
  if (!actor) return;
  const brain = getNpcBrain(actor);

  brain.trust = Math.max(-5, Math.min(5, (brain.trust || 0) + (trustDelta || 0)));

  if (newMemObj && newMemObj.summary) {
    const imp = parseInt(newMemObj.importance || 3, 10);
    brain.short_term_memory.push({
      summary: newMemObj.summary,
      importance: imp,
      timestamp: Date.now()
    });
    if (imp >= 4) {
      brain.long_term_memory.push({
        summary: newMemObj.summary,
        importance: imp,
        clarity: 1.0,
        timestamp: Date.now()
      });
    }
  }

  await actor.setFlag(MODULE_ID, "npc_brain", brain);
  await decayNpcMemories(actor, false);
}

function collectListeningNpcs(speakerTokenDoc = null, hearingRadiusFt = 35) {
  const scene = game.scenes?.active;
  if (!scene) return [];

  const gridSize = scene.grid?.size || 100;
  const gridDist = scene.grid?.distance || 5;

  if (!speakerTokenDoc) {
    speakerTokenDoc = scene.tokens.contents.find(t => t.actor?.type === "character" || t.actor?.hasPlayerOwner);
  }

  const npcTokens = scene.tokens.contents.filter(t => t.actor && t.actor.type !== "character" && !t.actor.hasPlayerOwner && !t.hidden);
  const listeners = [];

  for (const npc of npcTokens) {
    let distFt = 10;
    if (speakerTokenDoc) {
      const dx = (npc.x - speakerTokenDoc.x) / gridSize;
      const dy = (npc.y - speakerTokenDoc.y) / gridSize;
      distFt = Math.round(Math.hypot(dx, dy) * gridDist);
      if (distFt > hearingRadiusFt) continue;

      try {
        const origin = { x: speakerTokenDoc.x + gridSize / 2, y: speakerTokenDoc.y + gridSize / 2 };
        const dest = { x: npc.x + gridSize / 2, y: npc.y + gridSize / 2 };
        if (CONFIG.Canvas?.polygonBackends?.sound) {
          const soundBlocked = CONFIG.Canvas.polygonBackends.sound.testCollision(origin, dest, { type: "sound", mode: "any" });
          if (soundBlocked) continue;
        }
      } catch (e) {}
    }

    const brain = getNpcBrain(npc.actor);
    const formattedLongMem = (brain.long_term_memory || []).map(m => {
      const c = m.clarity ?? 1.0;
      const stateLabel = c >= 0.8 ? "[鮮明]" : (c >= 0.4 ? "[うろ覚え・曖昧]" : "[ほぼ忘却・勘違い]");
      return `${stateLabel} (重要度:${m.importance}) ${m.summary}`;
    });

    listeners.push({
      token_id: npc.id,
      name: npc.name,
      role: npc.actor.system?.details?.type?.value || "NPC",
      distance_ft: distFt,
      goal: brain.goal,
      trust: brain.trust,
      public_known: brain.public_known,
      secret_known: brain.secret_known,
      ignorance: brain.ignorance,
      short_term_memory: (brain.short_term_memory || []).map(s => s.summary),
      long_term_memory: formattedLongMem,
      voice: brain.voice
    });
  }

  listeners.sort((a, b) => a.distance_ft - b.distance_ft);
  return listeners.slice(0, 4);
}

async function handleNpcDialogueResponse(data) {
  const scene = game.scenes?.active;
  if (!scene) return;

  const res = data.result || {};
  const primaryToken = scene.tokens.get(res.primary_npc_id);
  const npcName = primaryToken?.name || res.primary_npc_name || "NPC";

  setMascotState("speaking", `💬 ${npcName}が反応したよ！`, 5000);

  if (primaryToken) {
    await updateNpcMemoryAfterDialogue(primaryToken, res.trust_delta, res.new_memory);
  }

  if (primaryToken && res.physical_action && res.physical_action !== "stay") {
    const speakerToken = scene.tokens.get(data.speaker_token_id);
    if (res.physical_action === "approach_speaker" && speakerToken) {
      await moveNpcByIntent(primaryToken, "approach_target", speakerToken.id);
    } else if (res.physical_action === "step_back" && speakerToken) {
      await moveNpcByIntent(primaryToken, "retreat_from_enemies", speakerToken.id);
    } else if (res.physical_action === "leave_scene") {
      await primaryToken.update({ alpha: 0.3 });
    } else if (res.physical_action === "turn_hostile") {
      ui.notifications.warn(`${npcName} が敵対行動に移りました！`);
    }
  }

  if (primaryToken?.object && canvas?.hud?.bubbles) {
    const bubbleText = res.speech ? res.speech : (res.narrative_action ? `*${res.narrative_action.slice(0, 35)}…*` : "");
    if (bubbleText) {
      await canvas.hud.bubbles.broadcast(primaryToken.object, bubbleText, { emote: !res.speech });
    }
  }

  const ambientList = Array.isArray(res.ambient_reactions) ? res.ambient_reactions : [];
  for (const amb of ambientList) {
    const ambToken = scene.tokens.get(amb.npc_id);
    if (ambToken?.object && amb.silent_emote && canvas?.hud?.bubbles) {
      await canvas.hud.bubbles.broadcast(ambToken.object, `*${amb.silent_emote}*`, { emote: true });
    }
  }

  const ambientHtml = ambientList.length > 0
    ? `<div style="margin-top:8px; padding-top:6px; border-top:1px dashed #ccc; font-size:12px; color:#555;">
         ${ambientList.map(a => `<p style="margin:2px 0;"><em>👀 <strong>${a.npc_name}:</strong> ${a.silent_emote}</em></p>`).join("")}
       </div>`
    : "";

  const offer = res.check_offer;
  const checkBtnHtml = (offer && offer.skill)
    ? `<div style="margin-top:10px; background:#2c2c34; padding:6px 8px; border-radius:4px; border-left:3px solid #f39c12;">
         <div style="font-size:11px; color:#f39c12; margin-bottom:4px;">💡 態度の裏に何かがありそうだ…</div>
         <button type="button" class="ai-npc-check-btn"
                 data-npc-id="${res.primary_npc_id}"
                 data-skill="${offer.skill}"
                 data-dc="${offer.dc || 13}"
                 data-hint="${offer.target_secret_hint || ''}"
                 style="width:100%; background:#e67e22; color:#fff; border:none; padding:5px; border-radius:3px; cursor:pointer; font-weight:bold; font-size:12px;">
           🎲 【${offer.skill} DC${offer.dc || 13}】に挑戦して本音を引き出す
         </button>
       </div>`
    : "";

  const novelCardHtml = `
  <div class="ai-novel-dialogue" style="font-family: 'Yu Mincho', 'Hiragino Mincho ProN', serif, inherit; line-height: 1.75; font-size: 13.5px; color: #1a1a1a; background: #fdfaf2; padding: 10px 12px; border-radius: 4px; border-left: 4px solid #782e22; box-shadow: 0 1px 3px rgba(0,0,0,0.15);">
    <p style="margin: 0 0 8px 0; text-indent: 1em; color: #333;">
      ${res.narrative_action || ""}
    </p>
    ${res.speech ? `<p style="margin: 0 0 6px 0; font-weight: bold; color: #5a1810; font-size: 14px;">「${res.speech}」</p>` : ""}
    ${ambientHtml}
    ${checkBtnHtml}
  </div>`;

  await ChatMessage.create({
    speaker: {
      token: primaryToken?.id,
      actor: primaryToken?.actor?.id,
      alias: npcName
    },
    content: novelCardHtml
  });
}

function openNpcBrainManagerGUI(targetActor = null) {
  if (!targetActor) {
    const controlled = canvas?.tokens?.controlled || [];
    if (controlled.length > 0 && controlled[0].actor) {
      targetActor = controlled[0].actor;
    } else if (game.actors) {
      targetActor = game.actors.contents.find(a => a.type === "npc") || game.actors.contents[0];
    }
  }

  if (!targetActor) {
    ui.notifications.warn("編集するNPCアクターが見つかりません。マップ上のNPCトークンを選択してください。");
    return;
  }

  const npcActors = game.actors.contents.filter(a => a.type !== "character" || !a.hasPlayerOwner);
  const brain = getNpcBrain(targetActor);
  const actorOptions = npcActors.map(a => `<option value="${a.id}" ${a.id === targetActor.id ? "selected" : ""}>${a.name}</option>`).join("");

  const longMemRows = (brain.long_term_memory || []).map(m => `
    <div class="brain-mem-row" style="display:flex; gap:4px; align-items:center; margin-bottom:4px; background:#141418; padding:4px; border-radius:4px;">
      <input type="text" class="mem-summary" value="${m.summary || ''}" style="flex:1; background:#0c0c0e; color:#eee; border:1px solid #333; padding:3px; font-size:11px;" />
      <label style="font-size:10px; color:#aaa;">重要度:
        <input type="number" class="mem-imp" min="1" max="5" value="${m.importance || 3}" style="width:36px; background:#0c0c0e; color:#f39c12; border:1px solid #333;" />
      </label>
      <label style="font-size:10px; color:#aaa;">鮮明度:
        <input type="number" class="mem-clarity" min="0.1" max="1.0" step="0.1" value="${m.clarity ?? 1.0}" style="width:44px; background:#0c0c0e; color:#2ecc71; border:1px solid #333;" />
      </label>
    </div>
  `).join("");

  const content = `
  <div style="font-family:inherit; color:#f0f0e0; background:#1e1e24; padding:10px; border-radius:6px; display:flex; flex-direction:column; gap:8px;">
    <div style="display:flex; gap:8px; align-items:center; border-bottom:2px solid #e67e22; padding-bottom:6px;">
      <label style="font-weight:bold; color:#f39c12;">🎭 対象NPC:</label>
      <select id="brain-actor-select" style="flex:1; background:#121216; color:#fff; padding:5px; border:1px solid #555; border-radius:4px;">
        ${actorOptions}
      </select>
      <button type="button" id="btn-decay-test" style="background:#34495e; color:#fff; border:none; padding:5px 8px; border-radius:4px; cursor:pointer; font-size:11px;" title="時間経過による記憶の風化・うろ覚え化を1ステップ進めます">
        ⏳ 時間経過(記憶風化)
      </button>
    </div>

    <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap:8px;">
      <div>
        <label style="font-size:11px; color:#f39c12;">🤝 PCへの信頼度 (-5〜+5)</label>
        <input type="number" id="brain-trust" min="-5" max="5" value="${brain.trust ?? 0}" style="width:100%; background:#121216; color:#fff; padding:4px; border:1px solid #444;" />
      </div>
      <div>
        <label style="font-size:11px; color:#f39c12;">🧠 記憶保持力 (0.4=老衰 〜 1.0=不滅)</label>
        <input type="number" id="brain-retention" min="0.3" max="1.0" step="0.05" value="${brain.memory_retention ?? 0.8}" style="width:100%; background:#121216; color:#fff; padding:4px; border:1px solid #444;" />
      </div>
      <div>
        <label style="font-size:11px; color:#f39c12;">🗣️ TTSボイス・ピッチ・速度</label>
        <div style="display:flex; gap:2px;">
          <select id="brain-voice-name" style="flex:1; background:#121216; color:#fff; font-size:11px;">
            <option value="ja-JP-KeitaNeural" ${brain.voice?.name?.includes("Keita") ? "selected" : ""}>男性(Keita)</option>
            <option value="ja-JP-NanamiNeural" ${brain.voice?.name?.includes("Nanami") ? "selected" : ""}>女性(Nanami)</option>
          </select>
          <input type="text" id="brain-voice-pitch" value="${brain.voice?.pitch || '+0Hz'}" placeholder="-10Hz" style="width:52px; background:#121216; color:#fff; font-size:11px;" title="声の高さ(例: -12Hz, +8Hz)" />
          <input type="text" id="brain-voice-rate" value="${brain.voice?.rate || '+0%'}" placeholder="-10%" style="width:48px; background:#121216; color:#fff; font-size:11px;" title="話す速さ(例: -15%, +10%)" />
        </div>
      </div>
    </div>

    <div>
      <label style="font-size:12px; color:#f39c12; font-weight:bold;">🎯 現在の目的・行動原理 (Goal)</label>
      <input type="text" id="brain-goal" value="${brain.goal || ''}" style="width:100%; background:#121216; color:#fff; padding:5px; border:1px solid #444; border-radius:4px;" />
    </div>

    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:8px;">
      <div>
        <label style="font-size:12px; color:#2ecc71; font-weight:bold;">📖 知っている表の知識 (Public Known)</label>
        <textarea id="brain-public" rows="3" style="width:100%; background:#121216; color:#eee; padding:5px; border:1px solid #444; font-size:12px;">${brain.public_known || ""}</textarea>
      </div>
      <div>
        <label style="font-size:12px; color:#e74c3c; font-weight:bold;">🔒 隠している秘密と開示条件 (Secret Known)</label>
        <textarea id="brain-secret" rows="3" style="width:100%; background:#121216; color:#eee; padding:5px; border:1px solid #444; font-size:12px;">${brain.secret_known || ""}</textarea>
      </div>
    </div>

    <div>
      <label style="font-size:12px; color:#95a5a6; font-weight:bold;">🚫 知らないこと・無知の境界 (Ignorance - メタ発言防止)</label>
      <input type="text" id="brain-ignorance" value="${brain.ignorance || ''}" style="width:100%; background:#121216; color:#ccc; padding:5px; border:1px solid #444; border-radius:4px; font-size:12px;" />
    </div>

    <div>
      <label style="font-size:12px; color:#d2b4de; font-weight:bold;">📚 長期記憶リスト (鮮明度 0.8以上=鮮明 / 0.4〜0.7=うろ覚え / 0.4未満=忘却)</label>
      <div id="brain-mem-container" style="max-height:130px; overflow-y:auto; background:#101014; padding:6px; border:1px solid #333; border-radius:4px;">
        ${longMemRows || '<div style="font-size:11px; color:#777;">長期記憶はまだありません（会話を通じて自動蓄積されます）</div>'}
      </div>
    </div>

    <button type="button" id="btn-save-brain" style="background:#27ae60; color:#fff; border:none; padding:8px; border-radius:4px; cursor:pointer; font-weight:bold; margin-top:4px;">
      💾 このNPCの知識・記憶・声色を保存
    </button>
  </div>`;

  if (brainDialogInstance) {
    brainDialogInstance.close();
  }

  brainDialogInstance = new Dialog({
    title: `🧠 NPCブレイン＆記憶・声色管理 - ${targetActor.name}`,
    content,
    buttons: {},
    render: (html) => {
      const root = (html instanceof HTMLElement) ? html : (html[0] || html);

      root.querySelector("#brain-actor-select")?.addEventListener("change", (ev) => {
        const nextActor = game.actors.get(ev.target.value);
        if (nextActor) {
          brainDialogInstance.close();
          openNpcBrainManagerGUI(nextActor);
        }
      });

      root.querySelector("#btn-decay-test")?.addEventListener("click", async () => {
        await decayNpcMemories(targetActor, true);
        ui.notifications.info(`${targetActor.name} の記憶が時間経過で風化（うろ覚え化）しました。`);
        brainDialogInstance.close();
        openNpcBrainManagerGUI(targetActor);
      });

      root.querySelector("#btn-save-brain")?.addEventListener("click", async () => {
        const updated = getNpcBrain(targetActor);
        updated.trust = parseInt(root.querySelector("#brain-trust")?.value || "0", 10);
        updated.memory_retention = parseFloat(root.querySelector("#brain-retention")?.value || "0.8");
        updated.goal = root.querySelector("#brain-goal")?.value || "";
        updated.public_known = root.querySelector("#brain-public")?.value || "";
        updated.secret_known = root.querySelector("#brain-secret")?.value || "";
        updated.ignorance = root.querySelector("#brain-ignorance")?.value || "";
        updated.voice = {
          name: root.querySelector("#brain-voice-name")?.value || "ja-JP-KeitaNeural",
          pitch: root.querySelector("#brain-voice-pitch")?.value || "+0Hz",
          rate: root.querySelector("#brain-voice-rate")?.value || "+0%"
        };

        const memRows = root.querySelectorAll(".brain-mem-row");
        if (memRows.length > 0) {
          updated.long_term_memory = Array.from(memRows).map(row => ({
            summary: row.querySelector(".mem-summary")?.value || "",
            importance: parseInt(row.querySelector(".mem-imp")?.value || "3", 10),
            clarity: parseFloat(row.querySelector(".mem-clarity")?.value || "1.0"),
            timestamp: Date.now()
          })).filter(m => m.summary.trim().length > 0);
        }

        await targetActor.setFlag(MODULE_ID, "npc_brain", updated);
        ui.notifications.info(`『${targetActor.name}』のブレイン設定を保存しました！`);
        brainDialogInstance.close();
      });
    }
  }, { width: 640, height: "auto", resizable: true });

  brainDialogInstance.render(true);
}

// =========================================================================
// 6. NPC / モンスター自律戦闘エンジン (知覚抽出 ➔ AI判断 ➔ 壁回避移動・吹き出し・ロール)
// =========================================================================
function extractNpcPerception(npcTokenDoc) {
  const actor = npcTokenDoc?.actor;
  if (!actor) return null;

  const scene = npcTokenDoc.parent;
  const gridSize = scene.grid?.size || 100;
  const gridDist = scene.grid?.distance || 5;
  const dims = scene.dimensions ?? (scene.getDimensions ? scene.getDimensions() : {});
  const offsetX = dims.sceneX ?? 0;
  const offsetY = dims.sceneY ?? 0;

  const hpVal = actor.system?.attributes?.hp?.value ?? 10;
  const hpMax = actor.system?.attributes?.hp?.max ?? 10;
  const ratio = hpMax > 0 ? hpVal / hpMax : 1;
  const healthStatus = ratio > 0.7 ? "健在・余裕あり" : (ratio > 0.3 ? "負傷・消耗" : "瀕死・危機的");

  const rawBio = actor.system?.details?.biography?.value || actor.system?.biography || "";
  const cleanBio = rawBio.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 300);

  const actions = (actor.items?.contents || [])
    .filter(i => ["weapon", "feat", "spell", "action"].includes(i.type))
    .slice(0, 8)
    .map(i => `・${i.name} (${i.type})`)
    .join("\n") || "・基本攻撃";

  const rooms = scene.getFlag(MODULE_ID, "rooms") || [];
  const tx = (npcTokenDoc.x - offsetX) / gridSize;
  const ty = (npcTokenDoc.y - offsetY) / gridSize;
  const currentRoom = rooms.find(r => tx >= r.grid_x && tx < r.grid_x + r.grid_w && ty >= r.grid_y && ty < r.grid_y + r.grid_h) || {};

  const pcTokens = scene.tokens.contents.filter(t => (t.actor?.type === "character" || t.actor?.hasPlayerOwner) && t.id !== npcTokenDoc.id);
  const targets = pcTokens.map(pc => {
    const dx = (pc.x - npcTokenDoc.x) / gridSize;
    const dy = (pc.y - npcTokenDoc.y) / gridSize;
    const distGrids = Math.hypot(dx, dy);
    const distFt = Math.round(distGrids * gridDist);

    let hasLos = true;
    try {
      const origin = { x: npcTokenDoc.x + gridSize / 2, y: npcTokenDoc.y + gridSize / 2 };
      const dest = { x: pc.x + gridSize / 2, y: pc.y + gridSize / 2 };
      if (CONFIG.Canvas?.polygonBackends?.sight) {
        hasLos = !CONFIG.Canvas.polygonBackends.sight.testCollision(origin, dest, { type: "sight", mode: "any" });
      }
    } catch (e) {}

    return {
      token_id: pc.id,
      name: pc.name,
      distance_ft: distFt,
      has_los: hasLos,
      adjacent: distGrids <= 1.5
    };
  });

  return {
    token_id: npcTokenDoc.id,
    npc_name: npcTokenDoc.name,
    npc_role: actor.system?.details?.type?.value || actor.type || "NPC",
    npc_bio: cleanBio || "この領域の住人・警戒中",
    hp_current: hpVal,
    hp_max: hpMax,
    health_status: healthStatus,
    move_speed: actor.system?.attributes?.movement?.walk || 30,
    available_actions: actions,
    room_name: currentRoom.name || "周辺区画",
    room_flavor: currentRoom.flavor || "",
    room_gm_notes: currentRoom.gm_notes || "",
    perceived_targets: targets
  };
}

async function moveNpcByIntent(npcTokenDoc, moveIntent, targetTokenId) {
  if (!moveIntent || moveIntent === "hold_position") return;
  const scene = npcTokenDoc.parent;
  const gridSize = scene.grid?.size || 100;
  const targetDoc = targetTokenId ? scene.tokens.get(targetTokenId) : null;

  const stepDirs = [
    [1, 0], [-1, 0], [0, 1], [0, -1],
    [1, 1], [1, -1], [-1, 1], [-1, -1]
  ];

  let currX = npcTokenDoc.x;
  let currY = npcTokenDoc.y;
  const maxSteps = 3;

  for (let step = 0; step < maxSteps; step++) {
    let bestPos = null;
    let bestScore = Infinity;

    for (const [dx, dy] of stepDirs) {
      const nextX = currX + dx * gridSize;
      const nextY = currY + dy * gridSize;

      const origin = { x: currX + gridSize / 2, y: currY + gridSize / 2 };
      const dest = { x: nextX + gridSize / 2, y: nextY + gridSize / 2 };
      let blocked = false;
      try {
        if (CONFIG.Canvas?.polygonBackends?.move) {
          blocked = CONFIG.Canvas.polygonBackends.move.testCollision(origin, dest, { type: "move", mode: "any" });
        }
      } catch (e) {}
      if (blocked) continue;

      const occupied = scene.tokens.contents.some(t => t.id !== npcTokenDoc.id && Math.abs(t.x - nextX) < gridSize * 0.5 && Math.abs(t.y - nextY) < gridSize * 0.5);
      if (occupied) continue;

      if (targetDoc) {
        const distToTarget = Math.hypot(nextX - targetDoc.x, nextY - targetDoc.y);
        if (moveIntent === "approach_target" || moveIntent === "flank_target") {
          const score = Math.abs(distToTarget - gridSize);
          if (score < bestScore) { bestScore = score; bestPos = { x: nextX, y: nextY }; }
        } else if (moveIntent === "retreat_from_enemies" || moveIntent === "seek_cover_or_gimmick") {
          const score = -distToTarget;
          if (score < bestScore) { bestScore = score; bestPos = { x: nextX, y: nextY }; }
        }
      }
    }

    if (bestPos) {
      currX = bestPos.x;
      currY = bestPos.y;
    }
  }

  if (currX !== npcTokenDoc.x || currY !== npcTokenDoc.y) {
    await npcTokenDoc.update({ x: currX, y: currY });
  }
}

async function executeNpcTurnOnCanvas(data) {
  const scene = game.scenes?.active;
  if (!scene) return;
  const npcTokenDoc = scene.tokens.get(data.token_id);
  if (!npcTokenDoc) return;

  setMascotState("speaking", `⚔️ ${npcTokenDoc.name}の行動！`, 5000);

  await moveNpcByIntent(npcTokenDoc, data.move_intent, data.target_token_id);

  const tokenObj = npcTokenDoc.object;
  if (tokenObj && data.speech && canvas?.hud?.bubbles) {
    await canvas.hud.bubbles.broadcast(tokenObj, data.speech, { emote: false });
  }

  const actionBadge = data.action_name ? `<span style="background:#782e22; color:#fff; padding:1px 6px; border-radius:3px; font-size:11px;">${data.action_name}</span>` : "";
  const chatHtml = `
    <div style="border-left: 3px solid #e67e22; padding-left: 8px;">
      <div><em>${data.emote || ""}</em> ${actionBadge}</div>
      ${data.speech ? `<div style="margin-top:4px; font-weight:bold; color:#d35400;">「${data.speech}」</div>` : ""}
    </div>
  `;
  await ChatMessage.create({
    speaker: { token: npcTokenDoc.id, actor: npcTokenDoc.actor?.id, alias: npcTokenDoc.name },
    content: chatHtml
  });

  const item = data.action_name ? npcTokenDoc.actor?.items?.getName(data.action_name) : null;
  if (item && typeof item.use === "function") {
    try { await item.use(); } catch (e) {}
  } else if (data.dice_formula) {
    const roll = new Roll(data.dice_formula);
    await roll.evaluate();
    await roll.toMessage({ speaker: { alias: `${npcTokenDoc.name} (${data.action_name || '判定'})` } });
  }
}

// =========================================================================
// 7. スラッシュコマンド (/ai, /agy) & 通常チャット発言のNPC聴取インターセプト
// =========================================================================
function sendWsPayload(payload) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
  } else {
    ui.notifications.warn("AI GM Bridge: Python サーバー (8765) に接続されていません。");
    setMascotState("offline", "サーバーと通信できないみたい…", 4000, false);
  }
}

function sendAiCommand(prompt) {
  const clean = (prompt || "").trim();

  // 引数なし、または "gui" / "ビルド" 指定時は4タブシナリオビルダーGUIを起動
  if (!clean || /^(gui|builder|ビルド|シナリオ)$/i.test(clean)) {
    openScenarioBuilderGUI();
    return;
  }

  // "brain" / "記憶" 指定時はNPCブレイン管理GUIを起動
  if (/^(brain|npc|ブレイン|記憶)$/i.test(clean)) {
    openNpcBrainManagerGUI();
    return;
  }

  setMascotState("thinking", "指示を解析しているよ…", 10000);
  sendWsPayload({
    event: "ai_command",
    user: game.user.name || "GM",
    prompt: clean,
    actors_context: collectSelectedActorsContext()
  });
}

function parseAiCommand(text) {
  if (!text) return null;
  const stripped = text
    .replace(/<br\s*[\/]?>/gi, "\n")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&#160;/gi, " ")
    .replace(/[\u00A0\u3000]/g, " ")
    .trim()
    .replace(/^["'“”「]+|["'“”」]+$/g, "")
    .trim();

  const match = stripped.match(/^\/(?:ai|agy)(?:[\s\u3000]+([\s\S]*))?$/i);
  if (match) {
    return (match[1] || "").trim();
  }
  return null;
}

function registerAiCommands() {
  if (typeof ChatLog !== "undefined" && ChatLog.CHAT_COMMANDS) {
    const aiCmd = {
      rgx: /^\s*["'“”「]?\s*\/(?:ai|agy)(?:[\s\u3000]+|&nbsp;|&#160;|[\u00A0\u3000]|$)([\s\S]*)$/i,
      isRoll: false,
      isMultiline: false,
      fn: function(command, match) {
        if (game.user && !game.user.isGM) return false;
        const rawPrompt = match[1] || "";
        const prompt = rawPrompt
          .replace(/<[^>]*>/g, "")
          .replace(/&nbsp;/gi, " ")
          .replace(/&#160;/gi, " ")
          .replace(/[\u00A0\u3000]/g, " ")
          .trim()
          .replace(/["'“”」]+\$/, "")
          .trim();
        sendAiCommand(prompt);
        return false;
      }
    };
    ChatLog.CHAT_COMMANDS.ai = aiCmd;
    ChatLog.CHAT_COMMANDS.agy = aiCmd;
  }
}

registerAiCommands();
Hooks.once("init", registerAiCommands);

Hooks.on("chatMessage", (chatLog, messageText) => {
  // 1. GMの /ai コマンド判定
  if (game.user?.isGM) {
    const prompt = parseAiCommand(messageText);
    if (prompt !== null) {
      sendAiCommand(prompt);
      return false;
    }
  }

  // 2. 通常のプレイヤー/GM発言時、周囲に声が届くNPCがいればNPC会話システムへ転送
  const rawText = (messageText || "").replace(/<[^>]*>/g, "").trim();
  if (rawText && !rawText.startsWith("/")) {
    if (game.user?.isGM && ws && ws.readyState === WebSocket.OPEN) {
      const speakerToken = canvas?.tokens?.controlled?.[0]?.document || null;
      const listeners = collectListeningNpcs(speakerToken);
      if (listeners.length > 0) {
        const scene = game.scenes?.active;
        const rooms = scene?.getFlag(MODULE_ID, "rooms") || [];
        const currentRoom = rooms[0] || { name: scene?.name || "現在の場所", flavor: "" };

        setMascotState("thinking", "周囲のNPCが発言を聞いて反応を考えているよ…", 10000);
        sendWsPayload({
          event: "npc_dialogue_request",
          speaker_name: speakerToken?.name || game.user.name,
          speaker_token_id: speakerToken?.id || null,
          player_input: rawText,
          room_name: currentRoom.name,
          room_flavor: currentRoom.flavor,
          listening_npcs: listeners,
          check_result_context: "なし（通常の会話）"
        });
      }
    }
  }
});

// =========================================================================
// 8. WebSocket 通信ハンドラ & シーン自動構築
// =========================================================================
Hooks.once("ready", async function() {
  console.log("AI GM Bridge | === 全機能統合モジュール (GUI / マスコット / 自律NPC / 空間音響) ロード完了 ===");

  // PL・GM全員の画面にミニキャラを表示
  initAssistantMascotUI();

  // PL側でのSocket受信（ミニキャラのアニメーション・吹き出し同期）
  if (game.socket) {
    game.socket.on(`module.${MODULE_ID}`, (data) => {
      if (data.action === "sync_mascot") {
        setMascotState(data.state, data.bubbleText, data.durationMs, false);
      }
    });
  }

  if (!game.user.isGM) return;
  registerAiCommands();

  function connect() {
    ws = new WebSocket("ws://127.0.0.1:8765");

    ws.onopen = () => {
      console.log("AI GM Bridge | WebSocket接続完了 (8765)");
      setMascotState("idle", "準備完了！左ダブルクリックでビルダー、右クリックでNPC記憶管理が開くよ！", 5000);
    };

    ws.onmessage = async (event) => {
      try {
        const data = JSON.parse(event.data);
        console.log("AI GM Bridge | 受信:", data);

        // A. シナリオビルダーGUIへのドラフト応答反映
        if (data.action === "builder_stage_result") {
          setMascotState("speaking", "下書きができたよ！内容を確認・修正してみてね。", 5000);
          const stage = data.stage;
          const res = data.result || {};

          if (stage === "region") {
            const { publicText, dmText } = formatRegionDraftToText(res);
            const nameEl = document.getElementById("reg-name");
            const pubEl = document.getElementById("reg-public");
            const dmEl = document.getElementById("reg-dm");
            const audEl = document.getElementById("reg-audio");
            if (nameEl && res.region_name) nameEl.value = res.region_name;
            if (pubEl) pubEl.value = publicText;
            if (dmEl) dmEl.value = dmText;
            if (audEl && res.audio_prompts?.region_ambience_en) audEl.value = res.audio_prompts.region_ambience_en;
          }
          else if (stage === "settlement") {
            const { publicText, dmText } = formatSettlementDraftToText(res);
            const nameEl = document.getElementById("town-name");
            const pubEl = document.getElementById("town-public");
            const dmEl = document.getElementById("town-dm");
            const joyEl = document.getElementById("town-audio-joy");
            const sorEl = document.getElementById("town-audio-sorrow");
            if (nameEl && res.settlement_name) nameEl.value = res.settlement_name;
            if (pubEl) pubEl.value = publicText;
            if (dmEl) dmEl.value = dmText;
            if (joyEl && res.audio_prompts?.joy_bgm_en) joyEl.value = res.audio_prompts.joy_bgm_en;
            if (sorEl && res.audio_prompts?.sorrow_bgm_en) sorEl.value = res.audio_prompts.sorrow_bgm_en;
          }
          else if (stage === "wilderness") {
            const pub = res.public_lore || {};
            const dm = res.dm_secrets || {};
            window._lastWildernessLandmarks = res.landmarks || [];
            const nameEl = document.getElementById("wild-name");
            const pubEl = document.getElementById("wild-public");
            const dmEl = document.getElementById("wild-dm");
            if (nameEl && res.wilderness_title) nameEl.value = res.wilderness_title;
            if (pubEl) {
              pubEl.value = `【旅路の概要】\n${pub.route_overview || ""}\n\n【天候表】\n${(pub.weather_table || []).join("\n")}`;
            }
            if (dmEl) {
              const lmSummary = (res.landmarks || []).map(l => `・#${l.id} ${l.title}: ${l.tts_narration}`).join("\n");
              dmEl.value = `【尾行・襲撃の脅威】\n${dm.stalking_threat || ""}\n\n【迂回路の秘密】\n${dm.shortcut_secret || ""}\n\n【中継ランドマーク】\n${lmSummary}`;
            }
          }
          else if (stage === "site_lore") {
            const { publicText, dmText } = formatSiteDraftToText(res);
            const nameEl = document.getElementById("site-name");
            const pubEl = document.getElementById("site-public");
            const dmEl = document.getElementById("site-dm");
            if (nameEl && res.site_name) nameEl.value = res.site_name;
            if (pubEl) pubEl.value = publicText;
            if (dmEl) dmEl.value = dmText;
          }
          return;
        }

        // B. 集落BGMのプレイリスト自動登録完了通知
        if (data.action === "register_settlement_playlist") {
          const sName = data.settlement_name || "集落";
          await createOrUpdateSettlementPlaylist({
            settlementName: sName,
            joySrc: data.joy_src,
            sorrowSrc: data.sorrow_src
          });

          setMascotState("speaking", `🎵『${sName}』のBGMプレイリストが完成したよ！`, 5000);

          const remoteHtml = `
          <div style="border: 2px solid #8e44ad; border-radius: 6px; padding: 10px; background: rgba(25, 20, 35, 0.92); color: #ecf0f1;">
            <div style="font-weight: bold; color: #d2b4de; font-size: 14px; margin-bottom: 4px;">
              🎶 『${sName}』プレイリスト登録完了
            </div>
            <div style="font-size: 12px; color: #ccc; margin-bottom: 6px;">
              ジャーナル内、または以下のボタンからワンクリックで街のBGMを切り替えられます：
            </div>
            ${buildBgmControlBarHtml(sName)}
          </div>`;

          await ChatMessage.create({
            speaker: { alias: ASSISTANT_DM_CONFIG.name },
            content: remoteHtml,
            whisper: [game.user.id]
          });
          return;
        }

        // C. NPC自律戦闘ターンの実行
        if (data.action === "execute_npc_turn") {
          await executeNpcTurnOnCanvas(data);
          return;
        }

        // D. NPC小説風会話レスポンスの描画・実行
        if (data.action === "npc_dialogue_response") {
          await handleNpcDialogueResponse(data);
          return;
        }

        // E. 通常チャット投稿
        if (data.action === "chat") {
          setMascotState("idle", "", 1000);
          await ChatMessage.create({
            speaker: { alias: data.speaker || ASSISTANT_DM_CONFIG.name },
            content: data.content
          });
          return;
        }

        // F. ダイスロール
        if (data.action === "roll") {
          const roll = new Roll(data.formula);
          await roll.evaluate();
          await roll.toMessage({
            speaker: { alias: data.speaker || `${ASSISTANT_DM_CONFIG.name} (判定)` }
          });
          return;
        }

        // G. 音声再生 (ミニキャラを発話アニメーションに連動)
        if (data.action === "play_sound") {
          if (game.audio?.context?.state === "suspended") {
            await game.audio.context.resume();
          }
          setMascotState("speaking", data.subtitle || "🎙️ 音声再生中…", 6500);
          await foundry.audio.AudioHelper.play({
            src: data.src,
            volume: data.volume ?? 1.0,
            autoplay: true,
            loop: false
          }, false);
          return;
        }

        // H. インタラクティブ選択カード
        if (data.action === "interactive_card") {
          setMascotState("speaking", data.title || "選択してね！", 4000);
          const title = data.title || ASSISTANT_DM_CONFIG.name;
          const message = data.message || "選択してください：";
          const choices = data.choices || [];

          const buttonsHtml = choices.map(c => `
            <button class="ai-gm-choice-btn" data-choice-value="${c.value}" data-choice-label="${c.label}"
                    style="flex: 1 1 45%; min-width: 120px; padding: 6px 10px; margin: 2px; cursor: pointer; border-radius: 4px; border: 1px solid #782e22; background: #2f2f2f; color: #f0f0e0; font-size: 13px;">
              ${c.label}
            </button>
          `).join("");

          const cardHtml = `
          <div class="ai-gm-interactive-card" style="border: 2px solid #e67e22; border-radius: 6px; padding: 10px; background: rgba(20, 20, 25, 0.85); color: #f0f0e0; margin-top: 4px;">
            <div style="font-weight: bold; color: #f39c12; font-size: 14px; margin-bottom: 6px;">🧙‍♂️ ${title}</div>
            <p style="margin: 0 0 10px 0; font-size: 13px;">${message}</p>
            <div class="ai-gm-choice-container" style="display: flex; flex-wrap: wrap; gap: 4px;">${buttonsHtml}</div>
          </div>`;

          await ChatMessage.create({
            speaker: { alias: data.speaker || ASSISTANT_DM_CONFIG.name },
            content: cardHtml,
            whisper: [game.user.id]
          });
          return;
        }

        // I. ダンジョン / 野外廃墟 / ワイルダネス シーン自動構築
        if (data.action === "create_dungeon_scene") {
          ui.notifications.info("AI GM Bridge: シーンと空間演出を構築中...");
          const gridSize = data.grid_size || 100;
          const sceneWidth = data.grid_width * gridSize;
          const sceneHeight = data.grid_height * gridSize;
          const isRuinsOrWild = data.scene_mode === "ruins" || data.scene_mode === "wilderness";

          const sceneData = {
            name: data.name || "地下迷宮",
            width: sceneWidth,
            height: sceneHeight,
            padding: 0,
            background: { src: data.background_src },
            initialLevel: "defaultLevel0000",
            levels: [{
              _id: "defaultLevel0000",
              name: isRuinsOrWild ? "地上・野外" : "地下1階",
              background: { src: data.background_src }
            }],
            grid: { size: gridSize, distance: 5, units: "ft" },
            darkness: isRuinsOrWild ? 0.35 : 0.75,
            globalLight: { enabled: data.scene_mode === "wilderness" },
            navigation: true
          };

          const newScene = await Scene.create(sceneData);
          const dims = newScene.dimensions ?? (newScene.getDimensions ? newScene.getDimensions() : {});
          const offsetX = dims.sceneX ?? 0;
          const offsetY = dims.sceneY ?? 0;

          const firstLevel = newScene.levels?.get("defaultLevel0000") || newScene.levels?.contents?.[0];
          if (firstLevel && (!firstLevel.background?.src || firstLevel.background.src !== data.background_src)) {
            await newScene.updateEmbeddedDocuments("Level", [{
              _id: firstLevel.id,
              background: { src: data.background_src }
            }]);
          }

          let actualWalls = 0;
          if (data.walls && data.walls.length > 0) {
            const wallDocs = data.walls.map(w => {
              const wallType = w[4] || "normal";
              const isLowWall = wallType === "low_wall" || wallType === "rubble";
              const isDoor = wallType === "door";
              const isSecret = wallType === "secret_door";
              return {
                c: [w[0] + offsetX, w[1] + offsetY, w[2] + offsetX, w[3] + offsetY],
                move: CONST.WALL_MOVEMENT_TYPES?.NORMAL ?? 20,
                sight: isLowWall ? (CONST.EDGE_SENSE_TYPES?.NONE ?? 0) : (CONST.EDGE_SENSE_TYPES?.NORMAL ?? 20),
                light: isLowWall ? (CONST.EDGE_SENSE_TYPES?.NONE ?? 0) : (CONST.EDGE_SENSE_TYPES?.NORMAL ?? 20),
                sound: CONST.EDGE_SENSE_TYPES?.NORMAL ?? 20,
                door: isDoor ? 1 : (isSecret ? 2 : 0)
              };
            });
            await newScene.createEmbeddedDocuments("Wall", wallDocs);
            actualWalls = wallDocs.length;
          }

          const lights = [];
          if (data.start_pos) {
            lights.push({
              x: data.start_pos[0] * gridSize + gridSize / 2 + offsetX,
              y: data.start_pos[1] * gridSize + gridSize / 2 + offsetY,
              config: { bright: 20, dim: 40, color: "#2ecc71", alpha: 0.45, animation: { type: "pulse", speed: 3, intensity: 3 } }
            });
          }
          if (data.goal_pos) {
            lights.push({
              x: data.goal_pos[0] * gridSize + gridSize / 2 + offsetX,
              y: data.goal_pos[1] * gridSize + gridSize / 2 + offsetY,
              config: { bright: 20, dim: 40, color: "#e74c3c", alpha: 0.45, animation: { type: "torch", speed: 4, intensity: 4 } }
            });
          }
          if (data.rooms && data.rooms.length > 0) {
            for (const r of data.rooms) {
              const presetKey = r.light_preset || (r.safe_rest ? "bonfire_bright" : null);
              const preset = presetKey ? LIGHT_PRESETS[presetKey] : null;
              if (preset) {
                lights.push({
                  x: r.cx + offsetX,
                  y: r.cy + offsetY,
                  config: preset
                });
              }
            }
          }
          if (lights.length > 0) {
            await newScene.createEmbeddedDocuments("AmbientLight", lights);
          }

          const sounds = [];
          if (data.rooms && data.rooms.length > 0) {
            for (const r of data.rooms) {
              if (r.ambient_sound_src) {
                sounds.push({
                  x: r.cx + offsetX,
                  y: r.cy + offsetY,
                  radius: Math.max(r.grid_w || 8, r.grid_h || 8) * gridSize * 0.85,
                  path: r.ambient_sound_src,
                  repeat: true,
                  volume: 0.65,
                  walls: true,
                  easing: true
                });
              }
            }
          }
          if (sounds.length > 0) {
            await newScene.createEmbeddedDocuments("AmbientSound", sounds);
          }

          let noteCount = 0;
          let createdJournal = null;
          if (data.rooms && data.rooms.length > 0) {
            const pages = [];

            if (data.lore) {
              const lore = data.lore;
              pages.push({
                name: `📖 【全体伝承・大目標】${lore.floor_title || data.name}`,
                type: "text",
                title: { show: true, level: 1 },
                ownership: { default: CONST.DOCUMENT_OWNERSHIP_LEVELS?.OBSERVER ?? 2 },
                text: {
                  content: `
                  <div style="line-height: 1.6; font-size: 14px;">
                    <h2 style="color: #782e22; border-bottom: 2px solid #782e22;">🏰 ${lore.dungeon_name || data.name}</h2>
                    <p><strong>【歴史と背景】:</strong> ${lore.history_summary || lore.public_lore || ""}</p>
                    <p><strong>【フロアの成り立ち】:</strong> ${lore.floor_origin || ""}</p>
                    <section class="secret" style="background:#fff8e7; border-left:4px solid #e74c3c; padding:8px; margin-top:10px;">
                      <strong>🔒 DM秘匿 (中目標・逆転劇・勢力):</strong><br>
                      ${lore.dm_secrets || ""}
                    </section>
                  </div>`,
                  format: 1
                }
              });
            }

            for (const r of data.rooms) {
              const badge = r.safe_rest
                ? `<span style="background:#27ae60; color:#fff; padding:2px 6px; border-radius:3px; font-size:11px; margin-left:8px;">⛺ 安全な野営地</span>`
                : (r.role === "sub_goal" ? `<span style="background:#8e44ad; color:#fff; padding:2px 6px; border-radius:3px; font-size:11px; margin-left:8px;">🔑 中目標ギミック</span>` : "");

              const gmSecretHtml = r.gm_notes ? `
                <section class="secret" style="margin-top: 10px; background: #fff8e7; border-left: 4px solid #f39c12; padding: 8px 10px; font-size: 12px; color: #7f5200;">
                  <strong>🔒 GM専用ディレクション (判定・中目標・隣接リンク):</strong><br>${r.gm_notes}
                </section>` : "";

              pages.push({
                name: r.name,
                type: "text",
                title: { show: true, level: 1 },
                ownership: { default: CONST.DOCUMENT_OWNERSHIP_LEVELS?.OBSERVER ?? 2 },
                text: {
                  content: `
                  <div style="line-height: 1.6; font-size: 14px;">
                    <div style="font-size: 16px; font-weight: bold; color: #782e22; border-bottom: 2px solid #782e22; margin-bottom: 8px;">
                      ${r.name} ${badge}
                    </div>
                    <p style="font-style: italic;">${r.flavor}</p>
                    ${gmSecretHtml}
                  </div>`,
                  format: 1
                }
              });
            }

            const rootFolder = await getOrCreateAtlasFolder("【AI World Atlas】");
            const siteFolder = await getOrCreateAtlasFolder(ATLAS_CATEGORIES.site, rootFolder.id);

            createdJournal = await JournalEntry.create({
              name: `【探索録】${newScene.name}`,
              folder: siteFolder.id,
              ownership: { default: CONST.DOCUMENT_OWNERSHIP_LEVELS?.OBSERVER ?? 2 },
              pages: pages
            });

            if (createdJournal) {
              const pagesList = createdJournal.pages?.contents ?? [];
              const pageOffset = data.lore ? 1 : 0;
              const noteDocs = data.rooms.map((r, idx) => ({
                entryId: createdJournal.id,
                pageId: pagesList[idx + pageOffset]?.id ?? null,
                x: r.cx + offsetX,
                y: r.cy + offsetY,
                text: r.name,
                texture: { src: r.icon || "icons/svg/book.svg" },
                iconSize: 36,
                fontSize: 16
              }));
              await newScene.createEmbeddedDocuments("Note", noteDocs);
              noteCount = noteDocs.length;
              await newScene.setFlag(MODULE_ID, "rooms", data.rooms);
            }
          }

          setMascotState("speaking", `新シーン『${newScene.name}』が完成したよ！`, 6000);

          const journalBtnHtml = createdJournal ? `
            <button class="ai-gm-journal-open-btn" data-journal-id="${createdJournal.id}"
                    style="flex: 1; padding: 6px; background: #8e44ad; color: #fff; border: none; border-radius: 4px; cursor: pointer;">
              設定録を開く
            </button>` : "";

          const successCard = `
          <div style="border: 2px solid #27ae60; border-radius: 6px; padding: 10px; background: rgba(20, 30, 25, 0.92); color: #ecf0f1;">
            <div style="font-weight: bold; color: #2ecc71; font-size: 14px; margin-bottom: 6px;">
              🏰 新シーン『${newScene.name}』を生成しました！
            </div>
            <div style="font-size: 12px; margin-bottom: 8px; line-height: 1.4;">
              ・サイズ: ${data.grid_width}x${data.grid_height} マス / 区画数: ${data.room_count || noteCount} 区画<br>
              ・壁セグメント: ${actualWalls} 本 / 照明: ${lights.length} 箇所 / 3D音源: ${sounds.length} 箇所
            </div>
            <div style="display: flex; gap: 6px;">
              <button class="ai-gm-scene-jump-btn" data-scene-id="${newScene.id}"
                      style="flex: 1; padding: 6px; background: #27ae60; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-weight: bold;">
                全員で移動
              </button>
              <button class="ai-gm-scene-view-btn" data-scene-id="${newScene.id}"
                      style="flex: 1; padding: 6px; background: #34495e; color: #fff; border: none; border-radius: 4px; cursor: pointer;">
                GMのみ確認
              </button>
              ${journalBtnHtml}
            </div>
          </div>`;

          await ChatMessage.create({
            speaker: { alias: ASSISTANT_DM_CONFIG.name },
            content: successCard,
            whisper: [game.user.id]
          });
        }

      } catch (err) {
        console.error("AI GM Bridge | エラー:", err);
      }
    };

    ws.onclose = () => {
      setMascotState("offline", "", 2000, false);
      setTimeout(connect, 5000);
    };

    ws.onerror = () => {};
  }

  connect();

  // =========================================================================
  // 9. グローバルUIイベント監視 & コンバット手番検知 & トークン部屋進入検知
  // =========================================================================

  // A. チャット内ボタン（選択肢・シーン移動・ジャーナル展開）
  Hooks.on("renderChatMessage", (message, html) => {
    if (!game.user.isGM) return;
    const root = (html instanceof HTMLElement) ? html : (html[0] || html);
    if (!root || !root.querySelectorAll) return;

    root.querySelectorAll(".ai-gm-choice-btn").forEach(btn => {
      btn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        const value = btn.getAttribute("data-choice-value");
        const label = btn.getAttribute("data-choice-label") || btn.textContent.trim();
        const container = btn.closest(".ai-gm-choice-container");
        if (container) {
          container.querySelectorAll(".ai-gm-choice-btn").forEach(b => { b.disabled = true; b.style.opacity = "0.5"; });
        }
        btn.style.borderColor = "#2ecc71";
        btn.style.fontWeight = "bold";
        btn.style.opacity = "1.0";
        sendWsPayload({ event: "ai_choice", user: game.user.name, value, label });
      });
    });

    root.querySelectorAll(".ai-gm-scene-jump-btn").forEach(btn => {
      btn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        const targetScene = game.scenes.get(btn.getAttribute("data-scene-id"));
        if (targetScene) await targetScene.activate();
      });
    });

    root.querySelectorAll(".ai-gm-scene-view-btn").forEach(btn => {
      btn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        const targetScene = game.scenes.get(btn.getAttribute("data-scene-id"));
        if (targetScene) await targetScene.view();
      });
    });

    root.querySelectorAll(".ai-gm-journal-open-btn").forEach(btn => {
      btn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        const targetJournal = game.journal.get(btn.getAttribute("data-journal-id"));
        if (targetJournal) targetJournal.sheet.render(true);
      });
    });
  });

  // B. ジャーナル内・チャット内のBGM切り替えボタン & NPCスキル判定ボタン（イベント委譲）
  document.body.addEventListener("click", async (ev) => {
    // 1. 集落BGMワンクリック切り替え
    const bgmBtn = ev.target.closest?.(".ai-bgm-switch-btn");
    if (bgmBtn && game.user?.isGM) {
      ev.preventDefault();
      const settlementName = bgmBtn.getAttribute("data-settlement");
      const mood = bgmBtn.getAttribute("data-mood");
      if (settlementName && mood) {
        await switchSettlementBgm(settlementName, mood);
      }
      return;
    }

    // 2. NPC会話のスキル判定挑戦ボタン
    const checkBtn = ev.target.closest?.(".ai-npc-check-btn");
    if (checkBtn) {
      ev.preventDefault();
      checkBtn.disabled = true;
      checkBtn.style.opacity = "0.6";
      const npcId = checkBtn.getAttribute("data-npc-id");
      const skill = checkBtn.getAttribute("data-skill") || "説得";
      const dc = parseInt(checkBtn.getAttribute("data-dc") || "13", 10);
      const hint = checkBtn.getAttribute("data-hint") || "";

      const roll = new Roll("1d20 + 3");
      await roll.evaluate();
      const success = roll.total >= dc;
      await roll.toMessage({
        speaker: { alias: `${game.user.name} (${skill}判定: 目標DC${dc})` },
        flavor: success
          ? `<b style="color:#27ae60;">✨ 判定成功！ (${roll.total} >= ${dc})</b>`
          : `<b style="color:#c0392b;">💥 判定失敗… (${roll.total} < ${dc})</b>`
      });

      const npcToken = game.scenes?.active?.tokens?.get(npcId);
      if (npcToken && game.user?.isGM) {
        const listeners = collectListeningNpcs(npcToken);
        sendWsPayload({
          event: "npc_dialogue_request",
          speaker_name: game.user.name,
          speaker_token_id: null,
          player_input: `（${skill}を試みて本音を引き出そうとした）`,
          room_name: game.scenes?.active?.name || "現場",
          room_flavor: "",
          listening_npcs: listeners,
          check_result_context: success
            ? `【${skill}判定に成功(出目${roll.total}/DC${dc})】NPCは警戒を解き、隠していた秘密（${hint}）を具体的に打ち明けること！`
            : `【${skill}判定に失敗(出目${roll.total}/DC${dc})】NPCはさらに警戒し、話題を逸らすか態度を硬化させること。`
        });
      }
    }
  });

  // C. コンバットトラッカーの手番更新時にNPCなら自律戦闘行動をトリガー
  Hooks.on("updateCombat", (combat, updateData) => {
    if (!game.user.isGM || !combat.started) return;
    if (updateData.turn === undefined && updateData.round === undefined) return;

    const combatant = combat.combatant;
    if (!combatant || !combatant.token) return;
    const isPC = combatant.actor?.type === "character" || combatant.actor?.hasPlayerOwner;
    if (!isPC) {
      const perception = extractNpcPerception(combatant.token);
      if (perception) {
        setMascotState("thinking", `${combatant.token.name}の戦術を思考中…`, 8000);
        sendWsPayload({ event: "npc_turn_request", perception });
      }
    }
  });

  // D. トークン移動による部屋進入検知 (room_entered 自動音声ナレーション)
  const lastRoomByToken = new Map();

  Hooks.on("updateToken", (tokenDoc, change) => {
    if (!game.user.isGM) return;
    if (change.x === undefined && change.y === undefined) return;

    const scene = tokenDoc.parent;
    if (!scene) return;
    const rooms = scene.getFlag(MODULE_ID, "rooms");
    if (!Array.isArray(rooms) || rooms.length === 0) return;

    const dims = scene.dimensions ?? (scene.getDimensions ? scene.getDimensions() : {});
    const offsetX = dims.sceneX ?? 0;
    const offsetY = dims.sceneY ?? 0;
    const gridSize = scene.grid?.size || 100;

    const tx = ((change.x ?? tokenDoc.x) - offsetX) / gridSize;
    const ty = ((change.y ?? tokenDoc.y) - offsetY) / gridSize;

    for (const r of rooms) {
      if (tx >= r.grid_x && tx < r.grid_x + r.grid_w && ty >= r.grid_y && ty < r.grid_y + r.grid_h) {
        const prevRoom = lastRoomByToken.get(tokenDoc.id);
        if (prevRoom !== r.name) {
          lastRoomByToken.set(tokenDoc.id, r.name);
          setMascotState("speaking", `📍 ${r.name}`, 5000);
          sendWsPayload({
            event: "room_entered",
            token_name: tokenDoc.name,
            room_name: r.name,
            flavor: r.flavor || ""
          });
        }
        return;
      }
    }
    lastRoomByToken.delete(tokenDoc.id);
  });
});
