// 部屋進入判定 (純粋関数・Foundry 非依存)

/**
 * 部屋ページの情景テキストの先頭にあれば「到達済み」とみなす印。
 * 読み上げ後に自動で付け、GM が消せばまた読み上げる
 */
export const REACHED_MARK = "<到達済>";
const REACHED_MARK_HTML = "&lt;到達済&gt;";

function decodeEntities(text) {
  return text
    .replace(/&nbsp;/g, " ")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&");
}

/**
 * ページ HTML に到達済みの印があるか。
 * エディタで class が消えても判定できるよう、いずれかの段落の先頭に印があれば到達済みとする
 */
export function isRoomReached(pageHtml) {
  if (!pageHtml) return false;
  // HTML ソースに生の <到達済> が書かれていてもタグとして消えないよう先に実体参照へ
  const blocks = String(pageHtml).split(REACHED_MARK).join(REACHED_MARK_HTML).split(/<\/?(?:p|div|h[1-6]|li|br)\b[^>]*>/i);
  return blocks.some(b => decodeEntities(b.replace(/<[^>]*>/g, "")).trim().startsWith(REACHED_MARK));
}

/**
 * 情景テキスト (class="ai-gm-room-flavor" の段落) の先頭に到達済みの印を付けた HTML を返す。
 * その段落が無い (古いページ・GM が書き換えた) ときはページ先頭に印の段落を足す
 */
export function markRoomReached(pageHtml) {
  const html = pageHtml ?? "";
  if (isRoomReached(html)) return html;
  const flavorOpen = /<p\b[^>]*class="[^"]*\bai-gm-room-flavor\b[^"]*"[^>]*>/i;
  if (flavorOpen.test(html)) return html.replace(flavorOpen, m => `${m}${REACHED_MARK_HTML} `);
  return `<p>${REACHED_MARK_HTML}</p>${html}`;
}

/**
 * キャンバス座標 (x, y) を含む部屋を返す。部屋の矩形はグリッド単位 (grid_x, grid_y, grid_w, grid_h)
 * offsetX/offsetY は scene.dimensions.sceneX/sceneY (パディング分)
 */
export function findRoomAt(rooms, x, y, { gridSize = 100, offsetX = 0, offsetY = 0 } = {}) {
  if (!Array.isArray(rooms)) return null;
  return rooms.find(r => {
    const rx0 = r.grid_x * gridSize + offsetX;
    const rx1 = (r.grid_x + r.grid_w) * gridSize + offsetX;
    const ry0 = r.grid_y * gridSize + offsetY;
    const ry1 = (r.grid_y + r.grid_h) * gridSize + offsetY;
    return x >= rx0 && x <= rx1 && y >= ry0 && y <= ry1;
  }) ?? null;
}

/**
 * 部屋進入ナレーションの対象トークンか。
 * プレイヤーが所有するトークンに加え、所有者未設定の PC (actor.type === "character") も対象にする
 * (GM が 1 人でテストするときや、PC をまだプレイヤーに割り当てていないとき)。
 * 非表示トークンと NPC (AI の NPC 移動など) は対象外。
 */
export function shouldNarrateToken({ hidden = false, hasPlayerOwner = false, actorType = null } = {}) {
  if (hidden) return false;
  return Boolean(hasPlayerOwner) || actorType === "character";
}

/** トークン左上座標とマス数から中心座標を求める */
export function tokenCenter({ x, y, width = 1, height = 1 }, gridSize = 100) {
  return { x: x + (width * gridSize) / 2, y: y + (height * gridSize) / 2 };
}

/**
 * 部屋進入の記録。同じトークンが同じ部屋に留まる間、
 * および一度ナレーションした部屋 (シーン単位) では再通知しない
 * (到達済みの判定は部屋ページの印で行い、ページが無い部屋だけ visitedByScene を使う)
 */
export class RoomVisitTracker {
  constructor() {
    this.tokenLastRoom = new Map();
    this.visitedByScene = new Map();
    this.inFlight = new Set();
  }

  /** トークンが別の部屋へ移ったときだけ true (同じ部屋の中の移動では false) */
  changedRoom(tokenId, roomId) {
    if (this.tokenLastRoom.get(tokenId) === roomId) return false;
    this.tokenLastRoom.set(tokenId, roomId);
    return true;
  }

  /** 読み上げ処理中の部屋に別トークンが同時に入っても二重に読まないための印 */
  begin(sceneId, roomId) {
    const key = `${sceneId}:${roomId}`;
    if (this.inFlight.has(key)) return false;
    this.inFlight.add(key);
    return true;
  }

  finish(sceneId, roomId) {
    this.inFlight.delete(`${sceneId}:${roomId}`);
  }

  /** 新しくナレーションすべき進入なら true (部屋ページが無いときの判定) */
  enter(sceneId, tokenId, roomId) {
    if (!this.changedRoom(tokenId, roomId)) return false;

    let visited = this.visitedByScene.get(sceneId);
    if (!visited) {
      visited = new Set();
      this.visitedByScene.set(sceneId, visited);
    }
    if (visited.has(roomId)) return false;
    visited.add(roomId);
    return true;
  }
}
