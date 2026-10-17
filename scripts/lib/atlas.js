// シナリオビルダーの地図帳 (【AI World Atlas】ジャーナル) まわりの純粋関数 (Foundry 非依存)
//
// 地図帳エントリは 1 つの JournalEntry に 3 ページを持つ:
//   public: プレイヤー公開情報 / dm: DM 専用情報 / canon: 確定事項と未回収の伏線 (DM 専用)
// 確定事項・伏線・抽選したカード・旅路の seed とランドマークは atlas_meta flag にも構造化して保存し、
// 後の段階のプロンプトにはそちらを渡す (ページの文章は GM が自由に書き換えてよい)。

import { escapeHtml } from "./html.js";

export const ATLAS_ROOT_FOLDER = "【AI World Atlas】";

export const ATLAS_CATEGORIES = {
  region: "1_地域 (Regions)",
  settlement: "2_集落・街 (Settlements)",
  wilderness: "3_周辺・旅路 (Wilderness)",
  site: "4_遺跡・迷宮 (Sites)",
};

/** builder_generate_stage の stage -> 地図帳のカテゴリ */
export const STAGE_CATEGORY = {
  region: "region",
  settlement: "settlement",
  wilderness: "wilderness",
  site_lore: "site",
};

export const PAGE_KINDS = {
  public: "📖 プレイヤー公開情報",
  dm: "🔒 DM専用情報",
  canon: "📌 確定事項と伏線 (AI 引き継ぎ用)",
};

// 抽選の除外に使う、最近の地図帳エントリの数
export const RECENT_ENTRY_COUNT = 12;
// ジャーナルから読む文章の上限 (プロンプトの膨張防止。サーバー側でも切り詰める)
export const MAX_CONTEXT_TEXT = 2000;

/** 改行区切りのテキストを、空行と先頭の「・」「-」を除いた配列にする */
export function splitLines(text) {
  return String(text ?? "")
    .split(/\r?\n/)
    .map(line => line.trim().replace(/^[・\-]\s*/, "").trim())
    .filter(Boolean);
}

export function joinLines(list) {
  return (Array.isArray(list) ? list : []).map(v => `・${v}`).join("\n");
}

/** プレーンテキストを段落 HTML にする (AI テキストなので必ずエスケープ) */
export function textToParagraphsHtml(text) {
  return String(text ?? "")
    .split(/\n{2,}/)
    .map(block => block.trim())
    .filter(Boolean)
    .map(block => `<p>${escapeHtml(block).replace(/\r?\n/g, "<br>")}</p>`)
    .join("");
}

/** ジャーナルの HTML を、プロンプトに渡すプレーンテキストに戻す */
export function htmlToText(html) {
  return String(html ?? "")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/p>/gi, "\n\n")
    .replace(/<\/(li|h[1-6]|div)>/gi, "\n")
    .replace(/<li[^>]*>/gi, "・")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function section(title, body) {
  const text = String(body ?? "").trim();
  return text ? `【${title}】\n${text}` : "";
}

function sections(list) {
  return list.filter(Boolean).join("\n\n");
}

function designNotes(res) {
  const notes = [];
  if (Array.isArray(res.design_notes) && res.design_notes.length) notes.push(joinLines(res.design_notes));
  if (res.consistency_notes) notes.push(`整合性: ${res.consistency_notes}`);
  return notes.length ? section("設計メモ (AI)", notes.join("\n")) : "";
}

/** 旅路のランドマーク一覧 (DM 欄に表示するだけ。構築には構造化データを使う) */
export function landmarkSummary(landmarks) {
  return (Array.isArray(landmarks) ? landmarks : [])
    .map(l => `・#${(Number(l.id) || 0) + 1} ${l.title ?? ""}${l.safe_rest ? " (野営可)" : ""}: ${l.tts_narration ?? ""}`)
    .join("\n");
}

/**
 * builder_stage_result の下書きを、画面の入力欄に入れる文字列にする。
 * 返り値: { name, publicText, dmText, canonText, threadsText }
 */
export function formatStageDraft(stage, res) {
  res = res && typeof res === "object" ? res : {};
  const pub = res.public_lore && typeof res.public_lore === "object" ? res.public_lore : {};
  const dm = res.dm_secrets && typeof res.dm_secrets === "object" ? res.dm_secrets : {};
  let name = "";
  let publicText = "";
  let dmText = "";

  if (stage === "region") {
    name = res.region_name;
    const resources = (pub.natural_resources || []).map(r => `・${r.name} (${r.location}): ${r.usage}`).join("\n");
    const layers = (pub.history_layers || []).map(h => `・${h.era}: ${h.what_happened} / 今も残るもの: ${h.what_remains}`).join("\n");
    publicText = sections([
      section("地形と気候", pub.terrain_and_climate),
      section("天然資源", resources),
      section("歴史の層 (古い順)", layers),
      section("旅人の第一印象", pub.travel_impression),
    ]);
    dmText = sections([
      section("命名の作法", res.naming_style),
      section("見えざる脅威", dm.hidden_threat),
      section("勢力間の緊張", dm.faction_tensions),
      section("埋もれた秘密", dm.ancient_secret),
      designNotes(res),
    ]);
  } else if (stage === "settlement") {
    name = res.settlement_name;
    const craft = pub.micro_craft || {};
    const npcs = (pub.key_npcs || [])
      .map(n => `・${n.name ?? ""} (${n.role ?? ""})${n.personality ? `: ${n.personality}` : ""}${n.want ? ` / 望み: ${n.want}` : ""}`)
      .join("\n");
    publicText = sections([
      section("建築と街並み", pub.architecture_and_scenery),
      section("一次産業", pub.primary_industries),
      section("二次産業・交易品", pub.secondary_industries),
      section(`手仕事の品『${craft.item_name ?? ""}』`, [craft.description, craft.made_from ? `材料: ${craft.made_from}` : ""].filter(Boolean).join("\n")),
      section("暮らしと料理", pub.daily_life_and_food),
      section("人々の楽しみ", pub.local_joys),
      section("影を落とす悲しみ", pub.visible_sorrows),
      section("主要 NPC", npcs),
    ]);
    dmText = sections([
      section("悲しみの真相", dm.sorrow_truth),
      section("手仕事の品の探索フック", dm.micro_craft_hook),
      section("NPC の隠し事", dm.npc_hidden_agenda),
      designNotes(res),
    ]);
  } else if (stage === "wilderness") {
    name = res.wilderness_title;
    publicText = sections([
      section("旅路の概要", pub.route_overview),
      section("天候表", (pub.weather_table || []).map(w => `・${w}`).join("\n")),
    ]);
    dmText = sections([
      section("見張り・追跡の脅威", dm.stalking_threat),
      section("迂回路の秘密", dm.shortcut_secret),
      section("ランドマーク (参考表示。構築には下書きのデータを使用)", landmarkSummary(res.landmarks)),
      designNotes(res),
    ]);
  } else if (stage === "site_lore") {
    name = res.site_name;
    const amb = pub.sensory_ambience || {};
    const subs = (dm.sub_objectives || [])
      .map((s, i) => `${i + 1}. ${s.title} (${s.target_topology})\n   関わり: ${s.connection_to_town_or_pc}\n   達成効果: ${s.effect}`)
      .join("\n");
    const factions = (dm.factions || []).map(f => `・${f.name} (${f.territory}): ${f.status}`).join("\n");
    publicText = sections([
      section("大目標", pub.clear_main_goal),
      section("導入", pub.pc_narrative_hook),
      section("外観と歴史", pub.exterior_and_history),
      section("五感", [`視覚: ${amb.visual ?? ""}`, `聴覚: ${amb.auditory ?? ""}`, `嗅覚: ${amb.olfactory ?? ""}`, `空気: ${amb.air ?? ""}`].join("\n")),
    ]);
    dmText = sections([
      section("ミッションの型", res.mission_archetype),
      section("時代と元の用途", [res.era, res.original_purpose].filter(Boolean).join(" / ")),
      section("真相", dm.true_situation),
      section("中目標", subs),
      section("逆転の展開", dm.catharsis_payoff),
      section("勢力", factions),
      designNotes(res),
    ]);
  }

  return {
    name: name ?? "",
    publicText,
    dmText,
    canonText: joinLines(res.canon),
    threadsText: joinLines(res.open_threads),
  };
}

/**
 * 地図帳エントリのページデータ。DM ページと確定事項ページはプレイヤーに見せない。
 * consts: { ownership: { NONE, OBSERVER }, html } (CONST.DOCUMENT_OWNERSHIP_LEVELS など)
 */
export function buildAtlasPages({ publicText, dmText, canon, openThreads }, consts, moduleId) {
  const NONE = consts?.ownership?.NONE ?? 0;
  const OBSERVER = consts?.ownership?.OBSERVER ?? 2;
  const html = consts?.html ?? 1;
  const page = (kind, content, level) => ({
    name: PAGE_KINDS[kind],
    type: "text",
    title: { show: true, level: 1 },
    text: { content, format: html },
    ownership: { default: level },
    flags: { [moduleId]: { atlas_page: kind } },
  });
  const canonHtml = [
    "<h3>確定事項 (後の段階で変更しない)</h3>",
    `<ul>${(canon || []).map(c => `<li>${escapeHtml(c)}</li>`).join("")}</ul>`,
    "<h3>未回収の伏線</h3>",
    `<ul>${(openThreads || []).map(t => `<li>${escapeHtml(t)}</li>`).join("")}</ul>`,
  ].join("");
  return [
    page("public", textToParagraphsHtml(publicText), OBSERVER),
    page("dm", textToParagraphsHtml(dmText), NONE),
    page("canon", canonHtml, NONE),
  ];
}

/**
 * 地図帳エントリ (plain object 化したもの) を、プロンプトに渡す文脈にする。
 * entry: { name, meta, pages: [{ kind, name, content, ownershipDefault }] }
 * kind が無い古いページは、名前に DM を含むか所有権 NONE なら DM 扱い。
 */
export function atlasEntryContext(entry) {
  if (!entry) return null;
  let publicText = "";
  let dmText = "";
  for (const p of entry.pages || []) {
    const kind = p.kind ?? (String(p.name ?? "").includes("DM") || p.ownershipDefault === 0 ? "dm" : "public");
    if (kind === "canon") continue;
    const text = htmlToText(p.content);
    if (kind === "dm") dmText += text + "\n";
    else publicText += text + "\n";
  }
  const meta = entry.meta || {};
  return {
    category: meta.categoryKey ?? null,
    name: entry.name ?? "",
    public_lore: publicText.trim().slice(0, MAX_CONTEXT_TEXT),
    dm_secrets: dmText.trim().slice(0, MAX_CONTEXT_TEXT),
    canon: Array.isArray(meta.canon) ? meta.canon : [],
    open_threads: Array.isArray(meta.open_threads) ? meta.open_threads : [],
  };
}

/** 地図帳全体の目次 (主題の重複と名前の衝突を避けるため。上位段階として渡すものは除く) */
export function atlasIndex(entries, excludeIds = []) {
  const exclude = new Set(excludeIds.filter(Boolean));
  return (entries || [])
    .filter(e => !exclude.has(e.id))
    .map(e => ({ category: e.meta?.categoryKey ?? null, name: e.name, canon: (e.meta?.canon || []).slice(0, 3) }));
}

/** 最近の地図帳エントリで使った発想カードの id (次の抽選で避ける) */
export function recentCardIds(entries, extra = [], limit = RECENT_ENTRY_COUNT) {
  const sorted = [...(entries || [])].sort((a, b) => (b.meta?.updatedAt ?? 0) - (a.meta?.updatedAt ?? 0));
  const ids = sorted.slice(0, limit).flatMap(e => (Array.isArray(e.meta?.cards) ? e.meta.cards : []));
  return [...new Set([...ids, ...(extra || [])])];
}

/**
 * 親の連なり (地域 → 集落 → 旅路) を重複なしで並べる。
 * picks: 画面で選んだ { region, settlement, wilderness } の id。未選択なら、選んだ子の meta.parent_ids から補う。
 * lookup(id) は { id, meta } を返す。
 */
export function resolveParentIds(picks, lookup) {
  const ids = { region: picks?.region || null, settlement: picks?.settlement || null, wilderness: picks?.wilderness || null };
  for (const key of ["wilderness", "settlement"]) {
    const parents = ids[key] ? lookup(ids[key])?.meta?.parent_ids : null;
    if (!parents) continue;
    if (!ids.settlement && parents.settlement) ids.settlement = parents.settlement;
    if (!ids.region && parents.region) ids.region = parents.region;
  }
  return ["region", "settlement", "wilderness"].map(k => ids[k]).filter(Boolean);
}

/** PC / NPC の文脈 (FVTT の Actor を plain object 化したもの) */
export function actorsContext(actors) {
  const pcs = [];
  const npcs = [];
  for (const a of actors || []) {
    const bio = htmlToText(a.bio).replace(/\s+/g, " ").slice(0, 350);
    if (a.type === "character" || a.hasPlayerOwner) {
      pcs.push({ name: a.name, info: [a.race, a.background].filter(Boolean).join(" "), bio: bio || "冒険者" });
    } else {
      npcs.push({ name: a.name, role: a.role || "NPC", bio });
    }
  }
  return { pcs, npcs };
}

/**
 * シナリオビルダーの「作成中」の行 (builder_progress)。
 * sinceSec は進捗を受け取ってからの秒数 (次の進捗が届くまで画面側で数え進める)。
 */
export function formatBuilderProgress(progress, sinceSec = 0, kind = "draft") {
  // kind: "draft" (下書き作成) / "build" (シーン構築)。進捗が届いていれば、そちらの kind を使う
  const base = (progress?.kind ?? kind) === "build" ? "⏳ シーンを構築中です…" : "⏳ AI が下書きを作成中です…";
  if (!progress) return base;
  const since = Math.max(0, Math.floor(Number(sinceSec) || 0));
  const parts = [];
  const steps = Number(progress.steps) || 1;
  if (steps > 1) parts.push(`工程 ${progress.step}/${steps}「${progress.step_label ?? ""}」`);
  if (progress.detail) parts.push(progress.detail);
  parts.push(`経過 ${(Number(progress.elapsed) || 0) + since}秒`);
  const remaining = (Number(progress.remaining) || 0) - since;
  if (remaining > 0) parts.push(`タイムアウトまで残り ${remaining}秒`);
  else parts.push(progress.auto_extend ? "タイムアウト。自動で延長します" : "タイムアウト。延長するかチャットのカードで選んでください");
  return `${base} ${parts.join(" ・ ")}`;
}
