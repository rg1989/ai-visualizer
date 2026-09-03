#!/bin/bash
# zai-vision-test.sh -- does THIS subscription accept images and video on
# Z.AI's native endpoints? Uses the token already in the environment of the
# voice line (ANTHROPIC_AUTH_TOKEN, set by the GLM launcher). Sends one
# webcam frame as a base64 image_url, and one small public sample mp4 as a
# video_url, to glm-5.3-flash on both the general and the coding endpoints,
# and prints what each said. Nothing is stored.
set -u
: "${ANTHROPIC_AUTH_TOKEN:?no ANTHROPIC_AUTH_TOKEN in the environment; run this from the voice line}"
KIND_ONLY="${1:-}"; EP_ONLY="${2:-}"; MODEL="${ZAI_MODEL:-glm-5.3-flash}"
RESULTS="${TMPDIR:-/tmp}/zai-vision-test.txt"
if [ "$KIND_ONLY" = "all-bg" ]; then
  # All four probes in the background, results to a file: a turn that gets
  # interrupted (a key press) cannot kill them, and anyone can read the file.
  : > "$RESULTS"; nohup "$0" "" "" >> "$RESULTS" 2>&1 &
  echo "vision test running in the background; results in $RESULTS (allow up to 6 minutes)"; exit 0
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
FRAME="${TMPDIR:-/tmp}/zai-vision-test.jpg"
if [ -n "${ZAI_TEST_IMAGE:-}" ]; then FRAME="$ZAI_TEST_IMAGE"; else "$HERE/cam-look.sh" "$FRAME" >/dev/null 2>&1 || { echo "could not grab a frame"; exit 2; }; fi
B64="$(base64 < "$FRAME" | tr -d '\n')"
VIDEO_URL="https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4"
IMG_BODY="$(python3 -c 'import json,sys; print(json.dumps({"model": sys.argv[1], "max_tokens": 120, "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + sys.argv[2]}}, {"type": "text", "text": "In one sentence: what is in this picture?"}]}]}))' "$MODEL" "$B64")"
VID_BODY="$(python3 -c 'import json,sys; print(json.dumps({"model": sys.argv[1], "max_tokens": 120, "messages": [{"role": "user", "content": [{"type": "video_url", "video_url": {"url": sys.argv[2]}}, {"type": "text", "text": "In one sentence: what happens in this video?"}]}]}))' "$MODEL" "$VIDEO_URL")"
for EP in "general https://api.z.ai/api/paas/v4/chat/completions" "coding https://api.z.ai/api/coding/paas/v4/chat/completions"; do
  NAME="${EP%% *}"; URL="${EP#* }"
  [ -z "$EP_ONLY" ] || [ "$EP_ONLY" = "$NAME" ] || continue
  for KIND in image video; do
    [ -z "$KIND_ONLY" ] || [ "$KIND_ONLY" = "$KIND" ] || continue
    BODY="$IMG_BODY"; [ "$KIND" = video ] && BODY="$VID_BODY"
    OUT="$(curl -s --max-time 90 -w '\n%{http_code}' -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" -H "Content-Type: application/json" --data-binary "$BODY" "$URL")"
    CODE="${OUT##*$'\n'}"; JSON="${OUT%$'\n'*}"
    printf '%-8s %-6s HTTP %s  ' "$NAME" "$KIND" "$CODE"
    printf '%s' "$JSON" | python3 -c '
import json,sys
try: d=json.loads(sys.stdin.read())
except Exception: print("(non-JSON reply)"); sys.exit()
if "choices" in d:
    m=d["choices"][0]["message"]; u=d.get("usage",{})
    print(repr((m.get("content") or "")[:140]), "| tokens in", u.get("prompt_tokens"), "out", u.get("completion_tokens"))
else: print("error:", json.dumps(d)[:200])'
  done
done
