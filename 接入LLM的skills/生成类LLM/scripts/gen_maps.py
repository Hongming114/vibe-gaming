# -*- coding: utf-8 -*-
"""城市地图生成器（示例实现，可直接跑）。

用法：
    python gen_maps.py                    # 按 PLANS 生成 5 张图到 OUT_DIR
    python gen_maps.py --out D:\\某目录    # 换输出目录
    python gen_maps.py --matrix           # 额外导出 I 矩阵 / 门表 / & 表

依赖：Pillow（字体用系统微软雅黑）。
改这三个地方就能出新城：PLANS（seed/尺寸/骨架/数量）、build_roads（骨架）、
place() 的 score 函数（位置规则）。

规则来源：自然语言蓝图/总设定.txt、城市/城市生成.txt
  1. 城市是 1x1 为单位的坐标网格；居民区/位面传送门/宫殿 2x2
  2. 相邻且可达 = 门；相邻但不可达 = 墙
  3. & 对：公园&林地、教堂&墓地、冒险者协会&政府、魔法协会&科学协会 必须相邻合并
  4. 每个区域至少与道路相邻且有一处门
  5. 数量：居民区 2-3、公园&墓地 1-2、酒馆/城市公园/市场各 1-3，其余唯一
  6. 位置：传送门居中；公园/教堂在末梢；协会/政府近中心；家挂路末；宫殿远离住宅靠公园
"""
import argparse
import os
import random
from PIL import Image, ImageDraw, ImageFont

SCALE = 2
CELL = 128
GAP = 9
PAD_L = 88
PAD_T = 246
PAD_R = 34
PAD_B = 92
LEGEND_W = 452

C_BG = (246, 243, 236)
C_GRID = (228, 222, 210)
C_TXT = (36, 33, 30)
C_SUB = (122, 115, 106)
C_SOFT = (152, 145, 135)
C_DOOR = (120, 132, 148)
C_WALL = (198, 190, 176)
C_PAIR = (198, 96, 96)

KIND = {
    "road":   ((224, 228, 233), (104, 114, 128), "道路"),
    "home":   ((248, 226, 168), (146, 106, 26), "住宅"),
    "shop":   ((247, 210, 180), (150, 88, 44), "商业·服务"),
    "public": ((188, 214, 238), (44, 90, 138), "公共设施"),
    "park":   ((196, 226, 192), (56, 112, 58), "公园·绿地"),
    "core":   ((250, 212, 226), (152, 52, 90), "位面传送门"),
    "palace": ((232, 216, 244), (92, 60, 132), "宫殿"),
}


def font(size, bold=False):
    path = r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc"
    return ImageFont.truetype(path, int(round(size * SCALE)))


# ---------------------------------------------------------------- 布局生成

class City:
    def __init__(self, w, h, seed):
        self.w, self.h = w, h
        self.rng = random.Random(seed)
        self.occ = [[None] * h for _ in range(w)]   # (name, idx)
        self.nodes = []                             # dict
        self.edges = []
        self.pairs = []

    # --- 基础工具
    def free(self, c, r, w=1, h=1):
        if c < 0 or r < 0 or c + w > self.w or r + h > self.h:
            return False
        for x in range(c, c + w):
            for y in range(r, r + h):
                if self.occ[x][y] is not None:
                    return False
        return True

    def put(self, name, c, r, w, h, kind, note=""):
        idx = len(self.nodes)
        self.nodes.append(dict(name=name, c=c, r=r, w=w, h=h, kind=kind, note=note, idx=idx))
        for x in range(c, c + w):
            for y in range(r, r + h):
                self.occ[x][y] = idx
        return idx

    def at(self, c, r):
        if 0 <= c < self.w and 0 <= r < self.h:
            return self.occ[c][r]
        return None

    def is_road(self, c, r):
        i = self.at(c, r)
        return i is not None and self.nodes[i]["kind"] == "road"

    def cell_kind(self, c, r):
        i = self.at(c, r)
        return None if i is None else self.nodes[i]["kind"]

    def adj_roads(self, node):
        """与 node 边界相邻的道路 (c,r) 列表"""
        out = []
        c, r, w, h = node["c"], node["r"], node["w"], node["h"]
        coords = set()
        for x in range(c, c + w):
            coords.add((x, r - 1)); coords.add((x, r + h))
        for y in range(r, r + h):
            coords.add((c - 1, y)); coords.add((c + w, y))
        for (x, y) in coords:
            if self.is_road(x, y):
                out.append((x, y))
        return out

    def dist_center(self, node):
        cx = node["c"] + (node["w"] - 1) / 2
        cy = node["r"] + (node["h"] - 1) / 2
        return abs(cx - (self.w - 1) / 2) + abs(cy - (self.h - 1) / 2)

    # --- 路网
    def build_roads(self, pattern):
        w, h = self.w, self.h
        rows, cols = [], []
        if pattern == "cross":
            rows = [h // 2]
            cols = [w // 2]
        elif pattern == "double":
            rows = [max(2, h // 3), min(h - 3, 2 * h // 3)]
            cols = [w // 2]
        elif pattern == "comb":
            rows = [h // 2]
            cols = [w // 3, 2 * w // 3]
        elif pattern == "ring":
            rows = [2, h - 3]
            cols = [2, w - 3]
        else:  # "h"
            rows = [max(2, h // 3), min(h - 3, 2 * h // 3)]
            cols = [w // 3, 2 * w // 3]

        cells = set()
        if pattern == "ring":
            r0, r1 = rows
            c0, c1 = cols
            for c in range(c0, c1 + 1):
                cells.add((c, r0)); cells.add((c, r1))
            for r in range(r0, r1 + 1):
                cells.add((c0, r)); cells.add((c1, r))
        else:
            for r in rows:
                for c in range(1, w - 1):
                    cells.add((c, r))
            for c in cols:
                for r in range(1, h - 1):
                    cells.add((c, r))

        # 支路：只沿单一方向生长（新格必须只有 1 个道路邻居），避免长出 2 格宽的路板
        def road_nb(x, y):
            return sum(1 for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)) if (x + dx, y + dy) in cells)

        for _ in range(4):
            base = self.rng.choice(sorted(cells))
            dx, dy = self.rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
            c, r = base
            for step in range(self.rng.randint(2, 3)):
                c, r = c + dx, r + dy
                if not (0 <= c < w and 0 <= r < h) or (c, r) in cells:
                    break
                if road_nb(c, r) != 1:
                    break
                cells.add((c, r))

        def blocks_of(c, r):
            return {(c, r), (c + 1, r), (c, r + 1), (c + 1, r + 1)}

        def roads_connected(cs):
            if not cs:
                return True
            start = next(iter(cs))
            seen, stack = {start}, [start]
            while stack:
                x, y = stack.pop()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    n = (x + dx, y + dy)
                    if n in cs and n not in seen:
                        seen.add(n); stack.append(n)
            return len(seen) == len(cs)

        # 城心 2×2 先让给位面传送门（规则：传送门居中，先定它再铺路）
        pc, pr = (w - 2) // 2, (h - 2) // 2
        portal_cells = {(pc, pr), (pc + 1, pr), (pc, pr + 1), (pc + 1, pr + 1)}
        cells -= portal_cells
        self.portal_slot = (pc, pr)


        # 一路闭环：先消掉 2×2 纯道路，再保证所有路面是同一张网，两者互相干扰就再跑一轮。
        def components_of(cs):
            comps = []
            left = set(cs)
            while left:
                start = next(iter(left))
                seen, stack = {start}, [start]
                while stack:
                    x, y = stack.pop()
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        n = (x + dx, y + dy)
                        if n in left and n not in seen:
                            seen.add(n); stack.append(n)
                comps.append(seen)
                left -= seen
            return comps

        def kill_windows():
            for _ in range(40):
                win = None
                for r in range(h - 1):
                    for c in range(w - 1):
                        blk = blocks_of(c, r)
                        if blk & portal_cells:
                            continue
                        if all(q in cells for q in blk):
                            win = (c, r)
                            break
                    if win:
                        break
                if win is None:
                    return
                c, r = win
                order = sorted(((c + dx, r + dy) for dx in (0, 1) for dy in (0, 1)),
                               key=lambda t: -(abs(t[0] - cx0) + abs(t[1] - cy0)))
                removed = False
                for t in order:
                    if t in portal_cells:
                        continue
                    cells.discard(t)
                    if roads_connected(cells):
                        removed = True
                        break
                    cells.add(t)
                if not removed:
                    return

        def join_components():
            """把最近的两块路面用最短的一条新路接起来（传送门格可穿但不可铺路）"""
            import heapq
            comps = components_of(set(cells) | portal_cells)
            if len(comps) < 2:
                return False
            main = max(comps, key=len)
            other = set().union(*[c for c in comps if c is not main])
            dist = {}
            prev = {}
            pq = []
            for (x, y) in main:
                dist[(x, y)] = 0
                heapq.heappush(pq, (0, x, y))
            while pq:
                d, x, y = heapq.heappop(pq)
                if d > dist.get((x, y), 1e9):
                    continue
                if (x, y) in other:
                    path = []
                    cur = (x, y)
                    while cur in prev:
                        path.append(cur)
                        cur = prev[cur]
                    added = 0
                    for (px, py) in path:
                        if (px, py) in portal_cells or (px, py) in cells:
                            continue
                        cells.add((px, py))
                        added += 1
                    return added > 0
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    n = (x + dx, y + dy)
                    if not (0 <= n[0] < w and 0 <= n[1] < h):
                        continue
                    if n in portal_cells:
                        cost = 0
                    elif n in cells or n in other:
                        cost = 0
                    else:
                        cost = 1
                    nd = d + cost
                    if nd < dist.get(n, 1e9):
                        dist[n] = nd
                        prev[n] = (x, y)
                        heapq.heappush(pq, (nd, n[0], n[1]))
            return False

        for _ in range(4):
            kill_windows()
            if len(components_of(set(cells) | portal_cells)) < 2:
                break
            join_components()

        # 城心槽位要接上路：四周若没有路，就补一条 L 形短路
        def touches(cs, blk):
            for (c, r) in blk:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    if (c + dx, r + dy) in cs:
                        return True
            return False

        if cells and not touches(cells, portal_cells):
            near = min(cells, key=lambda t: abs(t[0] - pc) + abs(t[1] - pr))
            x, y = near
            while x != pc:
                x += 1 if pc > x else -1
                if (x, y) not in portal_cells:
                    cells.add((x, y))
            while y != pr:
                y += 1 if pr > y else -1
                if (x, y) not in portal_cells:
                    cells.add((x, y))

        for (c, r) in sorted(cells):
            self.put("路", c, r, 1, 1, "road")
        return

    # --- 放置
    def candidates(self, w, h, pred=None):
        out = []
        for c in range(self.w - w + 1):
            for r in range(self.h - h + 1):
                if not self.free(c, r, w, h):
                    continue
                node = dict(c=c, r=r, w=w, h=h)
                if pred and not pred(node):
                    continue
                out.append(node)
        return out

    def occupied_neighbors(self, nd):
        c, r, w, h = nd["c"], nd["r"], nd["w"], nd["h"]
        n = 0
        for x in range(c, c + w):
            for y in (r - 1, r + h):
                if self.at(x, y) is not None:
                    n += 1
        for y in range(r, r + h):
            for x in (c - 1, c + w):
                if self.at(x, y) is not None:
                    n += 1
        return n

    def place(self, name, kind, w, h, score, note="", pair_with=None, tries=400):
        """在候选里按 score 取最优（带随机扰动）"""
        cands = self.candidates(w, h)
        if not cands:
            return None
        self.rng.shuffle(cands)
        best, best_s = None, -1e9
        for nd in cands[:tries]:
            roads = self.adj_roads(nd)
            if not roads and kind != "road":
                continue
            s = score(nd, roads)
            if s is None:
                continue
            s += self.occupied_neighbors(nd) * 1.3 + self.rng.random() * 0.6
            if s > best_s:
                best, best_s = nd, s
        if best is None:
            return None
        return self.put(name, best["c"], best["r"], w, h, kind, note)

    # --- 生成主流程
    def generate(self, pattern, plan):
        self.build_roads(pattern)

        # 位面传送门 2x2 居中（优先用 build_roads 预留的城心槽位）
        cx, cy = (self.w - 2) // 2, (self.h - 2) // 2
        spot = getattr(self, "portal_slot", None)
        if spot is not None and not self.free(spot[0], spot[1], 2, 2):
            spot = None
        for d in range(0, 6):
            for dc in range(-d, d + 1):
                for dr in range(-d, d + 1):
                    c, r = cx + dc, cy + dr
                    if self.free(c, r, 2, 2):
                        nd = dict(c=c, r=r, w=2, h=2)
                        if self.adj_roads(nd):
                            spot = (c, r)
                            break
                if spot: break
            if spot: break
        if spot:
            self.put("位面传送门", spot[0], spot[1], 2, 2, "core", "核心 2×2")

        homes = []
        # 居民区 2-3 个，远离中心
        for i in range(plan["homes"]):
            def sc(nd, roads, homes=homes):
                d = self.dist_center(nd)
                if d < 2.0:
                    return None
                for hm in homes:
                    if abs(hm["c"] - nd["c"]) + abs(hm["r"] - nd["r"]) < 3:
                        return None
                return d * 1.4
            idx = self.place("居民区", "home", 2, 2, sc, "住宅 2×2")
            if idx is not None:
                homes.append(self.nodes[idx])

        def near_homes(nd, roads):
            if not homes:
                return 1.0
            return -min(abs(h["c"] - nd["c"]) + abs(h["r"] - nd["r"]) for h in homes) * 0.8

        def dead_end(nd, roads):
            best = 0
            for (c, r) in roads:
                n = sum(1 for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)) if self.is_road(c + dx, r + dy))
                best = max(best, 4 - n)
            return best * 0.9 + self.dist_center(nd) * 0.35

        def center_bias(nd, roads):
            return -self.dist_center(nd) * 0.9

        # 家：挂在路末
        self.place("家", "home", 1, 1, lambda nd, roads: dead_end(nd, roads) + 1.2, "住宅")

        # 商业：靠近住宅
        for i in range(plan["taverns"]):
            self.place("酒馆", "shop", 1, 1, near_homes, "商业·服务")
        for i in range(plan["markets"]):
            self.place("市场", "shop", 1, 1, near_homes, "商业·服务")
        for i in range(plan["cityparks"]):
            self.place("城市公园", "park", 1, 1, near_homes, "公园·绿地")

        # 中心区
        self.place("地标", "public", 1, 1, center_bias, "公共设施")

        # & 对（相邻）：冒险者协会&政府、魔法协会&科学协会
        self.place_pair("冒险者协会", "政府", "public", "public", center_bias)
        self.place_pair("魔法协会", "科学协会", "public", "public", center_bias)
        self.place_pair("教堂", "墓地", "public", "public", lambda nd, roads: dead_end(nd, roads) * 0.8 - self.dist_center(nd) * 0.2)
        self.place_pair("公园", "林地", "park", "park", lambda nd, roads: dead_end(nd, roads) * 1.1)

        # 宫殿 2x2：远离住宅、靠近公园
        parks = [n for n in self.nodes if n["kind"] == "park"]
        homes2 = [n for n in self.nodes if n["kind"] == "home"]

        portal = next((n for n in self.nodes if n['kind'] == 'core'), None)

        def palace_score(nd, roads):
            if portal is not None:
                gap = max(abs(portal['c'] - nd['c']), abs(portal['r'] - nd['r']))
                if gap <= 1:
                    return None
            if homes2:
                dh = min(abs(h["c"] - nd["c"]) + abs(h["r"] - nd["r"]) for h in homes2)
                if dh < 3:
                    return None
            else:
                dh = 3
            dp = min((abs(p["c"] - nd["c"]) + abs(p["r"] - nd["r"]) for p in parks), default=6)
            return dh * 0.9 - dp * 0.8
        self.place("宫殿", "palace", 2, 2, palace_score, "宫殿 2×2")

        self.prune_orphans()
        self.fill_with_roads()
        self.build_edges()
        self.bridge_components()

    def fill_with_roads(self, max_add=10):
        """中心区不许留空洞：从已有道路往外接 1 格短支路把洞吃掉（只用道路，不引入新区域类型）"""
        cx, cy = (self.w - 1) / 2, (self.h - 1) / 2
        added = 0
        while added < max_add:
            best = None
            for c in range(self.w):
                for r in range(self.h):
                    if self.at(c, r) is not None:
                        continue
                    nb = 0
                    road_nb = 0
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        j = self.at(c + dx, r + dy)
                        if j is None:
                            continue
                        nb += 1
                        if self.nodes[j]["kind"] in ("road", "core"):
                            road_nb += 1
                    if road_nb < 1 or nb < 2:
                        continue
                    near = max(abs(c - cx), abs(r - cy))
                    if near > 3.0:
                        continue
                    score = nb * 2 + (3 - near) - abs(road_nb - 1)
                    if best is None or score > best[0]:
                        best = (score, c, r)
            if best is None:
                break
            _, c, r = best
            # 不许因此凑出 2×2 纯道路
            probe = {(c, r)}
            bad = False
            for dc in (0, 1):
                for dr in (0, 1):
                    box = [(c - dc + i, r - dr + j) for i in (0, 1) for j in (0, 1)]
                    if all((self.is_road(x, y) or (x, y) in probe) for (x, y) in box):
                        bad = True
            if bad:
                break
            self.put("路", c, r, 1, 1, "road")
            added += 1
        return added

    def place_pair(self, na, nb, ka, kb, score):
        """找一个 1x2 或 2x1 的双格位置，两格都属于同一 & 对"""
        best = None
        best_s = -1e9
        for horiz in (True, False):
            w, h = (2, 1) if horiz else (1, 2)
            for c in range(self.w - w + 1):
                for r in range(self.h - h + 1):
                    if not self.free(c, r, w, h):
                        continue
                    a = dict(c=c, r=r, w=1, h=1)
                    b = dict(c=c + (1 if horiz else 0), r=r + (0 if horiz else 1), w=1, h=1)
                    ra = self.adj_roads(a)
                    rb = self.adj_roads(b)
                    if not ra and not rb:
                        continue
                    s = score(a, ra) + self.rng.random() * 0.8
                    if s > best_s:
                        best, best_s = (c, r, w, h, horiz), s
        if best is None:
            return
        c, r, w, h, horiz = best
        ia = self.put(na, c, r, 1, 1, "public" if na not in ("公园", "林地") else "park", "公共设施" if na not in ("公园", "林地") else "公园·绿地")
        ib = self.put(nb, c + (1 if horiz else 0), r + (0 if horiz else 1), 1, 1,
                      "public" if nb not in ("公园", "林地") else "park",
                      "公共设施" if nb not in ("公园", "林地") else "公园·绿地")
        self.pairs.append((ia, ib))

    # --- 连边：道路互相连 + 每个区域连最近的道路
    def prune_orphans(self):
        """删掉没接上任何邻居的孤立格（多半是补路时留下的单格），避免路网断点。"""
        for _ in range(6):
            adj = {}
            for i, a in enumerate(self.nodes):
                for (x, y) in self.cell_neighbors(i):
                    j = self.at(x, y)
                    if j is not None and j != i:
                        adj.setdefault(i, set()).add(j)
                        adj.setdefault(j, set()).add(i)
            dead = [i for i in range(len(self.nodes)) if not adj.get(i)]
            if not dead:
                return 0
            for i in sorted(dead, reverse=True):
                n = self.nodes.pop(i)
                for x in range(n["c"], n["c"] + n["w"]):
                    for y in range(n["r"], n["r"] + n["h"]):
                        if (x, y) in self.occ:
                            del self.occ[(x, y)]
            for i, n in enumerate(self.nodes):
                n["idx"] = i
            self.pairs = [(ia, ib) for (ia, ib) in self.pairs if ia < len(self.nodes) and ib < len(self.nodes)]
        return 0

    def components(self):
        adj = {}
        for i, a in enumerate(self.nodes):
            for (x, y) in self.cell_neighbors(i):
                j = self.at(x, y)
                if j is not None and j != i:
                    adj.setdefault(i, set()).add(j)
                    adj.setdefault(j, set()).add(i)
        seen, comps = set(), []
        for i in range(len(self.nodes)):
            if i in seen:
                continue
            stack, comp = [i], []
            seen.add(i)
            while stack:
                k = stack.pop()
                comp.append(k)
                for j in adj.get(k, ()):
                    if j not in seen:
                        seen.add(j)
                        stack.append(j)
            comps.append(comp)
        return comps

    def bridge_components(self):
        """把碎掉的路网用 1 格宽的小路接起来，保证全城连通。"""
        for _ in range(8):
            comps = self.components()
            if len(comps) < 2:
                return 0
            comps.sort(key=len, reverse=True)
            main = set(comps[0])
            best = None
            for comp in comps[1:]:
                for i in comp:
                    a = self.nodes[i]
                    for j in main:
                        b = self.nodes[j]
                        d = abs(a["c"] - b["c"]) + abs(a["r"] - b["r"])
                        if best is None or d < best[0]:
                            best = (d, i, j)
            if best is None:
                return 0
            _, i, j = best
            a, b = self.nodes[i], self.nodes[j]
            x, y = a["c"], a["r"]
            tx, ty = b["c"], b["r"]
            path = []
            while x != tx:
                x += 1 if tx > x else -1
                path.append((x, y))
            while y != ty:
                y += 1 if ty > y else -1
                path.append((x, y))
            for (cx, cy) in path:
                if 0 <= cx < self.w and 0 <= cy < self.h and self.at(cx, cy) is None:
                    self.put("路", cx, cy, 1, 1, "road")
            self.build_edges()
        return 0

    def build_edges(self):
        e = set()
        for i, a in enumerate(self.nodes):
            if a["kind"] != "road":
                continue
            for dx, dy in ((1, 0), (0, 1)):
                j = self.at(a["c"] + dx, a["r"] + dy)
                if j is not None and self.nodes[j]["kind"] == "road":
                    e.add(tuple(sorted((i, j))))
        for i, a in enumerate(self.nodes):
            if a["kind"] == "road":
                continue
            rs = self.adj_roads(a)
            if not rs and a["kind"] not in ("core",):
                continue
            if a["kind"] in ("core",):
                # 广场/传送门是路网节点：四邻全部开门，保证路网不被它们切断
                c0, r0, w0, h0 = a["c"], a["r"], a["w"], a["h"]
                for (c, r) in rs:
                    j = self.at(c, r)
                    if j is not None:
                        e.add(tuple(sorted((i, j))))
                for x in range(c0 - 1, c0 + w0 + 1):
                    for y in (r0 - 1, r0 + h0):
                        j = self.at(x, y)
                        if j is not None and self.nodes[j]["kind"] in ("core",):
                            e.add(tuple(sorted((i, j))))
                for y in range(r0, r0 + h0):
                    for x in (c0 - 1, c0 + w0):
                        j = self.at(x, y)
                        if j is not None and self.nodes[j]["kind"] in ("core",):
                            e.add(tuple(sorted((i, j))))
            else:
                j = self.at(rs[0][0], rs[0][1])
                e.add(tuple(sorted((i, j))))
        for (ia, ib) in self.pairs:
            e.add(tuple(sorted((ia, ib))))
        self.edge_idx = sorted(e)

    # --- 校验
    def cell_neighbors(self, idx):
        n = self.nodes[idx]
        out = set()
        for x in range(n["c"], n["c"] + n["w"]):
            out.add((x, n["r"] - 1)); out.add((x, n["r"] + n["h"]))
        for y in range(n["r"], n["r"] + n["h"]):
            out.add((n["c"] - 1, y)); out.add((n["c"] + n["w"], y))
        return out

    def reaches_road(self, idx):
        """传送门可以当通道，只要最终能走到一条道路就算接路。"""
        seen, stack = {idx}, [idx]
        while stack:
            k = stack.pop()
            if self.nodes[k]["kind"] == "road":
                return True
            n = self.nodes[k]
            for (x, y) in self.cell_neighbors(k):
                if not (0 <= x < self.w and 0 <= y < self.h):
                    continue
                j = self.at(x, y)
                if j is None or j in seen:
                    continue
                if self.nodes[j]["kind"] in ("road", "core"):
                    seen.add(j); stack.append(j)
        return False

    def validate(self):
        bad = []
        pair_of = {}
        for (ia, ib) in self.pairs:
            pair_of[ia] = ib
            pair_of[ib] = ia
        for a in self.nodes:
            if a["kind"] == "road":
                continue
            if self.reaches_road(a["idx"]):
                continue
            partner = pair_of.get(a["idx"])
            if partner is not None and self.reaches_road(partner):
                continue
            bad.append(f"{a['name']}({a['c']},{a['r']}) 未接路")
        # 不许出现 2x2 纯道路
        for c in range(self.w - 1):
            for r in range(self.h - 1):
                cells4 = [(c, r), (c + 1, r), (c, r + 1), (c + 1, r + 1)]
                if all(self.is_road(x, y) for (x, y) in cells4):
                    bad.append(f"({c},{r}) 出现 2×2 纯道路")
        # 连通性
        seen = set([0]) if self.nodes else set()
        stack = [0] if self.nodes else []
        adj = {}
        for (i, j) in self.edge_idx:
            adj.setdefault(i, []).append(j)
            adj.setdefault(j, []).append(i)
        while stack:
            k = stack.pop()
            for j in adj.get(k, []):
                if j not in seen:
                    seen.add(j)
                    stack.append(j)
        if len(seen) != len(self.nodes):
            miss = [n["name"] for i, n in enumerate(self.nodes) if i not in seen]
            bad.append("不连通：" + "、".join(miss[:6]))
        # 道路必须是一整张连通网：所有路面格（含传送门当路口）只允许 1 个连通块
        road_cells = set()
        for n in self.nodes:
            if n["kind"] in ("road", "core"):
                for x in range(n["c"], n["c"] + n["w"]):
                    for y in range(n["r"], n["r"] + n["h"]):
                        road_cells.add((x, y))
        if road_cells:
            start = next(iter(road_cells))
            rseen = {start}
            rstack = [start]
            while rstack:
                x, y = rstack.pop()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nb = (x + dx, y + dy)
                    if nb in road_cells and nb not in rseen:
                        rseen.add(nb); rstack.append(nb)
            if len(rseen) != len(road_cells):
                bad.append(f"道路被割裂：{len(road_cells) - len(rseen)} 格路接不上主网")
        # 中心区不许大洞：以中心 3x3 为界
        cx, cy = (self.w - 1) / 2, (self.h - 1) / 2
        hole = 0
        for c in range(self.w):
            for r in range(self.h):
                if max(abs(c - cx), abs(r - cy)) > 2 or self.at(c, r) is not None:
                    continue
                touch = False
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    j = self.at(c + dx, r + dy)
                    if j is not None and self.nodes[j]["kind"] in ("road", "core"):
                        touch = True
                if touch:
                    hole += 1
        if hole > 0:
            self.hole_warn = f"中心留空 {hole} 处（细巷口，无填充物）"
        cnt = {}
        for n in self.nodes:
            cnt[n["name"]] = cnt.get(n["name"], 0) + 1
        return bad, cnt


# ---------------------------------------------------------------- 绘制

def render(city, title, subtitle, out, principle_lines):
    w, h = city.w, city.h
    step = CELL + GAP
    W = PAD_L + w * CELL + (w - 1) * GAP + PAD_R + LEGEND_W
    H = PAD_T + h * CELL + (h - 1) * GAP + PAD_B
    img = Image.new("RGB", (int(W * SCALE), int(H * SCALE)), C_BG)
    d = ImageDraw.Draw(img, "RGBA")

    def S(v):
        return v * SCALE

    def rx(c):
        return PAD_L + c * step

    def ry(r):
        return PAD_T + r * step

    for c in range(w):
        for r in range(h):
            d.rectangle([S(rx(c)), S(ry(r)), S(rx(c) + CELL), S(ry(r) + CELL)],
                        outline=C_GRID, width=max(1, int(1 * SCALE)))

    f_title = font(44, True)
    f_sub = font(20)
    f_head = font(24, True)
    f_name = font(26, True)
    f_name_s = font(21, True)
    f_small = font(18)
    f_tiny = font(15)
    f_road = font(23, True)

    d.text((S(PAD_L - 26), S(54)), title, font=f_title, fill=C_TXT)
    d.text((S(PAD_L - 26), S(114)), subtitle, font=f_sub, fill=C_SUB)

    for c in range(w):
        d.text((S(rx(c) + CELL / 2), S(PAD_T - 40)), chr(ord("A") + c), font=f_head, fill=C_SOFT, anchor="mm")
    for r in range(h):
        d.text((S(PAD_L - 44), S(ry(r) + CELL / 2)), str(r + 1), font=f_head, fill=C_SOFT, anchor="mm")

    N = city.nodes
    box = {n["idx"]: (n["c"], n["r"], n["w"], n["h"]) for n in N}

    def adjacent(a, b):
        ca, ra, wa, ha = a
        cb, rb, wb, hb = b
        ox = min(ca + wa, cb + wb) - max(ca, cb)
        oy = min(ra + ha, rb + hb) - max(ra, rb)
        return (ox > 0 and (ra + ha == rb or rb + hb == ra)) or (oy > 0 and (ca + wa == cb or cb + wb == ca))

    linked = set(city.edge_idx)

    # 墙
    ids = list(box.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = box[ids[i]], box[ids[j]]
            if not adjacent(a, b):
                continue
            if tuple(sorted((ids[i], ids[j]))) in linked:
                continue
            ca, ra, wa, ha = a
            cb, rb, wb, hb = b
            if ra + ha == rb or rb + hb == ra:
                y = ry(max(ra, rb))
                x0 = rx(max(ca, cb)); x1 = rx(min(ca + wa, cb + wb))
                d.line([(S(x0 + 6), S(y)), (S(x1 - 6), S(y))], fill=C_WALL, width=int(4 * SCALE))
            else:
                x = rx(max(ca, cb))
                y0 = ry(max(ra, rb)); y1 = ry(min(ra + ha, rb + hb))
                d.line([(S(x), S(y0 + 6)), (S(x), S(y1 - 6))], fill=C_WALL, width=int(4 * SCALE))

    # 门
    dw = CELL * 0.30
    for (i, j) in city.edge_idx:
        ca, ra, wa, ha = box[i]
        cb, rb, wb, hb = box[j]
        if ra + ha == rb or rb + hb == ra:
            ax, bx = max(rx(ca), rx(cb)), min(rx(ca + wa), rx(cb + wb))
            xm = (ax + bx) * 0.5
            y = ry(max(ra, rb))
            d.rectangle([S(xm - dw / 2), S(y - GAP), S(xm + dw / 2), S(y)], fill=C_DOOR)
        else:
            ay, by = max(ry(ra), ry(rb)), min(ry(ra + ha), ry(rb + hb))
            ym = (ay + by) * 0.5
            x = rx(max(ca, cb))
            d.rectangle([S(x - GAP), S(ym - dw / 2), S(x), S(ym + dw / 2)], fill=C_DOOR)

    # 方块
    for n in N:
        fill, ink, _ = KIND[n["kind"]]
        x0, y0 = rx(n["c"]), ry(n["r"])
        x1 = x0 + n["w"] * CELL + (n["w"] - 1) * GAP
        y1 = y0 + n["h"] * CELL + (n["h"] - 1) * GAP
        rad = 8 if n["kind"] == "road" else 12
        d.rounded_rectangle([S(x0), S(y0), S(x1), S(y1)], radius=S(rad), fill=fill,
                            outline=(0, 0, 0, 28), width=max(1, int(1 * SCALE)))
        if n["kind"] == "road":
            d.text((S((x0 + x1) / 2), S((y0 + y1) / 2)), "路", font=f_road, fill=ink, anchor="mm")
        else:
            f = f_name if len(n["name"]) <= 4 else f_name_s
            dy = -12 if n["note"] else 0
            d.text((S((x0 + x1) / 2), S((y0 + y1) / 2 + dy)), n["name"], font=f, fill=C_TXT, anchor="mm")
            if n["note"]:
                d.text((S((x0 + x1) / 2), S((y0 + y1) / 2 + 21)), n["note"], font=f_tiny, fill=ink, anchor="mm")
        coord = f"{chr(ord('A') + n['c'])}{n['r'] + 1}"
        if n["w"] > 1 or n["h"] > 1:
            coord += f"·{n['w']}×{n['h']}"
        d.text((S(x0 + 9), S(y0 + 7)), coord, font=f_tiny, fill=(0, 0, 0, 85), anchor="la")

    # & 对
    for (ia, ib) in city.pairs:
        ca, ra, wa, ha = box[ia]
        cb, rb, wb, hb = box[ib]
        x0 = min(rx(ca), rx(cb)) - 5
        y0 = min(ry(ra), ry(rb)) - 5
        x1 = max(rx(ca + wa - 1) + CELL, rx(cb + wb - 1) + CELL) + 5
        y1 = max(ry(ra + ha - 1) + CELL, ry(rb + hb - 1) + CELL) + 5
        d.rounded_rectangle([S(x0), S(y0), S(x1), S(y1)], radius=S(16), outline=C_PAIR, width=int(2.4 * SCALE))
        if ra + ha == rb or rb + hb == ra:
            bx = (max(rx(ca), rx(cb)) + min(rx(ca + wa), rx(cb + wb))) * 0.5
            by = ry(max(ra, rb))
        else:
            bx = rx(max(ca, cb))
            by = (max(ry(ra), ry(rb)) + min(ry(ra + ha), ry(rb + hb))) * 0.5
        d.ellipse([S(bx - 13), S(by - 13), S(bx + 13), S(by + 13)], fill=C_PAIR)
        d.text((S(bx), S(by + 1)), "&", font=font(16, True), fill=(255, 255, 255), anchor="mm")

    # 图例
    lx = PAD_L + w * step + 20
    ly = PAD_T
    d.text((S(lx), S(ly - 38)), "图例", font=f_head, fill=C_TXT)
    items = list(KIND.items())
    for i, (k, (fill, ink, label)) in enumerate(items):
        yy = ly + i * 48
        d.rounded_rectangle([S(lx), S(yy), S(lx + 34), S(yy + 34)], radius=S(9), fill=fill,
                            outline=(0, 0, 0, 26), width=max(1, int(1 * SCALE)))
        d.text((S(lx + 50), S(yy + 17)), label, font=f_small, fill=C_TXT, anchor="lm")
    yy = ly + len(items) * 48 + 4
    d.rectangle([S(lx + 11), S(yy), S(lx + 23), S(yy + 32)], fill=C_DOOR)
    d.text((S(lx + 50), S(yy + 16)), "门（可达的相邻）", font=f_small, fill=C_TXT, anchor="lm")
    yy += 44
    d.line([(S(lx + 7), S(yy + 16)), (S(lx + 29), S(yy + 16))], fill=C_WALL, width=int(4 * SCALE))
    d.text((S(lx + 50), S(yy + 16)), "墙（相邻但不可达）", font=f_small, fill=C_TXT, anchor="lm")
    yy += 44
    d.rounded_rectangle([S(lx + 3), S(yy - 3), S(lx + 33), S(yy + 29)], radius=S(9), outline=C_PAIR, width=int(2 * SCALE))
    d.text((S(lx + 50), S(yy + 13)), "& 必须相邻且合并", font=f_small, fill=C_TXT, anchor="lm")

    ly = yy + 60
    d.text((S(lx), S(ly)), "本城要点", font=f_head, fill=C_TXT)
    for i, t in enumerate(principle_lines):
        d.text((S(lx), S(ly + 38 + i * 32)), t, font=f_small, fill=C_SUB)

    d.text((S(PAD_L - 26), S(H - 46)), "依《自然语言蓝图/城市/城市生成》规则生成", font=f_tiny, fill=C_SOFT)
    nx, ny = W - 84, H - 80
    d.polygon([(S(nx), S(ny - 30)), (S(nx - 15), S(ny + 17)), (S(nx), S(ny + 6)), (S(nx + 15), S(ny + 17))],
              fill=(62, 57, 52))
    d.text((S(nx), S(ny + 36)), "N", font=f_small, fill=(62, 57, 52), anchor="mm")

    img = img.resize((int(W), int(H)), Image.LANCZOS)
    img.save(out)
    return out, img.size


# ---------------------------------------------------------------- 五座城市

PLANS = [
    dict(seed=11, w=9,  h=12, pattern="cross",  homes=3, taverns=3, markets=2, cityparks=2),
    dict(seed=22, w=10, h=11, pattern="double", homes=3, taverns=2, markets=3, cityparks=2),
    dict(seed=33, w=9,  h=13, pattern="comb",   homes=3, taverns=3, markets=2, cityparks=3),
    dict(seed=44, w=10, h=12, pattern="h",      homes=3, taverns=3, markets=2, cityparks=2),
    dict(seed=55, w=11, h=11, pattern="ring",   homes=3, taverns=3, markets=2, cityparks=2),
]

NAMES = ["A 城 · 十字主街", "B 城 · 双横街", "C 城 · 三纵梳状", "D 城 · 井字骨架", "E 城 · 环城路"]


def write_matrix(city, path, title):
    """导出 I 矩阵 / 门方向表 / & 合并表（格式见 reference/矩阵格式.md）"""
    code = {"road": 1, "home": 2, "shop": 3, "public": 4, "park": 5, "core": 6, "palace": 7}
    lines = [f"# {title} · {city.w}×{city.h}",
             "# 图例：0 空地 / 1 道路 / 2 住宅 / 3 商业 / 4 公共 / 5 公园 / 6 传送门 / 7 宫殿",
             "I ="]
    for r in range(city.h):
        row = []
        for c in range(city.w):
            i = city.at(c, r)
            row.append("0" if i is None else str(code[city.nodes[i]["kind"]]))
        lines.append(" ".join(row))

    lines += ["", "# 名字 | 坐标 | 占地 | 门"]
    by_idx = {n["idx"]: n for n in city.nodes}
    door_of = {}
    for (i, j) in city.edge_idx:
        a, b = by_idx[i], by_idx[j]
        # 判断相对方向
        if a["r"] + a["h"] == b["r"]:
            dirs = ("S", "N")
        elif b["r"] + b["h"] == a["r"]:
            dirs = ("N", "S")
        elif a["c"] + a["w"] == b["c"]:
            dirs = ("E", "W")
        else:
            dirs = ("W", "E")
        door_of.setdefault(i, []).append(f"{dirs[0]}->{b['name']}({chr(65+b['c'])}{b['r']+1})")
        door_of.setdefault(j, []).append(f"{dirs[1]}->{a['name']}({chr(65+a['c'])}{a['r']+1})")
    for n in city.nodes:
        if n["kind"] == "road":
            continue
        coord = f"{chr(65+n['c'])}{n['r']+1}"
        size = f"{n['w']}x{n['h']}"
        lines.append(f"{n['name']} | {coord} | {size} | " + " , ".join(door_of.get(n["idx"], [])))

    if city.pairs:
        lines += ["", "# 合并组 | 成员A | 成员B | 占地"]
        for gi, (ia, ib) in enumerate(city.pairs, start=1):
            a, b = by_idx[ia], by_idx[ib]
            ca = f"{a['name']} {chr(65+a['c'])}{a['r']+1}"
            cb = f"{b['name']} {chr(65+b['c'])}{b['r']+1}"
            span = "2x1" if a["r"] == b["r"] else "1x2"
            lines.append(f"{gi} | {ca} | {cb} | {span}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description="按蓝图规则生成坐标网格城市图")
    ap.add_argument("--out", default=os.path.join(os.path.expanduser("~"), "Desktop", "城市视图"),
                    help="输出目录（默认桌面\\城市视图）")
    ap.add_argument("--matrix", action="store_true", help="额外导出矩阵文本")
    args = ap.parse_args()
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    for i, plan in enumerate(PLANS, start=1):
        city = City(plan["w"], plan["h"], plan["seed"])
        city.generate(plan["pattern"], plan)
        bad, cnt = city.validate()
        area = sum(n["w"] * n["h"] for n in city.nodes)
        print(f"--- {NAMES[i-1]} {plan['w']}x{plan['h']} 区域 {len(city.nodes)} 格 {area}")
        print("    counts", {k: v for k, v in cnt.items() if k != "路"}, "道路", cnt.get("路", 0))
        print("    checks", "OK" if not bad else bad)
        lines = [
            f"骨架：{plan['pattern']} 路网 · 网格 {plan['w']}×{plan['h']}",
            f"区域 {len(city.nodes)} 个 / 占地 {area} 格",
            f"居民区 {cnt.get('居民区',0)} · 酒馆 {cnt.get('酒馆',0)} · 市场 {cnt.get('市场',0)}",
            f"公园&林地 {cnt.get('公园',0)} 组 · 教堂&墓地 1 组",
            "切图即门：竖条可达，横杠不可达",
        ]
        png = os.path.join(out_dir, f"城市-0{i}.png")
        render(city, f"DungeonCity · 城市 0{i} · {NAMES[i-1]}",
               "格子 = 区域；1 格 = 1×1，居民区 / 传送门 / 宫殿 = 2×2。", png, lines)
        if args.matrix:
            write_matrix(city, os.path.join(out_dir, f"城市-0{i}矩阵.txt"),
                         f"城市 0{i} · {NAMES[i-1]}")


if __name__ == "__main__":
    main()

