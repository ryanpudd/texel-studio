"""painter_hook.py: the texel-painter's export gate, driven through its stdin/stdout hook contract."""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "painter_hook.py"


class Session:
    """One painter session (or subagent) feeding hook events in order."""

    def __init__(self, tmp_path, session_id="s1", agent_id=None):
        self.base = {"session_id": session_id, "scratchpad_dir": str(tmp_path), "cwd": str(tmp_path)}
        if agent_id:
            self.base |= {"agent_id": agent_id, "agent_type": "texel-painter"}

    def _run(self, mode, event):
        return subprocess.run([sys.executable, str(HOOK), mode], input=json.dumps(self.base | event),
                              capture_output=True, text=True)

    def did(self, tool, response="ok", **tool_input):
        result = self._run("post", {"hook_event_name": "PostToolUse", "tool_name": f"mcp__texel__{tool}",
                                    "tool_input": tool_input, "tool_response": response})
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def created(self, canvas_id, size):
        self.did("create_canvas", response=[{"type": "text", "text": f"Created canvas {canvas_id}: {size}x{size}, transparent."}],
                 size=size, palette=["#000000"])

    def export(self, canvas_id="c1", tool="export_png"):
        """Returns None if the export may go ahead, else the deny reason shown to the painter."""
        result = self._run("pre", {"hook_event_name": "PreToolUse", "tool_name": f"mcp__texel__{tool}",
                                   "tool_input": {"canvas_id": canvas_id, "path": "C:/x.png"}})
        assert result.returncode == 0, result.stderr
        if not result.stdout.strip():
            return None
        out = json.loads(result.stdout)["hookSpecificOutput"]
        assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "deny"
        return out["permissionDecisionReason"]


def rounds(s, n, canvas_id="c1"):
    for _ in range(n):
        s.did("draw", canvas_id=canvas_id, ops=[])
        s.did("view_canvas", canvas_id=canvas_id)


def test_a_16px_sprite_needs_two_critique_views(tmp_path):
    s = Session(tmp_path)
    s.created("c1", 16)
    rounds(s, 1)
    reason = s.export()
    assert reason and "1 of 2" in reason and "view_canvas" in reason
    rounds(s, 1)
    assert s.export() is None


def test_a_64px_sprite_needs_three(tmp_path):
    s = Session(tmp_path)
    s.created("hero", 64)
    rounds(s, 2, "hero")
    assert "2 of 3" in s.export("hero")
    rounds(s, 1, "hero")
    assert s.export("hero") is None


def test_drawing_after_the_last_view_blocks_export_until_viewed(tmp_path):
    s = Session(tmp_path)
    s.created("c1", 16)
    rounds(s, 3)
    s.did("draw", canvas_id="c1", ops=[])
    reason = s.export("c1", tool="export_tileset")
    assert reason and "since your last view_canvas" in reason
    s.did("view_canvas", canvas_id="c1")
    assert s.export() is None


def test_undo_and_clear_count_as_changes(tmp_path):
    s = Session(tmp_path)
    s.created("c1", 8)
    rounds(s, 2)
    s.did("undo", canvas_id="c1")
    assert s.export() is not None
    s.did("view_canvas", canvas_id="c1")
    s.did("clear", canvas_id="c1")
    assert s.export() is not None


def test_views_before_the_first_draw_are_not_critique_rounds(tmp_path):
    s = Session(tmp_path)
    s.created("c1", 16)
    s.did("view_canvas", canvas_id="c1")  # e.g. looking at a from_png canvas before editing
    s.did("view_canvas", canvas_id="c1")
    rounds(s, 1)
    assert "1 of 2" in s.export()


def test_state_is_kept_per_agent_and_per_session(tmp_path):
    done = Session(tmp_path, agent_id="painter-a")
    done.created("c1", 16)
    rounds(done, 2)
    fresh = Session(tmp_path, agent_id="painter-b")
    fresh.created("c1", 16)
    assert done.export() is None
    assert fresh.export() is not None
    assert Session(tmp_path, session_id="s2").export() is None  # an unknown canvas isn't gated


def test_unknown_canvas_or_size_is_not_gated(tmp_path):
    s = Session(tmp_path)
    assert s.export("never-seen") is None


def test_hook_never_crashes_on_odd_input(tmp_path):
    for payload in ("", "not json", json.dumps({"tool_name": "mcp__texel__draw"})):
        for mode in ("pre", "post"):
            r = subprocess.run([sys.executable, str(HOOK), mode], input=payload, capture_output=True, text=True)
            assert r.returncode == 0, (mode, payload, r.stderr)
