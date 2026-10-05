"""JSON 网表解析与校验(端子 + 圆孔)。

格式:
{
  "terminals": [{"id": "T1", "net": "GND", "layer": "top", "x": 2.0, "y": 2.0}],
  "holes": [{"id": "H1", "x": 5.0, "y": 5.0, "diameter": 1.5, "plated": true}]
}
坐标为毫米, 与 Gerber 同一板坐标系(顶底层不自动镜像)。
拒绝重复 ID、非有限值、非正直径、重叠或相切的圆孔。
"""
import json
import math
from dataclasses import dataclass

from .errors import NetlistError


@dataclass
class Terminal:
    id: str
    net: str
    layer: str   # top 或 bottom
    x: float
    y: float


@dataclass
class Hole:
    id: str
    x: float
    y: float
    diameter: float
    plated: bool


def _no_constant(name):
    raise NetlistError("JSON 含非有限数值 " + name)


def _num(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NetlistError(where + " 须为数值", where)
    if not math.isfinite(value):
        raise NetlistError(where + " 须为有限数值", where)
    return float(value)


def _text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise NetlistError(where + " 须为非空字符串", where)
    return value


def _entry_list(data, key):
    value = data.get(key, [])
    if not isinstance(value, list):
        raise NetlistError(key + " 须为数组", key)
    return value


def load_netlist(text):
    """解析并校验网表 JSON, 返回 (terminals, holes)。"""
    try:
        data = json.loads(text, parse_constant=_no_constant)
    except NetlistError:
        raise
    except json.JSONDecodeError as exc:
        raise NetlistError("JSON 解析失败: %s" % exc.msg,
                           "line %d" % exc.lineno)
    if not isinstance(data, dict):
        raise NetlistError("网表须为 JSON 对象")

    terminals = []
    for i, raw in enumerate(_entry_list(data, "terminals")):
        where = "terminals[%d]" % i
        if not isinstance(raw, dict):
            raise NetlistError(where + " 须为对象", where)
        layer = _text(raw.get("layer"), where + ".layer")
        if layer not in ("top", "bottom"):
            raise NetlistError(where + ".layer 须为 top 或 bottom", where)
        terminals.append(Terminal(
            id=_text(raw.get("id"), where + ".id"),
            net=_text(raw.get("net"), where + ".net"),
            layer=layer,
            x=_num(raw.get("x"), where + ".x"),
            y=_num(raw.get("y"), where + ".y"),
        ))

    holes = []
    for i, raw in enumerate(_entry_list(data, "holes")):
        where = "holes[%d]" % i
        if not isinstance(raw, dict):
            raise NetlistError(where + " 须为对象", where)
        diameter = _num(raw.get("diameter"), where + ".diameter")
        if diameter <= 0:
            raise NetlistError(where + ".diameter 须为正数", where)
        plated = raw.get("plated")
        if not isinstance(plated, bool):
            raise NetlistError(where + ".plated 须为布尔值", where)
        holes.append(Hole(
            id=_text(raw.get("id"), where + ".id"),
            x=_num(raw.get("x"), where + ".x"),
            y=_num(raw.get("y"), where + ".y"),
            diameter=diameter,
            plated=plated,
        ))

    _check_unique([t.id for t in terminals], "端子")
    _check_unique([h.id for h in holes], "圆孔")
    _check_hole_clash(holes)
    return terminals, holes


def _check_unique(ids, label):
    seen = set()
    for i in ids:
        if i in seen:
            raise NetlistError("重复的%s ID %s" % (label, i))
        seen.add(i)


def _check_hole_clash(holes):
    for a in range(len(holes)):
        for b in range(a + 1, len(holes)):
            ha, hb = holes[a], holes[b]
            dist = math.hypot(ha.x - hb.x, ha.y - hb.y)
            if dist <= (ha.diameter + hb.diameter) / 2.0 + 1e-9:
                raise NetlistError(
                    "圆孔 %s 与 %s 重叠或相切" % (ha.id, hb.id), "holes")
