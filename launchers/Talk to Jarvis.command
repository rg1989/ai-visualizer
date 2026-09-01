#!/bin/bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export CLAUDE_CONFIG_DIR="$HOME/jarvis-config"
# Web search for the brain (tavily MCP, see ../.mcp.json). Keychain, not a
# file: this key would otherwise sit in a repo. Missing key = no search,
# never a failed launch.
export TAVILY_API_KEY="$(security find-generic-password -s jarvis-tavily -w 2>/dev/null)"
cd "$HOME/my-agent" && ./fullstack-agent/start.sh voice
