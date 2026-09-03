#!/bin/bash
# Talk to Jarvis, with the face over every window instead of a browser tab.
# Same launcher, one variable: start.sh reads it.
export JARVIS_FACE=overlay
exec "$(dirname "$0")/Talk to Jarvis.command"
