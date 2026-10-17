// 部屋進入判定 (純粋関数・Foundry 非依存)

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
 */
export class RoomVisitTracker {
  constructor() {
    this.tokenLastRoom = new Map();
    this.visitedByScene = new Map();
  }

  /** 新しくナレーションすべき進入なら true */
  enter(sceneId, tokenId, roomId) {
    if (this.tokenLastRoom.get(tokenId) === roomId) return false;
    this.tokenLastRoom.set(tokenId, roomId);

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
