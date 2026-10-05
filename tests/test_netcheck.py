import json
import math

import pytest
from fastapi.testclient import TestClient

from copper_net201.errors import GerberError, NetcheckError, NetlistError
from copper_net201.geometry import _quad_segs, build_geometry
from copper_net201.netcheck import analyze
from copper_net201.netlist import load_netlist
from copper_net201.parser import Parser
from copper_net201.svg_export import geometry_to_svg
from main import app

HEADER = "%FSLAX24Y24*%\n%MOMM*%\n"


def geom(body, tol=0.01):
    p = Parser(HEADER + body + "\nM02*\n")
    return build_geometry(p.parse(), p.apertures, tol)


def region(x1, y1, x2, y2):
    def c(v):
        return str(int(round(v * 10000)))
    return ("%%LPD*%%\nG36*\nX%sY%sD02*\nX%sY%sD01*\nX%sY%sD01*\n"
            "X%sY%sD01*\nX%sY%sD01*\nG37*"
            % (c(x1), c(y1), c(x2), c(y1), c(x2), c(y2),
               c(x1), c(y2), c(x1), c(y1)))


def netlist(terminals, holes):
    return load_netlist(json.dumps(
        {"terminals": terminals, "holes": holes}))


def term(tid, net, layer, x, y):
    return {"id": tid, "net": net, "layer": layer, "x": x, "y": y}


def hole(hid, x, y, d, plated=True):
    return {"id": hid, "x": x, "y": y, "diameter": d, "plated": plated}


EMPTY = "%ADD10C,0.5*%\nX0Y0D02*"


def test_plated_hole_connects_layers():
    top = geom(region(0, 0, 10, 10))
    bottom = geom(region(0, 0, 10, 10))
    terminals, holes = netlist(
        [term("T1", "A", "top", 1, 1), term("T2", "A", "bottom", 9, 9)],
        [hole("H1", 5, 5, 2.0)])
    rep = analyze(top, bottom, terminals, holes, 0.01)
    assert rep["islands"] == {"top": 1, "bottom": 1}
    assert rep["shorts"] == []
    assert rep["opens"] == []
    assert rep["unlanded_terminals"] == []
    assert len(rep["actual_nets"]) == 1
    assert rep["actual_nets"][0]["holes"] == ["H1"]
    assert {t["id"] for t in rep["actual_nets"][0]["terminals"]} == {"T1", "T2"}


def test_nonplated_hole_does_not_connect():
    top = geom(region(0, 0, 10, 10))
    bottom = geom(region(0, 0, 10, 10))
    terminals, holes = netlist(
        [term("T1", "A", "top", 1, 1), term("T2", "A", "bottom", 9, 9)],
        [hole("H1", 5, 5, 2.0, plated=False)])
    rep = analyze(top, bottom, terminals, holes, 0.01)
    assert rep["shorts"] == []
    assert rep["opens"] == [{"net": "A", "groups": [["T1"], ["T2"]]}]


def test_point_touch_same_layer_not_conductive():
    top = geom(region(0, 0, 10, 10) + region(10, 10, 20, 20))
    bottom = geom(EMPTY)
    terminals, holes = netlist(
        [term("T1", "A", "top", 5, 5), term("T2", "A", "top", 15, 15)], [])
    rep = analyze(top, bottom, terminals, holes, 0.01)
    assert rep["islands"]["top"] == 2
    assert rep["opens"] == [{"net": "A", "groups": [["T1"], ["T2"]]}]


def test_point_touch_hole_wall_not_conductive():
    # 铜块只在 (5,10) 一点与孔周相切, 不构成正长度接触
    top = geom(region(0, 10, 5, 20))
    bottom = geom(EMPTY)
    terminals, holes = netlist(
        [term("T1", "A", "top", 2, 15)], [hole("H1", 5, 5, 10.0)])
    rep = analyze(top, bottom, terminals, holes, 0.01)
    assert rep["actual_nets"][0]["holes"] == []


def test_short_reported_with_chain():
    top = geom(region(0, 0, 10, 10))
    bottom = geom(region(0, 0, 10, 10))
    terminals, holes = netlist(
        [term("T1", "GND", "top", 1, 1), term("T2", "VCC", "bottom", 9, 9)],
        [hole("H1", 5, 5, 2.0)])
    rep = analyze(top, bottom, terminals, holes, 0.01)
    assert len(rep["shorts"]) == 1
    short = rep["shorts"][0]
    assert short["nets"] == ["GND", "VCC"]
    assert short["terminals"] == ["T1", "T2"]
    kinds = [c["type"] for c in short["chain"]]
    assert kinds == ["island", "hole", "island"]
    assert short["chain"][1]["id"] == "H1"
    assert rep["opens"] == []


def test_multi_hop_through_two_plated_holes():
    top = geom(region(0, 0, 5, 5) + region(10, 0, 15, 5))
    bottom = geom(region(0, 0, 15, 5))
    terminals, holes = netlist(
        [term("T1", "A", "top", 1, 1), term("T2", "A", "top", 14, 4)],
        [hole("H1", 2.5, 2.5, 1.0), hole("H2", 12.5, 2.5, 1.0)])
    rep = analyze(top, bottom, terminals, holes, 0.01)
    assert rep["islands"] == {"top": 2, "bottom": 1}
    assert rep["opens"] == []
    assert rep["shorts"] == []
    net = [n for n in rep["actual_nets"] if n["terminals"]]
    assert len(net) == 1
    assert net[0]["holes"] == ["H1", "H2"]


def test_unlanded_terminal_listed_and_excluded():
    top = geom(region(0, 0, 10, 10))
    bottom = geom(EMPTY)
    terminals, holes = netlist(
        [term("T1", "A", "top", 5, 5), term("T2", "A", "top", 50, 50)], [])
    rep = analyze(top, bottom, terminals, holes, 0.01)
    assert [t["id"] for t in rep["unlanded_terminals"]] == ["T2"]
    assert rep["opens"] == []
    assert rep["shorts"] == []


def test_terminal_on_boundary_counts_as_landed():
    top = geom(region(0, 0, 10, 10))
    bottom = geom(EMPTY)
    terminals, holes = netlist([term("T1", "A", "top", 10, 5)], [])
    rep = analyze(top, bottom, terminals, holes, 0.01)
    assert rep["unlanded_terminals"] == []


def test_terminal_ambiguity_at_island_junction():
    top = geom(region(0, 0, 10, 10) + region(10, 10, 20, 20))
    bottom = geom(EMPTY)
    terminals, holes = netlist([term("T1", "A", "top", 10, 10)], [])
    with pytest.raises(NetcheckError) as e:
        analyze(top, bottom, terminals, holes, 0.01)
    assert "歧义" in str(e.value)


# ---- 网表校验 ----

def test_duplicate_terminal_id_rejected():
    with pytest.raises(NetlistError) as e:
        netlist([term("T1", "A", "top", 0, 0),
                 term("T1", "B", "top", 1, 1)], [])
    assert "重复" in str(e.value)


def test_duplicate_hole_id_rejected():
    with pytest.raises(NetlistError):
        netlist([], [hole("H1", 0, 0, 1), hole("H1", 10, 10, 1)])


def test_non_finite_values_rejected():
    with pytest.raises(NetlistError):
        load_netlist('{"terminals": [{"id": "T1", "net": "A",')
    # NaN / Infinity 字面量
    with pytest.raises(NetlistError):
        load_netlist('{"terminals": [{"id": "T1", "net": "A", "layer": "top",'
                     ' "x": NaN, "y": 0}], "holes": []}')
    # 1e999 解析为 inf
    with pytest.raises(NetlistError):
        load_netlist('{"terminals": [{"id": "T1", "net": "A", "layer": "top",'
                     ' "x": 1e999, "y": 0}], "holes": []}')


def test_bad_layer_and_diameter_rejected():
    with pytest.raises(NetlistError):
        netlist([term("T1", "A", "mid", 0, 0)], [])
    with pytest.raises(NetlistError):
        netlist([], [hole("H1", 0, 0, 0)])
    with pytest.raises(NetlistError):
        netlist([], [hole("H1", 0, 0, -1)])


def test_overlapping_and_tangent_holes_rejected():
    with pytest.raises(NetlistError) as e:
        netlist([], [hole("H1", 0, 0, 2), hole("H2", 1, 0, 2)])
    assert "重叠或相切" in str(e.value)
    with pytest.raises(NetlistError):
        netlist([], [hole("H1", 0, 0, 2), hole("H2", 2, 0, 2)])  # 相切
    # 留有间隙则通过
    netlist([], [hole("H1", 0, 0, 2), hole("H2", 2.1, 0, 2)])


# ---- 顺带修复 ----

def test_quad_segs_follows_tolerance():
    assert _quad_segs(1.0, 0.1) < _quad_segs(1.0, 0.001)
    for tol in (0.1, 0.01, 0.001):
        q = _quad_segs(1.0, tol)
        sagitta = 1.0 * (1 - math.cos(math.pi / (2 * q)))
        assert sagitta <= tol


def test_svg_viewbox_not_offset():
    g = geom("%ADD10R,2.0X2.0*%\nD10*\nX1000000Y1000000D03*")
    svg = geometry_to_svg(g)
    assert 'viewBox="0 0 2 2"' in svg
    assert "translate(-99,0)" in svg


def test_truncation_error_has_position():
    with pytest.raises(GerberError) as e:
        Parser(HEADER + "%ADD10C,0.5*%\nD10*\nX0Y0D03*\n").parse()
    assert "截断" in str(e.value)
    assert e.value.line == 5
    with pytest.raises(GerberError) as e2:
        Parser(HEADER + "G36*\nX0Y0D02*\nX1000Y0D01*\n").parse()
    assert e2.value.line == 3


# ---- 跨文件联动: HTTP 接口 ----

SIMPLE_TOP = (HEADER + region(0, 0, 10, 10) + "\nM02*\n")


def test_netcheck_endpoint_and_isolation():
    client = TestClient(app)
    nl = json.dumps({
        "terminals": [term("T1", "GND", "top", 1, 1),
                      term("T2", "VCC", "bottom", 9, 9)],
        "holes": [hole("H1", 5, 5, 2.0)],
    })
    resp = client.post("/api/netcheck", files={
        "top": ("top.gbr", SIMPLE_TOP),
        "bottom": ("bottom.gbr", SIMPLE_TOP),
        "netlist": ("net.json", nl),
    }, data={"tolerance": "0.01"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["shorts"]) == 1
    assert body["opens"] == []

    bad = client.post("/api/netcheck", files={
        "top": ("top.gbr", SIMPLE_TOP),
        "bottom": ("bottom.gbr", SIMPLE_TOP),
        "netlist": ("net.json", "{\"terminals\": 1}"),
    })
    assert bad.status_code == 422
    # 失败不交付部分结果: 无 actual_nets 字段
    assert "actual_nets" not in bad.json()["detail"]

    bad2 = client.post("/api/netcheck", files={
        "top": ("top.gbr", "D10*\nX0Y0D03*\nM02*\n"),
        "bottom": ("bottom.gbr", SIMPLE_TOP),
        "netlist": ("net.json", nl),
    })
    assert bad2.status_code == 422
    assert bad2.json()["detail"]["layer"] == "top"
    assert bad2.json()["detail"]["line"] is not None


def test_rebuild_endpoint_still_works():
    client = TestClient(app)
    resp = client.post("/api/rebuild",
                       files={"file": ("a.gbr", SIMPLE_TOP)},
                       data={"tolerance": "0.01"})
    assert resp.status_code == 200
    stats = resp.json()
    assert stats["area_mm2"] == pytest.approx(100.0, rel=1e-3)
    svg = client.get(stats["svg_url"])
    assert svg.status_code == 200
    assert 'viewBox="0 0 10 10"' in svg.text
