- **The MCP server runs on both `mcp` SDK majors (#914).** `clm.mcp.server`
  now resolves mcp 2's `mcp.server.mcpserver.MCPServer` and falls back to the
  pre-2.0 `mcp.server.fastmcp.FastMCP`, so `clm mcp` and
  `clm export agent-guide` start on either. The `[mcp]` extra's temporary
  `<2` cap (PR #915) is replaced by `mcp>=1.0.0,<3`; the lock keeps whichever
  major the repo's `exclude-newer` pin admits (1.x today — mcp 2.0.0 shipped
  after it), and flips to 2.x at the next pin bump without a code change. A
  new stdio-handshake test starts the real `clm mcp` process and lists its
  tools, and the nightly `mcp-forward-compat` job runs the MCP suite against
  the newest 2.x so the path the lock does not exercise is still tested.
