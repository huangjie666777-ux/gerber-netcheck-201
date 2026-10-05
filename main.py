"""FastAPI 请求处理层: 上传、铜层重建、SVG 下载、双层网表核对。"""
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from copper_net201.errors import GerberError, NetcheckError, NetlistError
from copper_net201.geometry import build_geometry, summarize
from copper_net201.netcheck import analyze
from copper_net201.netlist import load_netlist
from copper_net201.parser import Parser
from copper_net201.svg_export import geometry_to_svg

app = FastAPI(title="Gerber NetCheck 201")

# svg_id -> (svg_text, stats)
_results = {}


def _rebuild(text, tolerance):
    parser = Parser(text)
    events = parser.parse()
    geom = build_geometry(events, parser.apertures, tolerance)
    stats = summarize(geom)
    svg = geometry_to_svg(geom)
    return stats, svg


def _read_gerber(raw, layer):
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        detail = "仅支持 ASCII 编码的 Gerber 文件"
        if layer:
            detail = {"layer": layer, "error": detail}
        raise HTTPException(422, detail)


@app.post("/api/rebuild")
async def rebuild(file: UploadFile = File(...),
                  tolerance: float = Form(0.01)):
    if tolerance <= 0:
        raise HTTPException(422, "tolerance 必须为正数(毫米)")
    text = _read_gerber(await file.read(), None)
    try:
        stats, svg = _rebuild(text, tolerance)
    except GerberError as exc:
        raise HTTPException(422, {
            "error": exc.message, "line": exc.line, "source": exc.source})
    svg_id = uuid.uuid4().hex
    _results[svg_id] = svg
    stats["svg_id"] = svg_id
    stats["svg_url"] = "/api/rebuild/%s/svg" % svg_id
    return stats


@app.get("/api/rebuild/{svg_id}/svg")
async def download_svg(svg_id: str):
    svg = _results.get(svg_id)
    if svg is None:
        raise HTTPException(404, "结果不存在或已过期")
    return Response(
        svg, media_type="image/svg+xml",
        headers={"Content-Disposition":
                 'attachment; filename="copper_%s.svg"' % svg_id[:8]})


@app.post("/api/netcheck")
async def netcheck(top: UploadFile = File(...),
                   bottom: UploadFile = File(...),
                   netlist: UploadFile = File(...),
                   tolerance: float = Form(0.01)):
    if tolerance <= 0:
        raise HTTPException(422, "tolerance 必须为正数(毫米)")
    geoms = {}
    for layer, upload in (("top", top), ("bottom", bottom)):
        text = _read_gerber(await upload.read(), layer)
        try:
            parser = Parser(text)
            events = parser.parse()
            geoms[layer] = build_geometry(events, parser.apertures, tolerance)
        except GerberError as exc:
            raise HTTPException(422, {
                "layer": layer, "error": exc.message,
                "line": exc.line, "source": exc.source})
    try:
        nl_text = (await netlist.read()).decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(422, "网表须为 UTF-8 编码的 JSON 文件")
    try:
        terminals, holes = load_netlist(nl_text)
        return analyze(geoms["top"], geoms["bottom"],
                       terminals, holes, tolerance)
    except NetlistError as exc:
        raise HTTPException(422, {
            "error": exc.message, "location": exc.location})
    except NetcheckError as exc:
        raise HTTPException(422, {"error": exc.message})
