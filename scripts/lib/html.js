// HTML 文字列処理 (純粋関数・Foundry 非依存)

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** AI やサーバー由来のテキストを HTML に埋め込む前に必ず通す */
export function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, ch => ESCAPES[ch]);
}

/** HTML タグを除去してプレーンテキストにする */
export function stripHtml(html) {
  if (!html) return "";
  return String(html).replace(/<[^>]*>/g, "").trim();
}

/** プレーンテキストをエスケープし、改行を <br> にする */
export function textToHtml(value) {
  return escapeHtml(value).replace(/\r?\n/g, "<br>");
}
