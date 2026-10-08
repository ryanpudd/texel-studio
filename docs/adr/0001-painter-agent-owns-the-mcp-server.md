---
status: proposed
---

# The painter agent owns Texel's MCP server; no skill, no plugin

Texel's canvas tools are only needed when someone is making pixel art, so we keep them out of ordinary Claude Code sessions. The `texel-painter` agent definition declares the MCP server inline in its `mcpServers` frontmatter, and its prompt carries the painting know-how (workflow, iteration loop, sprite-type craft, palette conventions). The same agent is run either as an **art session** (`claude --agent texel-painter`, for multi-turn critique with the user) or delegated as a subagent from a game-dev session. Contract facts about each tool live in its tool description; server instructions stay minimal.

## Considered Options

- **A companion skill plus a globally configured server.** Rejected: every session would pay for the server instructions and the deferred tool names, and the main session could paint inline, filling its context with `draw`/`view_canvas` image churn.
- **A Claude Code plugin that bundles the server, agent and skill.** Rejected for now: plugin-shipped agents can't declare `mcpServers`, so the server would register at plugin level and be visible in every session, which defeats the isolation. Easier install is tracked in the "Installable packaging" fog on the map.
- **Subagent only.** Rejected: the painter can't talk to the user mid-run, so critique and retries would be lost. The art session restores them.

## Consequences

- Installing means copying the agent file into `.claude/agents/` or `~/.claude/agents/`, with the server command pointing at the repo venv.
- You choose the art session at launch; you can't switch into it mid-conversation.
- Delegated runs are stateless (every run ends with an export, and edits start from `create_canvas(from_png=…)`), so they don't depend on the MCP server process surviving between subagent runs.
- Rests on documented Claude Code behaviour that hasn't been tested yet; see the ticket *Verify agent-scoped MCP behaviour in Claude Code* (ryanpudd/texel-studio#9). Mark this ADR accepted once that passes.

Decided in *Painting skill vs tool descriptions split* (ryanpudd/texel-studio#8).
