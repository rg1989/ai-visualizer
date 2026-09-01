#!/bin/bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export CLAUDE_CONFIG_DIR="$HOME/jarvis-config-glm"
# Web search for the brain (tavily MCP, see ../.mcp.json). Keychain, not a
# file: this key would otherwise sit in a repo. Missing key = no search,
# never a failed launch.
export TAVILY_API_KEY="$(security find-generic-password -s jarvis-tavily -w 2>/dev/null)"
export BACKTALK_CONFIG="$HOME/my-agent/backtalk/backtalk.glm.json"
export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
# z.ai coding-plan model ids (docs.z.ai/devpack/tool/claude). The SDK still asks for
# haiku/sonnet/opus internally; these map them onto GLM so nothing 404s or leaks home.
export ANTHROPIC_DEFAULT_HAIKU_MODEL="glm-5.3-flash"
export ANTHROPIC_DEFAULT_SONNET_MODEL="glm-5.3"
export ANTHROPIC_DEFAULT_OPUS_MODEL="glm-5.3"
export ANTHROPIC_AUTH_TOKEN="$(security find-generic-password -s jarvis-glm -w 2>/dev/null)"
[ -n "$ANTHROPIC_AUTH_TOKEN" ] || { echo "No Z.AI key yet. Add it once (you will be prompted privately): security add-generic-password -s jarvis-glm -a zai -w"; exit 1; }
cd "$HOME/my-agent" && ./fullstack-agent/start.sh voice
