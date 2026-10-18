#!/usr/bin/env python3
"""
ダンジョン自動生成ツール (dungeon_generator.py)

概要:
    指定されたシード値、サイズ（小/中/大/特大）、部屋の密度、ループ率（サイクロマティック数）に基づき、
    グラフ理論（最小全域木: MST）と幾何空間分割によりダンジョンを自動生成します。

記号:
    ■ : 壁 (Wall)
    □ : 部屋 / 通路 (Floor)
    ◆ : スタート地点 (Start)
    ★ : 最深部 / ゴール (Goal / Boss Room)

サイズプリセット:
    小 (small)  : 20 x 20
    中 (medium) : 50 x 50
    大 (large)  : 100 x 100
    巨大 (xlarge): 200 x 200
"""

import argparse
import random
import sys
import time
from typing import List, Tuple, Optional, Dict

# マップ表現記号
WALL = "■"
FLOOR = "□"
START = "◆"
GOAL = "★"

# 空間タイプ (シーンモード)
#   underground: 地下迷宮・城塞 (完全閉鎖空間。扉・隠し扉あり)
#   ruins      : 野外の廃墟・古戦場 (崩落石垣・低い壁・瓦礫で視線が通る)
#   wilderness : 野外の旅路 (ポイントクロール。部屋=ランドマーク、通路=道。壁なし)
SCENE_MODES = ("underground", "ruins", "wilderness")

# 壁タイプ (extract_walls の5要素目。FVTT 側で Wall の move/sight/door に変換)
WALL_TYPES = ("normal", "low_wall", "rubble", "door", "secret_door")

# 部屋ごとの照明プリセット (FVTT 側の scripts/lib/scene-docs.js LIGHT_PRESETS と揃えること)
LIGHT_PRESET_KEYS = ("torch_warm", "bonfire_bright", "magic_blue", "acid_green", "moonlight_dim", "dark")

# AI フレーバーが無い場合のモード別テーマ (underground は get_room_flavor_notes 内の themes を使用)
MODE_FALLBACK_THEMES = {
    "ruins": [
        {"type": "breach", "title": "崩れた城壁の切れ目", "icon": "icons/svg/door-open-outline.svg",
         "flavor": "崩落した石垣の隙間から風が吹き込み、足元には砕けた切石と枯れ草が散らばっている。壁の向こうの気配が、低くなった石積み越しにうかがえる。"},
        {"type": "courtyard", "title": "苔むした中庭", "icon": "icons/svg/statue.svg",
         "flavor": "敷石の継ぎ目から雑草が伸び、中央の干上がった泉には苔が厚く張りついている。四方の回廊は半ば崩れ、空が大きく開けている。"},
        {"type": "tower", "title": "倒壊した見張り塔", "icon": "icons/svg/eye.svg",
         "flavor": "折れた塔の基部に螺旋階段の名残が数段だけ残る。積み重なった瓦礫の上からは、周囲の廃墟を見渡せそうだ。"},
        {"type": "chapel", "title": "草に埋もれた礼拝堂", "icon": "icons/svg/temple.svg",
         "flavor": "屋根の落ちた礼拝堂に陽光が差し込み、倒れた長椅子を蔦が覆っている。祭壇の石には古い紋章がかすかに残る。"},
        {"type": "barracks", "title": "焼け落ちた兵舎跡", "icon": "icons/svg/sword.svg",
         "flavor": "黒く焦げた梁が斜めに突き出し、灰の積もった床に錆びた鎧の部品が転がっている。焦げ臭さが今も残っている。"},
        {"type": "well", "title": "古井戸の広場", "icon": "icons/svg/book.svg",
         "flavor": "石組みの古井戸を囲むように小さな広場がある。井戸の底からは湿った冷気が上がり、縄の擦れた跡が新しい。"},
    ],
    "wilderness": [
        {"type": "signpost", "title": "古い道標の分かれ道", "icon": "icons/svg/direction.svg",
         "flavor": "苔に覆われた石の道標が二股の道に立っている。刻まれた地名は風化し、片方の道には新しい轍が残る。"},
        {"type": "ford", "title": "渓流の渡し場", "icon": "icons/svg/waterfall.svg",
         "flavor": "浅瀬を冷たい水が音を立てて流れ、平たい飛び石が対岸へ続いている。水際の泥に大きな足跡がある。"},
        {"type": "camp", "title": "倒木の野営跡", "icon": "icons/svg/campfire.svg",
         "flavor": "巨大な倒木が風よけになった窪地に、古い焚き火の跡が残っている。雨をしのげる張り出しもある。"},
        {"type": "lookout", "title": "崖上の見張り岩", "icon": "icons/svg/eye.svg",
         "flavor": "せり出した岩棚から谷の先まで見通せる。冷たい風が強く吹きつけ、遠くに目的地の影がかすむ。"},
        {"type": "marsh", "title": "霧の湿原", "icon": "icons/svg/hazard.svg",
         "flavor": "膝まで沈む湿地に白い霧が漂い、どこからか水鳥の羽音が聞こえる。足元の道は板切れで細く続いている。"},
        {"type": "trail", "title": "獣道の茂み", "icon": "icons/svg/pawprint.svg",
         "flavor": "背丈ほどの藪を分けて細い獣道が続く。枝に引っかかった毛の束と、折られたばかりの小枝が目につく。"},
    ],
}

MODE_ENDPOINTS = {
    "underground": {
        "start": ("迷宮の入口広間 (安全地帯)", "icons/svg/door-open-outline.svg",
                  "地上へと続く階段の麓にある、冷たい地下の空気が吹き抜ける石造りの広間。古い苔むした石畳が広がり、ここから未知の迷宮が始まる。",
                  "安全な退路。地上へ撤退可能。"),
        "goal": ("最深部：災厄の祭壇 (ボス部屋)", "icons/svg/skull.svg",
                 "圧倒的な威圧感と澱んだ魔力が充満する巨大な円形広間。中央の黒曜石の祭壇には不穏な紫の光が揺らめき、古代の封印紋が刻まれている。",
                 "最深部ボス部屋。手強い敵と主要な財宝が眠る。"),
    },
    "ruins": {
        "start": ("廃墟の外門跡 (安全地帯)", "icons/svg/door-open-outline.svg",
                  "崩れかけた門柱の間を風が抜け、草に埋もれた石畳が奥の廃墟へと続いている。振り返れば来た道がよく見通せる。",
                  "安全な退路。外の街道へ撤退可能。"),
        "goal": ("最奥：崩れた大広間 (決戦の場)", "icons/svg/skull.svg",
                 "天井の半分が崩れ落ちた大広間に、瓦礫で組まれた粗末な玉座が据えられている。焚き火の煙と鉄の匂いが立ちこめている。",
                 "最奥の決戦場。首領と主要な財宝がある。"),
    },
    "wilderness": {
        "start": ("集落の出口 (出発地点)", "icons/svg/door-open-outline.svg",
                  "集落の柵が途切れた先に、踏み固められた街道が丘の向こうへ続いている。背後から家々の煙の匂いが届く。",
                  "旅の出発地点。集落へいつでも戻れる。"),
        "goal": ("目的地の入口", "icons/svg/ruins.svg",
                 "木々が途切れた先に、目指してきた場所の入口が姿を現す。あたりの鳥の声が急に途絶えた。",
                 "旅路の終点。ここから廃墟・迷宮へ入る。"),
    },
}

ROOM_LABEL = {"underground": "部屋", "ruins": "区画", "wilderness": "地点"}

# PNG 描画色 (壁/床)
MODE_COLORS = {
    "underground": {"wall": (25, 25, 28), "floor": (195, 185, 165)},      # 岩壁と石畳
    "ruins": {"wall": (74, 88, 58), "floor": (172, 164, 146)},            # 草地・崩れた石垣と敷石
    "wilderness": {"wall": (52, 90, 46), "floor": (178, 152, 104)},       # 森と土の道・開けた空き地
}


class UnionFind:
    """素集合データ構造 (Kruskal法による最小全域木構築用)"""
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        if self.parent[i] == i:
            return i
        self.parent[i] = self.find(self.parent[i])
        return self.parent[i]

    def union(self, i: int, j: int) -> bool:
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i != root_j:
            self.parent[root_i] = root_j
            return True
        return False


class DungeonGenerator:
    def __init__(
        self,
        width: int = 100,
        height: int = 100,
        seed: Optional[int] = None,
        density: Optional[float] = None,
        loop_ratio: Optional[float] = None,
        scene_mode: str = "underground"
    ):
        if scene_mode not in SCENE_MODES:
            raise ValueError(f"scene_mode は {SCENE_MODES} のいずれかを指定してください: {scene_mode}")
        self.scene_mode = scene_mode
        self.width = max(20, width)
        self.height = max(20, height)

        # シード値の決定
        if seed is None:
            self.seed = random.randint(1, 99999999)
        else:
            self.seed = seed
        self.rng = random.Random(self.seed)

        # 密度の決定 (0.2 〜 0.8 の間でランダム決定)
        if density is None:
            self.density = round(self.rng.uniform(0.25, 0.75), 2)
        else:
            self.density = max(0.05, min(1.0, density))

        # ループ率（サイクロマティック数の制御比率: 0.0〜0.5）
        if loop_ratio is None:
            self.loop_ratio = round(self.rng.uniform(0.10, 0.35), 2)
        else:
            self.loop_ratio = max(0.0, min(1.0, loop_ratio))

        # グリッドの初期化 (すべて壁)
        self.grid: List[List[str]] = [[WALL for _ in range(self.width)] for _ in range(self.height)]
        self.rooms: List[Tuple[int, int, int, int]] = []  # (x, y, w, h)
        self.room_centers: List[Tuple[int, int]] = []
        self.edges: List[Tuple[int, int]] = []
        self.cyclomatic_number: int = 0
        self.start_pos: Optional[Tuple[int, int]] = None
        self.goal_pos: Optional[Tuple[int, int]] = None
        self.start_room_idx: int = 0
        self.goal_room_idx: int = 0
        self.max_depth: int = 0
        # 閉路に含まれない辺 (橋)。in_cycle 判定に使用
        self.bridge_edges: set = set()
        self.floor_count: int = 0
        self.elapsed_time: float = 0.0

    def _carve_room(self, x: int, y: int, w: int, h: int):
        """指定範囲を床として掘削"""
        for r in range(y, y + h):
            row = self.grid[r]
            for c in range(x, x + w):
                if row[c] != FLOOR:
                    row[c] = FLOOR
                    self.floor_count += 1

    def _carve_corridor(self, x1: int, y1: int, x2: int, y2: int):
        """2点間をマンハッタン配線（L字型）で掘削"""
        # 水平
        for x in range(min(x1, x2), max(x1, x2) + 1):
            if 0 < y1 < self.height - 1 and 0 < x < self.width - 1:
                if self.grid[y1][x] != FLOOR:
                    self.grid[y1][x] = FLOOR
                    self.floor_count += 1
        # 垂直
        for y in range(min(y1, y2), max(y1, y2) + 1):
            if 0 < y < self.height - 1 and 0 < x2 < self.width - 1:
                if self.grid[y][x2] != FLOOR:
                    self.grid[y][x2] = FLOOR
                    self.floor_count += 1

    def _carve_dead_ends(self, count: int = 10, max_length: int = 15):
        """袋小路（行き止まり路）を生成"""
        directions = [(0, 1), (0, -1), (1, 0), (-1, 0)]
        for _ in range(count):
            if not self.rooms:
                break
            room = self.rng.choice(self.rooms)
            rx, ry, rw, rh = room
            cx, cy = rx + rw // 2, ry + rh // 2

            dx, dy = self.rng.choice(directions)
            length = self.rng.randint(5, max_length)

            curr_x, curr_y = cx, cy
            for _ in range(length):
                curr_x += dx
                curr_y += dy
                if 1 <= curr_x < self.width - 1 and 1 <= curr_y < self.height - 1:
                    if self.grid[curr_y][curr_x] != FLOOR:
                        self.grid[curr_y][curr_x] = FLOOR
                        self.floor_count += 1
                else:
                    break

    def _seal_outer_walls(self):
        """外周（最上行・最下行・最左列・最右列）を確実に壁 (■) に設定"""
        for x in range(self.width):
            self.grid[0][x] = WALL
            self.grid[self.height - 1][x] = WALL
        for y in range(self.height):
            self.grid[y][0] = WALL
            self.grid[y][self.width - 1] = WALL

    def generate(self, target_room_count: Optional[int] = None):
        """
        ダンジョンを生成 (MST + ループ付加 + 最短経路ゴール探索)
        target_room_count を指定すると密度による部屋数計算を上書きする (ワイルダネスのランドマーク数など)
        """
        t0 = time.time()
        area = self.width * self.height

        # サイズに応じたスケール調整
        # 20x20: ~4-6部屋 / 50x50: ~10-20部屋 / 100x100: ~20-50部屋 / 200x200: ~40-100部屋
        # 密度に応じた部屋数スケーリング
        # 100x100マス時:
        #   density=0.4 (標準)      : 約 21 部屋
        #   density=0.7 (密集/多め) : 約 33 部屋
        #   density=1.0 (ギッシリ)  : 約 48 部屋
        scale_factor = (area / 10000) ** 0.5
        base_room_count = int(scale_factor * (10 + 38 * (self.density ** 1.3)))
        base_room_count = max(3, min(500, base_room_count))
        if target_room_count is not None:
            base_room_count = max(2, min(500, int(target_room_count)))

        # 部屋サイズの決定 (密度が高いほど部屋もやや広めに)
        # 小さいマップ (20x20 など) は部屋の最小辺を 3 マスまで下げて部屋数を確保する
        room_floor = max(3, min(5, min(self.width, self.height) // 10))
        min_room_size = max(room_floor, int(min(self.width, self.height) * (0.025 + 0.012 * self.density)))
        max_room_size = max(min_room_size + 3, int(min(self.width, self.height) * (0.055 + 0.020 * self.density)))

        separate_rooms = min(self.width, self.height) < 60
        attempts = base_room_count * 10
        for _ in range(attempts):
            w = self.rng.randint(min_room_size, max_room_size)
            h = self.rng.randint(min_room_size, max_room_size)
            x = self.rng.randint(2, self.width - w - 3)
            y = self.rng.randint(2, self.height - h - 3)

            cx, cy = x + w // 2, y + h // 2
            # 既存の部屋中心と近すぎる場合はスキップして別座標を試行
            too_close = False
            for ecx, ecy in self.room_centers:
                if abs(cx - ecx) < min_room_size and abs(cy - ecy) < min_room_size:
                    too_close = True
                    break
            # 小さいマップでは部屋どうしの重なりを許すと 1 つの大部屋に潰れるので、1 マスの隙間を空ける
            if not too_close and separate_rooms:
                too_close = any(
                    x < ex + ew + 1 and ex < x + w + 1 and y < ey + eh + 1 and ey < y + h + 1
                    for ex, ey, ew, eh in self.rooms
                )
            if too_close and len(self.rooms) > 0:
                continue

            self._carve_room(x, y, w, h)
            self.rooms.append((x, y, w, h))
            self.room_centers.append((cx, cy))

            if len(self.rooms) >= base_room_count:
                break

        num_rooms = len(self.rooms)

        # 2. グラフ理論による部屋間の全域木 (MST) 構築
        # 全部屋ペアのマンハッタン距離を計算
        all_candidate_edges = []
        for i in range(num_rooms):
            c1 = self.room_centers[i]
            for j in range(i + 1, num_rooms):
                c2 = self.room_centers[j]
                dist = abs(c1[0] - c2[0]) + abs(c1[1] - c2[1])
                all_candidate_edges.append((dist, i, j))

        all_candidate_edges.sort(key=lambda e: e[0])

        # Kruskal 法による最小全域木の抽出（全部屋が 100% 連結されることを保証）
        uf = UnionFind(num_rooms)
        mst_edges: List[Tuple[int, int]] = []
        rejected_edges: List[Tuple[int, int]] = []

        for dist, u, v in all_candidate_edges:
            if uf.union(u, v):
                mst_edges.append((u, v))
            else:
                rejected_edges.append((u, v))

        # 3. サイクロマティック数 (ループ度) の制御
        # ループ率に応じて余分な辺を追加し、周回路を形成
        num_loops_to_add = int(len(mst_edges) * self.loop_ratio)
        added_cycle_edges = []
        if rejected_edges and num_loops_to_add > 0:
            # 近距離の辺を優先的に選定
            added_cycle_edges = rejected_edges[:num_loops_to_add]

        final_edges = mst_edges + added_cycle_edges
        self.edges = final_edges
        # サイクロマティック数 M = |E| - |V| + 1
        self.cyclomatic_number = len(final_edges) - num_rooms + 1

        # 通路をグリッドに掘削
        for u, v in final_edges:
            c1 = self.room_centers[u]
            c2 = self.room_centers[v]
            if self.rng.random() < 0.5:
                self._carve_corridor(c1[0], c1[1], c2[0], c2[1])
            else:
                self._carve_corridor(c2[0], c2[1], c1[0], c1[1])

        # 袋小路の追加
        dead_end_count = int(num_rooms * 0.5)
        self._carve_dead_ends(count=dead_end_count, max_length=int(max_room_size * 1.5))

        # 4. スタート地点 (◆) と 最深部・ゴール (★) の数学的選定
        # スタートは部屋 0
        start_room_idx = 0
        start_x, start_y = self.room_centers[start_room_idx]
        self.start_pos = (start_x, start_y)
        self.grid[start_y][start_x] = START

        # グラフ上の最短経路探索 (BFS) で最も遠い部屋をゴールに選定
        adj: Dict[int, List[int]] = {i: [] for i in range(num_rooms)}
        for u, v in final_edges:
            adj[u].append(v)
            adj[v].append(u)

        dist = [-1] * num_rooms
        dist[start_room_idx] = 0
        queue = [start_room_idx]
        for curr in queue:
            for nxt in adj[curr]:
                if dist[nxt] == -1:
                    dist[nxt] = dist[curr] + 1
                    queue.append(nxt)

        self.adj = adj
        self.dist = dist
        self.bridge_edges = self._find_bridges(num_rooms, adj)

        farthest_room_idx = max(range(num_rooms), key=lambda i: dist[i])
        self.start_room_idx = start_room_idx
        self.goal_room_idx = farthest_room_idx
        self.max_depth = dist[farthest_room_idx]

        goal_x, goal_y = self.room_centers[farthest_room_idx]
        self.goal_pos = (goal_x, goal_y)
        self.grid[goal_y][goal_x] = GOAL

        # 5. 外周を必ず壁で封鎖
        self._seal_outer_walls()

        self.elapsed_time = time.time() - t0

    def get_room_topologies(self) -> List[Dict]:
        """各部屋の離散グラフ上のトポロジー（次数・深さ・面積・サイクル所属等）を抽出"""
        topologies = []
        bridges = self.bridge_edges

        for i, room in enumerate(self.rooms):
            rx, ry, rw, rh = room
            neighbors = self.adj.get(i, []) if hasattr(self, 'adj') else []
            deg = len(neighbors)
            depth = self.dist[i] if hasattr(self, 'dist') and i < len(self.dist) else 0
            is_start = (i == self.start_room_idx)
            is_goal = (i == self.goal_room_idx)
            # 橋でない辺 (どこかの閉路に含まれる辺) に接していれば周回路上の部屋
            in_cycle = any((min(i, v), max(i, v)) not in bridges for v in neighbors)

            topologies.append({
                "id": i,
                "deg": deg,
                "depth": depth,
                "start": is_start,
                "goal": is_goal,
                "in_cycle": in_cycle,
                "neighbors": sorted(neighbors),
                "area": rw * rh,
                "bounds": [rx, ry, rw, rh],
                "center": self.room_centers[i]
            })
        return topologies

    @staticmethod
    def _find_bridges(num_nodes: int, adj: Dict[int, List[int]]) -> set:
        """
        無向グラフの橋 (取り除くと連結成分が増える辺) を (小さいid, 大きいid) の集合で返す。
        閉路に含まれる辺は橋ではない。再帰を使わない Tarjan 法 (部屋数が多くても再帰上限に当たらない)
        """
        disc = [-1] * num_nodes
        low = [0] * num_nodes
        bridges = set()
        timer = 0
        for root in range(num_nodes):
            if disc[root] != -1:
                continue
            disc[root] = low[root] = timer
            timer += 1
            # (ノード, 親, 隣接リストの走査位置, 親への辺を1本スキップ済みか)
            stack = [(root, -1, 0, False)]
            while stack:
                node, parent, idx, skipped = stack.pop()
                neighbors = adj.get(node, [])
                if idx < len(neighbors):
                    stack.append((node, parent, idx + 1, skipped))
                    nxt = neighbors[idx]
                    if nxt == parent and not skipped:
                        # 親への辺は1本だけ無視する (多重辺は閉路として扱う)
                        stack[-1] = (node, parent, idx + 1, True)
                        continue
                    if disc[nxt] == -1:
                        disc[nxt] = low[nxt] = timer
                        timer += 1
                        stack.append((nxt, node, 0, False))
                    else:
                        low[node] = min(low[node], disc[nxt])
                else:
                    if parent != -1:
                        low[parent] = min(low[parent], low[node])
                        if low[node] > disc[parent]:
                            bridges.add((min(node, parent), max(node, parent)))
        return bridges

    def get_fill_ratio(self) -> float:
        """床の充填率 (Fill Ratio: ρ)"""
        return round(self.floor_count / (self.width * self.height), 4)

    def to_string(self) -> str:
        """マップ全体を文字列として取得"""
        return "\n".join("".join(row) for row in self.grid)

    def render_to_png(self, output_path: str, cell_size: int = 10):
        """グリッドを標準PNG画像ファイルとして書き出し (純粋Python/zlib実装)"""
        import zlib, struct
        img_w = self.width * cell_size
        img_h = self.height * cell_size

        palette = MODE_COLORS[self.scene_mode]
        color_wall = palette["wall"]
        color_floor = palette["floor"]
        color_start = (46, 204, 113)    # 緑のスタート地点
        color_goal = (231, 76, 60)      # 赤のゴール/ボス部屋

        raw_data = bytearray()
        for y in range(img_h):
            raw_data.append(0)  # filter type 0
            grid_y = y // cell_size
            row = self.grid[grid_y]
            for x in range(img_w):
                grid_x = x // cell_size
                cell = row[grid_x]
                if cell == WALL:
                    c = color_wall
                elif cell == FLOOR:
                    c = color_floor
                elif cell == START:
                    c = color_start
                elif cell == GOAL:
                    c = color_goal
                else:
                    c = color_wall
                raw_data.extend(c)

        compressed = zlib.compress(raw_data, level=4)
        with open(output_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            ihdr = struct.pack(">IIBBBBB", img_w, img_h, 8, 2, 0, 0, 0)
            f.write(struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr)))
            f.write(struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", zlib.crc32(b"IDAT" + compressed)))
            f.write(struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND")))

    def extract_walls(self, grid_size: int = 100) -> List[list]:
        """
        壁と扉の線分リスト [x0, y0, x1, y1, type] を返却 (座標はマップ画像上のピクセル)。
        type は WALL_TYPES のいずれか。
          underground: 境界は normal。部屋の出入口に door / secret_door (袋小路の部屋)
          ruins      : 境界を短く区切り normal / low_wall / rubble を混在。出入口の一部に door
          wilderness : 壁なし (空リスト)
        乱数は seed から派生させるため、同じ seed なら何度呼んでも同じ結果になる。
        """
        if self.scene_mode == "wilderness":
            return []
        rng = random.Random(f"{self.seed}:walls")
        walls: List[list] = []
        for seg in self._extract_boundary_segments(grid_size):
            if self.scene_mode == "ruins":
                for piece in self._split_segment(seg, max_cells=4, grid_size=grid_size):
                    roll = rng.random()
                    wall_type = "normal" if roll < 0.55 else ("low_wall" if roll < 0.85 else "rubble")
                    walls.append(piece + [wall_type])
            else:
                walls.append(seg + ["normal"])
        walls.extend(self._door_segments(grid_size, rng))
        return walls

    @staticmethod
    def _split_segment(seg: List[int], max_cells: int, grid_size: int) -> List[List[int]]:
        """軸平行の線分を max_cells マスごとに分割"""
        x0, y0, x1, y1 = seg
        step = max_cells * grid_size
        pieces = []
        if y0 == y1:
            start = x0
            while start < x1:
                end = min(start + step, x1)
                pieces.append([start, y0, end, y1])
                start = end
        else:
            start = y0
            while start < y1:
                end = min(start + step, y1)
                pieces.append([x0, start, x1, end])
                start = end
        return pieces

    def _room_openings(self) -> List[Tuple[int, str, int, int, int]]:
        """
        各部屋の出入口 (通路が部屋の辺に接する幅1〜2マスの開口部) を列挙。
        戻り値: (部屋index, 向き "h"|"v", 線の位置, 開始マス, 終了マス)
        部屋の辺に沿って通路が走っている場合 (長い開口) は扉を付けない
        """
        openings = []
        for idx, (rx, ry, rw, rh) in enumerate(self.rooms):
            sides = [
                ("h", ry, [(c, ry - 1) for c in range(rx, rx + rw)]),        # 上辺
                ("h", ry + rh, [(c, ry + rh) for c in range(rx, rx + rw)]),  # 下辺
                ("v", rx, [(rx - 1, r) for r in range(ry, ry + rh)]),        # 左辺
                ("v", rx + rw, [(rx + rw, r) for r in range(ry, ry + rh)]),  # 右辺
            ]
            for orient, line, cells in sides:
                run_start = None
                run_len = 0
                for k, (cx, cy) in enumerate(cells + [(-1, -1)]):
                    open_cell = 0 <= cx < self.width and 0 <= cy < self.height and self.grid[cy][cx] != WALL
                    if open_cell:
                        if run_start is None:
                            run_start = k
                        run_len += 1
                        continue
                    if run_start is not None and run_len <= 2:
                        first = cells[run_start]
                        begin = first[0] if orient == "h" else first[1]
                        openings.append((idx, orient, line, begin, begin + run_len))
                    run_start, run_len = None, 0
        return openings

    def _door_segments(self, grid_size: int, rng: random.Random) -> List[list]:
        """出入口に扉を配置。袋小路の部屋 (次数1) は一定確率で隠し扉にする"""
        doors = []
        seen = set()
        for idx, orient, line, begin, end in self._room_openings():
            key = (orient, line, begin, end)
            if key in seen:
                continue
            seen.add(key)
            is_dead_end = len(self.adj.get(idx, [])) == 1 if hasattr(self, "adj") else False
            special_room = idx in (self.start_room_idx, self.goal_room_idx)
            roll = rng.random()
            if self.scene_mode == "ruins":
                # 野外廃墟は扉の多くが朽ちて失われている
                if roll >= 0.25:
                    continue
                door_type = "door"
            else:
                if is_dead_end and not special_room and roll < 0.35:
                    door_type = "secret_door"
                elif roll < 0.75:
                    door_type = "door"
                else:
                    continue
            if orient == "h":
                doors.append([begin * grid_size, line * grid_size, end * grid_size, line * grid_size, door_type])
            else:
                doors.append([line * grid_size, begin * grid_size, line * grid_size, end * grid_size, door_type])
        return doors

    def _extract_boundary_segments(self, grid_size: int = 100) -> List[List[int]]:
        """ダンジョンの部屋・通路と壁の境界線を抽出し、結合された線分リスト [x0, y0, x1, y1] として返却"""
        from collections import defaultdict

        h_by_y = defaultdict(list)
        for y in range(self.height):
            for x in range(self.width):
                is_floor = self.grid[y][x] != WALL
                if is_floor and (y == 0 or self.grid[y - 1][x] == WALL):
                    h_by_y[y].append((x, x + 1))
                if is_floor and (y == self.height - 1 or self.grid[y + 1][x] == WALL):
                    h_by_y[y + 1].append((x, x + 1))

        v_by_x = defaultdict(list)
        for x in range(self.width):
            for y in range(self.height):
                is_floor = self.grid[y][x] != WALL
                if is_floor and (x == 0 or self.grid[y][x - 1] == WALL):
                    v_by_x[x].append((y, y + 1))
                if is_floor and (x == self.width - 1 or self.grid[y][x + 1] == WALL):
                    v_by_x[x + 1].append((y, y + 1))

        walls = []
        for y, segs in h_by_y.items():
            if not segs:
                continue
            segs.sort()
            cur_start, cur_end = segs[0]
            for s, e in segs[1:]:
                if s <= cur_end:
                    cur_end = max(cur_end, e)
                else:
                    walls.append([cur_start * grid_size, y * grid_size, cur_end * grid_size, y * grid_size])
                    cur_start, cur_end = s, e
            walls.append([cur_start * grid_size, y * grid_size, cur_end * grid_size, y * grid_size])

        for x, segs in v_by_x.items():
            if not segs:
                continue
            segs.sort()
            cur_start, cur_end = segs[0]
            for s, e in segs[1:]:
                if s <= cur_end:
                    cur_end = max(cur_end, e)
                else:
                    walls.append([x * grid_size, cur_start * grid_size, x * grid_size, cur_end * grid_size])
                    cur_start, cur_end = s, e
            walls.append([x * grid_size, cur_start * grid_size, x * grid_size, cur_end * grid_size])

        return walls

    def get_preview(self, center: Optional[Tuple[int, int]] = None, width: int = 40, height: int = 20) -> str:
        """指定座標周辺の拡大プレビューを取得"""
        cx, cy = center if center else (self.start_pos or (self.width // 2, self.height // 2))
        half_w = width // 2
        half_h = height // 2

        start_x = max(0, min(self.width - width, cx - half_w))
        start_y = max(0, min(self.height - height, cy - half_h))

        lines = []
        for y in range(start_y, start_y + height):
            lines.append("".join(self.grid[y][start_x : start_x + width]))
        return "\n".join(lines)

    def _index_custom_flavors(self, custom_flavors: Optional[List[Dict]], id_base: Optional[int] = None) -> Dict[int, Dict]:
        """
        AI生成フレーバーを部屋インデックス (0始まり) で引ける辞書に変換。
        プロンプトは 0 始まりの id を渡すが、AI が 1 始まりで返すこともあるため、
        応答全体で id の基準を1回だけ判定する (部屋ごとに i / i+1 を両方引くと隣室のフレーバーが混入する)。
        """
        if not custom_flavors:
            return {}
        by_id: Dict[int, Dict] = {}
        for cf in custom_flavors:
            if not isinstance(cf, dict):
                continue
            try:
                cid = int(cf.get("id"))
            except (TypeError, ValueError):
                continue
            by_id[cid] = cf
        if not by_id:
            return {}
        num_rooms = len(self.rooms)
        # スタート部屋 (id 0) は prioritize_key_topologies で必ずプロンプトに含まれるため、
        # id 0 が無ければ AI は 1 始まりで返したと判断できる
        # id_base を指定された場合 (呼び出し側で 0 始まりに正規化済み等) は判定しない
        offset = id_base if id_base is not None else (0 if 0 in by_id else 1)
        return {cid - offset: cf for cid, cf in by_id.items() if 0 <= cid - offset < num_rooms}

    def get_room_flavor_notes(
        self, grid_size: int = 100, custom_flavors: Optional[List[Dict]] = None, id_base: Optional[int] = None
    ) -> List[Dict]:
        """各部屋のフレーバーノート（名前・情景描写・アイコン・座標）を生成。AI生成カスタムフレーバーにも対応"""
        num_rooms = len(self.rooms)
        if num_rooms == 0:
            return []

        # custom_flavors が提供されている場合、部屋インデックス (0始まり) による辞書化
        cf_map = self._index_custom_flavors(custom_flavors, id_base=id_base)

        themes = [
            {
                "type": "armory",
                "title": "朽ちた武器庫",
                "icon": "icons/svg/sword.svg",
                "flavor": "壁際の木製ラックは崩れ落ち、錆びついた長剣や歪んだ鉄盾が散乱している。鉄錆と湿気た土の混ざった臭気が鼻をつき、古い戦いの名残を感じさせる。"
            },
            {
                "type": "crypt",
                "title": "眠れる納骨堂",
                "icon": "icons/svg/skull.svg",
                "flavor": "壁に穿たれた無数の窪みに、風化した頭蓋骨と骨が几帳面に並べられている。中央には古い石棺が鎮座し、かすかな悪寒が冒険者の背筋を走る。"
            },
            {
                "type": "shrine",
                "title": "廃れた偶像の礼拝堂",
                "icon": "icons/svg/temple.svg",
                "flavor": "倒れた石の長椅子の先に、顔を削り取られた異形の神像が佇んでいる。微かな魔力の残り香があり、床の退色した魔法陣が淡く揺らめいている。"
            },
            {
                "type": "treasury",
                "title": "忘れ去られた宝物庫",
                "icon": "icons/svg/chest.svg",
                "flavor": "頑丈な鉄扉の奥の小部屋。埃をかぶった木箱や真鍮の金具が置かれ、床の隅にはかつて黄金を求めて力尽きた冒険者の遺留品が転がっている。"
            },
            {
                "type": "lab",
                "title": "錬金術師の作業場",
                "icon": "icons/svg/eye.svg",
                "flavor": "割れたガラス管や変色した羊皮紙が作業台に散乱している。酸っぱい薬品の刺激臭が漂い、奥の棚には怪しげな液体が満たされたフラスコが残る。"
            },
            {
                "type": "prison",
                "title": "鎖された地下牢",
                "icon": "icons/svg/door-steel.svg",
                "flavor": "太い鉄格子で仕切られた湿った牢屋。壁には錆びた手枷と鎖が吊るされ、床の染みは何者かが長く苦しめられた凄惨な過去を物語っている。"
            },
            {
                "type": "library",
                "title": "湿った図書保管庫",
                "icon": "icons/svg/book.svg",
                "flavor": "湿気で歪んだ本棚に、カビだらけの革装丁の書物が詰まっている。触れれば崩れそうな羊皮紙の山から、古いインクと埃の匂いが立ち込めている。"
            },
            {
                "type": "barracks",
                "title": "旧衛兵詰所",
                "icon": "icons/svg/shield.svg",
                "flavor": "腐食した木製テーブルと倒れた椅子が残された部屋。かつてここで番をしていた衛兵たちのサイコロや割れた酒瓶が転がっている。"
            },
            {
                "type": "cavern",
                "title": "落石の洞穴",
                "icon": "icons/svg/trap.svg",
                "flavor": "天井の一部が崩落し、鋭い巨岩が床を塞いでいる。岩の隙間からは冷涼な風が吹き抜け、どこからかコツコツと岩を叩くような不気味な物音が反響する。"
            },
            {
                "type": "dining",
                "title": "朽ち果てた大食堂",
                "icon": "icons/svg/statue.svg",
                "flavor": "長い食卓と食器の破片が散らばる広間。中央の暖炉には冷え切った灰が残り、天井の蜘蛛の巣が微風に揺れている。"
            },
            {
                "type": "storeroom",
                "title": "地下貯蔵庫",
                "icon": "icons/svg/door-closed.svg",
                "flavor": "巨大な木樽が転がり、腐った穀物や麻袋が山積みになっている。足元を素早く走るネズミや害虫の気配が闇の中に消えていく。"
            },
            {
                "type": "guard_post",
                "title": "哨戒の監視所",
                "icon": "icons/svg/target.svg",
                "flavor": "通路を見渡す位置に作られた防衛拠点。銃眼のような覗き窓が穿たれ、床には使い古された矢やボウガンの矢柄が落ちている。"
            }
        ]

        # シャッフルしたテーマリストを用意（シード値依存で再現性あり）
        theme_pool = list(MODE_FALLBACK_THEMES.get(self.scene_mode, themes))
        self.rng.shuffle(theme_pool)

        results = []
        for i in range(num_rooms):
            rx, ry, rw, rh = self.rooms[i]
            cx, cy = self.room_centers[i]

            # AI生成カスタムフレーバーが存在するかチェック
            cf = cf_map.get(i)

            label = ROOM_LABEL[self.scene_mode]
            endpoints = MODE_ENDPOINTS[self.scene_mode]
            light_preset = None
            ambient_sound_src = None
            if cf:
                room_type = cf.get("role") or "room"
                title = cf.get("title") or f"{label} {i + 1}"
                icon = cf.get("icon") or "icons/svg/door-steel.svg"
                flavor = cf.get("tts_narration") or cf.get("flavor", "")
                gm_notes = cf.get("gm_notes", "")
                is_empty = bool(cf.get("is_empty", False))
                safe_rest = bool(cf.get("safe_rest", False))
                light_preset = cf.get("light_preset") if cf.get("light_preset") in LIGHT_PRESET_KEYS else None
                ambient_sound_src = cf.get("ambient_sound_src") or None
            elif i == self.start_room_idx:
                room_type = "start"
                title, icon, flavor, gm_notes = endpoints["start"]
                is_empty = False
                safe_rest = True
            elif i == self.goal_room_idx:
                room_type = "goal"
                title, icon, flavor, gm_notes = endpoints["goal"]
                is_empty = False
                safe_rest = False
            else:
                tmpl = theme_pool[(i - 1) % len(theme_pool)]
                room_type = tmpl["type"]
                title = tmpl["title"]
                icon = tmpl["icon"]
                flavor = tmpl["flavor"]
                gm_notes = ""
                is_empty = False
                safe_rest = False

            prefix = f"{label} {i + 1}"
            results.append({
                "id": i + 1,
                "name": f"{prefix}: {title}" if not title.startswith(prefix) else title,
                "title": title,
                # role と type は同じ値 (reference 版は role、旧版は type を参照していたため両方出す)
                "type": room_type,
                "role": room_type,
                "light_preset": light_preset,
                "ambient_sound_src": ambient_sound_src,
                "icon": icon,
                "grid_x": rx,
                "grid_y": ry,
                "grid_w": rw,
                "grid_h": rh,
                "cx": cx * grid_size + grid_size // 2,
                "cy": cy * grid_size + grid_size // 2,
                "flavor": flavor,
                "gm_notes": gm_notes,
                "is_empty": is_empty,
                "safe_rest": safe_rest
            })

        return results


def parse_size(size_str: str) -> Tuple[int, int]:
    """サイズ引数 ('small', 'medium', 'large', 'xlarge' または 'WxH') を解析"""
    preset = size_str.lower().strip()
    if preset in ["small", "s", "小"]:
        return 20, 20
    elif preset in ["medium", "m", "中"]:
        return 50, 50
    elif preset in ["large", "l", "大"]:
        return 100, 100
    elif preset in ["xlarge", "xl", "巨大", "特大", "huge"]:
        return 200, 200
    elif "x" in preset or "," in preset:
        sep = "x" if "x" in preset else ","
        parts = preset.split(sep)
        try:
            return int(parts[0].strip()), int(parts[1].strip())
        except ValueError:
            pass
    return 20, 20


def parse_density(density_str: Optional[str]) -> Optional[float]:
    """密度引数を解析"""
    if not density_str:
        return None
    val = density_str.lower().strip()
    if val in ["low", "低"]:
        return 0.25
    elif val in ["medium", "中"]:
        return 0.50
    elif val in ["high", "高"]:
        return 0.80
    try:
        return float(val)
    except ValueError:
        return None


def main():
    parser = argparse.ArgumentParser(
        description="ダンジョン自動生成ツール (MST全連結保証・数学的パラメータ制御)"
    )
    parser.add_argument(
        "-s", "--seed",
        type=int,
        default=None,
        help="乱数シード値 (未指定時はランダム)"
    )
    parser.add_argument(
        "--size",
        type=str,
        default="small",
        help="サイズプリセット: small/小(20x20), medium/中(50x50), large/大(100x100), xlarge/巨大(200x200), または WxH (既定: small)"
    )
    parser.add_argument(
        "-w", "--width",
        type=int,
        default=None,
        help="ダンジョンの横幅 (指定時は --size の幅を上書き)"
    )
    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="ダンジョンの縦幅 (指定時は --size の高さを上書き)"
    )
    parser.add_argument(
        "-d", "--density",
        type=str,
        default=None,
        help="部屋の密度: low/低, medium/中, high/高 または 0.1〜1.0 (未指定時はランダム)"
    )
    parser.add_argument(
        "-l", "--loop-ratio",
        type=float,
        default=None,
        help="ループ率 (サイクロマティック数制御: 0.0=純木構造/袋小路のみ 〜 0.5=周回路多数。未指定時はランダム)"
    )
    parser.add_argument(
        "-m", "--mode",
        choices=SCENE_MODES,
        default="underground",
        help="シーンモード: underground (地下迷宮) / ruins (地上の廃墟) / wilderness (野外・壁なし) (既定: underground)"
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="ダンジョン出力ファイルパス (例: dungeon.txt)"
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="スタート地点周辺のプレビューのみ表示"
    )
    parser.add_argument(
        "--print-all",
        action="store_true",
        help="コンソールにマップ全行を出力 (巨大サイズ時は非推奨)"
    )

    args = parser.parse_args()

    width, height = parse_size(args.size)
    if args.width:
        width = args.width
    if args.height:
        height = args.height

    density = parse_density(args.density)

    generator = DungeonGenerator(
        width=width,
        height=height,
        seed=args.seed,
        density=density,
        loop_ratio=args.loop_ratio,
        scene_mode=args.mode
    )
    generator.generate()

    print("=" * 65)
    print("【ダンジョン生成完了 (数学的グラフ検証済)】")
    print(f"・シード値 (Seed)         : {generator.seed}")
    print(f"・モード (Scene Mode)     : {generator.scene_mode}")
    print(f"・サイズ (Dimensions)     : {generator.width} x {generator.height} マス ({generator.width * generator.height:,} セル)")
    print(f"・部屋密度 (Density)      : {generator.density}")
    print(f"・部屋数 (|V|)            : {len(generator.rooms)} 部屋 (全域木連結保証)")
    print(f"・通路接続数 (|E|)        : {len(generator.edges)} 接続")
    print(f"・ループ度 (Cyclomatic M) : {generator.cyclomatic_number} (独立閉路数)")
    print(f"・充填率 (Fill Ratio: ρ)  : {generator.get_fill_ratio() * 100:.2f}%")
    print(f"・スタート位置 (Start: ◆) : (X: {generator.start_pos[0]}, Y: {generator.start_pos[1]})")
    print(f"・最深部ゴール (Goal: ★)  : (X: {generator.goal_pos[0]}, Y: {generator.goal_pos[1]}) [最遠深さ: {generator.max_depth} 部屋]")
    print(f"・外周壁封鎖 (Outer Walls): 100% 密閉保証 (■)")
    print(f"・生成時間 (Time)         : {generator.elapsed_time:.3f} 秒")
    print("=" * 65)

    # 出力ファイルパスの決定 (中・大・特大で未指定の場合は安全のため自動保存)
    output_path = args.output
    if not output_path and (width >= 500 or height >= 500):
        output_path = f"dungeon_{width}x{height}_seed{generator.seed}.txt"

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            for row in generator.grid:
                f.write("".join(row) + "\n")
        print(f"ダンジョンマップを保存しました: {output_path}\n")

    # プレビュー表示
    if args.print_all:
        print(generator.to_string())
    else:
        print("--- スタート地点 (◆) 周辺プレビュー (40x20マス) ---")
        print(generator.get_preview(center=generator.start_pos, width=40, height=20))
        print("--------------------------------------------------")
        print("--- 最深部ゴール (★) 周辺プレビュー (40x20マス) ---")
        print(generator.get_preview(center=generator.goal_pos, width=40, height=20))
        print("--------------------------------------------------")
        if not output_path:
            print("※ 全体マップをファイルに保存するには -o オプション (例: -o dungeon.txt) を指定してください。")


if __name__ == "__main__":
    main()
