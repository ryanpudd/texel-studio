import subprocess
import sys
from pathlib import Path

from PIL import Image

from canvas import Canvas, generate_tileset

REPO_ROOT = Path(__file__).resolve().parent.parent

RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)
CLEAR = (0, 0, 0, 0)
PALETTE = ["#FF0000", "#00FF00"]


def rendered(canvas: Canvas) -> list[str]:
    """Read to_image() back as rows of R / G / . so expectations are literal pictures."""
    img = canvas.to_image()
    key = {RED: "R", GREEN: "G", CLEAR: "."}
    return [
        "".join(key[img.getpixel((x, y))] for x in range(img.width))
        for y in range(img.height)
    ]


def test_canvas_module_imports_without_web_or_llm_stack():
    heavy = ["langchain_core", "langgraph", "fastapi", "google.genai", "dotenv"]
    probe = (
        "import sys, canvas; "
        f"print([m for m in {heavy!r} if m in sys.modules])"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


# ── Primitives, observed through to_image ──

def test_new_canvas_renders_fully_transparent():
    assert rendered(Canvas(3, PALETTE)) == ["...", "...", "..."]


def test_fill_rect_is_inclusive():
    c = Canvas(4, PALETTE)
    c.fill_rect(1, 1, 2, 2, 0)
    assert rendered(c) == ["....", ".RR.", ".RR.", "...."]


def test_fill_rect_clips_to_the_canvas():
    c = Canvas(4, PALETTE)
    c.fill_rect(-2, -2, 1, 1, 1)
    assert rendered(c) == ["GG..", "GG..", "....", "...."]


def test_fill_rect_with_invalid_colour_changes_nothing():
    c = Canvas(3, PALETTE)
    result = c.fill_rect(0, 0, 2, 2, 5)
    assert result.startswith("Error")
    assert rendered(c) == ["...", "...", "..."]


def test_transparent_index_erases():
    c = Canvas(3, PALETTE)
    c.fill_rect(0, 0, 2, 2, 0)
    c.set_pixel(1, 1, -1)
    assert rendered(c) == ["RRR", "R.R", "RRR"]


def test_draw_line_diagonal():
    c = Canvas(4, PALETTE)
    c.draw_line(0, 0, 3, 3, 1)
    assert rendered(c) == ["G...", ".G..", "..G.", "...G"]


def test_filled_circle_of_radius_one_is_a_plus():
    c = Canvas(5, PALETTE)
    c.draw_circle(2, 2, 1, 0)
    assert rendered(c) == [".....", "..R..", ".RRR.", "..R..", "....."]


def test_filled_triangle_includes_its_edges():
    c = Canvas(4, PALETTE)
    c.draw_triangle(0, 0, 3, 0, 0, 3, 0)
    assert rendered(c) == ["RRRR", "RRR.", "RR..", "R..."]


def test_unfilled_triangle_draws_only_its_edges():
    c = Canvas(5, PALETTE)
    c.draw_triangle(0, 0, 4, 0, 0, 4, 0, fill=False)
    assert rendered(c) == ["RRRRR", "R..R.", "R.R..", "RR...", "R...."]


def test_noise_fill_covers_region_with_only_the_given_colours():
    c = Canvas(6, ["#FF0000", "#00FF00", "#0000FF"])
    c.fill_noise(1, 1, 4, 4, colors=[0, 1], seed=7)
    region = {c.pixels[y][x] for y in range(1, 5) for x in range(1, 5)}
    assert region <= {0, 1}
    assert c.pixels[0][0] == -1 and c.pixels[5][5] == -1


def test_flood_fill_is_four_connected_and_stops_at_other_colours():
    c = Canvas(4, PALETTE)
    c.draw_line(0, 3, 3, 0, 0)  # anti-diagonal wall of red
    changed = c.flood_fill(0, 0, 1)
    assert rendered(c) == ["GGGR", "GGR.", "GR..", "R..."]
    assert changed == 6


def test_flood_fill_onto_the_same_colour_changes_nothing():
    c = Canvas(3, PALETTE)
    assert c.flood_fill(1, 1, -1) == 0
    assert rendered(c) == ["...", "...", "..."]


def test_mirror_left_to_right_copies_the_left_half_over_the_right():
    c = Canvas(4, PALETTE)
    c.fill_rect(0, 0, 0, 3, 0)
    c.set_pixel(1, 1, 1)
    c.set_pixel(3, 2, 1)  # right half gets overwritten
    changed = c.mirror("x", "left_to_right")
    assert rendered(c) == ["R..R", "RGGR", "R..R", "R..R"]
    assert changed == 5  # four in column 3, one in column 2


def test_mirror_bottom_to_top_copies_the_bottom_half_over_the_top():
    c = Canvas(4, PALETTE)
    c.fill_rect(0, 3, 3, 3, 0)
    c.set_pixel(2, 2, 1)
    c.mirror("y", "bottom_to_top")
    assert rendered(c) == ["RRRR", "..G.", "..G.", "RRRR"]


def test_mirror_rejects_a_direction_that_does_not_match_the_axis():
    c = Canvas(4, PALETTE)
    for axis, direction in [("x", "top_to_bottom"), ("y", "left_to_right"), ("z", "left_to_right")]:
        try:
            c.mirror(axis, direction)
        except ValueError:
            continue
        raise AssertionError(f"mirror({axis!r}, {direction!r}) should raise")


# ── to_visual_grid ──

def test_visual_grid_small_canvas_uses_hex_ruler_and_letters_from_ten():
    c = Canvas(4, [f"#0000{i:02X}" for i in range(11)])
    c.set_pixel(0, 0, 0)
    c.set_pixel(1, 1, 10)
    assert c.to_visual_grid().split("\n") == [
        "   0123",
        " 0 0...",
        " 1 .A..",
        " 2 ....",
        " 3 ....",
    ]


def test_visual_grid_large_canvas_uses_two_line_decimal_ruler():
    lines = Canvas(32, PALETTE).to_visual_grid().split("\n")
    assert lines[0] == "   " + " " * 10 + "1" * 10 + "2" * 10 + "33"
    assert lines[1] == "   " + "0123456789" * 3 + "01"
    assert lines[2] == "  0" + "." * 32
    assert len(lines) == 2 + 32


# ── view text (grid + legend + filled) ──

def test_view_text_lists_used_colours_most_used_first_then_the_fill_count():
    c = Canvas(4, [f"#0000{i:02X}" for i in range(11)])
    c.set_pixel(0, 0, 0)
    c.set_pixel(1, 1, 10)
    c.set_pixel(2, 1, 10)
    assert c.to_view_text().split("\n") == [
        "   0123",
        " 0 0...",
        " 1 .AA.",
        " 2 ....",
        " 3 ....",
        "",
        "Legend (char = index #hex: px):",
        "A = 10 #00000A: 2px",
        "0 = 0 #000000: 1px",
        "Filled: 3/16 px",
    ]


def test_view_text_of_an_empty_canvas_says_so():
    lines = Canvas(4, PALETTE).to_view_text().split("\n")
    assert lines[-2:] == ["Legend: no colours used yet", "Filled: 0/16 px"]


# ── view image ──

def view_png(canvas: Canvas) -> Image.Image:
    import io
    return Image.open(io.BytesIO(canvas.to_view_png())).convert("RGBA")


def test_view_png_is_about_512px_plus_an_18px_label_margin_at_every_size():
    for size in (8, 16, 32, 64):
        assert view_png(Canvas(size, PALETTE)).size == (18 + 512, 18 + 512)


def test_view_png_shows_palette_colours_and_a_non_grey_checker_behind_transparency():
    c = Canvas(16, PALETTE)
    c.set_pixel(5, 6, 0)
    img = view_png(c)
    cell, margin = 32, 18

    def centre(x, y):
        return img.getpixel((margin + x * cell + cell // 2, margin + y * cell + cell // 2))

    assert centre(5, 6) == RED
    checker_a, checker_b = centre(0, 0), centre(1, 0)
    assert checker_a != checker_b
    for r, g, b, _ in (checker_a, checker_b):
        assert not (r == g == b), "a grey checker hides grey palette colours"


def test_view_png_gridlines_every_4px_use_a_contrasting_colour():
    img = view_png(Canvas(16, PALETTE))
    cell, margin = 32, 18
    y = margin + cell // 2
    minor = img.getpixel((margin + 1 * cell, y))
    major = img.getpixel((margin + 4 * cell, y))
    assert minor != major
    assert img.getpixel((margin + 2 * cell, y)) == minor
    assert img.getpixel((margin + 8 * cell, y)) == major


# ── generate_tileset ──

def solid_tile(size=16, rgba=(100, 150, 200, 255)) -> Image.Image:
    return Image.new("RGBA", (size, size), rgba)


def test_tileset_has_sixteen_variants_at_the_base_size():
    variants = generate_tileset(solid_tile())
    assert sorted(variants) == list(range(16))
    assert all(v.size == (16, 16) for v in variants.values())


def test_tileset_mask_15_is_the_untouched_base_tile():
    base = solid_tile()
    assert generate_tileset(base)[15].tobytes() == base.tobytes()


def test_tileset_mask_0_rounds_corners_and_outlines_edges_but_keeps_the_centre():
    base = solid_tile()
    isolated = generate_tileset(base)[0]
    assert isolated.getpixel((0, 0))[3] == 0
    assert isolated.getpixel((8, 8)) == (100, 150, 200, 255)
    assert isolated.getpixel((0, 8)) != (100, 150, 200, 255)
