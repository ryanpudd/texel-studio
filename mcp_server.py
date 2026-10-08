#!/usr/bin/env python3
"""
Texel MCP server: Texel's canvas tools over stdio, with Claude Code as the painter.

Launched by the texel-painter agent (see docs/adr/0001-painter-agent-owns-the-mcp-server.md).
stdout carries the MCP protocol, so anything else must go to stderr.
"""

import inspect
import re
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from PIL import Image as PILImage

from canvas import Canvas, generate_tileset, grid_char

ALWAYS_LOAD = {"anthropic/alwaysLoad": True}
SIZES = (8, 16, 32, 64)
MAX_COLOURS = 36  # the grid encodes indices as 0-9, A-Z

INSTRUCTIONS = (
    "Texel: pixel-art canvas tools; you are the painter. Canvases live in memory and are lost when this "
    "server stops (every subagent run and every resume gets a fresh server), so export_png anything worth "
    "keeping before you finish; create_canvas(from_png=...) reloads a native (scale=1) export. "
    "Call view_canvas after every draw to check your work before moving on."
)

HEX = re.compile(r"#?([0-9A-Fa-f]{6})")


def _normalise_hex(entry: str) -> str:
    m = HEX.fullmatch(entry.strip())
    if not m:
        raise ToolError(f"palette entry {entry!r} is not a #RRGGBB hex colour")
    return "#" + m.group(1).upper()


def _read_palette_file(path: str) -> tuple[list[str], list[str]]:
    """Parse a .hex (Lospec) or .gpl (GIMP) palette into (hex colours, role names)."""
    p = Path(path)
    if not p.is_absolute():
        raise ToolError(
            f"palette_file must be an absolute path, got {path!r}: the server's working directory "
            "is not the game project's"
        )
    if p.suffix.lower() not in (".hex", ".gpl"):
        raise ToolError(f"palette_file must be a .hex or .gpl file, got {p.suffix or 'no extension'!r}")
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise ToolError(f"cannot read palette_file {path!r}: {e}")

    colours: list[str] = []
    roles: list[str] = []
    if p.suffix.lower() == ".hex":
        for line in lines:
            if line.strip():
                colours.append(_normalise_hex(line))
                roles.append("")
        return colours, roles

    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or s == "GIMP Palette" or re.match(r"(Name|Columns):", s):
            continue
        parts = s.split(None, 3)
        try:
            r, g, b = (int(v) for v in parts[:3])
        except ValueError:
            raise ToolError(f"palette_file line {line!r} is not 'R G B  Name'")
        if not all(0 <= v <= 255 for v in (r, g, b)):
            raise ToolError(f"palette_file line {line!r} has a channel outside 0-255")
        colours.append(f"#{r:02X}{g:02X}{b:02X}")
        roles.append(parts[3].strip() if len(parts) > 3 else "")
    return colours, roles


def _resolve_palette(palette: list[str] | None, palette_file: str | None) -> tuple[list[str], list[str]]:
    if (palette is None) == (palette_file is None):
        raise ToolError("pass exactly one of palette (inline hex list) or palette_file (absolute .hex/.gpl path)")
    if palette_file is not None:
        colours, roles = _read_palette_file(palette_file)
    else:
        colours, roles = [_normalise_hex(p) for p in palette], [""] * len(palette)
    if not 1 <= len(colours) <= MAX_COLOURS:
        raise ToolError(f"a palette needs 1-{MAX_COLOURS} colours, got {len(colours)}")
    return colours, roles


def _palette_table(palette: list[str], roles: list[str]) -> str:
    rows = [f"{i:>2}  {grid_char(i)}  {hex_}" + (f"  {role}" if role else "")
            for i, (hex_, role) in enumerate(zip(palette, roles))]
    return "Palette (index, char, hex, role):\n" + "\n".join(rows)


def _duplicate_warnings(palette: list[str]) -> list[str]:
    first: dict[str, int] = {}
    warnings = []
    for i, hex_ in enumerate(palette):
        if hex_ in first:
            warnings.append(f"Warning: index {i} repeats {hex_} (same as index {first[hex_]}).")
        else:
            first[hex_] = i
    return warnings


class _OpArgs:
    """Typed, validated access to one op's fields. Raises ValueError with a painter-readable reason."""

    _MISSING = object()

    def __init__(self, op: dict, canvas: Canvas):
        self.op = op
        self.canvas = canvas
        self.used = {"op"}

    def _get(self, name, default=_MISSING):
        self.used.add(name)
        if name in self.op:
            return self.op[name]
        if default is self._MISSING:
            raise ValueError(f"missing field {name!r}")
        return default

    def int(self, name: str, default=_MISSING) -> int:
        v = self._get(name, default)
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{name!r} must be an integer, got {v!r}")
        return v

    def num(self, name: str) -> float:
        v = self._get(name)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{name!r} must be a number, got {v!r}")
        return v

    def bool(self, name: str, default: bool) -> bool:
        v = self._get(name, default)
        if not isinstance(v, bool):
            raise ValueError(f"{name!r} must be true or false, got {v!r}")
        return v

    def check_colour(self, v, name: str) -> int:
        top = len(self.canvas.palette) - 1
        if isinstance(v, bool) or not isinstance(v, int) or not -1 <= v <= top:
            raise ValueError(f"{name!r} colour {v!r} is not a palette index (use -1 for transparent, or 0-{top})")
        return v

    def colour(self, name: str = "color") -> int:
        return self.check_colour(self._get(name), name)

    def colours(self, name: str = "colors") -> list[int]:
        v = self._get(name)
        if not isinstance(v, list) or not v:
            raise ValueError(f"{name!r} must be a non-empty list of palette indices")
        return [self.check_colour(i, name) for i in v]

    def choice(self, name: str, options) -> str:
        v = self._get(name)
        if v not in options:
            raise ValueError(f"{name!r} must be one of {list(options)}, got {v!r}")
        return v

    def check_no_extras(self):
        extra = sorted(set(self.op) - self.used)
        if extra:
            raise ValueError(f"unexpected field(s) {extra}")


def _op_pixels(c: Canvas, a: _OpArgs) -> int:
    entries = a._get("pixels")
    if not isinstance(entries, list):
        raise ValueError("'pixels' must be a list of [x, y, color]")
    skipped = 0
    for e in entries:
        if not (isinstance(e, list) and len(e) == 3 and all(isinstance(v, int) and not isinstance(v, bool) for v in e)):
            raise ValueError(f"pixel entry {e!r} must be [x, y, color] integers")
        x, y, col = e
        a.check_colour(col, "pixels")
        if 0 <= x < c.size and 0 <= y < c.size:
            c.pixels[y][x] = col
        else:
            skipped += 1
    return skipped


def _op_mirror(c: Canvas, a: _OpArgs) -> None:
    axis = a.choice("axis", Canvas.MIRROR_DIRECTIONS)
    direction = a.choice("direction", Canvas.MIRROR_DIRECTIONS[axis])
    c.mirror(axis, direction)


# Each op reads and validates its fields through _OpArgs before touching the canvas.
OPS = {
    "pixels": _op_pixels,
    "rect": lambda c, a: c.fill_rect(a.int("x1"), a.int("y1"), a.int("x2"), a.int("y2"), a.colour()),
    "line": lambda c, a: c.draw_line(a.int("x1"), a.int("y1"), a.int("x2"), a.int("y2"), a.colour()),
    "circle": lambda c, a: c.draw_circle(a.int("cx"), a.int("cy"), a.int("r"), a.colour(), a.bool("fill", True)),
    "ellipse": lambda c, a: c.draw_ellipse(
        a.int("cx"), a.int("cy"), a.int("rx"), a.int("ry"), a.colour(), a.bool("fill", True)),
    "triangle": lambda c, a: c.draw_triangle(
        a.int("x1"), a.int("y1"), a.int("x2"), a.int("y2"), a.int("x3"), a.int("y3"), a.colour(),
        a.bool("fill", True)),
    "rotated_rect": lambda c, a: c.draw_rotated_rect(
        a.int("cx"), a.int("cy"), a.int("w"), a.int("h"), a.num("angle"), a.colour()),
    "flood_fill": lambda c, a: c.flood_fill(a.int("x"), a.int("y"), a.colour()),
    "mirror": _op_mirror,
    "noise_rect": lambda c, a: c.fill_noise(
        a.int("x1"), a.int("y1"), a.int("x2"), a.int("y2"), a.colours(), a.int("seed", 42)),
    "noise_circle": lambda c, a: c.fill_noise_circle(
        a.int("cx"), a.int("cy"), a.int("r"), a.colours(), a.int("seed", 42)),
    "voronoi_rect": lambda c, a: c.fill_voronoi(
        a.int("x1"), a.int("y1"), a.int("x2"), a.int("y2"), a.colours(), a.int("cells", 8), a.int("seed", 42)),
}


def _changed(before: list[list[int]], after: list[list[int]]) -> int:
    return sum(a != b for ra, rb in zip(before, after) for a, b in zip(ra, rb))


def _apply_op(c: Canvas, op) -> str:
    if not isinstance(op, dict):
        raise ValueError(f"each op must be an object with an 'op' key, got {op!r}")
    kind = op.get("op")
    if kind not in OPS:
        raise ValueError(f"unknown op {kind!r} (known: {', '.join(OPS)})")
    args = _OpArgs(op, c)
    before = [row[:] for row in c.pixels]
    result = OPS[kind](c, args)
    args.check_no_extras()
    summary = f"{kind}: {_changed(before, c.pixels)}px"
    if kind == "pixels" and result:
        summary += f" ({result} off-canvas skipped)"
    return summary


HISTORY_LIMIT = 50
MAX_SCALE = 16


def _export_target(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        raise ToolError(f"path must be absolute, got {path!r}")
    if p.suffix.lower() != ".png":
        raise ToolError(f"path must end in .png, got {path!r}")
    return p


def _check_scale(scale: int) -> None:
    if not 1 <= scale <= MAX_SCALE:
        raise ToolError(f"scale must be an integer from 1 to {MAX_SCALE}, got {scale}")


def _upscale(img: PILImage.Image, scale: int) -> PILImage.Image:
    return img.resize((img.width * scale, img.height * scale), PILImage.NEAREST) if scale > 1 else img


def _pixels_from_png(path: str, palette: list[str]) -> list[list[int]]:
    """Load a native-size PNG as palette indices. Opaque pixels must be exact palette colours."""
    p = Path(path)
    if not p.is_absolute():
        raise ToolError(f"from_png must be an absolute path, got {path!r}")
    try:
        img = PILImage.open(p).convert("RGBA")
    except OSError as e:
        raise ToolError(f"cannot read from_png {path!r}: {e}")
    if img.width != img.height:
        raise ToolError(f"from_png must be square, got {img.width}x{img.height}")
    if img.width not in SIZES:
        raise ToolError(f"from_png must be a native {SIZES} px sprite, got {img.width}x{img.height} "
                        "(upscaled exports can't be loaded; save with scale=1)")
    index: dict[tuple[int, int, int], int] = {}
    for i, hex_ in enumerate(palette):
        index.setdefault((int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16)), i)
    pixels = []
    for y in range(img.height):
        row = []
        for x in range(img.width):
            r, g, b, a = img.getpixel((x, y))
            if a == 0:
                row.append(-1)
            elif a < 255:
                raise ToolError(f"from_png pixel ({x}, {y}) is semi-transparent (alpha {a}); "
                                "only fully opaque or fully transparent pixels can be loaded")
            elif (r, g, b) not in index:
                raise ToolError(f"from_png pixel ({x}, {y}) is #{r:02X}{g:02X}{b:02X}, which is not in the palette")
            else:
                row.append(index[(r, g, b)])
        pixels.append(row)
    return pixels


def build_server() -> MCPServer:
    """A fresh server with its own in-memory canvas registry."""
    server = MCPServer("texel", instructions=INSTRUCTIONS)

    def canvas_tool(fn):
        """Register an always-loaded tool whose description is its docstring, de-indented."""
        return server.tool(description=inspect.getdoc(fn), meta=ALWAYS_LOAD)(fn)

    canvases: dict[str, Canvas] = {}
    history: dict[str, list[list[list[int]]]] = {}  # per canvas: pixel snapshots, newest last

    def remember(canvas_id: str, snapshot: list[list[int]]) -> None:
        h = history.setdefault(canvas_id, [])
        h.append(snapshot)
        del h[:-HISTORY_LIMIT]

    @canvas_tool
    def create_canvas(
        size: int | None = None,
        palette: list[str] | None = None,
        palette_file: str | None = None,
        canvas_id: str | None = None,
        from_png: str | None = None,
    ) -> str:
        """Create a square canvas and return its canvas_id plus the palette table.

        size: 8, 16, 32 or 64 (square only). Optional when from_png is given, since the PNG sets it.
        Palette: pass exactly one of
          palette: inline list of 1-36 hex colours ("#RRGGBB", "#" optional, no alpha), or
          palette_file: ABSOLUTE path to a .hex (one RRGGBB per line) or .gpl (GIMP "R G B  Name") file.
        Prefer palette_file: a project .gpl with a role name per colour (outline, skin, skin-shadow...) keeps
        sprites consistent and shows the roles in the reply. The palette is copied in now and never changes.
        Pixels hold palette INDICES; the reply's table maps index -> grid char -> hex -> role.
        Duplicate colours are allowed (warned) so two indices can stay separate on purpose.
        canvas_id: optional name; defaults to c1, c2, ... A duplicate id is an error.
        from_png: ABSOLUTE path to a native-size PNG (e.g. an export_png with scale=1) to load as the starting
        pixels, for editing a saved sprite. Every opaque pixel must exactly match a palette colour (first
        match wins); alpha 0 becomes -1. Undo history starts empty.
        """
        if from_png is None and size is None:
            raise ToolError(f"size is required (one of {SIZES}) unless from_png is given")
        if size is not None and size not in SIZES:
            raise ToolError(f"size must be one of {SIZES}")
        colours, roles = _resolve_palette(palette, palette_file)
        cid = canvas_id or next(f"c{n}" for n in range(1, len(canvases) + 2) if f"c{n}" not in canvases)
        if cid in canvases:
            raise ToolError(f"canvas_id {cid!r} already exists")
        if from_png is not None:
            pixels = _pixels_from_png(from_png, colours)
            if size is not None and size != len(pixels):
                raise ToolError(f"size {size} conflicts with from_png, which is {len(pixels)}x{len(pixels)}")
            size = len(pixels)
            canvases[cid] = Canvas(size, colours, pixels)
            state = f"loaded from {from_png} (no undo history)"
        else:
            canvases[cid] = Canvas(size, colours)
            state = "transparent"
        return "\n".join([
            f"Created canvas {cid}: {size}x{size}, {state}.",
            _palette_table(colours, roles),
            *_duplicate_warnings(colours),
        ])

    def get(canvas_id: str) -> Canvas:
        if canvas_id not in canvases:
            known = ", ".join(canvases) or "none"
            raise ToolError(f"unknown canvas_id {canvas_id!r} (known: {known})")
        return canvases[canvas_id]

    @canvas_tool
    def draw(canvas_id: str, ops: list[dict]) -> str:
        """Draw on a canvas: apply a list of ops in order. The call is atomic: if any op is invalid, nothing
        is drawn and the error names the op. A whole call is one undo step. Returns pixels changed per op.

        Coordinates: (0,0) is top-left, x goes right, y goes down; all bounds are inclusive.
        Colours are palette indices; -1 is transparent (erases). Shapes and fills clip at the canvas edge.
        Each op is an object with an "op" key:
          {"op":"pixels","pixels":[[x,y,color],...]}   off-canvas points are skipped and counted
          {"op":"rect","x1","y1","x2","y2","color"}     filled rectangle
          {"op":"line","x1","y1","x2","y2","color"}     1px line
          {"op":"circle","cx","cy","r","color","fill":true}
          {"op":"ellipse","cx","cy","rx","ry","color","fill":true}
          {"op":"triangle","x1","y1","x2","y2","x3","y3","color","fill":true}
          {"op":"rotated_rect","cx","cy","w","h","angle","color"}   filled; angle in degrees
          {"op":"flood_fill","x","y","color"}           recolours the 4-connected same-colour region
          {"op":"mirror","axis":"x","direction":"left_to_right"|"right_to_left"}
          {"op":"mirror","axis":"y","direction":"top_to_bottom"|"bottom_to_top"}
                                                        copies one half of the whole canvas onto the other
          {"op":"noise_rect","x1","y1","x2","y2","colors":[...],"seed":42}       random texture
          {"op":"noise_circle","cx","cy","r","colors":[...],"seed":42}
          {"op":"voronoi_rect","x1","y1","x2","y2","colors":[...],"cells":8,"seed":42}  stone/cell pattern
        "fill", "seed" and "cells" are optional with the defaults shown; other fields are required.
        Nothing is shown after drawing: call view_canvas to check the result.
        """
        c = get(canvas_id)
        saved = [row[:] for row in c.pixels]
        lines = []
        for n, op in enumerate(ops, 1):
            try:
                lines.append(f"{n}. {_apply_op(c, op)}")
            except Exception as e:  # any failure, expected or not, must leave the canvas untouched
                c.pixels = saved
                kind = op.get("op") if isinstance(op, dict) else None
                raise ToolError(f"op {n} ({kind}): {e}. Nothing was drawn; canvas unchanged.")
        remember(canvas_id, saved)
        return "\n".join(lines) if lines else "No ops; canvas unchanged."

    @canvas_tool
    def undo(canvas_id: str, steps: int = 1) -> str:
        """Undo the last `steps` mutating calls (draw or clear) on a canvas. A whole draw call is one step.
        History keeps the last 50 steps per canvas. Asking for more steps than exist undoes what it can and
        says so. Canvases loaded with from_png start with no history.
        """
        c = get(canvas_id)
        if steps < 1:
            raise ToolError("steps must be at least 1")
        h = history.setdefault(canvas_id, [])
        n = min(steps, len(h))
        for _ in range(n):
            c.pixels = h.pop()
        if n < steps:
            return f"Undid {n} of {steps} requested steps; no more history."
        return f"Undid {n} step(s). {len(h)} left."

    @canvas_tool
    def clear(canvas_id: str) -> str:
        """Reset every pixel of a canvas to transparent (-1). This is one undo step, so undo brings the
        previous pixels back. The palette is unchanged.
        """
        c = get(canvas_id)
        remember(canvas_id, c.pixels)
        c.pixels = [[-1] * c.size for _ in range(c.size)]
        return f"Cleared {canvas_id}. Undo restores it."

    @canvas_tool
    def view_canvas(canvas_id: str) -> list:
        """See a canvas. Returns two parts:
        1. Text: a grid with one char per pixel, a column ruler on top and row numbers on the left
           (x = column, y = row, (0,0) top-left). Chars: 0-9 = palette indices 0-9, A-Z = indices 10-35,
           "." = transparent. Then a legend of every colour in use (char = index #hex: pixel count),
           most-used first, and "Filled: n/N px".
        2. Image: the sprite upscaled to ~512px over a dark blue-grey checkerboard (the checker is
           transparency), with 1px gridlines, a magenta line every 4 pixels, and x/y labels along the
           top and left edges.
        Check the result against the brief after every draw, then fix what's wrong.
        """
        c = get(canvas_id)
        return [c.to_view_text(), Image(data=c.to_view_png(), format="png")]

    @canvas_tool
    def export_png(canvas_id: str, path: str, scale: int = 1, overwrite: bool = False) -> str:
        """Export a canvas as one RGBA PNG at size x scale pixels; transparent (-1) pixels get alpha 0.

        path: ABSOLUTE path ending in .png. Missing parent directories are created.
        scale: integer 1-16, nearest-neighbour upscale. scale=1 is the native sprite, which is the save
        format: create_canvas(from_png=...) can reload it later; upscaled exports can't be reloaded.
        overwrite: an existing file is an error unless overwrite is true.
        Returns the path written and its pixel size. Use view_canvas, not this, to look at the result.
        """
        c = get(canvas_id)
        target = _export_target(path)
        _check_scale(scale)
        if target.exists() and not overwrite:
            raise ToolError(f"{target} already exists; pass overwrite=true to replace it")
        img = _upscale(c.to_image(), scale)
        target.parent.mkdir(parents=True, exist_ok=True)
        img.save(target)
        return f"Wrote {target} ({img.width}x{img.height})."

    @canvas_tool
    def export_tileset(canvas_id: str, path: str, scale: int = 1, overwrite: bool = False) -> str:
        """Export a canvas as a 16-tile autotile tileset for a game to pick tiles by their neighbours.

        Writes <stem>_00.png ... <stem>_15.png next to path (an ABSOLUTE path ending in .png; the file
        itself is not written). The number is a bitmask of the neighbours present: TOP=1, RIGHT=2,
        BOTTOM=4, LEFT=8, so 15 (all neighbours) is the canvas unchanged and 0 is an isolated tile with
        outline, edge shading and rounded corners. Variants are made at native size, then upscaled by
        scale (integer 1-16, nearest-neighbour).
        Their shading and outline use colours outside the palette; the canvas itself stays palette-pure.
        If any of the 16 files exists, nothing is written unless overwrite is true. RGBA PNGs.
        Returns the paths written and the tile size.
        """
        c = get(canvas_id)
        target = _export_target(path)
        _check_scale(scale)
        targets = [target.with_name(f"{target.stem}_{mask:02d}.png") for mask in range(16)]
        existing = [t.name for t in targets if t.exists()]
        if existing and not overwrite:
            raise ToolError(f"{', '.join(existing)} already exist(s) in {target.parent}; "
                            "pass overwrite=true to replace. Nothing was written.")
        variants = generate_tileset(c.to_image())
        target.parent.mkdir(parents=True, exist_ok=True)
        for mask, t in enumerate(targets):
            _upscale(variants[mask], scale).save(t)
        edge = c.size * scale
        return f"Wrote 16 tiles ({edge}x{edge} each):\n" + "\n".join(str(t) for t in targets)

    @canvas_tool
    def list_canvases() -> str:
        """List the canvases that exist in this server process: canvas_id, size and palette length for each.
        Canvases live in memory only; a new server process (a new subagent run or a resumed session)
        starts with none.
        """
        if not canvases:
            return "No canvases."
        return "\n".join(f"{cid}: {c.size}x{c.size}, {len(c.palette)} colours" for cid, c in canvases.items())

    return server


if __name__ == "__main__":
    build_server().run()
