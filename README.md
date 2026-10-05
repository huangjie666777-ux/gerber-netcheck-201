# Gerber NetCheck 201

接收单层 ASCII Gerber 光绘文件, 重建实际铜层轮廓, 输出面积/包围盒/连通块/孔洞统计, 并从同一最终几何生成 SVG 下载。纯后端, 无前端页面。

## 模块分工

- `copper_net201/parser.py` — 词法/语法解析, 输出事件流(含原文行号定位)
- `copper_net201/geometry.py` — 曝光几何引擎: 圆弧离散、光圈缓冲、区域填充、LPD/LPC 按序合成(Shapely)
- `copper_net201/svg_export.py` — 最终几何 → SVG(Y 轴翻转、evenodd 孔洞)
- `copper_net201/netlist.py` — JSON 网表解析与校验(端子/圆孔、重复 ID、非有限值、孔冲突)
- `copper_net201/netcheck.py` — 扣孔、铜岛划分、镀铜孔桥接、实际网络与短路/断路报告
- `main.py` — FastAPI 请求处理: 上传、参数校验、错误响应、结果暂存、网表核对

## 支持的 Gerber 子集

- `%FSLAXnnYnn*%` 前导零省略 + 绝对坐标; `%MOMM*%` / `%MOIN*%`
- `%ADDnnC,d*%` 圆形、 `%ADDnnR,xXy*%` 轴对齐矩形(仅闪光), 均无孔
- `Dnn` 选光圈; `D01` 绘制 / `D02` 移动 / `D03` 闪光; X/Y 省略沿用当前位置
- `G01` 直线; `G75` 下 `G02`/`G03` 顺/逆圆弧(跨象限、整圆, I/J 为相对起点的圆心偏移)
- `G36`/`G37` 单个简单直线闭环区域, 按内部填充
- `%LPD*%` 加铜 / `%LPC*%` 扣除, 严格按指令顺序组合, 后续 LPD 可恢复先前清除
- `G04` 注释、 `M02` 结束(缺失视为截断)

未定义光圈、非法圆弧、未闭合/自交区域、未支持指令、截断文件均返回 HTTP 422 及带行号与原文的错误, 不交付部分结果。

## 运行

```bash
.venv/bin/pip install -r requirements.txt   # 环境已备好可跳过
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

## API

### POST /api/rebuild

multipart 上传: `file` = Gerber 文件; `tolerance` = 曲线近似误差(mm, 正数, 默认 0.01)。

```bash
curl -s -F "file=@samples/sample1.gbr" -F "tolerance=0.01" http://127.0.0.1:8000/api/rebuild
```

返回示例:

```json
{
  "area_mm2": 1234.567,
  "bbox_mm": {"min_x": 0, "min_y": 0, "max_x": 30, "max_y": 20, "width": 30, "height": 20},
  "components": 1,
  "holes": 1,
  "svg_id": "...",
  "svg_url": "/api/rebuild/<svg_id>/svg"
}
```

空铜层正常返回(area 为 0, bbox 为 null)。错误返回 422:

```json
{"detail": {"error": "未定义的光圈 D10", "line": 7, "source": "D10"}}
```

### GET /api/rebuild/{svg_id}/svg

下载由同一最终几何生成的 SVG(附件形式, 孔洞以 evenodd 正确表达, Y 轴方向已翻转)。

```bash
curl -OJ http://127.0.0.1:8000/api/rebuild/<svg_id>/svg
```

## 双层板网表核对

### POST /api/netcheck

multipart 一次上传: `top` = 顶层 Gerber; `bottom` = 底层 Gerber; `netlist` = JSON 网表;
`tolerance` = 曲线近似误差(mm, 正数, 默认 0.01)。两层须为同一板坐标系, 不自动镜像。
任一步失败整体返回 422, 不交付部分结果。

```bash
curl -s -F "top=@samples/netcheck_top.gbr" -F "bottom=@samples/netcheck_bottom.gbr" \
     -F "netlist=@samples/netlist.json" -F "tolerance=0.01" \
     http://127.0.0.1:8000/api/netcheck
```

网表 JSON 格式(坐标毫米):

```json
{
  "terminals": [{"id": "T1", "net": "GND", "layer": "top", "x": 2.0, "y": 2.0}],
  "holes": [{"id": "H1", "x": 5.0, "y": 5.0, "diameter": 1.5, "plated": true}]
}
```

- `terminals[].layer` 仅 `top`/`bottom`; `holes[].diameter` 为正数; `plated` 为布尔
- 拒绝: 重复 ID、非有限值(NaN/Infinity/1e999)、非正直径、重叠或相切的圆孔

核对语义:

- 每层先扣除全部孔盘, 再按剩余铜划分铜岛; 同层仅点接触不导通
- 镀铜孔的孔壁连接两层所有沿孔周有正长度接触的铜岛(点接触不算), 支持多孔传递
- 非镀铜孔只扣铜; 顶底平面重叠不直接导通
- 端子落到指定层扣孔后的铜岛(边界算落铜); 多岛交点报 422 歧义;
  未落铜端子列入 `unlanded_terminals`, 不参与短断路分组

返回:

```json
{
  "islands": {"top": 2, "bottom": 2},
  "actual_nets": [{"id": 1, "islands": {"top": [1], "bottom": [1]},
                   "holes": ["H1"],
                   "terminals": [{"id": "T1", "net": "GND", "layer": "top"}]}],
  "unlanded_terminals": [],
  "shorts": [{"actual_net": 1, "nets": ["GND", "VCC"], "terminals": ["T1", "T2"],
              "chain": [{"type": "island", "layer": "top", "index": 1},
                        {"type": "hole", "id": "H1"},
                        {"type": "island", "layer": "bottom", "index": 1}]}],
  "opens": [{"net": "A", "groups": [["T1"], ["T2", "T3"]]}]
}
```

- `shorts`: 异名网络同属一个实际网络, `chain` 给出铜岛与镀铜孔的连接链
- `opens`: 同名网络已落铜端子分属多个实际网络, `groups` 为分离的端子组

样例: `samples/netcheck_top.gbr` / `samples/netcheck_bottom.gbr` / `samples/netlist.json`
(两个镀铜孔分别连通 GND/VCC, 一个非镀铜孔仅扣铜, 核对通过无短断路)。

## 自测

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m compileall -q copper_net201 main.py tests
```

样例见 `samples/`: `sample1.gbr`(区域+闪光+走线+圆弧+LPC 开孔后恢复)、`sample2.gbr`(英寸单位+整圆+顺时针圆弧)。

Repository: https://github.com/huangjie666777-ux/gerber-netcheck-201
