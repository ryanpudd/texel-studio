# Texel Studio

An AI pixel-art tool where a language model paints sprites by calling deterministic canvas tools against a fixed palette, rather than generating pixels by diffusion.

## Language

**Painter**:
The language model that decides which canvas tools to call to produce a sprite. Today it is Texel's internal agent LLM; when Texel runs as an MCP server, the painter is the MCP client's model (e.g. Claude Code).
_Avoid_: Agent (ambiguous with the agent loop), artist

**Painter agent**:
The Claude Code agent definition that makes Claude Code the painter. It carries the painting craft and workflow, and it is the only place Texel's canvas tools are available.
_Avoid_: Painting skill, art agent

**Art session**:
A Claude Code session started as the painter agent, where the user and the painter work on sprites together over many turns. The alternative is delegation, where another session runs the painter agent as a subagent for one sprite job.
_Avoid_: Agent mode, art mode

**Canvas tools**:
The fixed set of drawing, texture, and inspection operations a painter may call on a canvas.
_Avoid_: Brushes, actions

**Op**:
A single drawing primitive (rect, line, flood fill, mirror, …) inside one call to the `draw` canvas tool. A call's ops apply in order, and the call succeeds or fails as a whole.
_Avoid_: Command, step, action

**Palette**:
The ordered list of colours a canvas may use, fixed when the canvas is created. Pixels store an index into it, so a colour's position is part of the painter's contract.
_Avoid_: Colours, swatches

**Autotile tileset**:
The 16 variants of one base tile, one per combination of exposed edges (top, right, bottom, left), so a game can pick the right tile from its neighbours. It is derived from the canvas at export time, and its edge shading may use colours outside the palette.
_Avoid_: Tilemap, atlas (an atlas is one way to pack it, not the thing itself)

**Reference**:
An image the painter tries to match in pixel art. It informs the painter; it is never drawn onto the canvas.
_Avoid_: Concept art (that is one way a reference is produced, not the thing itself)
