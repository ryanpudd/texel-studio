#!/usr/bin/env python3
"""
Export gate for the texel-painter agent, run as Claude Code hooks from the agent's frontmatter.

  painter_hook.py post   PostToolUse on mcp__texel__.*: records canvas sizes, changes and views.
  painter_hook.py pre    PreToolUse on mcp__texel__export_*: denies the export, with a reason the painter
                         sees, if the canvas changed since its last view_canvas or it has had fewer
                         critique views than its size needs.

A critique view is a view_canvas after the canvas's first change. State lives in a small JSON file per
session and agent, so parallel painter subagents don't share counts. The hook never fails a tool call
because of its own errors: anything unexpected means "no decision".
"""

import json
import re
import sys
import tempfile
from pathlib import Path

PREFIX = "mcp__texel__"
CHANGES = {"draw", "undo", "clear"}
CREATED = re.compile(r"Created canvas (\S+): (\d+)x\d+")


def min_rounds(size: int) -> int:
    return 2 if size <= 16 else 3


def state_file(event: dict) -> Path:
    base = Path(event.get("scratchpad_dir") or tempfile.gettempdir()) / "texel-painter-hook"
    who = event.get("agent_id") or "main"
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{event.get('session_id', 'unknown')}-{who}.json")
    return base / name


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(event: dict) -> None:
    tool = event.get("tool_name", "").removeprefix(PREFIX)
    args = event.get("tool_input") or {}
    path = state_file(event)
    canvases = load(path)

    if tool == "create_canvas":
        m = CREATED.search(json.dumps(event.get("tool_response")))
        if not m:
            return
        canvases[m.group(1)] = {"size": int(m.group(2)), "changed": False, "dirty": False, "views": 0}
    elif tool in CHANGES or tool == "view_canvas":
        c = canvases.get(args.get("canvas_id"))
        if c is None:
            return
        if tool in CHANGES:
            c["changed"] = c["dirty"] = True
        else:
            c["dirty"] = False
            c["views"] += c["changed"]
    else:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(canvases), encoding="utf-8")


def decide(event: dict) -> str | None:
    """The reason to deny this export, or None to let it through."""
    canvas_id = (event.get("tool_input") or {}).get("canvas_id")
    c = load(state_file(event)).get(canvas_id)
    if c is None:
        return None
    if c["dirty"]:
        return (f"Not exported: you've changed {canvas_id} since your last view_canvas. Call view_canvas, "
                "update your checklist against the brief and reference, fix anything wrong, then export.")
    need = min_rounds(c["size"])
    if c["views"] < need:
        return (f"Not exported: {canvas_id} has had {c['views']} of {need} critique rounds this "
                f"{c['size']}px sprite needs. Call view_canvas, compare it item by item with your "
                "checklist, the brief and the reference, fix what's weakest, and view again before exporting.")
    return None


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        event = json.loads(sys.stdin.read())
        if not isinstance(event, dict):
            return 0
        if mode == "post":
            record(event)
        elif mode == "pre":
            reason = decide(event)
            if reason:
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }}))
    except Exception as e:  # never break the painter's tool call because the gate itself failed
        print(f"painter_hook: ignored error: {e!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
