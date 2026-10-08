"""
PROTOTYPE, throwaway. Paints the same sprites under each view_canvas format with headless Claude Code,
then builds a contact sheet + stats table. See prototype_view_canvas_mcp.py.

  venv/Scripts/python.exe prototype_view_canvas_trials.py [--model sonnet] [--only text,both512] [--jobs 4]

Output: prototype_view_canvas_runs/<condition>__<sprite>/{sprite.png, result.json, tools.jsonl}
        prototype_view_canvas_runs/contact_sheet.png, prototype_view_canvas_runs/stats.md
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
OUT = os.path.join(HERE, "prototype_view_canvas_runs")

PICO8 = ["#000000", "#1d2b53", "#7e2553", "#008751", "#ab5236", "#5f574f", "#c2c3c7", "#fff1e8",
         "#ff004d", "#ffa300", "#ffec27", "#00e436", "#29adff", "#83769c", "#ff77a8", "#ffccaa"]

SPRITES = {
    "sword": (16, "an iron sword with a brown leather-wrapped hilt and gold crossguard, pointing up-right",
              "This is an ITEM ICON. Draw the object on a transparent (-1) background. Compact, chunky, recognizable, "
              "with transparent padding at the edges."),
    "cobble": (16, "a grey cobblestone floor tile",
               "This is a BLOCK TILE. Fill EVERY pixel, no transparency. It will be tiled next to copies of itself, "
               "so it must tile seamlessly."),
    "goblin": (32, "a green goblin warrior, front view, holding a small wooden club",
               "This is a CHARACTER SPRITE on a transparent (-1) background. Clear silhouette, transparent padding at the edges."),
}

CONDITIONS = {
    "text":         {"TEXEL_VIEW": "text"},
    "image512":     {"TEXEL_VIEW": "image", "TEXEL_VIEW_PX": "512", "TEXEL_VIEW_GRID": "1"},
    "both512":      {"TEXEL_VIEW": "both", "TEXEL_VIEW_PX": "512", "TEXEL_VIEW_GRID": "1"},
    "both256plain": {"TEXEL_VIEW": "both", "TEXEL_VIEW_PX": "256", "TEXEL_VIEW_GRID": "0"},
}


def prompt_for(sprite: str, png_path: str) -> str:
    size, subject, hint = SPRITES[sprite]
    return (f"Using only the texel MCP tools, paint {subject} as {size}x{size} pixel art.\n"
            f"Palette (pass exactly this to create_canvas): {json.dumps(PICO8)}\n{hint}\n"
            f"Check your work with view_canvas as you go and fix anything that looks wrong. "
            f"When you are happy with it, export_png to {png_path} at scale 1, then reply with one line: DONE.")


def run(cond: str, sprite: str, model: str | None, budget: str) -> dict:
    d = os.path.join(OUT, f"{cond}__{sprite}")
    os.makedirs(d, exist_ok=True)
    png = os.path.join(d, "sprite.png")
    if os.path.exists(png) and os.path.exists(os.path.join(d, "result.json")):
        print(f"[{cond}/{sprite}] already done, skipping", flush=True)
        return {}
    for f in ("sprite.png", "tools.jsonl", "result.json"):
        p = os.path.join(d, f)
        if os.path.exists(p):
            os.remove(p)
    cfg = {"mcpServers": {"texel": {
        "type": "stdio", "command": PY, "args": [os.path.join(HERE, "prototype_view_canvas_mcp.py")],
        "env": {**CONDITIONS[cond.split("@")[0]], "TEXEL_LOG": os.path.join(d, "tools.jsonl")}}}}
    cfg_path = os.path.join(d, "mcp.json")
    with open(cfg_path, "w") as f:
        json.dump(cfg, f)
    cmd = [shutil.which("claude"), "-p", prompt_for(sprite, png), "--mcp-config", cfg_path, "--strict-mcp-config",
           "--tools", "", "--allowedTools", "mcp__texel__*", "--setting-sources", "",
           "--no-session-persistence", "--output-format", "json", "--max-budget-usd", budget]
    if model:
        cmd += ["--model", model]
    p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, encoding="utf-8")
    try:
        res = json.loads(p.stdout)
    except json.JSONDecodeError:
        res = {"error": p.stdout[-2000:] + p.stderr[-2000:]}
    with open(os.path.join(d, "result.json"), "w") as f:
        json.dump(res, f, indent=2)
    print(f"[{cond}/{sprite}] done: png={os.path.exists(png)} cost={res.get('total_cost_usd')}", flush=True)
    return res


def stats(cond: str, sprite: str) -> dict:
    d = os.path.join(OUT, f"{cond}__{sprite}")
    try:
        res = json.load(open(os.path.join(d, "result.json")))
    except FileNotFoundError:
        res = {}
    calls = []
    if os.path.exists(os.path.join(d, "tools.jsonl")):
        calls = [json.loads(l) for l in open(os.path.join(d, "tools.jsonl"))]
    u = res.get("usage", {})
    return {
        "cost": res.get("total_cost_usd"), "turns": res.get("num_turns"),
        "secs": round(res.get("duration_ms", 0) / 1000),
        "in_tok": (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)),
        "out_tok": u.get("output_tokens"),
        "draws": sum(c["tool"] == "draw" for c in calls),
        "draw_errors": sum(c["tool"] == "draw" and "error" in c for c in calls),
        "views": sum(c["tool"] == "view_canvas" for c in calls),
        "undos": sum(c["tool"] == "undo" for c in calls),
        "png": os.path.exists(os.path.join(d, "sprite.png")),
    }


def contact_sheet(conds, sprites):
    cell, pad, lab = 192, 12, 22
    W = pad + len(sprites) * (cell * 2 + pad)  # each sprite: single + 2x2 tiled preview
    H = lab + len(conds) * (cell + lab + pad)
    sheet = Image.new("RGBA", (W + 130, H), (24, 24, 32, 255))
    d = ImageDraw.Draw(sheet)
    for j, s in enumerate(sprites):
        d.text((110 + pad + j * (cell * 2 + pad), 4), f"{s} (left: 1x, right: 2x2 tiled)", fill=(220, 220, 220, 255))
    for i, c in enumerate(conds):
        y = lab + i * (cell + lab + pad)
        d.text((6, y + cell // 2), c, fill=(255, 255, 255, 255))
        for j, s in enumerate(sprites):
            x = 110 + pad + j * (cell * 2 + pad)
            st = stats(c, s)
            path = os.path.join(OUT, f"{c}__{s}", "sprite.png")
            for k in range(2):
                bg = Image.new("RGBA", (cell, cell))
                bd = ImageDraw.Draw(bg)
                for yy in range(0, cell, 12):
                    for xx in range(0, cell, 12):
                        bd.rectangle([xx, yy, xx + 11, yy + 11],
                                     fill=(70, 70, 90, 255) if (xx + yy) // 12 % 2 else (50, 50, 66, 255))
                if os.path.exists(path):
                    im = Image.open(path).convert("RGBA")
                    if k == 1:
                        t = Image.new("RGBA", (im.width * 2, im.height * 2))
                        for a in range(2):
                            for b in range(2):
                                t.paste(im, (a * im.width, b * im.height))
                        im = t
                    bg.alpha_composite(im.resize((cell, cell), Image.NEAREST))
                sheet.paste(bg, (x + k * cell, y))
            cost = f"${st['cost']:.2f}" if st["cost"] is not None else "-"
            d.text((x, y + cell + 3), f"{cost} in={st['in_tok']//1000}k views={st['views']} draws={st['draws']}",
                   fill=(200, 200, 200, 255))
    p = os.path.join(OUT, "contact_sheet.png")
    sheet.save(p)
    return p


def table(conds, sprites):
    rows = ["| condition | sprite | cost $ | input tok | output tok | turns | draws (err) | views | undos | secs |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for c in conds:
        for s in sprites:
            st = stats(c, s)
            cost = f"{st['cost']:.2f}" if st["cost"] is not None else "-"
            rows.append(f"| {c} | {s} | {cost} | {st['in_tok']} | {st['out_tok']} | {st['turns']} | "
                        f"{st['draws']} ({st['draw_errors']}) | {st['views']} | {st['undos']} | {st['secs']} |")
    md = "\n".join(rows)
    with open(os.path.join(OUT, "stats.md"), "w") as f:
        f.write(md + "\n")
    return md


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--only", help="comma-separated conditions")
    ap.add_argument("--sprites", help="comma-separated sprites")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--budget", default="3")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--reps", type=int, default=1)
    a = ap.parse_args()
    conds = a.only.split(",") if a.only else list(CONDITIONS)
    sprites = a.sprites.split(",") if a.sprites else list(SPRITES)
    conds = [f"{c}@r{r}" for c in conds for r in range(1, a.reps + 1)]
    if not a.report_only:
        with ThreadPoolExecutor(a.jobs) as ex:
            list(ex.map(lambda cs: run(*cs, a.model, a.budget), [(c, s) for c in conds for s in sprites]))
    print(table(conds, sprites))
    print(contact_sheet(conds, sprites))
