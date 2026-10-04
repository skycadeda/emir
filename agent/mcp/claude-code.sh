# Claude Code: register the EMIR MCP server (run once, in the project directory).
# Needs `emir` on PATH with the MCP SDK: pip install -e '.[mcp]' from the clone root.
# Shared with the team instead: claude mcp add --scope project emir -- emir mcp
# (writes .mcp.json, the same as claude-code.mcp.json here).
claude mcp add emir -- emir mcp
