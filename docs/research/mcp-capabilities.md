# MCP SDK and Claude Code capabilities (research for #2)

Researched 2026-10-08 for the headless stdio MCP server plan (#1). Sources are primary: the MCP spec, the official `mcp` Python SDK docs and PyPI, and the Claude Code docs. Where I checked something by running it, the section says so. The probe ran in a scratch venv with Python 3.11.15, `mcp` 2.3.0 and Claude Code 2.1.292.

## TL;DR

| Question | Answer | Impact on plan |
| --- | --- | --- |
| PNG in tool results, and does the model see it? | **Yes.** A tool can return `[str, Image(...)]`, which goes on the wire as `TextContent` + `ImageContent`. Claude Code shows PNG, JPEG, GIF and WebP results to the model inline. It may scale them down. | The plan works. Upscale small sprites before returning them, because Claude Code may downscale but won't upscale. |
| Server `instructions` | `MCPServer("texel", instructions="...")`. Claude Code always loads them at session start, even with tool search on. They are truncated at 2,048 chars. | This is the main place to tell Claude when to use Texel. |
| Tool loading | Tool search is on by default. Only tool **names** and server **instructions** are loaded up front. Full definitions load on demand. Tool descriptions are also truncated at 2,048 chars. There is no fixed cap on tool count. | Name tools clearly. Mark the core loop tools `anthropic/alwaysLoad` if needed. |
| Windows stdio from a venv | `claude mcp add texel --scope project -- <abs path>\venv\Scripts\python.exe <abs path>\mcp_server.py`. This writes `.mcp.json`, which needs one-time approval. | Works. Use absolute paths. Never write to stdout. |
| SDK and version | `mcp` **2.3.0** (v2 is the stable line). Requires Python **>=3.10**. `FastMCP` has been renamed to `MCPServer`. | The repo venv is **3.11.15**, which is compatible. Pin `mcp[cli]>=2.3,<3`. |

Nothing found contradicts the plan.

---

## 1. Image content in tool results

### Protocol

In the 2025-11-25 spec, a tool result's `content` is a list that can mix `text`, `image`, `audio`, `resource_link` and `resource` blocks. An image block is `{"type":"image","data":"<base64>","mimeType":"image/png"}`.
Source: <https://modelcontextprotocol.io/specification/2025-11-25/server/tools#tool-result>

### Python SDK (v2)

- `from mcp.server.mcpserver import Image`. Build it with `Image(path=...)` or `Image(data=png_bytes, format="png")`. If you pass `data=`, also pass `format=`. Without it the SDK defaults to `image/png`.
  Source: <https://py.sdk.modelcontextprotocol.io/servers/media/>
- Content blocks and `Image`/`Audio`, "on their own, as the items of a `list`, `tuple` or `Sequence`", opt out of structured output automatically. So the tool gets no output schema and `structured_content` is `None`.
  Source: <https://py.sdk.modelcontextprotocol.io/servers/structured-output/#content-blocks-and-media>
- On migration from v1, `Image`/`Audio` convert to content blocks as before, and ready-made content blocks are kept as-is. Source: <https://py.sdk.modelcontextprotocol.io/migration/#what-is-unchanged-on-mcpserver>

**Verified by running it.** This tool:

```python
@mcp.tool()
def snapshot() -> list:
    """Return a text summary plus a PNG of the canvas."""
    return ["canvas 16x16, 256 px painted", Image(data=png_bytes, format="png")]
```

produced `content = [TextContent("canvas 16x16, ..."), ImageContent(mime_type="image/png", data="iVBOR...")]`, with `outputSchema=None` and `structured_content=None`.

For a full override (for example `isError` or `_meta`), you can also return a `CallToolResult` directly.

### Does Claude Code show the image to the model?

**Yes.** From the Claude Code docs, under "Images in tool results":

> When an MCP tool returns a PNG, JPEG, GIF, or WebP image, Claude sees the image inline in the conversation. The inline copy may be scaled down or compressed to fit the model's image size limits. Claude Code also saves the original bytes to a file in the session's `tool-results` directory under `~/.claude/projects/` and gives Claude the path.

Saving the image to a file requires Claude Code v2.1.283 or later. The local install is 2.1.292.
Source: <https://code.claude.com/docs/en/mcp#images-in-tool-results>

Caveats:

- Image results stay subject to `MAX_MCP_OUTPUT_TOKENS` (default 25,000, with a warning above 10,000). The `anthropic/maxResultSizeChars` override only applies to text.
  Source: <https://code.claude.com/docs/en/mcp#mcp-output-limits-and-warnings>
- My own inference, not stated in the docs: a 16x16 to 64x64 sprite is tiny, and Claude Code only scales images *down*. To make pixels legible to the model, upscale with nearest-neighbour (for example 8x to 512px) and maybe add a grid. Keep the PNG small enough to stay well under the token limit.

## 2. Server-level instructions

- **SDK:** `MCPServer("texel", instructions="...")`. Pass it **as a keyword argument**. In v2 the positional order changed to `name, title, description, instructions, ...`. A v1-style positional `instructions` argument silently becomes `title`, and then no instructions are sent.
  Source: <https://py.sdk.modelcontextprotocol.io/migration/#mcpserver-constructor-title-description-and-version-added-to-the-positional-parameters>
  **Verified:** `client.instructions` returned the configured string.
- **Claude Code:** "Only tool names and server instructions load at session start". Server instructions "help Claude understand when to search for your tools, similar to how skills work". The docs recommend that instructions cover:
  - what category of tasks the tools handle
  - when Claude should search for them
  - the key capabilities

  Instructions are truncated at **2,048 chars** by default. This limit is configurable with `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH` (v2.1.280 or later).
  Source: <https://code.claude.com/docs/en/mcp#for-mcp-server-authors>, <https://code.claude.com/docs/en/env-vars>

## 3. Tool loading (tool search)

Source: <https://code.claude.com/docs/en/mcp#scale-with-mcp-tool-search>

- Tool search is **on by default**, and MCP tool definitions are deferred. At session start Claude sees only tool names and server instructions. It calls `ToolSearch` to load full schemas and descriptions.
- "Claude Code doesn't impose a fixed per-server tool cap; the practical limit is your context window budget."
- Tool search is turned off (all tools load up front) in these cases:
  - `ENABLE_TOOL_SEARCH=false`
  - a non-first-party `ANTHROPIC_BASE_URL`
  - `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`
  - models older than the 4.5 generation on Google Cloud's Agent Platform
  - Microsoft Foundry deployments hosted on Azure

  `ENABLE_TOOL_SEARCH=auto[:N]` loads tools up front until their definitions reach N% of the context window (10% by default).
- **Opting out of deferral:**
  - Set `"alwaysLoad": true` on the server entry in `.mcp.json`.
  - Or, per tool, set `_meta["anthropic/alwaysLoad"] = true`. **Verified:** the SDK passes this through with `@mcp.tool(meta={"anthropic/alwaysLoad": True})`.

  Setting `alwaysLoad` makes startup wait for the server, up to 5 seconds.
- Each tool description is truncated at 2,048 chars. Put the critical details first.
- Related per-tool `_meta` keys:
  - `anthropic/maxResultSizeChars`: text result threshold, default 50,000 and up to 500,000.
  - `anthropic/requiresUserInteraction`: forces approval on every call.

  Source: same page, "MCP output limits and warnings" and the approval section.

**Implications for Texel.** Tool count is not a hard constraint. Each extra tool still costs a name in context, plus a `ToolSearch` round trip before its first use. Tool names should be self-explanatory, since names are all Claude sees before searching. Descriptions should stay under 2 KB with the key information first. The server instructions should describe the paint loop and name the tools. If the painting loop needs the core tools (paint, snapshot) every turn, mark just those `anthropic/alwaysLoad`.

## 4. stdio server from a Windows venv

Sources: <https://code.claude.com/docs/en/mcp#option-3-add-a-local-stdio-server>, <https://code.claude.com/docs/en/mcp#project-scope>, <https://py.sdk.modelcontextprotocol.io/get-started/real-host/>, <https://py.sdk.modelcontextprotocol.io/run/>

- Syntax: `claude mcp add [options] <name> -- <command> [args...]`. Everything after `--` goes to the server untouched. Options such as `--scope` and `--env` go before `--`.
- In the SDK, `mcp.run()` with no argument means stdio. Keep it under `if __name__ == "__main__":`. "Never let anything but the SDK write to stdout." For Texel this means any `print` or logging must go to stderr, because stdout carries the protocol.
- Project scope: `claude mcp add --scope project ...` writes `.mcp.json` at the repo root, which should be committed. Interactive sessions prompt once for approval. `claude mcp list` shows `Pending approval` until then. `-p`, Agent SDK and cloud sessions load project servers without asking.
- Claude Code sets `CLAUDE_PROJECT_DIR` in the server's environment. `.mcp.json` supports `${VAR}` and `${VAR:-default}` expansion in `command` and `args`. Because `CLAUDE_PROJECT_DIR` is not set in Claude Code's own environment, referencing it in `command` or `args` needs a default, for example `${CLAUDE_PROJECT_DIR:-.}`.
- Suggested `.mcp.json`. This is a pattern built from the docs above and has not been tested end to end in Claude Code yet:

  ```json
  {
    "mcpServers": {
      "texel": {
        "type": "stdio",
        "command": "${CLAUDE_PROJECT_DIR:-.}/venv/Scripts/python.exe",
        "args": ["${CLAUDE_PROJECT_DIR:-.}/mcp_server.py"]
      }
    }
  }
  ```

  The venv path is Windows-specific (`venv/Scripts/python.exe`) and the venv isn't committed, so a portable alternative is `"command": "uv", "args": ["run", "--with", "mcp[cli]", "mcp", "run", "<abs path>/mcp_server.py"]`, which is what the SDK docs recommend. With `uv run`, stdout from uv itself is not an issue.
- **Windows notes:**
  - Pointing directly at `venv\Scripts\python.exe` avoids the need to activate the venv.
  - Older Claude Code docs required a `cmd /c` wrapper for `npx`-based servers on native Windows. The current docs no longer mention it. In any case it only matters for `.cmd` shims, not for a real `.exe` like `python.exe`. This is inference.
  - Startup timeout: `MCP_TIMEOUT`, default 30 s. Importing heavy dependencies (langchain and similar) at startup could be slow, so keep the MCP entry point's imports lean.

## 5. SDK package and version

- PyPI `mcp` latest is **2.3.0** (2026-10-02), with `requires_python >=3.10`. The 1.x line is still maintained (1.30.0 was released 2026-09-07).
  Source: <https://pypi.org/pypi/mcp/json>
- "These docs describe **v2**, the current stable release line." Install with `uv add "mcp[cli]"` or `pip install "mcp[cli]"`. On Windows the SDK pulls in `pywin32` for stdio subprocess management.
  Source: <https://py.sdk.modelcontextprotocol.io/get-started/installation/>
- **v2 renames.** `FastMCP` is now `MCPServer`:
  - `from mcp.server import MCPServer`
  - `from mcp.server.mcpserver import Image, Audio`
  - `mcp.server.fastmcp` no longer exists.

  Transport options moved from the constructor to `run()`. Most v1 FastMCP tutorials and examples online use the old names.
  Source: <https://py.sdk.modelcontextprotocol.io/migration/#fastmcp-renamed-to-mcpserver>
- The standalone `fastmcp` package (jlowin's) is a separate third-party project and is not the official SDK. This comes from background knowledge and wasn't checked against a source in this pass. Use the official `mcp` package.
- **This repo:** `venv/pyvenv.cfg` reports `version = 3.11.15`, which meets the requirement. **Verified:** `mcp` 2.3.0 installs and runs under Python 3.11.15. `requirements.txt` doesn't currently include `mcp`. The recommendation is to add `mcp[cli]>=2.3,<3`, and possibly as an optional or separate requirements file, because the web app doesn't need it.

## Open questions and uncertainty

- End-to-end behaviour in Claude Code itself (a real `claude mcp add` against a Texel server, and the model describing a returned sprite) hasn't been run. The docs claims are clear, but a quick smoke test in the first build slice would close this.
- The exact downscale limits Claude Code applies to inline images aren't documented on the MCP page. They follow the model's image size limits.
- I couldn't find whether Claude Code re-sends server instructions after a `notifications/tools/list_changed`. This is probably irrelevant, since instructions are static.
