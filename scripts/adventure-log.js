// 冒険記録スキル (Foundry 依存): チャットの記録などを集めてサーバーに頼み、返ってきた内容を冒険日誌ジャーナルに書く
//
// - `/ai 記録を作って` → サーバーが adventure_log_collect を返す → collectAdventureLog が adventure_log_request を送る
// - マスコットのメニュー「冒険記録を更新」からも collectAdventureLog("update") を呼ぶ (最新の回に書き足す。回が無ければ作る)
// - サーバーの adventure_log_write を受けて、本文のページと一覧のページ (取得物・人物・場所) を書く
// 純粋関数は scripts/lib/adventure-log.js (tests/js)

import { generatedFlags, MODULE_ID } from "./lib/chat-filter.js";
import { escapeHtml } from "./lib/html.js";
import {
  appendReplayHtml,
  buildListPageHtml,
  inventoryDiff,
  inventorySnapshot,
  LIST_PAGES,
  LOG_FLAG,
  LOG_JOURNAL_NAME,
  mergeEntries,
  normalizeLogMeta,
  planSession,
  replayToHtml,
  sessionPageName,
  sessionPageSort,
  transcriptSince,
} from "./lib/adventure-log.js";

const SUPPORT_SPEAKER = "サポートGM Geminiちゃん";

let sendToServer = null;
// 頼んだ回の情報 (どこまで読んだか・所持品の控え)。adventure_log_write を受けたら使う
let pending = null;

export function setLogSender(fn) {
  sendToServer = fn;
}

function findLogJournal() {
  return game.journal?.find(j => j.getFlag?.(MODULE_ID, LOG_FLAG)) ?? null;
}

function playerCharacters() {
  return (game.actors?.contents ?? []).filter(a => a.hasPlayerOwner && a.type === "character");
}

function plainActor(actor) {
  return {
    id: actor.id,
    name: actor.name,
    items: (actor.items?.contents ?? []).map(i => ({ name: i.name, quantity: i.system?.quantity })),
  };
}

function plainMessage(m) {
  return {
    timestamp: m.timestamp,
    alias: m.speaker?.alias ?? m.author?.name ?? "",
    content: m.content,
    isRoll: m.isRoll,
    rolls: (m.rolls ?? []).map(r => ({ formula: r.formula, total: r.total })),
    flavor: m.flavor,
    whisper: (m.whisper?.length ?? 0) > 0,
  };
}

function sceneNamesOf(messages, since) {
  const names = new Set();
  for (const m of messages) {
    if ((m.timestamp ?? 0) <= since) continue;
    const name = game.scenes?.get(m.speaker?.scene)?.name;
    if (name) names.add(name);
  }
  return [...names];
}

async function supportChat(content) {
  await ChatMessage.create({
    speaker: { alias: SUPPORT_SPEAKER },
    content,
    whisper: [game.user.id],
    flags: generatedFlags(),
  });
}

/** 記録の材料を集めてサーバーに頼む。mode: create (新しい回) / update (最新の回に書き足す) */
export async function collectAdventureLog(mode = "create") {
  if (!sendToServer) {
    ui.notifications.warn("AI GM Bridge: Python サーバー (8765) に接続されていません。");
    return false;
  }
  const journal = findLogJournal();
  const meta = normalizeLogMeta(journal?.getFlag(MODULE_ID, LOG_FLAG));
  const plan = planSession(meta, mode);
  const messages = game.messages?.contents ?? [];
  const transcript = transcriptSince(messages.map(plainMessage), plan.since);
  if (!transcript.length) {
    await supportChat("📖 前の記録から新しい出来事がまだないみたい。冒険が進んだらまた呼んでね！");
    return false;
  }
  const pcs = playerCharacters();
  const inventory = inventorySnapshot(pcs.map(plainActor));
  const page = plan.pageId ? journal?.pages?.get(plan.pageId) : null;
  pending = { ...plan, until: Math.max(...messages.map(m => m.timestamp ?? 0)), inventory };
  return sendToServer({
    event: "adventure_log_request",
    mode: plan.mode,
    session_number: plan.number,
    transcript,
    pcs: pcs.map(a => a.name),
    place_hints: sceneNamesOf(messages, plan.since),
    inventory_changes: inventoryDiff(meta.inventory, inventory),
    previous_replay: plan.mode === "update" ? (page?.text?.content ?? "") : "",
  });
}

/** サーバーからの adventure_log_collect */
export async function handleAdventureLogCollect(data) {
  await collectAdventureLog(data?.mode === "update" ? "update" : "create");
}

async function ensureJournal() {
  const existing = findLogJournal();
  if (existing) return existing;
  const levels = CONST.DOCUMENT_OWNERSHIP_LEVELS ?? {};
  return JournalEntry.create({
    name: LOG_JOURNAL_NAME,
    // 冒険日誌はプレイヤーも読める
    ownership: { default: levels.OBSERVER ?? 2 },
    flags: { [MODULE_ID]: { [LOG_FLAG]: normalizeLogMeta(null) } },
  });
}

/** サーバーからの adventure_log_write: 本文のページと一覧のページを書き、flag を更新する */
export async function handleAdventureLogWrite(data) {
  const journal = await ensureJournal();
  const meta = normalizeLogMeta(journal.getFlag(MODULE_ID, LOG_FLAG));
  const html = CONST.JOURNAL_ENTRY_PAGE_FORMATS?.HTML ?? 1;
  // 頼んだ回の情報が無い (再読み込みした後など) ときは、届いた回の番号で今までの分として扱う
  const plan = pending ?? { mode: data.mode, number: data.session_number, since: 0, pageId: null, until: Date.now(), inventory: meta.inventory };
  pending = null;
  const number = plan.number ?? data.session_number ?? 1;
  const name = sessionPageName(number, data.title);

  const target = plan.mode === "update" ? journal.pages.get(plan.pageId) : null;
  let pageId;
  if (target) {
    await target.update({ name, "text.content": appendReplayHtml(target.text?.content, data.replay) });
    pageId = target.id;
  } else {
    const [created] = await journal.createEmbeddedDocuments("JournalEntryPage", [{
      name,
      type: "text",
      sort: sessionPageSort(number),
      text: { content: replayToHtml(data.replay), format: html },
    }]);
    pageId = created?.id ?? null;
  }

  const lists = {
    items: mergeEntries(meta.items, data.items, number, true),
    people: mergeEntries(meta.people, data.people, number),
    places: mergeEntries(meta.places, data.places, number),
  };
  const updates = [];
  const creates = [];
  for (const [kind, page] of Object.entries(LIST_PAGES)) {
    const content = buildListPageHtml(kind, lists[kind]);
    const existing = journal.pages.find(p => p.getFlag?.(MODULE_ID, "log_list") === kind);
    if (existing) updates.push({ _id: existing.id, "text.content": content });
    else creates.push({ name: page.name, type: "text", sort: page.sort, text: { content, format: html }, flags: { [MODULE_ID]: { log_list: kind } } });
  }
  if (updates.length) await journal.updateEmbeddedDocuments("JournalEntryPage", updates);
  if (creates.length) await journal.createEmbeddedDocuments("JournalEntryPage", creates);

  // 書き足したときも、その回がどこから始まったか (since) は最初の値のまま
  const previous = meta.sessions.find(s => s.number === number);
  const sessions = meta.sessions.filter(s => s.number !== number);
  sessions.push({ number, pageId, title: data.title ?? "", since: previous?.since ?? plan.since ?? 0, until: plan.until ?? Date.now() });
  sessions.sort((a, b) => a.number - b.number);
  await journal.setFlag(MODULE_ID, LOG_FLAG, { ...lists, sessions, inventory: plan.inventory ?? meta.inventory });

  const counts = `取得物 ${data.items?.length ?? 0}・人物 ${data.people?.length ?? 0}・場所 ${data.places?.length ?? 0}`;
  const note = data.fallback ? "<br>⚠️ ごめんね、AI のまとめが届かなかったから、チャットの抜粋を残しておいたよ。" : "";
  await supportChat(`📖 冒険日誌に「${escapeHtml(name)}」を${target ? "書き足した" : "書いた"}よ！(${counts})${note}
    <div style="margin-top: 6px;"><button class="ai-gm-journal-open-btn" data-journal-id="${escapeHtml(journal.id)}"
      style="width: 100%; padding: 6px; background: #8e44ad; color: #fff; border: none; border-radius: 4px; cursor: pointer;">冒険日誌を開く</button></div>`);
}
