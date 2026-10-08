"""
PROTOTYPE, throwaway. Answers "Painter feedback format from view_canvas" (ryanpudd/texel-studio#7).

A rough stdio MCP server on the surface decided in "Canvas tool surface exposed over MCP" (#4),
with view_canvas output switchable by env var so the same sprites can be painted under each format:

  TEXEL_VIEW       text | image | both          (default both)
  TEXEL_VIEW_PX    upscaled image edge in px    (default 512)
  TEXEL_VIEW_GRID  1 = gridlines + coord labels on the image, 0 = plain (default 1)
  TEXEL_LOG        jsonl file; every tool call is appended with its result size

Not production: no tests, minimal validation, imports Canvas straight from agent.py.
"""

import copy
import io
import json
import os
import sys
import time

from PIL import Image as PILImage, ImageDraw
from mcp.server import MCPServer
from mcp.server.mcpserver import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent import Canvas  # noqa: E402

VIEW = os.getenv("TEXEL_VIEW", "both")
VIEW_PX = int(os.getenv("TEXEL_VIEW_PX", "512"))
VIEW_GRID = os.getenv("TEXEL_VIEW_GRID", "1") == "1"
LOG = os.getenv("TEXEL_LOG")

ALWAYS = {"anthropic/alwaysLoad": True}

mcp = MCPServer(
    "texel",
    instructions=(
        "Texel: pixel-art canvas tools. You are the painter. create_canvas, then draw batches of ops, "
        "view_canvas to check your work, undo mistakes, export_png when done. Colours are palette indices, -1 = transparent. "
        "(0,0) is top-left; x goes right, y goes down."
    ),
)

canvases: dict[str, Canvas] = {}
history: dict[str, list] = {}


def log(tool: str, **fields):
    if not LOG:
        return
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), "tool": tool, **fields}) + "\n")


def _get(canvas_id: str) -> Canvas:
    if canvas_id not in canvases:
        raise ValueError(f"unknown canvas_id {canvas_id!r}")
    return canvases[canvas_id]


def _snapshot(cid: str):
    h = history[cid]
    h.append(copy.deepcopy(canvases[cid].pixels))
    del h[:-50]


@mcp.tool(meta=ALWAYS)
def create_canvas(size: int, palette: list[str], canvas_id: str | None = None) -> str:
    """Create a square transparent canvas. size is 8, 16, 32 or 64. palette is a list of 1-36 '#rrggbb' hex
    colours, copied in and immutable. Returns the canvas_id and an index/char/hex table."""
    if size not in (8, 16, 32, 64):
        raise ValueError("size must be 8, 16, 32 or 64")
    if not 1 <= len(palette) <= 36:
        raise ValueError("palette must have 1-36 colours")
    cid = canvas_id or f"c{len(canvases) + 1}"
    if cid in canvases:
        raise ValueError(f"canvas_id {cid!r} already exists")
    canvases[cid] = Canvas(size, [p.lower() for p in palette])
    history[cid] = []
    table = "\n".join(f"  {i} ({_char(i)}) {h}" for i, h in enumerate(palette))
    log("create_canvas", canvas_id=cid, size=size)
    return f"Created {cid}: {size}x{size}, transparent.\nPalette:\n{table}"


def _char(i: int) -> str:
    return "." if i < 0 else str(i) if i < 10 else chr(ord("A") + i - 10)


def _color(c: Canvas, v) -> int:
    v = int(v)
    if v < -1 or v >= len(c.palette):
        raise ValueError(f"colour index {v} invalid (use -1..{len(c.palette) - 1})")
    return v


def _changed(before, after) -> int:
    return sum(1 for ra, rb in zip(before, after) for a, b in zip(ra, rb) if a != b)


def _flood(c: Canvas, x: int, y: int, color: int):
    if not (0 <= x < c.size and 0 <= y < c.size):
        return
    target = c.pixels[y][x]
    if target == color:
        return
    stack = [(x, y)]
    while stack:
        px, py = stack.pop()
        if 0 <= px < c.size and 0 <= py < c.size and c.pixels[py][px] == target:
            c.pixels[py][px] = color
            stack += [(px + 1, py), (px - 1, py), (px, py + 1), (px, py - 1)]


def _mirror(c: Canvas, axis: str, direction: str):
    n = c.size
    if axis == "x":  # left/right halves
        src_left = direction in ("left_to_right", "ltr")
        for y in range(n):
            for x in range(n // 2):
                if src_left:
                    c.pixels[y][n - 1 - x] = c.pixels[y][x]
                else:
                    c.pixels[y][x] = c.pixels[y][n - 1 - x]
    elif axis == "y":
        src_top = direction in ("top_to_bottom", "ttb")
        for y in range(n // 2):
            if src_top:
                c.pixels[n - 1 - y] = list(c.pixels[y])
            else:
                c.pixels[y] = list(c.pixels[n - 1 - y])
    else:
        raise ValueError("mirror axis must be 'x' or 'y'")


def _apply(c: Canvas, op: dict) -> int | str:
    kind = op.get("op")
    before = copy.deepcopy(c.pixels)
    skipped = 0
    if kind == "pixels":
        for x, y, col in op["pixels"]:
            col = _color(c, col)
            if 0 <= x < c.size and 0 <= y < c.size:
                c.pixels[y][x] = col
            else:
                skipped += 1
    elif kind == "rect":
        c.fill_rect(op["x1"], op["y1"], op["x2"], op["y2"], _color(c, op["color"]))
    elif kind == "line":
        c.draw_line(op["x1"], op["y1"], op["x2"], op["y2"], _color(c, op["color"]))
    elif kind == "circle":
        c.draw_circle(op["cx"], op["cy"], op["r"], _color(c, op["color"]), op.get("fill", True))
    elif kind == "ellipse":
        c.draw_ellipse(op["cx"], op["cy"], op["rx"], op["ry"], _color(c, op["color"]), op.get("fill", True))
    elif kind == "triangle":
        c.draw_triangle(op["x1"], op["y1"], op["x2"], op["y2"], op["x3"], op["y3"], _color(c, op["color"]))
    elif kind == "flood_fill":
        _flood(c, op["x"], op["y"], _color(c, op["color"]))
    elif kind == "mirror":
        _mirror(c, op["axis"], op["direction"])
    elif kind == "noise_rect":
        cols = [_color(c, v) for v in op["colors"]]
        c.fill_noise(op["x1"], op["y1"], op["x2"], op["y2"], cols, op.get("seed", 42))
    else:
        raise ValueError(f"unknown op {kind!r}")
    n = _changed(before, c.pixels)
    return f"{kind}: {n}px" + (f" ({skipped} off-canvas skipped)" if skipped else "")


@mcp.tool(meta=ALWAYS)
def draw(canvas_id: str, ops: list[dict]) -> str:
    """Apply a list of ops in order, atomically (any invalid op = nothing changes). One undo step per call.
    Each op is an object with an "op" key. Colours are palette indices, -1 = transparent. Coordinates inclusive; shapes clip.
      {"op":"pixels","pixels":[[x,y,color],...]}
      {"op":"rect","x1","y1","x2","y2","color"}             filled, inclusive
      {"op":"line","x1","y1","x2","y2","color"}             1px Bresenham
      {"op":"circle","cx","cy","r","color","fill":true}
      {"op":"ellipse","cx","cy","rx","ry","color","fill":true}
      {"op":"triangle","x1","y1","x2","y2","x3","y3","color"} filled
      {"op":"flood_fill","x","y","color"}                   4-connected
      {"op":"mirror","axis":"x"|"y","direction":"left_to_right"|"right_to_left"|"top_to_bottom"|"bottom_to_top"}
      {"op":"noise_rect","x1","y1","x2","y2","colors":[...],"seed":42}
    """
    c = _get(canvas_id)
    saved = copy.deepcopy(c.pixels)
    try:
        lines = [_apply(c, op) for op in ops]
    except (KeyError, TypeError, ValueError) as e:
        c.pixels = saved
        log("draw", canvas_id=canvas_id, ops=len(ops), error=str(e))
        raise ValueError(f"draw failed, canvas unchanged: {e}")
    history[canvas_id].append(saved)
    del history[canvas_id][:-50]
    log("draw", canvas_id=canvas_id, ops=len(ops))
    return "\n".join(lines)


@mcp.tool(meta=ALWAYS)
def undo(canvas_id: str, steps: int = 1) -> str:
    """Undo the last `steps` mutating calls (a whole draw batch is one step)."""
    c = _get(canvas_id)
    h = history[canvas_id]
    n = min(steps, len(h))
    for _ in range(n):
        c.pixels = h.pop()
    log("undo", canvas_id=canvas_id, steps=n)
    return f"Undid {n} step(s). {len(h)} left."


@mcp.tool(meta=ALWAYS)
def clear(canvas_id: str) -> str:
    """Reset the canvas to transparent (undoable)."""
    c = _get(canvas_id)
    _snapshot(canvas_id)
    c.pixels = [[-1] * c.size for _ in range(c.size)]
    log("clear", canvas_id=canvas_id)
    return "Cleared."


def _text_view(c: Canvas) -> str:
    """The existing make_tools view: grid + legend + quadrant layout."""
    counts: dict[int, int] = {}
    for row in c.pixels:
        for v in row:
            counts[v] = counts.get(v, 0) + 1
    legend = []
    for idx, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        legend.append(f". = transparent: {n}px" if idx < 0 else f"{_char(idx)} = {idx}({c.palette[idx]}): {n}px")
    filled = sum(n for i, n in counts.items() if i >= 0)
    h = c.size // 2
    layout = (f"TOP-LEFT: {c.region_summary(0, 0, h-1, h-1)} | TOP-RIGHT: {c.region_summary(0, h, h-1, c.size-1)} | "
              f"BOTTOM-LEFT: {c.region_summary(h, 0, c.size-1, h-1)} | BOTTOM-RIGHT: {c.region_summary(h, h, c.size-1, c.size-1)}")
    return (f"{c.to_visual_grid()}\n\nLEGEND: {', '.join(legend[:12])}\n"
            f"Filled: {filled}/{c.size * c.size}px\nLAYOUT: {layout}")


def _image_view(c: Canvas) -> bytes:
    """Nearest-neighbour upscale on a checkerboard (so transparency reads), optional grid + coord labels."""
    cell = max(1, VIEW_PX // c.size)
    edge = cell * c.size
    margin = 18 if VIEW_GRID else 0
    out = PILImage.new("RGBA", (edge + margin, edge + margin), (24, 24, 24, 255))
    checker = PILImage.new("RGBA", (edge, edge))
    cd = ImageDraw.Draw(checker)
    for y in range(c.size):
        for x in range(c.size):
            # dark blue-grey checker: a neutral grey checker camouflaged grey palette colours
            shade = (44, 44, 64, 255) if (x + y) % 2 else (30, 30, 44, 255)
            cd.rectangle([x * cell, y * cell, (x + 1) * cell - 1, (y + 1) * cell - 1], fill=shade)
    sprite = c.to_image().resize((edge, edge), PILImage.NEAREST)
    checker.alpha_composite(sprite)
    out.paste(checker, (margin, margin))
    if VIEW_GRID:
        d = ImageDraw.Draw(out)
        for i in range(c.size + 1):
            p = margin + i * cell
            d.line([(p, margin), (p, margin + edge)], fill=(0, 0, 0, 255) if i % 4 else (255, 0, 255, 255), width=1)
            d.line([(margin, p), (margin + edge, p)], fill=(0, 0, 0, 255) if i % 4 else (255, 0, 255, 255), width=1)
        step = 1 if c.size <= 16 else 4
        for i in range(0, c.size, step):
            mid = margin + i * cell + cell // 2
            d.text((mid - 4, 3), str(i), fill=(230, 230, 230, 255))
            d.text((1, mid - 5), str(i), fill=(230, 230, 230, 255))
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


@mcp.tool(meta=ALWAYS)
def view_canvas(canvas_id: str) -> list:
    """See the canvas. Call it after drawing to check your work."""
    c = _get(canvas_id)
    parts: list = []
    text = ""
    png = b""
    if VIEW in ("text", "both"):
        text = _text_view(c)
        parts.append(text)
    if VIEW in ("image", "both"):
        png = _image_view(c)
        if VIEW == "image":
            parts.append(f"{canvas_id}: {c.size}x{c.size}. Image: {VIEW_PX}px, "
                         + ("magenta gridline every 4px, labels are x/y coords." if VIEW_GRID else "no grid."))
        parts.append(Image(data=png, format="png"))
    log("view_canvas", canvas_id=canvas_id, view=VIEW, px=VIEW_PX, grid=VIEW_GRID, text_chars=len(text), png_bytes=len(png))
    return parts


@mcp.tool(meta=ALWAYS)
def export_png(canvas_id: str, path: str, scale: int = 1, overwrite: bool = False) -> str:
    """Write the canvas as an RGBA PNG to an absolute path, upscaled by integer `scale`."""
    c = _get(canvas_id)
    if not os.path.isabs(path):
        raise ValueError("path must be absolute")
    if os.path.exists(path) and not overwrite:
        raise ValueError("file exists; pass overwrite=true")
    img = c.to_image()
    if scale > 1:
        img = img.resize((c.size * scale, c.size * scale), PILImage.NEAREST)
    img.save(path)
    log("export_png", canvas_id=canvas_id, path=path)
    return f"Wrote {path}"


if __name__ == "__main__":
    mcp.run()
