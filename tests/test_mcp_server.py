"""The MCP server, exercised through a real in-process MCP client."""

import anyio
from mcp import Client

from mcp_server import build_server


def call(server, tool: str, **arguments):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(tool, arguments)
    return anyio.run(go)


def text(result) -> str:
    return "\n".join(block.text for block in result.content if block.type == "text")


def ok(server, tool: str, **arguments) -> str:
    result = call(server, tool, **arguments)
    assert not result.is_error, text(result)
    return text(result)


def error(server, tool: str, **arguments) -> str:
    result = call(server, tool, **arguments)
    assert result.is_error, f"expected {tool} to fail, got: {text(result)}"
    return text(result)


# ── what the painter sees up front ──

def listing(server):
    async def go():
        async with Client(server) as client:
            return client.instructions, (await client.list_tools()).tools
    return anyio.run(go)


TOOLS = ["create_canvas", "list_canvases", "draw", "undo", "clear", "view_canvas", "export_png", "export_tileset"]


def test_exactly_the_eight_canvas_tools_all_always_loaded_and_within_claude_codes_limits():
    instructions, tools = listing(build_server())
    assert sorted(t.name for t in tools) == sorted(TOOLS)
    for t in tools:
        assert t.meta == {"anthropic/alwaysLoad": True}, t.name
        assert 100 < len(t.description) <= 2048, (t.name, len(t.description))
    assert len(instructions) <= 2048


def test_server_instructions_state_the_minimal_contract():
    instructions, _ = listing(build_server())
    for phrase in ("in memory", "export", "view_canvas"):
        assert phrase in instructions


def test_tool_descriptions_carry_the_contract_facts():
    _, tools = listing(build_server())
    d = {t.name: t.description for t in tools}
    assert "top-left" in d["draw"] and "inclusive" in d["draw"]
    for op in ("pixels", "rect", "line", "circle", "ellipse", "triangle", "rotated_rect",
               "flood_fill", "mirror", "noise_rect", "noise_circle", "voronoi_rect"):
        assert f'"{op}"' in d["draw"], op
    assert "-1" in d["draw"] and "atomic" in d["draw"]
    assert "A-Z" in d["view_canvas"] and "." in d["view_canvas"]
    assert "prefer palette_file" in d["create_canvas"].lower()
    assert "outside the palette" in d["export_tileset"]
    assert "overwrite" in d["export_png"] and "scale=1" in d["export_png"]


# ── create_canvas / list_canvases ──

def test_create_canvas_with_an_inline_palette_replies_with_the_palette_table():
    server = build_server()
    reply = ok(server, "create_canvas", size=16, palette=["#ff0000", "00ff00"])
    assert "c1" in reply and "16x16" in reply
    assert " 0  0  #FF0000" in reply.split("\n")
    assert " 1  1  #00FF00" in reply.split("\n")


GPL = """GIMP Palette
Name: Goblins
Columns: 4
#
255   0   0\toutline
  0 255   0\tskin
  0   0 255
"""


def test_create_canvas_from_a_gpl_file_carries_role_names(tmp_path):
    path = tmp_path / "goblins.gpl"
    path.write_text(GPL)
    reply = ok(build_server(), "create_canvas", size=16, palette_file=str(path))
    lines = reply.split("\n")
    assert " 0  0  #FF0000  outline" in lines
    assert " 1  1  #00FF00  skin" in lines
    assert " 2  2  #0000FF" in lines


def test_create_canvas_from_a_hex_file(tmp_path):
    path = tmp_path / "pal.hex"
    path.write_text("ff0000\n00FF00\n\n")
    reply = ok(build_server(), "create_canvas", size=8, palette_file=str(path))
    assert " 1  1  #00FF00" in reply.split("\n")


def test_palette_file_is_copied_in_at_creation(tmp_path):
    server = build_server()
    path = tmp_path / "pal.hex"
    path.write_text("ff0000\n")
    ok(server, "create_canvas", size=8, palette_file=str(path))
    path.write_text("ff0000\n00ff00\n")
    assert "1 colours" in ok(server, "list_canvases")


def test_palette_rules(tmp_path):
    server = build_server()
    (tmp_path / "pal.pal").write_text("ff0000\n")
    cases = {
        "neither": dict(size=8),
        "both": dict(size=8, palette=["#000000"], palette_file=str(tmp_path / "x.hex")),
        "relative path": dict(size=8, palette_file="pal.hex"),
        "unsupported extension": dict(size=8, palette_file=str(tmp_path / "pal.pal")),
        "missing file": dict(size=8, palette_file=str(tmp_path / "nope.hex")),
        "empty": dict(size=8, palette=[]),
        "too many": dict(size=8, palette=["#000000"] * 37),
        "malformed": dict(size=8, palette=["#12345"]),
        "alpha": dict(size=8, palette=["#11223344"]),
        "bad size": dict(size=12, palette=["#000000"]),
    }
    for name, args in cases.items():
        message = error(server, "create_canvas", **args)
        assert message, name
    assert "No canvases" in ok(server, "list_canvases")


def test_relative_palette_path_error_explains_why():
    message = error(build_server(), "create_canvas", size=8, palette_file="palettes/game.gpl")
    assert "absolute" in message and "working directory" in message


def test_duplicate_colours_are_allowed_with_a_warning():
    reply = ok(build_server(), "create_canvas", size=8, palette=["#000000", "#FFFFFF", "#000000"])
    assert "Warning" in reply and "2" in reply and "#000000" in reply


def test_duplicate_canvas_id_is_an_error():
    server = build_server()
    ok(server, "create_canvas", size=8, palette=["#000000"], canvas_id="hero")
    assert "hero" in error(server, "create_canvas", size=8, palette=["#000000"], canvas_id="hero")


def new_canvas(size=8, palette=("#FF0000", "#00FF00")):
    """A server holding one transparent canvas 'a' (index 0 = red, 1 = green)."""
    server = build_server()
    ok(server, "create_canvas", size=size, palette=list(palette), canvas_id="a")
    return server


def grid(server, canvas_id="a") -> list[str]:
    """The pixel rows of view_canvas's text grid (8px canvas), without ruler or row labels."""
    view = text(call(server, "view_canvas", canvas_id=canvas_id))
    grid_text = view.split("\n\n")[0]
    return [line[3:] for line in grid_text.split("\n")[1:]]


EMPTY_8 = ["........"] * 8


def draw(server, *ops, canvas_id="a"):
    return call(server, "draw", canvas_id=canvas_id, ops=list(ops))


# ── draw ──

def test_draw_applies_ops_in_order_and_summarises_each():
    server = new_canvas()
    result = draw(
        server,
        {"op": "rect", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "color": 0},
        {"op": "pixels", "pixels": [[1, 1, 1], [2, 0, 1]]},
    )
    assert not result.is_error, text(result)
    assert text(result).split("\n") == ["1. rect: 4px", "2. pixels: 2px"]
    assert grid(server)[:3] == ["001.....", "01......", "........"]


def test_draw_is_atomic_when_a_later_op_is_invalid():
    server = new_canvas()
    result = draw(
        server,
        {"op": "rect", "x1": 0, "y1": 0, "x2": 7, "y2": 7, "color": 0},
        {"op": "circle", "cx": 3, "cy": 3, "r": 2, "color": 9},
    )
    assert result.is_error
    assert "op 2" in text(result) and "circle" in text(result) and "unchanged" in text(result)
    assert grid(server) == EMPTY_8


def test_draw_rejects_unknown_ops_and_malformed_arguments():
    server = new_canvas()
    for bad in (
        {"op": "spray", "x": 1},
        {"op": "rect", "x1": 0, "y1": 0, "x2": 1, "color": 0},
        {"op": "rect", "x1": 0, "y1": 0, "x2": 1, "y2": "one", "color": 0},
        {"op": "pixels", "pixels": [[1, 1]]},
        {"op": "mirror", "axis": "x", "direction": "top_to_bottom"},
        {"x1": 0},
    ):
        result = draw(server, bad)
        assert result.is_error and "canvas unchanged" in text(result), (bad, text(result))
    assert grid(server) == EMPTY_8


def test_every_colour_bearing_op_validates_its_colour():
    server = new_canvas()
    for bad in (
        {"op": "pixels", "pixels": [[0, 0, 2]]},
        {"op": "rect", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "color": -2},
        {"op": "line", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "color": 2},
        {"op": "circle", "cx": 3, "cy": 3, "r": 1, "color": 2},
        {"op": "ellipse", "cx": 3, "cy": 3, "rx": 2, "ry": 1, "color": 2},
        {"op": "triangle", "x1": 0, "y1": 0, "x2": 3, "y2": 0, "x3": 0, "y3": 3, "color": 2},
        {"op": "rotated_rect", "cx": 3, "cy": 3, "w": 3, "h": 2, "angle": 45, "color": 2},
        {"op": "flood_fill", "x": 0, "y": 0, "color": 2},
        {"op": "noise_rect", "x1": 0, "y1": 0, "x2": 3, "y2": 3, "colors": [0, 2]},
        {"op": "noise_circle", "cx": 3, "cy": 3, "r": 2, "colors": [2]},
        {"op": "voronoi_rect", "x1": 0, "y1": 0, "x2": 3, "y2": 3, "colors": [0, 7]},
        {"op": "noise_rect", "x1": 0, "y1": 0, "x2": 3, "y2": 3, "colors": []},
    ):
        result = draw(server, bad)
        assert result.is_error and "canvas unchanged" in text(result), (bad, text(result))
    assert grid(server) == EMPTY_8


def test_off_canvas_pixels_are_skipped_and_counted_while_shapes_clip():
    server = new_canvas()
    result = draw(
        server,
        {"op": "pixels", "pixels": [[0, 0, 0], [8, 0, 0], [-1, 3, 0]]},
        {"op": "rect", "x1": 6, "y1": 6, "x2": 20, "y2": 20, "color": 1},
    )
    assert text(result).split("\n") == ["1. pixels: 1px (2 off-canvas skipped)", "2. rect: 4px"]
    assert grid(server)[0] == "0......."
    assert grid(server)[7] == "......11"


def test_transparent_erases_and_flood_fill_and_mirror_work_through_draw():
    server = new_canvas()
    draw(
        server,
        {"op": "rect", "x1": 0, "y1": 0, "x2": 3, "y2": 7, "color": 0},
        {"op": "pixels", "pixels": [[1, 1, -1]]},
        {"op": "flood_fill", "x": 5, "y": 0, "color": 1},
        {"op": "mirror", "axis": "x", "direction": "left_to_right"},
    )
    assert grid(server)[:2] == ["00000000", "0.0000.0"]


def test_every_op_kind_is_accepted():
    server = new_canvas(size=16)
    result = draw(
        server,
        {"op": "pixels", "pixels": [[0, 0, 0]]},
        {"op": "rect", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "color": 0},
        {"op": "line", "x1": 0, "y1": 0, "x2": 5, "y2": 5, "color": 1},
        {"op": "circle", "cx": 8, "cy": 8, "r": 3, "color": 0, "fill": False},
        {"op": "ellipse", "cx": 8, "cy": 8, "rx": 4, "ry": 2, "color": 1},
        {"op": "triangle", "x1": 0, "y1": 15, "x2": 5, "y2": 15, "x3": 0, "y3": 10, "color": 0, "fill": False},
        {"op": "rotated_rect", "cx": 12, "cy": 3, "w": 4, "h": 2, "angle": 30, "color": 1},
        {"op": "flood_fill", "x": 15, "y": 15, "color": 0},
        {"op": "mirror", "axis": "y", "direction": "top_to_bottom"},
        {"op": "noise_rect", "x1": 0, "y1": 0, "x2": 3, "y2": 3, "colors": [0, 1], "seed": 3},
        {"op": "noise_circle", "cx": 8, "cy": 8, "r": 2, "colors": [0, 1]},
        {"op": "voronoi_rect", "x1": 10, "y1": 10, "x2": 15, "y2": 15, "colors": [0, 1], "cells": 4},
    )
    assert not result.is_error, text(result)
    assert len(text(result).split("\n")) == 12


# ── undo / clear ──

def dot(x, y, color=0):
    return {"op": "pixels", "pixels": [[x, y, color]]}


def test_undo_reverts_a_whole_draw_call_as_one_step():
    server = new_canvas()
    draw(server, dot(0, 0))
    draw(server, dot(1, 0), dot(2, 0))
    assert "Undid 1 step" in ok(server, "undo", canvas_id="a")
    assert grid(server)[0] == "0......."


def test_undo_past_the_start_of_history_undoes_what_it_can_and_says_so():
    server = new_canvas()
    draw(server, dot(0, 0))
    reply = ok(server, "undo", canvas_id="a", steps=3)
    assert "Undid 1 of 3" in reply and "no more history" in reply
    assert grid(server) == EMPTY_8


def test_clear_resets_to_transparent_and_can_be_undone():
    server = new_canvas()
    draw(server, dot(3, 3))
    ok(server, "clear", canvas_id="a")
    assert grid(server) == EMPTY_8
    ok(server, "undo", canvas_id="a")
    assert grid(server)[3] == "...0...."


def test_history_keeps_the_last_50_steps():
    server = new_canvas()
    for i in range(55):
        draw(server, dot(i % 8, i // 8))
    reply = ok(server, "undo", canvas_id="a", steps=60)
    assert "Undid 50 of 60" in reply
    assert grid(server)[0] == "00000..."  # the first 5 draws are beyond history


def test_a_failed_draw_adds_no_undo_step():
    server = new_canvas()
    draw(server, dot(0, 0))
    draw(server, {"op": "spray"})
    ok(server, "undo", canvas_id="a")
    assert grid(server) == EMPTY_8


# ── exports and from_png ──

from PIL import Image  # noqa: E402

RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)
CLEAR = (0, 0, 0, 0)


def test_export_png_writes_rgba_at_native_size(tmp_path):
    server = new_canvas()
    draw(server, dot(0, 0, 0), dot(1, 0, 1))
    out = tmp_path / "hero.png"
    reply = ok(server, "export_png", canvas_id="a", path=str(out))
    assert str(out) in reply and "8x8" in reply
    img = Image.open(out)
    assert img.mode == "RGBA" and img.size == (8, 8)
    assert [img.getpixel((x, 0)) for x in range(3)] == [RED, GREEN, CLEAR]


def test_export_png_upscales_by_an_integer_scale(tmp_path):
    server = new_canvas()
    draw(server, dot(1, 0, 0))
    out = tmp_path / "big.png"
    assert "32x32" in ok(server, "export_png", canvas_id="a", path=str(out), scale=4)
    img = Image.open(out)
    assert img.size == (32, 32)
    assert img.getpixel((4, 0)) == RED and img.getpixel((7, 3)) == RED and img.getpixel((8, 0)) == CLEAR


def test_export_creates_missing_parent_directories(tmp_path):
    out = tmp_path / "art" / "items" / "sword.png"
    ok(new_canvas(), "export_png", canvas_id="a", path=str(out))
    assert out.exists()


def test_export_path_and_scale_rules(tmp_path):
    server = new_canvas()
    for tool in ("export_png", "export_tileset"):
        assert "absolute" in error(server, tool, canvas_id="a", path="sword.png")
        assert ".png" in error(server, tool, canvas_id="a", path=str(tmp_path / "sword.jpg"))
        for scale in (0, 17):
            assert "scale" in error(server, tool, canvas_id="a", path=str(tmp_path / "s.png"), scale=scale)


def test_export_refuses_to_overwrite_unless_asked(tmp_path):
    server = new_canvas()
    out = tmp_path / "hero.png"
    out.write_bytes(b"precious")
    assert "overwrite" in error(server, "export_png", canvas_id="a", path=str(out))
    assert out.read_bytes() == b"precious"
    ok(server, "export_png", canvas_id="a", path=str(out), overwrite=True)
    assert Image.open(out).size == (8, 8)


def test_export_tileset_writes_sixteen_numbered_variants_next_to_path(tmp_path):
    server = new_canvas()
    draw(server, {"op": "rect", "x1": 0, "y1": 0, "x2": 7, "y2": 7, "color": 1})
    reply = ok(server, "export_tileset", canvas_id="a", path=str(tmp_path / "grass.png"), scale=2)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [f"grass_{i:02d}.png" for i in range(16)]
    assert str(tmp_path / "grass_15.png") in reply and "16x16" in reply
    base = Image.open(tmp_path / "grass_15.png")
    assert base.size == (16, 16) and base.getpixel((8, 8)) == GREEN
    assert Image.open(tmp_path / "grass_00.png").getpixel((0, 0))[3] == 0


def test_export_tileset_checks_every_target_before_writing_any(tmp_path):
    server = new_canvas()
    (tmp_path / "grass_07.png").write_bytes(b"precious")
    message = error(server, "export_tileset", canvas_id="a", path=str(tmp_path / "grass.png"))
    assert "grass_07.png" in message
    assert sorted(p.name for p in tmp_path.iterdir()) == ["grass_07.png"]


def test_from_png_round_trips_a_native_export(tmp_path):
    server = new_canvas()
    draw(server, {"op": "rect", "x1": 1, "y1": 1, "x2": 3, "y2": 2, "color": 0}, dot(7, 7, 1))
    before = grid(server)
    out = tmp_path / "saved.png"
    ok(server, "export_png", canvas_id="a", path=str(out))
    reply = ok(server, "create_canvas", palette=["#FF0000", "#00FF00"], from_png=str(out), canvas_id="b")
    assert "8x8" in reply
    assert grid(server, "b") == before


def test_from_png_maps_repeated_colours_to_the_first_index(tmp_path):
    server = new_canvas()
    draw(server, dot(0, 0, 1))
    out = tmp_path / "saved.png"
    ok(server, "export_png", canvas_id="a", path=str(out))
    ok(server, "create_canvas", palette=["#FF0000", "#00FF00", "#00FF00"], from_png=str(out), canvas_id="b")
    assert grid(server, "b")[0] == "1......."


def test_from_png_rules(tmp_path):
    server = build_server()

    def png(name, size, pixel):
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        img.putpixel((0, 0), pixel)
        img.save(tmp_path / name)
        return str(tmp_path / name)

    pal = ["#FF0000"]
    assert "(0, 0)" in error(server, "create_canvas", palette=pal, from_png=png("off.png", (8, 8), (1, 2, 3, 255)))
    assert "transparent" in error(server, "create_canvas", palette=pal, from_png=png("semi.png", (8, 8), (255, 0, 0, 128)))
    assert "square" in error(server, "create_canvas", palette=pal, from_png=png("wide.png", (16, 8), RED))
    assert "12" in error(server, "create_canvas", palette=pal, from_png=png("odd.png", (12, 12), RED))
    assert "size" in error(server, "create_canvas", palette=pal, size=16, from_png=png("ok.png", (8, 8), RED))
    assert "absolute" in error(server, "create_canvas", palette=pal, from_png="ok.png")
    assert "size" in error(server, "create_canvas", palette=pal)  # neither size nor from_png
    assert "No canvases" in ok(server, "list_canvases")


# ── view_canvas ──

def test_view_canvas_returns_the_text_view_and_a_png():
    server = new_canvas()
    result = call(server, "view_canvas", canvas_id="a")
    assert not result.is_error
    assert [b.type for b in result.content] == ["text", "image"]
    assert result.content[1].mime_type == "image/png"
    assert "Filled: 0/64 px" in result.content[0].text


def test_unknown_canvas_id_is_an_error_naming_the_known_ones():
    server = new_canvas()
    message = error(server, "view_canvas", canvas_id="nope")
    assert "nope" in message and "a" in message


def test_list_canvases_reports_id_size_and_palette_length():
    server = build_server()
    ok(server, "create_canvas", size=8, palette=["#000000"])
    ok(server, "create_canvas", size=32, palette=["#000000", "#FFFFFF"], canvas_id="hero")
    reply = ok(server, "list_canvases")
    assert "c1: 8x8, 1 colours" in reply
    assert "hero: 32x32, 2 colours" in reply


# ── the real stdio entry point ──

def stdio_session(script, cwd):
    import sys
    from mcp import StdioServerParameters

    params = StdioServerParameters(command=sys.executable, args=[str(script)], cwd=str(cwd))

    async def go():
        async with Client(params) as client:
            tools = (await client.list_tools()).tools
            created = await client.call_tool("create_canvas", {"size": 8, "palette": ["#FF0000"]})
            return sorted(t.name for t in tools), created
    return anyio.run(go)


def test_stdio_entry_point_serves_the_tools_from_any_working_directory(tmp_path):
    from pathlib import Path
    script = Path(__file__).resolve().parent.parent / "mcp_server.py"
    names, created = stdio_session(script, cwd=tmp_path)
    assert names == sorted(TOOLS)
    assert not created.is_error and "c1" in text(created)
