// 冒険記録スキル (冒険日誌ジャーナル) の純粋関数 (Foundry 非依存)
//
// 冒険日誌は 1 つの JournalEntry に次のページを持つ:
//   🎒 取得物 / 🤝 知り合った人々 / 🗺️ 訪れた場所 (毎回、flag の一覧から作り直す)
//   第N回 <見出し> (回ごとのリプレイ風の本文。「更新」では最新の回に書き足す)
// 一覧・回の区切り (どのメッセージまで記録したか)・PC の所持品の控えは adventure_log flag に保存する。

import { escapeHtml, stripHtml } from "./html.js";

export const LOG_JOURNAL_NAME = "📖 冒険日誌";
export const LOG_FLAG = "adventure_log";

export const LIST_PAGES = {
  items: { name: "🎒 取得物", sort: 100, empty: "まだ記録された取得物はありません。" },
  people: { name: "🤝 知り合った人々", sort: 200, empty: "まだ記録された人物はいません。" },
  places: { name: "🗺️ 訪れた場所", sort: 300, empty: "まだ記録された場所はありません。" },
};

const SESSION_SORT_BASE = 1000;
// 1 行の上限 (サーバー側でも切り詰める)
const MAX_LINE_CHARS = 600;

/** flag の中身を検める (古い・壊れた値でも落ちないように) */
export function normalizeLogMeta(raw) {
  const meta = raw && typeof raw === "object" ? raw : {};
  const list = v => (Array.isArray(v) ? v.filter(e => e && typeof e === "object" && e.name) : []);
  return {
    sessions: Array.isArray(meta.sessions) ? meta.sessions.filter(s => s && Number.isFinite(s.number)) : [],
    items: list(meta.items),
    people: list(meta.people),
    places: list(meta.places),
    inventory: meta.inventory && typeof meta.inventory === "object" ? meta.inventory : null,
  };
}

/** 新しい回を作るか、最新の回に書き足すか。number は回の番号、since はこの時刻より後のメッセージを読む */
export function planSession(meta, mode) {
  const last = meta.sessions.at(-1) ?? null;
  if (mode === "update" && last) {
    return { mode: "update", number: last.number, since: last.until ?? 0, pageId: last.pageId ?? null };
  }
  return { mode: "create", number: (last?.number ?? 0) + 1, since: last?.until ?? 0, pageId: null };
}

/**
 * チャットのメッセージ 1 件を「発言者・本文」にする。記録に残さないものは null
 * - ウィスパー (GM 宛ての秘密やカード) と、サポートGM (Geminiちゃん) との相談は残さない
 * - `/` で始まるコマンドは残さない。ダイスは「🎲 式 → 出目」にする
 */
export function messageToLine({ alias, content, isRoll, rolls, flavor, whisper } = {}) {
  if (whisper) return null;
  const speaker = String(alias ?? "").trim();
  if (speaker.startsWith("サポートGM")) return null;
  if (isRoll) {
    const results = (rolls ?? [])
      .filter(r => r && r.formula)
      .map(r => `${r.formula} → ${r.total}`)
      .join(" / ");
    if (!results) return null;
    const label = stripHtml(flavor ?? "").replace(/\s+/g, " ").trim();
    return { speaker, text: `🎲 ${label ? `${label} ` : ""}${results}`.slice(0, MAX_LINE_CHARS) };
  }
  const text = stripHtml(String(content ?? "").replace(/<br\s*\/?>|<\/p>|<\/div>|<\/li>/gi, "\n"))
    .replace(/[ \t　]+/g, " ")
    .replace(/\s*\n\s*/g, " / ")
    .trim();
  if (!text || text.startsWith("/")) return null;
  return { speaker, text: text.slice(0, MAX_LINE_CHARS) };
}

/** since より後のメッセージを古い順に「発言者・本文」にする */
export function transcriptSince(messages, since = 0) {
  return (messages ?? [])
    .filter(m => (m?.timestamp ?? 0) > since)
    .sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0))
    .map(messageToLine)
    .filter(Boolean);
}

/** PC の所持品の控え: { actorId: { name, items: { 品名: 個数 } } } */
export function inventorySnapshot(actors) {
  const snapshot = {};
  for (const actor of actors ?? []) {
    if (!actor?.id) continue;
    const items = {};
    for (const item of actor.items ?? []) {
      const name = String(item?.name ?? "").trim();
      if (!name) continue;
      const quantity = Number(item.quantity);
      items[name] = (items[name] ?? 0) + (Number.isFinite(quantity) && quantity > 0 ? quantity : 1);
    }
    snapshot[actor.id] = { name: String(actor.name ?? ""), items };
  }
  return snapshot;
}

/**
 * 前回の控えから増えた所持品。初めての記録 (控えが無い) と、新しく加わった PC は比べようがないので数えない
 */
export function inventoryDiff(previous, current) {
  if (!previous) return [];
  const changes = [];
  for (const [actorId, now] of Object.entries(current ?? {})) {
    const before = previous[actorId];
    if (!before) continue;
    for (const [name, quantity] of Object.entries(now.items ?? {})) {
      const gained = quantity - (before.items?.[name] ?? 0);
      if (gained > 0) changes.push({ holder: now.name, name, quantity: gained });
    }
  }
  return changes;
}

/**
 * 一覧に今回の分を足す。同じもの (人物・場所は名前、取得物は名前と持ち主) は 1 件にまとめ、
 * 新しい説明があれば書き換える。初めて記録した回 (session) は残す
 */
export function mergeEntries(existing, incoming, session, withHolder = false) {
  const key = e => (withHolder ? `${e.name}\u0000${e.holder ?? ""}` : e.name);
  const merged = existing.map(e => ({ ...e }));
  const index = new Map(merged.map((e, i) => [key(e), i]));
  for (const raw of incoming ?? []) {
    const name = String(raw?.name ?? "").trim();
    if (!name) continue;
    const entry = { name, note: String(raw.note ?? "").trim(), session };
    if (withHolder) entry.holder = String(raw.holder ?? "").trim();
    const at = index.get(key(entry));
    if (at === undefined) {
      index.set(key(entry), merged.length);
      merged.push(entry);
    } else if (entry.note) {
      merged[at] = { ...merged[at], note: entry.note, lastSession: session };
    }
  }
  return merged;
}

/** 一覧のページの HTML */
export function buildListPageHtml(kind, entries) {
  const page = LIST_PAGES[kind];
  if (!entries.length) return `<p>${escapeHtml(page.empty)}</p>`;
  const rows = entries.map(e => {
    const holder = e.holder ? ` <span class="ai-gm-log-holder">(${escapeHtml(e.holder)})</span>` : "";
    const note = e.note ? ` — ${escapeHtml(e.note)}` : "";
    return `<li><strong>${escapeHtml(e.name)}</strong>${holder}${note} <small>第${escapeHtml(e.session)}回</small></li>`;
  });
  return `<ul>\n${rows.join("\n")}\n</ul>`;
}

/** リプレイ風の本文 (改行区切り) を段落の HTML にする */
export function replayToHtml(replay) {
  return String(replay ?? "")
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(Boolean)
    .map(line => `<p>${escapeHtml(line)}</p>`)
    .join("\n");
}

/** 「更新」: 最新の回の本文の後に続きを書き足す (GM が書き換えた本文は残す) */
export function appendReplayHtml(existingHtml, replay) {
  return `${existingHtml ?? ""}\n<hr>\n${replayToHtml(replay)}`;
}

export function sessionPageName(number, title) {
  return `第${number}回 ${String(title ?? "").trim() || "冒険の記録"}`;
}

export function sessionPageSort(number) {
  return SESSION_SORT_BASE + number * 10;
}
