// 4 段階シナリオビルダー画面 (地域 → 集落 → 旅路 → 遺跡・迷宮) と地図帳ジャーナル (Foundry 依存)
//
// - 各段階の下書きはサーバー (builder_generate_stage) が作り、builder_stage_result で返ってくる
// - 下書きは GM が画面で直してから【AI World Atlas】ジャーナルに保存し、次の段階の文脈として使う
// - 確定事項・伏線・カード・旅路の seed とランドマークは atlas_meta flag に保存する (L11: リロードで消えない)
// 純粋関数は scripts/lib/atlas.js (tests/js/atlas.test.js)

import { escapeHtml } from "./lib/html.js";
import {
  ATLAS_CATEGORIES,
  ATLAS_ROOT_FOLDER,
  actorsContext,
  atlasEntryContext,
  atlasIndex,
  buildAtlasPages,
  formatBuilderProgress,
  formatStageDraft,
  htmlToText,
  joinLines,
  recentCardIds,
  resolveParentIds,
  splitLines,
  STAGE_CATEGORY,
} from "./lib/atlas.js";

const MODULE_ID = "ai-gm-bridge";
const REFERENCE_FLAG = "builder_reference_ids";
const MAX_REFERENCE_TEXT = 1500;

const TABS = [
  { id: "region", stage: "region", label: "🌍 1. 地域", defaultName: "新しい地域" },
  { id: "settlement", stage: "settlement", label: "🏘️ 2. 集落", defaultName: "新しい集落" },
  { id: "wilderness", stage: "wilderness", label: "🌲 3. 旅路", defaultName: "新しい旅路" },
  { id: "site", stage: "site_lore", label: "🏰 4. 遺跡・迷宮", defaultName: "新しい遺跡" },
];
// シーン構築は 2 通り: スクリプト (ジェネレータの地図と仮の区画だけ。文面は DM が書く) / AI (区画の情景も AI が書く)
const BUILD_BUTTONS = `
    <button type="button" class="aib-build aib-build-script" data-action="build" data-use-ai="false"><span>🛠️ 保存してシーン構築</span> <span>(スクリプト)</span></button>
    <button type="button" class="aib-build" data-action="build" data-use-ai="true"><span>🤖 保存してシーン構築</span> <span>(AI)</span></button>`;
const SETTLEMENT_SCALES = ["辺境の開拓村・小村", "街道の宿場町・交易町", "鉱山街・職人街", "城塞都市・港町"];

const state = {
  send: null,
  app: null,
  root: null,
  activeTab: "region",
  // tab -> { name, prompt, public, dm, canon, threads, ...選択値 }
  drafts: {},
  // tab -> 下書きに付随する構造化データ (cards, seed, map, landmarks, sub_objectives, audio_prompts ...)
  extras: {},
  // tab -> 今開いている (保存先の) 地図帳エントリ id
  openIds: {},
  // この画面で引いたカード (保存前でも次の抽選で避ける)
  sessionCards: [],
  // tab -> 作業中の種類 ("draft": 下書き作成 / "build": シーン構築)。作業中でなければ偽
  pending: {},
  // タイムアウトしたら GM に確認せずに自動で延長する (既定 OFF。サーバー側で上限 5 回)
  autoExtend: false,
  // tab -> 下書き作成・シーン構築の進捗 (builder_progress と受け取った時刻)
  progress: {},
  // tab -> 依頼した時刻 (全体の経過秒を出すため)
  startedAt: {},
  progressTimer: null,
};

/** bridge.js から WebSocket 送信関数を受け取る */
export function setBuilderSender(send) {
  state.send = send;
}

// -------------------------------------------------------------------------
// 地図帳ジャーナル
// -------------------------------------------------------------------------
function atlasMeta(doc) {
  return doc?.getFlag?.(MODULE_ID, "atlas_meta") ?? doc?.flags?.[MODULE_ID]?.atlas_meta ?? null;
}

function atlasEntries(categoryKey = null) {
  const subFolders = new Set(Object.values(ATLAS_CATEGORIES));
  return (game.journal?.contents ?? [])
    .map(doc => ({ id: doc.id, name: doc.name, meta: atlasMeta(doc), doc }))
    .filter(e => (e.meta?.categoryKey ? true : subFolders.has(e.doc.folder?.name)))
    .map(e => ({ ...e, meta: e.meta ?? { categoryKey: categoryOfFolder(e.doc.folder?.name) } }))
    .filter(e => !categoryKey || e.meta.categoryKey === categoryKey);
}

function categoryOfFolder(folderName) {
  return Object.entries(ATLAS_CATEGORIES).find(([, name]) => name === folderName)?.[0] ?? null;
}

function entryPlain(doc) {
  if (!doc) return null;
  return {
    id: doc.id,
    name: doc.name,
    meta: atlasMeta(doc) ?? {},
    pages: (doc.pages?.contents ?? []).map(p => ({
      kind: p.flags?.[MODULE_ID]?.atlas_page ?? null,
      name: p.name,
      content: p.text?.content ?? "",
      ownershipDefault: p.ownership?.default,
    })),
  };
}

async function getOrCreateFolder(name, parentId = null) {
  let folder = game.folders.find(f => f.type === "JournalEntry" && f.name === name && (f.folder?.id ?? null) === parentId);
  if (!folder) {
    folder = await Folder.create({ name, type: "JournalEntry", folder: parentId, color: "#782e22" });
  }
  return folder;
}

async function saveAtlasEntry(categoryKey, title, fields, meta, existingId) {
  const root = await getOrCreateFolder(ATLAS_ROOT_FOLDER);
  const sub = await getOrCreateFolder(ATLAS_CATEGORIES[categoryKey], root.id);
  const levels = CONST.DOCUMENT_OWNERSHIP_LEVELS ?? {};
  const pages = buildAtlasPages(fields, {
    ownership: { NONE: levels.NONE ?? 0, OBSERVER: levels.OBSERVER ?? 2 },
    html: CONST.JOURNAL_ENTRY_PAGE_FORMATS?.HTML ?? 1,
  }, MODULE_ID);
  const flagData = { ...meta, categoryKey, updatedAt: Date.now() };

  let entry = existingId ? game.journal.get(existingId) : null;
  entry ??= game.journal.find(j => j.name === title && j.folder?.id === sub.id) ?? null;
  if (entry) {
    const oldIds = entry.pages.contents.map(p => p.id);
    if (oldIds.length) await entry.deleteEmbeddedDocuments("JournalEntryPage", oldIds);
    await entry.update({ name: title, folder: sub.id });
    await entry.createEmbeddedDocuments("JournalEntryPage", pages);
    await entry.setFlag(MODULE_ID, "atlas_meta", flagData);
  } else {
    entry = await JournalEntry.create({
      name: title,
      folder: sub.id,
      pages,
      ownership: { default: levels.OBSERVER ?? 2 },
      flags: { [MODULE_ID]: { atlas_meta: flagData } },
    });
  }
  ui.notifications.info(`地図帳『${title}』を【${ATLAS_CATEGORIES[categoryKey]}】に保存しました。`);
  return entry;
}

// -------------------------------------------------------------------------
// プロンプトの文脈 (ワールド資料・上位段階・目次・固有名詞)
// -------------------------------------------------------------------------
function referenceIds() {
  return game.user?.getFlag?.(MODULE_ID, REFERENCE_FLAG) ?? [];
}

function worldContext() {
  const references = referenceIds()
    .map(id => game.journal?.get(id))
    .filter(Boolean)
    .map(doc => ({
      name: doc.name,
      text: (doc.pages?.contents ?? []).map(p => htmlToText(p.text?.content)).join("\n").slice(0, MAX_REFERENCE_TEXT),
    }));
  return {
    title: game.world?.title ?? "",
    description: htmlToText(game.world?.description ?? ""),
    references,
  };
}

function lookupEntry(id) {
  const doc = id ? game.journal?.get(id) : null;
  return doc ? { id, meta: atlasMeta(doc) } : null;
}

function stageContext(parentIds, selfId = null) {
  const entries = atlasEntries();
  return {
    world: worldContext(),
    parents: parentIds.map(id => atlasEntryContext(entryPlain(game.journal.get(id)))).filter(Boolean),
    atlas_index: atlasIndex(entries, [...parentIds, selfId]),
    actor_names: (game.actors?.contents ?? []).map(a => a.name).slice(0, 60),
    recent_cards: recentCardIds(entries, state.sessionCards),
  };
}

function parentPicks(tabId) {
  const d = state.drafts[tabId] ?? {};
  if (tabId === "settlement") return { region: d.parentRegion };
  if (tabId === "wilderness") return { region: d.parentRegion, settlement: d.parentSettlement };
  if (tabId === "site") return { settlement: d.parentSettlement, wilderness: d.parentWilderness };
  return {};
}

function selectedActors(ids) {
  const actors = (game.actors?.contents ?? []).filter(a => ids.includes(a.id));
  return actorsContext(actors.map(a => ({
    name: a.name,
    type: a.type,
    hasPlayerOwner: a.hasPlayerOwner,
    bio: a.system?.details?.biography?.value ?? "",
    race: a.system?.details?.race?.name ?? a.system?.details?.race ?? "",
    background: a.system?.details?.background?.name ?? a.system?.details?.background ?? "",
    role: a.system?.details?.type?.value ?? "",
  })));
}

// -------------------------------------------------------------------------
// 画面
// -------------------------------------------------------------------------
function options(entries, selectedId, emptyLabel) {
  const head = `<option value="">${escapeHtml(emptyLabel)}</option>`;
  return head + entries.map(e => `<option value="${escapeHtml(e.id)}" ${e.id === selectedId ? "selected" : ""}>${escapeHtml(e.name)}</option>`).join("");
}

function field(label, control, cls = "") {
  return `<div class="aib-field ${cls}"><span class="aib-label">${label}</span>${control}</div>`;
}

function select(name, inner) {
  return `<select data-field="${name}">${inner}</select>`;
}

function checkList(title, items, open = false) {
  const count = items.filter(i => i.checked).length;
  const boxes = items.map(i => `<label class="aib-check"><input type="checkbox" ${i.attr}="${escapeHtml(i.id)}" ${i.checked ? "checked" : ""}><span>${escapeHtml(i.label)}</span></label>`).join("");
  return `
    <details class="aib-box" ${open ? "open" : ""}>
      <summary>${title} (<span class="aib-count">${count}</span> 件選択中)</summary>
      <div class="aib-checks">${boxes || "<span>候補がありません</span>"}</div>
    </details>`;
}

function textFields(d, labels) {
  return `
    <div class="aib-grid aib-two">
      ${field("名前", `<input type="text" data-field="name" value="${escapeHtml(d.name)}" placeholder="空欄なら AI の案を使います">`)}
      ${field("GM 指示 (最優先)", `<input type="text" data-field="prompt" value="${escapeHtml(d.prompt)}" placeholder="${escapeHtml(labels.prompt)}">`)}
    </div>
    <div class="aib-grid aib-two">
      ${field("📖 プレイヤー公開情報", `<textarea data-field="public" class="aib-tall">${escapeHtml(d.public)}</textarea>`, "aib-pub")}
      ${field("🔒 DM 専用情報", `<textarea data-field="dm" class="aib-tall">${escapeHtml(d.dm)}</textarea>`, "aib-dm")}
    </div>
    <div class="aib-grid aib-two">
      ${field("📌 確定事項 (後で変えない事実。1 行 1 件)", `<textarea data-field="canon" class="aib-short">${escapeHtml(d.canon)}</textarea>`, "aib-canon")}
      ${field("🧵 未回収の伏線 (次の段階が回収)", `<textarea data-field="threads" class="aib-short">${escapeHtml(d.threads)}</textarea>`, "aib-canon")}
    </div>`;
}

function panelHtml(tab) {
  const d = state.drafts[tab.id] ?? {};
  const settings = [
    field("既存の地図帳を開く", select("open", options(atlasEntries(STAGE_CATEGORY[tab.stage]), state.openIds[tab.id], "(新規作成)"))),
  ];
  let extraBlock = "";
  let labels = { prompt: "雰囲気・地形・テーマなど" };
  let buttons = `
    <button type="button" class="aib-gen" data-action="generate">🤖 AI で下書き</button>
    <button type="button" class="aib-save" data-action="save">💾 地図帳に保存</button>`;

  if (tab.id === "settlement") {
    settings.push(
      field("上位の地域", select("parentRegion", options(atlasEntries("region"), d.parentRegion, "(なし: ワールド資料のみ)"))),
      field("規模", select("scale", SETTLEMENT_SCALES.map(s => `<option ${s === d.scale ? "selected" : ""}>${escapeHtml(s)}</option>`).join(""))),
    );
    labels = { prompt: "生業・雰囲気など (空欄なら地域の資源から)" };
  } else if (tab.id === "wilderness") {
    settings.push(
      field("上位の地域", select("parentRegion", options(atlasEntries("region"), d.parentRegion, "(集落の地域を使う)"))),
      field("出発地の集落", select("parentSettlement", options(atlasEntries("settlement"), d.parentSettlement, "(なし)"))),
    );
    labels = { prompt: "道中の地形や中継地のイメージ" };
    buttons += BUILD_BUTTONS;
  } else if (tab.id === "site") {
    const actors = game.actors?.contents ?? [];
    const pcs = actors.filter(a => a.type === "character" || a.hasPlayerOwner);
    const npcs = actors.filter(a => a.type === "npc" && !a.hasPlayerOwner);
    const checked = new Set(d.actorIds ?? pcs.map(a => a.id));
    const items = [
      ...pcs.map(a => ({ id: a.id, label: `👤 ${a.name}`, checked: checked.has(a.id), attr: "data-actor" })),
      ...npcs.map(a => ({ id: a.id, label: `🎭 ${a.name}`, checked: checked.has(a.id), attr: "data-actor" })),
    ];
    settings.push(
      field("上位の集落", select("parentSettlement", options(atlasEntries("settlement"), d.parentSettlement, "(なし)"))),
      field("道中の旅路", select("parentWilderness", options(atlasEntries("wilderness"), d.parentWilderness, "(なし)"))),
      field("空間の種類", select("siteMode", `
        <option value="ruins" ${d.siteMode !== "underground" ? "selected" : ""}>⛰️ 地上の廃墟</option>
        <option value="underground" ${d.siteMode === "underground" ? "selected" : ""}>🕳️ 地下迷宮</option>`)),
      field("規模", select("size", `
        <option value="small" ${d.size !== "medium" ? "selected" : ""}>小 (100x100)</option>
        <option value="medium" ${d.size === "medium" ? "selected" : ""}>中 (500x500・重い)</option>`)),
      field(`部屋密度 <span data-value-for="density">${escapeHtml(d.density ?? "0.5")}</span>`, `<input type="range" data-field="density" min="0.2" max="1.0" step="0.1" value="${escapeHtml(d.density ?? "0.5")}">`),
      field(`周回路 <span data-value-for="loop">${escapeHtml(d.loop ?? "0.25")}</span>`, `<input type="range" data-field="loop" min="0" max="0.5" step="0.05" value="${escapeHtml(d.loop ?? "0.25")}">`),
    );
    extraBlock = checkList("🎯 物語に絡める PC / NPC", items);
    labels = { prompt: "シチュエーションや仕掛けの要望" };
    buttons += BUILD_BUTTONS;
  }

  const busy = state.pending[tab.id] ? `<p class="aib-busy" data-busy-for="${tab.id}">${escapeHtml(progressText(tab.id))}</p>` : "";
  // 作業中は AI を呼ぶボタン (下書き・シーン構築) を押せなくする (二重の依頼を防ぐ)
  if (state.pending[tab.id]) buttons = buttons.replace(/data-action="(generate|build)"/g, 'data-action="$1" disabled');
  return `
    <section class="aib-panel ${state.activeTab === tab.id ? "active" : ""}" data-tab="${tab.id}">
      <div class="aib-grid aib-settings">${settings.join("")}</div>
      ${extraBlock}
      ${textFields(d, labels)}
      ${busy}
      <div class="aib-actions">${buttons}</div>
    </section>`;
}

function dialogHtml() {
  const refs = new Set(referenceIds());
  const atlasIds = new Set(atlasEntries().map(e => e.id));
  const refItems = (game.journal?.contents ?? [])
    .filter(j => !atlasIds.has(j.id) && !j.name.startsWith("【探索録】"))
    .map(j => ({ id: j.id, label: j.name, checked: refs.has(j.id), attr: "data-reference" }));
  return `
  <div class="aib">
    ${checkList("📚 ワールド資料として AI に渡すジャーナル", refItems)}
    <label class="aib-auto-extend"><input type="checkbox" data-auto-extend ${state.autoExtend ? "checked" : ""}> ⏱️ タイムアウトしたら自動で延長する (最大 5 回)</label>
    <div class="aib-tabs">
      ${TABS.map(t => `<button type="button" class="aib-tab ${state.activeTab === t.id ? "active" : ""}" data-target="${t.id}">${t.label}</button>`).join("")}
    </div>
    ${TABS.map(panelHtml).join("")}
  </div>`;
}

function panel(tabId) {
  return state.root?.querySelector?.(`.aib-panel[data-tab="${tabId}"]`) ?? null;
}

/** 画面の入力値を state.drafts に取り込む (再描画しても入力が消えないように) */
function captureDrafts() {
  if (!state.root?.isConnected) return;
  for (const tab of TABS) {
    const p = panel(tab.id);
    if (!p) continue;
    const d = { ...(state.drafts[tab.id] ?? {}) };
    p.querySelectorAll("[data-field]").forEach(el => {
      if (el.dataset.field !== "open") d[el.dataset.field] = el.value;
    });
    if (tab.id === "site") {
      d.actorIds = [...p.querySelectorAll("[data-actor]")].filter(el => el.checked).map(el => el.dataset.actor);
    }
    state.drafts[tab.id] = d;
  }
}

async function openDialog(content) {
  const title = "🧙 AI シナリオビルダー (地域 → 集落 → 旅路 → 遺跡)";
  const DialogV2 = foundry?.applications?.api?.DialogV2;
  if (DialogV2) {
    const app = new DialogV2({
      // 見た目は styles/builder.css (module.json の styles)。DialogV2 の content に <style> を書いても反映されない
      classes: ["ai-gm-builder"],
      window: { title, resizable: true },
      position: { width: 860, height: "auto" },
      content,
      buttons: [{ action: "close", label: "閉じる", default: true }],
    });
    await app.render({ force: true });
    return { app, root: app.element };
  }
  // v11: 旧 Dialog
  return new Promise(resolve => {
    const app = new Dialog({
      title,
      content,
      buttons: { close: { label: "閉じる" } },
      render: html => resolve({ app, root: html instanceof HTMLElement ? html : html[0] }),
    }, { classes: ["dialog", "ai-gm-builder"], width: 860, height: "auto", resizable: true });
    app.render(true);
  });
}

/**
 * シナリオビルダー画面を開く (開いていれば描き直す)。
 * capture: 描き直す前に今の入力値を取り込むか (下書きや保存済みエントリで入力値を置き換えた直後は false)
 */
export async function openScenarioBuilder(tabId = null, { capture = true } = {}) {
  if (capture) captureDrafts();
  if (tabId) state.activeTab = tabId;
  const old = state.app;
  state.app = null;
  state.root = null;
  if (old) await old.close?.();
  const { app, root } = await openDialog(dialogHtml());
  state.app = app;
  state.root = root;
  bindEvents(root);
}

function bindEvents(root) {
  // DialogV2 は内容を <form> で包むため、入力欄の Enter で画面が閉じないようにする
  root.addEventListener("keydown", ev => {
    if (ev.key === "Enter" && ev.target?.tagName === "INPUT") ev.preventDefault();
  });
  root.querySelectorAll('input[type="range"]').forEach(el => el.addEventListener("input", () => {
    const label = el.closest(".aib-field")?.querySelector(`[data-value-for="${el.dataset.field}"]`);
    if (label) label.textContent = el.value;
  }));
  root.querySelectorAll(".aib-box").forEach(box => box.addEventListener("change", () => {
    const count = box.querySelector(".aib-count");
    if (count) count.textContent = String(box.querySelectorAll('input[type="checkbox"]:checked').length);
  }));
  root.querySelectorAll(".aib-tab").forEach(btn => btn.addEventListener("click", () => {
    state.activeTab = btn.dataset.target;
    root.querySelectorAll(".aib-tab").forEach(b => b.classList.toggle("active", b === btn));
    root.querySelectorAll(".aib-panel").forEach(p => p.classList.toggle("active", p.dataset.tab === state.activeTab));
  }));
  root.querySelector("[data-auto-extend]")?.addEventListener("change", ev => {
    state.autoExtend = ev.target.checked;
  });
  root.querySelectorAll("[data-reference]").forEach(el => el.addEventListener("change", async () => {
    const ids = [...root.querySelectorAll("[data-reference]")].filter(c => c.checked).map(c => c.dataset.reference);
    await game.user?.setFlag?.(MODULE_ID, REFERENCE_FLAG, ids);
  }));
  for (const tab of TABS) {
    const p = panel(tab.id);
    p?.querySelector('[data-field="open"]')?.addEventListener("change", ev => loadEntry(tab, ev.target.value));
    p?.querySelectorAll("[data-action]").forEach(btn => btn.addEventListener("click", () => {
      captureDrafts();
      const action = btn.dataset.action;
      if (action === "generate") requestDraft(tab);
      else if (action === "save") saveTab(tab).then(() => openScenarioBuilder(tab.id));
      else if (action === "build") buildScene(tab, btn.dataset.useAi !== "false");
    }));
  }
}

// -------------------------------------------------------------------------
// 操作
// -------------------------------------------------------------------------
function loadEntry(tab, id) {
  captureDrafts();
  const doc = id ? game.journal.get(id) : null;
  if (!doc) {
    delete state.openIds[tab.id];
    return;
  }
  const plain = entryPlain(doc);
  const meta = plain.meta;
  const text = kind => plain.pages.filter(p => (p.kind ?? (p.ownershipDefault === 0 ? "dm" : "public")) === kind).map(p => htmlToText(p.content)).join("\n\n");
  state.openIds[tab.id] = id;
  state.drafts[tab.id] = {
    ...(state.drafts[tab.id] ?? {}),
    name: doc.name,
    public: text("public"),
    dm: text("dm"),
    canon: joinLines(meta.canon),
    threads: joinLines(meta.open_threads),
    parentRegion: meta.parent_ids?.region ?? "",
    parentSettlement: meta.parent_ids?.settlement ?? "",
    parentWilderness: meta.parent_ids?.wilderness ?? "",
    siteMode: meta.site_mode ?? state.drafts[tab.id]?.siteMode,
  };
  state.extras[tab.id] = {
    cards: meta.cards ?? [],
    seed: meta.seed ?? null,
    map: meta.map ?? null,
    landmarks: meta.landmarks ?? [],
    sub_objectives: meta.sub_objectives ?? [],
    audio_prompts: meta.audio_prompts ?? {},
  };
  openScenarioBuilder(tab.id, { capture: false });
}

function gmTypedName(tabId) {
  const name = state.drafts[tabId]?.name?.trim() ?? "";
  return name && name !== state.extras[tabId]?.aiName ? name : "";
}

function requestDraft(tab) {
  if (!state.send) {
    ui.notifications.warn("AI GM Bridge: Python サーバー (8765) に接続されていません。");
    return;
  }
  const d = state.drafts[tab.id] ?? {};
  const parentIds = resolveParentIds(parentPicks(tab.id), lookupEntry);
  const payload = {
    event: "builder_generate_stage",
    stage: tab.stage,
    user_prompt: d.prompt ?? "",
    // GM が自分で付けた名前は AI にもそのまま使わせる (AI が前回付けた名前は付け直してよい)
    fixed_name: gmTypedName(tab.id),
    auto_extend: state.autoExtend,
    ...stageContext(parentIds, state.openIds[tab.id]),
  };
  if (tab.id === "settlement") payload.settlement_scale = d.scale || SETTLEMENT_SCALES[0];
  if (tab.id === "site") {
    payload.site_mode = d.siteMode || "ruins";
    payload.size_key = d.size || "small";
    payload.actors_context = selectedActors(d.actorIds ?? []);
  }
  if (state.send(payload)) {
    state.pending[tab.id] = "draft";
    state.progress[tab.id] = null;
    state.startedAt[tab.id] = Date.now();
    ui.notifications.info(`AI GM Bridge: ${tab.label.replace(/^\S+\s*\d+\.\s*/, "")}の下書きを依頼しました。`);
    openScenarioBuilder(tab.id);
  }
}

function progressText(tabId) {
  const p = state.progress[tabId];
  const progress = p ? { ...p.data, auto_extend: state.autoExtend } : null;
  const total = state.startedAt[tabId] ? (Date.now() - state.startedAt[tabId]) / 1000 : null;
  return formatBuilderProgress(progress, p ? (Date.now() - p.at) / 1000 : 0, state.pending[tabId] || "draft", total);
}

/** 「作成中」の行だけを書き換える (画面全体を描き直すと入力中の文字が消えるため) */
function refreshProgressLines() {
  let busy = false;
  for (const tab of TABS) {
    if (!state.pending[tab.id]) continue;
    busy = true;
    const el = state.root?.querySelector?.(`[data-busy-for="${tab.id}"]`);
    if (el) el.textContent = progressText(tab.id);
  }
  // 作成中のタブが無くなったら、毎秒の数え進めを止める
  if (!busy && state.progressTimer) {
    clearInterval(state.progressTimer);
    state.progressTimer = null;
  }
}

/** サーバーから届いた下書き作成・シーン構築の進捗 (工程・完了数・残り秒) を「作成中」の行に出す */
export async function handleBuilderProgress(data) {
  const tab = TABS.find(t => t.stage === data.stage);
  if (!tab || !state.pending[tab.id]) return;
  if (data.finished) {
    // シーン構築が終わった (失敗も含む)。「構築中」の行を消して描き直す
    if (state.pending[tab.id] !== "build") return;
    state.pending[tab.id] = false;
    state.progress[tab.id] = null;
    refreshProgressLines();
    if (state.root?.isConnected) await openScenarioBuilder(tab.id);
    return;
  }
  state.progress[tab.id] = { data, at: Date.now() };
  refreshProgressLines();
  // 次の進捗が届くまでの間も、経過と残り秒を 1 秒ごとに数え進める
  state.progressTimer ??= setInterval(refreshProgressLines, 1000);
}

/** サーバーから届いた下書きを画面に反映する (画面が閉じていれば開き直す) */
export async function handleBuilderStageResult(data) {
  const tab = TABS.find(t => t.stage === data.stage);
  if (!tab) return;
  state.pending[tab.id] = false;
  state.progress[tab.id] = null;
  const res = data.result ?? {};
  const draft = formatStageDraft(data.stage, res);
  const cardIds = (data.cards ?? []).map(c => c.id).filter(Boolean);
  state.sessionCards.push(...cardIds);
  captureDrafts();
  const prev = state.drafts[tab.id] ?? {};
  const typedName = gmTypedName(tab.id);
  state.drafts[tab.id] = {
    ...prev,
    name: typedName || draft.name,
    public: draft.publicText,
    dm: draft.dmText,
    canon: draft.canonText,
    threads: draft.threadsText,
  };
  state.extras[tab.id] = {
    cards: cardIds,
    seed: data.seed ?? null,
    map: data.map ?? null,
    landmarks: res.landmarks ?? [],
    sub_objectives: res.dm_secrets?.sub_objectives ?? [],
    audio_prompts: res.audio_prompts ?? {},
    resolved_threads: res.resolved_threads ?? [],
    aiName: draft.name,
  };
  if (data.fallback) ui.notifications.warn("AI GM Bridge: AI の下書きを受け取れなかったため、骨組みを入れました。");
  else ui.notifications.info("AI GM Bridge: 下書きができました。確認・修正してから保存してください。");
  if (res.consistency_notes) ui.notifications.warn(`整合性メモ: ${res.consistency_notes}`);
  await openScenarioBuilder(tab.id, { capture: false });
}

function tabMeta(tab) {
  const d = state.drafts[tab.id] ?? {};
  const x = state.extras[tab.id] ?? {};
  const picks = parentPicks(tab.id);
  const parentIds = resolveParentIds(picks, lookupEntry);
  const byCategory = Object.fromEntries(parentIds.map(id => [atlasMeta(game.journal.get(id))?.categoryKey, id]));
  return {
    parent_ids: { region: byCategory.region ?? null, settlement: byCategory.settlement ?? null, wilderness: byCategory.wilderness ?? null },
    canon: splitLines(d.canon),
    open_threads: splitLines(d.threads),
    cards: x.cards ?? [],
    seed: x.seed ?? null,
    map: x.map ?? null,
    landmarks: x.landmarks ?? [],
    sub_objectives: x.sub_objectives ?? [],
    audio_prompts: x.audio_prompts ?? {},
    site_mode: tab.id === "site" ? (d.siteMode || "ruins") : undefined,
  };
}

async function saveTab(tab) {
  const d = state.drafts[tab.id] ?? {};
  const title = d.name?.trim() || tab.defaultName;
  const meta = tabMeta(tab);
  const entry = await saveAtlasEntry(STAGE_CATEGORY[tab.stage], title, {
    publicText: d.public ?? "",
    dmText: d.dm ?? "",
    canon: meta.canon,
    openThreads: meta.open_threads,
  }, meta, state.openIds[tab.id]);
  state.openIds[tab.id] = entry.id;
  state.drafts[tab.id] = { ...d, name: title };
  return { entry, meta };
}

async function buildScene(tab, useAi = true) {
  if (!state.send) {
    ui.notifications.warn("AI GM Bridge: Python サーバー (8765) に接続されていません。");
    return;
  }
  const { entry, meta } = await saveTab(tab);
  const d = state.drafts[tab.id] ?? {};
  const parentIds = Object.values(meta.parent_ids).filter(Boolean);
  const ctx = stageContext(parentIds, entry.id);
  // 遺跡自身の確定事項も、区画の情景を書くときの前提にする
  ctx.parents.push({ category: STAGE_CATEGORY[tab.stage], name: entry.name, canon: meta.canon, open_threads: meta.open_threads });
  const payload = {
    event: "builder_build_scene",
    name: entry.name,
    user_prompt: d.prompt ?? "",
    public_lore: d.public ?? "",
    dm_secrets: d.dm ?? "",
    auto_extend: state.autoExtend,
    use_ai: useAi,
    ...ctx,
  };
  if (tab.id === "wilderness") {
    Object.assign(payload, { scene_mode: "wilderness", map: meta.map, landmarks: meta.landmarks });
  } else {
    Object.assign(payload, {
      scene_mode: d.siteMode || "ruins",
      size_key: d.size || "small",
      density: Number(d.density ?? 0.5),
      loop_ratio: Number(d.loop ?? 0.25),
      sub_objectives: meta.sub_objectives,
    });
  }
  if (state.send(payload)) {
    state.pending[tab.id] = "build";
    state.progress[tab.id] = null;
    state.startedAt[tab.id] = Date.now();
    ui.notifications.info(`AI GM Bridge: 『${entry.name}』のシーン構築を依頼しました。`);
  }
  await openScenarioBuilder(tab.id);
}
