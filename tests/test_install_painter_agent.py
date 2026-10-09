"""The texel-painter agent installer: renders the template with this repo's paths."""

import subprocess
import sys
from pathlib import Path

import yaml

from install_painter_agent import install, render_agent

REPO_ROOT = Path(__file__).resolve().parent.parent


def frontmatter(agent_md: str) -> tuple[dict, str]:
    assert agent_md.startswith("---\n")
    head, body = agent_md[4:].split("\n---\n", 1)
    return yaml.safe_load(head), body


def test_rendered_agent_declares_the_texel_server_inline_with_absolute_paths():
    meta, body = frontmatter(render_agent())
    assert meta["name"] == "texel-painter"
    assert meta["model"] == "sonnet"
    assert meta["tools"] == "Read, Glob, Write, mcp__texel__*"
    [server] = meta["mcpServers"]
    texel = server["texel"]
    assert texel["type"] == "stdio"
    python, script = Path(texel["command"]), Path(texel["args"][0])
    assert python.is_absolute() and python.exists()
    assert script == (REPO_ROOT / "mcp_server.py") and script.exists()
    assert "{{" not in render_agent()


def test_rendered_agent_gates_exports_with_the_painter_hook():
    meta, _ = frontmatter(render_agent())
    texel = meta["mcpServers"][0]["texel"]
    hooks = meta["hooks"]

    [post] = hooks["PostToolUse"]
    assert post["matcher"] == "mcp__texel__.*"
    [pre] = hooks["PreToolUse"]
    assert pre["matcher"] == "mcp__texel__export_.*"
    for entry, mode in ((post, "post"), (pre, "pre")):
        [hook] = entry["hooks"]
        assert hook["type"] == "command"
        assert hook["command"] == texel["command"]  # the same venv Python as the server
        script, arg = hook["args"]
        assert Path(script) == REPO_ROOT / "painter_hook.py" and arg == mode


def test_description_states_the_delegation_handoff():
    meta, _ = frontmatter(render_agent())
    d = meta["description"]
    for word in ("brief", "size", "palette_file", "output path", "from_png", "tileset", "self-critique"):
        assert word in d, word


def test_prompt_carries_the_craft_not_the_tool_contract():
    _, body = frontmatter(render_agent())
    for must in ("view_canvas", "critique", "tile", "palette_file", ".gpl", "export"):
        assert must in body, must
    assert "style_prompt" not in body


def test_prompt_makes_edits_incremental_and_tracks_every_brief_item():
    _, body = frontmatter(render_agent())
    # Edits from from_png keep what's there: inventory first, targeted ops, never a repaint.
    assert "## Editing an existing sprite" in body
    for must in ("inventory", "never `clear`", "only the regions"):
        assert must in body, must
    # A checklist of the brief and reference, re-checked every round and reported at the end.
    assert "## Checklist" in body
    assert "Checklist:" in body.split("## Delegated report")[1]


def test_install_writes_to_a_project_agents_dir_and_refuses_to_clobber(tmp_path):
    written = install(project=tmp_path)
    assert written == tmp_path / ".claude" / "agents" / "texel-painter.md"
    assert written.read_text(encoding="utf-8") == render_agent()

    written.write_text("my edits", encoding="utf-8")
    try:
        install(project=tmp_path)
    except FileExistsError as e:
        assert "--force" in str(e)
    else:
        raise AssertionError("install should refuse to overwrite")
    assert written.read_text(encoding="utf-8") == "my edits"

    install(project=tmp_path, force=True)
    assert written.read_text(encoding="utf-8") == render_agent()


def test_cli_installs_into_a_project(tmp_path):
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "install_painter_agent.py"), "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".claude" / "agents" / "texel-painter.md").exists()
    assert "claude --agent texel-painter" in result.stdout
    assert "trust" in result.stdout  # project agents' MCP servers are skipped in untrusted folders
