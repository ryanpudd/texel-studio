#!/usr/bin/env python3
"""
Install the texel-painter Claude Code agent.

Renders agents/texel-painter.md with this checkout's venv Python and mcp_server.py, then writes it to
~/.claude/agents/ (all your projects) or <project>/.claude/agents/ (one game project).

  venv/Scripts/python.exe install_painter_agent.py                  # user-level
  venv/Scripts/python.exe install_painter_agent.py --project DIR    # one project
  ... --force                                                       # replace an existing install

Run it with the repo venv's Python: that's the interpreter the agent will launch the server with.
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
TEMPLATE = REPO_ROOT / "agents" / "texel-painter.md"
AGENT_FILE = "texel-painter.md"


def venv_python() -> Path:
    """The interpreter of the running environment (on Windows, sys.executable may be the base install)."""
    exe = Path(sys.prefix) / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return exe if exe.exists() else Path(sys.executable)


def _yaml_str(path: Path) -> str:
    # A JSON string is a valid double-quoted YAML scalar; dump it unquoted since the template quotes it.
    return json.dumps(path.as_posix())[1:-1]


def render_agent() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    return (text.replace("{{PYTHON}}", _yaml_str(venv_python()))
                .replace("{{SERVER}}", _yaml_str(REPO_ROOT / "mcp_server.py")))


def install(project: Path | None = None, force: bool = False) -> Path:
    base = Path(project) if project else Path.home()
    target = base / ".claude" / "agents" / AGENT_FILE
    if target.exists() and not force:
        raise FileExistsError(f"{target} already exists; rerun with --force to replace it")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_agent(), encoding="utf-8")
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, help="install into this project's .claude/agents/ instead of ~/.claude/agents/")
    ap.add_argument("--force", action="store_true", help="replace an existing texel-painter.md")
    a = ap.parse_args()
    try:
        target = install(a.project, a.force)
    except FileExistsError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Installed {target}")
    if a.project:
        print(f"Open {a.project} in Claude Code once and accept the trust dialog: until the folder is trusted,")
        print("Claude Code skips the agent's MCP server and the painter has no canvas tools.")
    print("Start an art session with:  claude --agent texel-painter")
    print("Resume one with:            claude --agent texel-painter --resume <id>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
