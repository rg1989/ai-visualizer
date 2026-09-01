#!/bin/bash
# Reusable glass components. One JSON object on stdin, one finished card.
#   component.sh <<'JSON'
#   {"component":"stat","title":"Systems","data":{"tiles":[...]}}
#   JSON
# Add --dry-run to print the glass payload instead of showing it.
# The catalog of components and their data shapes is the glass-components
# skill. --self-check runs the assertions in render.py.
cd "$(dirname "$0")" || exit 1
exec python3 render.py "$@"
