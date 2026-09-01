#!/bin/bash
# glass-state.sh — what is on the glass right now, printed for humans:
# the occupancy map, every item with where it sits and how long it has
# left, and where the next common spans would land. Run it before
# placing with an explicit cell and before talking about what is on
# screen.
#
#   glass 12x8   rev 17   viewers 1   faces: board
#      A B C D E F G H I J K L
#    1 # # . # # # # # # # # #
#    ...
#    a  map-1   map   "Aleppo"     J3 3x3   expires in ~2m
#   free: 2x2 at J6, 3x2 at J6, 3x3 no room, 3x4 no room
#
# The map rows arrive pre-rendered from the server — the one
# implementation of the grid math lives there, where --selfcheck can
# assert it — so this script only lays the reply out, it never
# recomputes occupancy.

# Same port resolution as glass.sh: the server's config, the server's
# precedence ("port" in ai-visualizer.json, else 8790), and no flag to
# disagree with it.
PORT="$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("port", 8790)))' \
        "$(dirname "$0")/../ai-visualizer.json" 2>/dev/null || echo 8790)"
URL="http://127.0.0.1:$PORT/cmd"

# state is a /cmd verb like any other, and the Content-Type is what
# gets it past the server's cross-site write defense (see glass.sh).
# --noproxy '*' keeps the localhost POST out of any http_proxy/all_proxy
# in the environment, same as glass.sh: a proxy in the middle would make
# every diagnosis line below mislead.
REPLY="$(curl -s --noproxy '*' --max-time 10 \
              -H 'Content-Type: application/json' \
              --data-binary '{"a":"state"}' "$URL")"
RC=$?
if [ "$RC" -ne 0 ]; then
  if [ "$RC" -eq 7 ]; then
    echo "glass-state.sh: the face isn't running (connection refused on $URL)." >&2
  else
    echo "glass-state.sh: could not reach $URL (curl exit $RC)." >&2
  fi
  exit 1
fi

# The stdlib formatter. ok:false here usually means the glass is off in
# the config — the server says why in plain English, so relay its line
# rather than inventing one. A reply without "ok" at all is a stranger
# on the face's port.
printf '%s' "$REPLY" | python3 -c '
import json, re, sys
raw = sys.stdin.read()
try:
    r = json.loads(raw)
    ok = r["ok"]
except (ValueError, KeyError, TypeError):
    sys.stderr.write("glass-state.sh: something else is on the face'\''s port"
                     " (%s replied, but not with glass JSON)\n" % sys.argv[1])
    if raw.strip():
        sys.stderr.write("reply began: %s\n" % raw.strip()[:200])
    sys.exit(1)
if not ok:
    # "message" is the plain-English line the server writes for humans;
    # "error" is only the machine slug (see _err in server.py).
    sys.stderr.write("glass-state.sh: %s\n"
                     % (r.get("message") or r.get("error") or json.dumps(r)))
    sys.exit(1)
def esc(s):
    # Belt to the server-side suspenders: /cmd refuses control characters
    # in titles and ids, but this table must stay un-forgeable even if a
    # future field slips through. Controls become visible \xNN; everything
    # else (accents included) prints as itself.
    return re.sub(r"[\x00-\x1f\x7f]",
                  lambda m: "\\x%02x" % ord(m.group()), str(s))
g = r.get("glass") or {}
items = g.get("items") or []
head = "glass 12x8   rev %s   viewers %s" % (g.get("rev", 0), r.get("viewers", 0))
faces = r.get("faces") or g.get("faces") or []
if faces:
    head += "   faces: %s" % ", ".join(faces)
print(head)
print("   " + " ".join("ABCDEFGHIJKL"))
for n, row in enumerate(r.get("map") or [], 1):
    print("%2d %s" % (n, " ".join(row)))
def life(it):
    left = it.get("expires_in")
    if left is None:
        return "pinned"
    return "expires in ~%ds" % left if left < 60 else "expires in ~%dm" % round(left / 60.0)
if items:
    # Column widths from the data, so ids, types and titles line up
    # whatever their lengths; letters match the map (listing order).
    # Widths are measured on the ESCAPED strings, the ones printed.
    wid = max(len(esc(it.get("id", ""))) for it in items)
    wtype = max(len(esc(it.get("type", ""))) for it in items)
    wtitle = max(len(esc(it.get("title", ""))) for it in items) + 2
    for n, it in enumerate(items):
        w, h = (it.get("span") or ["?", "?"])[:2]
        line = " %s  %-*s  %-*s  %-*s  %s %sx%s   %s" % (
            "abcdefghijklmnopqrstuvwxyz"[n % 26],
            wid, esc(it.get("id", "")), wtype, esc(it.get("type", "")),
            wtitle, "\"%s\"" % esc(it.get("title", "")),
            it.get("cell", "?"), w, h, life(it))
        if it.get("flags"):
            line += "  [%s]" % ",".join(it["flags"])
        print(line)
free = r.get("free") or {}
if free:
    print("free: " + ", ".join(("%s at %s" % (k, v)) if v else ("%s no room" % k)
                               for k, v in free.items()))
' "$URL"
