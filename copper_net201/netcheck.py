"""双层板网表核对: 铜岛划分、镀铜孔桥接、实际网络与短路/断路报告。

- 每层铜层先扣除全部孔盘, 再按剩余铜划分铜岛(同层仅点接触不导通);
- 镀铜孔壁连接两层所有沿孔周有正长度接触的铜岛(点接触不算);
- 非镀铜孔仅扣铜; 顶底平面重叠不导通; 支持多孔传递连接。
"""
import math
from collections import deque

from shapely.geometry import Point
from shapely.ops import unary_union

from .errors import NetcheckError
from .geometry import _quad_segs

_CONTACT_LEN = 1e-9  # 孔周正长度接触阈值(mm)


def _hole_disk(hole, tol):
    r = hole.diameter / 2.0
    return Point(hole.x, hole.y).buffer(r, quad_segs=_quad_segs(r, tol))


def _split_islands(copper, eps):
    """按正面积连通划分铜岛: 先腐蚀断开点接触, 再膨胀恢复外形。"""
    if copper.is_empty:
        return []
    shrunk = copper.buffer(-eps)
    islands = []
    for g in getattr(shrunk, "geoms", [shrunk]):
        if g.geom_type == "Polygon" and not g.is_empty:
            islands.append(g.buffer(eps))
    return islands


def _arc_samples(arcs):
    """从孔周接触弧段上取代表点。"""
    pts = []
    for g in getattr(arcs, "geoms", [arcs]):
        if g.geom_type == "LineString" and g.length > _CONTACT_LEN:
            for f in (0.25, 0.5, 0.75):
                pts.append(g.interpolate(f, normalized=True))
    return pts


def _nudge_outward(pt, hole, eps):
    """将孔周上的点沿径向向孔外(铜侧)微移。"""
    dx, dy = pt.x - hole.x, pt.y - hole.y
    d = math.hypot(dx, dy)
    if d <= 0:
        return pt
    s = 3.0 * eps / d
    return Point(pt.x + dx * s, pt.y + dy * s)


class _DSU:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        root = self.parent.setdefault(x, x)
        if root != x:
            self.parent[x] = root = self.find(root)
        return root

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def _chain(adj, start, goal):
    """两铜岛间的一条连接链(铜岛与镀铜孔交替)。"""
    def node_desc(n):
        return {"type": "island", "layer": n[0], "index": n[1] + 1}
    if start == goal:
        return [node_desc(start)]
    prev = {start: None}
    queue = deque([start])
    while queue and goal not in prev:
        cur = queue.popleft()
        for nxt, hid in adj.get(cur, []):
            if nxt not in prev:
                prev[nxt] = (cur, hid)
                queue.append(nxt)
    nodes, edges = [goal], []
    cur = goal
    while prev[cur] is not None:
        cur, hid = prev[cur]
        nodes.append(cur)
        edges.append(hid)
    nodes.reverse()
    edges.reverse()
    chain = []
    for k, n in enumerate(nodes):
        chain.append(node_desc(n))
        if k < len(edges):
            chain.append({"type": "hole", "id": edges[k]})
    return chain


def analyze(top_copper, bottom_copper, terminals, holes, tol):
    """连通分析, 返回实际网络/端子归属/短路/断路报告(dict)。"""
    eps = max(tol * 1e-3, 1e-9)  # 点接触断开裕量
    disks = {h.id: _hole_disk(h, tol) for h in holes}
    cut = unary_union(list(disks.values())) if disks else None
    layers = {}
    for name, geom in (("top", top_copper), ("bottom", bottom_copper)):
        if cut is not None and not geom.is_empty:
            geom = geom.difference(cut)
        layers[name] = geom
    islands = {name: _split_islands(g, eps) for name, g in layers.items()}
    # 命中测试域: 外扩少许, 使边界与凸角上的点也算落铜
    hit = {name: [isl.buffer(eps * 0.5) for isl in group]
           for name, group in islands.items()}

    dsu = _DSU()
    adj = {}
    hole_nodes = {}
    for h in holes:
        if not h.plated:
            continue
        ring = disks[h.id].exterior
        contacted = []
        for name in ("top", "bottom"):
            geom = layers[name]
            if geom.is_empty:
                continue
            arcs = ring.intersection(geom)
            if arcs.length <= _CONTACT_LEN:
                continue
            for sample in _arc_samples(arcs):
                pt = _nudge_outward(sample, h, eps)
                for idx, region in enumerate(hit[name]):
                    if region.covers(pt):
                        node = (name, idx)
                        if node not in contacted:
                            contacted.append(node)
        contacted.sort()
        hole_nodes[h.id] = contacted
        for a, b in zip(contacted, contacted[1:]):
            dsu.union(a, b)
            adj.setdefault(a, []).append((b, h.id))
            adj.setdefault(b, []).append((a, h.id))

    landed = {}
    unlanded = []
    for t in terminals:
        pt = Point(t.x, t.y)
        cand = [idx for idx, region in enumerate(hit[t.layer])
                if region.covers(pt)]
        if len(cand) > 1:
            raise NetcheckError(
                "端子 %s 落在 %s 层多个铜岛的交界处, 落铜歧义"
                % (t.id, t.layer))
        if cand:
            landed[t.id] = (t.layer, cand[0])
        else:
            unlanded.append(t)

    nodes = [(name, i) for name in ("top", "bottom")
             for i in range(len(islands[name]))]
    comps = {}
    for n in nodes:
        comps.setdefault(dsu.find(n), []).append(n)
    comp_list = sorted(comps.values(), key=lambda c: c[0])
    comp_id = {n: k + 1 for k, comp in enumerate(comp_list) for n in comp}

    def term_desc(t):
        return {"id": t.id, "net": t.net, "layer": t.layer}

    actual = []
    for k, comp in enumerate(comp_list, 1):
        members = set(comp)
        terms = [t for t in terminals if landed.get(t.id) in members]
        hids = [hid for hid, ns in hole_nodes.items()
                if any(n in members for n in ns)]
        actual.append({
            "id": k,
            "islands": {name: [i + 1 for (nm, i) in comp if nm == name]
                        for name in ("top", "bottom")},
            "holes": sorted(hids),
            "terminals": [term_desc(t) for t in terms],
        })

    shorts = []
    for k, comp in enumerate(comp_list, 1):
        members = set(comp)
        terms = [t for t in terminals if landed.get(t.id) in members]
        names = sorted({t.net for t in terms})
        if len(names) < 2:
            continue
        t_a = next(t for t in terms if t.net == names[0])
        t_b = next(t for t in terms if t.net == names[1])
        shorts.append({
            "actual_net": k,
            "nets": names,
            "terminals": sorted(t.id for t in terms),
            "chain": _chain(adj, landed[t_a.id], landed[t_b.id]),
        })

    by_net = {}
    for t in terminals:
        node = landed.get(t.id)
        if node is not None:
            by_net.setdefault(t.net, {}).setdefault(
                comp_id[node], []).append(t.id)
    opens = []
    for net in sorted(by_net):
        groups = by_net[net]
        if len(groups) > 1:
            ordered = sorted((sorted(ids) for ids in groups.values()),
                             key=lambda ids: ids[0])
            opens.append({"net": net, "groups": ordered})

    return {
        "islands": {name: len(islands[name]) for name in ("top", "bottom")},
        "actual_nets": actual,
        "unlanded_terminals": [
            {"id": t.id, "net": t.net, "layer": t.layer, "x": t.x, "y": t.y}
            for t in unlanded],
        "shorts": shorts,
        "opens": opens,
    }
