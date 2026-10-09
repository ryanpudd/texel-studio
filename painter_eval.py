#!/usr/bin/env python3
"""
Standing eval for the texel-painter agent: paints three fixed sprites through the agent with headless
Claude Code, then writes a contact sheet and a stats table. Use it before and after changing the agent
prompt or tool descriptions; a change only lands if the sprites don't get worse.

  venv/Scripts/python.exe painter_eval.py --label baseline [--reps 2] [--sprites sword,cobble] [--jobs 3]
  venv/Scripts/python.exe painter_eval.py --label baseline --report-only

Each run renders agents/texel-painter.md from this checkout into this repo's .claude/agents/ (the repo
must be a folder you've trusted in Claude Code, or the agent's MCP server is skipped) and runs
`claude -p --agent texel-painter` from the repo root.

Output: output/painter_eval/<label>/<sprite>@r<n>/{sprite.png, stream.jsonl}, plus contact_sheet.png and stats.md.
"""

import argparse
import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

from install_painter_agent import REPO_ROOT, install

OUT = REPO_ROOT / "output" / "painter_eval"

PALETTE = [  # PICO-8, with role names so the painter can reason about roles
    ("000000", "outline"), ("1D2B53", "navy"), ("7E2553", "plum"), ("008751", "leaf"),
    ("AB5236", "wood"), ("5F574F", "stone-dark"), ("C2C3C7", "stone"), ("FFF1E8", "cream"),
    ("FF004D", "red"), ("FFA300", "orange"), ("FFEC27", "gold"), ("00E436", "grass"),
    ("29ADFF", "sky"), ("83769C", "lilac"), ("FF77A8", "pink"), ("FFCCAA", "skin"),
]

SPRITES = {  # the fixed eval set from #7: name -> (size, sprite type, brief)
    "sword": (16, "item icon",
              "an iron sword with a brown leather-wrapped hilt and gold crossguard, pointing up-right"),
    "cobble": (16, "block/tileable", "a grey cobblestone floor tile"),
    "goblin": (32, "character", "a green goblin warrior, front view, holding a small wooden club"),
}

AGENT_TOOLS = ["mcp__texel__*", "Read", "Glob", "Write"]


def write_palette(run_dir: Path) -> Path:
    path = run_dir / "eval.gpl"
    rows = [f"{int(h[0:2], 16):3d} {int(h[2:4], 16):3d} {int(h[4:6], 16):3d}\t{role}" for h, role in PALETTE]
    path.write_text("GIMP Palette\nName: Eval (PICO-8)\n#\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def brief_for(sprite: str, palette: Path, png: Path) -> str:
    size, kind, subject = SPRITES[sprite]
    return (f"Brief: {subject}. Size {size}. Sprite type: {kind}. palette_file: {palette.as_posix()}. "
            f"Output path: {png.as_posix()}. No tileset.")


def run(run_dir: Path, sprite: str, budget: str) -> None:
    png = run_dir / "sprite.png"
    if png.exists() and (run_dir / "stream.jsonl").exists():
        print(f"[{run_dir.name}] already done, skipping", flush=True)
        return
    run_dir.mkdir(parents=True, exist_ok=True)
    for f in ("sprite.png", "stream.jsonl"):
        (run_dir / f).unlink(missing_ok=True)
    palette = write_palette(run_dir)
    # The prompt must come before --allowedTools, which is variadic.
    cmd = [shutil.which("claude"), "-p", brief_for(sprite, palette, png), "--agent", "texel-painter",
           "--allowedTools", *AGENT_TOOLS, "--output-format", "stream-json", "--verbose",
           "--no-session-persistence", "--max-budget-usd", budget]
    with open(run_dir / "stream.jsonl", "w", encoding="utf-8") as out:
        subprocess.run(cmd, cwd=REPO_ROOT, stdout=out, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, encoding="utf-8")
    st = stats(run_dir)
    print(f"[{run_dir.name}] done: png={st['png']} cost={st['cost']} views={st['views']}", flush=True)


def stats(run_dir: Path) -> dict:
    """Summarise one run from its stream-json transcript."""
    calls: list[str] = []
    errors = 0
    gated = 0
    result: dict = {}
    pending: dict[str, str] = {}
    stream = run_dir / "stream.jsonl"
    for line in (stream.read_text(encoding="utf-8").splitlines() if stream.exists() else []):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "assistant":
            for b in e["message"]["content"]:
                if b.get("type") == "tool_use":
                    name = b["name"].removeprefix("mcp__texel__")
                    calls.append(name)
                    pending[b["id"]] = name
        elif e.get("type") == "user":
            for b in e["message"].get("content", []):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    gated += "Not exported:" in json.dumps(b.get("content"))
                    if b.get("is_error"):
                        errors += pending.get(b.get("tool_use_id"), "") == "draw"
        elif e.get("type") == "result":
            result = e
    u = result.get("usage", {})
    return {
        "cost": result.get("total_cost_usd"), "turns": result.get("num_turns"),
        "secs": round(result.get("duration_ms", 0) / 1000),
        "in_tok": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0),
        "out_tok": u.get("output_tokens"),
        "draws": calls.count("draw"), "draw_errors": errors,
        "views": calls.count("view_canvas"), "undos": calls.count("undo"), "gated": gated,
        "png": (run_dir / "sprite.png").exists(),
        "report": (result.get("result") or "").strip(),
    }


def contact_sheet(label_dir: Path, runs: list[str], sprites: list[str]) -> Path:
    cell, pad, lab = 192, 12, 22
    width = 110 + pad + len(sprites) * (cell * 2 + pad)
    height = lab + len(runs) * (cell + lab + pad)
    sheet = Image.new("RGBA", (width, height), (24, 24, 32, 255))
    d = ImageDraw.Draw(sheet)
    for j, s in enumerate(sprites):
        d.text((110 + pad + j * (cell * 2 + pad), 4), f"{s} (left: 1x, right: 2x2 tiled)", fill=(220, 220, 220, 255))
    for i, rep in enumerate(runs):
        y = lab + i * (cell + lab + pad)
        d.text((6, y + cell // 2), rep, fill=(255, 255, 255, 255))
        for j, s in enumerate(sprites):
            x = 110 + pad + j * (cell * 2 + pad)
            run_dir = label_dir / f"{s}@{rep}"
            st = stats(run_dir)
            for k in range(2):
                bg = Image.new("RGBA", (cell, cell))
                bd = ImageDraw.Draw(bg)
                for yy in range(0, cell, 12):
                    for xx in range(0, cell, 12):
                        bd.rectangle([xx, yy, xx + 11, yy + 11],
                                     fill=(70, 70, 90, 255) if (xx + yy) // 12 % 2 else (50, 50, 66, 255))
                if st["png"]:
                    im = Image.open(run_dir / "sprite.png").convert("RGBA")
                    if k == 1:
                        tiled = Image.new("RGBA", (im.width * 2, im.height * 2))
                        for a in range(2):
                            for b in range(2):
                                tiled.paste(im, (a * im.width, b * im.height))
                        im = tiled
                    bg.alpha_composite(im.resize((cell, cell), Image.NEAREST))
                sheet.paste(bg, (x + k * cell, y))
            cost = f"${st['cost']:.2f}" if st["cost"] is not None else "-"
            d.text((x, y + cell + 3), f"{cost} views={st['views']} draws={st['draws']} undos={st['undos']}",
                   fill=(200, 200, 200, 255))
    path = label_dir / "contact_sheet.png"
    sheet.save(path)
    return path


def table(label_dir: Path, runs: list[str], sprites: list[str]) -> str:
    rows = ["| run | sprite | cost $ | input tok | output tok | turns | draws (err) | views | undos | exports gated | secs | png |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    reports = []
    for rep in runs:
        for s in sprites:
            st = stats(label_dir / f"{s}@{rep}")
            cost = f"{st['cost']:.2f}" if st["cost"] is not None else "-"
            rows.append(f"| {rep} | {s} | {cost} | {st['in_tok']} | {st['out_tok']} | {st['turns']} | "
                        f"{st['draws']} ({st['draw_errors']}) | {st['views']} | {st['undos']} | {st['gated']} | {st['secs']} | "
                        f"{'yes' if st['png'] else 'NO'} |")
            reports.append(f"### {s}@{rep}\n\n{st['report'] or '(no report)'}\n")
    md = "\n".join(rows) + "\n\n## Painter reports\n\n" + "\n".join(reports)
    (label_dir / "stats.md").write_text(md, encoding="utf-8")
    return md


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True, help="name for this eval run, e.g. baseline or slim-draw-desc")
    ap.add_argument("--sprites", help=f"comma-separated subset of {','.join(SPRITES)}")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--budget", default="1.5", help="max USD per sprite run")
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args()

    sprites = a.sprites.split(",") if a.sprites else list(SPRITES)
    runs = [f"r{n}" for n in range(1, a.reps + 1)]
    label_dir = OUT / a.label
    if not a.report_only:
        install(project=REPO_ROOT, force=True)  # always evaluate this checkout's agent prompt
        started = time.time()
        with ThreadPoolExecutor(a.jobs) as ex:
            list(ex.map(lambda rs: run(label_dir / f"{rs[1]}@{rs[0]}", rs[1], a.budget),
                        [(r, s) for r in runs for s in sprites]))
        print(f"finished in {round(time.time() - started)}s")
    print(table(label_dir, runs, sprites))
    print(contact_sheet(label_dir, runs, sprites))


if __name__ == "__main__":
    main()
