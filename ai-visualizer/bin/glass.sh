#!/bin/bash
# glass.sh — send one verb to the glass, the agent-controlled overlay on
# the face. The JSON comes from a single argv argument OR from stdin,
# and nothing else:
#
#   ./glass.sh '{"a":"show","type":"map","q":"Aleppo"}'
#   ./glass.sh <<'JSON'
#   {"a":"show","type":"note","title":"Plan","body":"it's alive"}
#   JSON
#
# The reply is pretty-printed, and any ok:false reply exits non-zero so
# the calling shell sees a refusal as a failure, not as more green text.
# The verbs and their replies are documented in server.py.

# The server's own config decides the port, with the server's own
# precedence: "port" in ai-visualizer.json, else 8790. Deliberately no
# --port flag — the config is the one source of truth, and a flag here
# would let this script and the server quietly disagree about where the
# glass lives.
PORT="$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("port", 8790)))' \
        "$(dirname "$0")/../ai-visualizer.json" 2>/dev/null || echo 8790)"
URL="http://127.0.0.1:$PORT/cmd"

# More than one argument means the JSON was not quoted and the shell
# split it into words — refuse loudly rather than posting the first
# fragment as if it were the whole payload.
if [ $# -gt 1 ]; then
  echo "glass.sh: expected ONE JSON argument (quote it) or JSON on stdin; got $# arguments" >&2
  exit 2
fi
if [ $# -eq 1 ]; then
  BODY="$1"
elif [ -t 0 ]; then
  # No argument and stdin is a terminal: waiting silently on a read
  # would look like a hang. Say how to call it instead.
  echo "usage: glass.sh '<json>'    or    glass.sh <<'JSON' ... JSON" >&2
  exit 2
else
  BODY="$(cat)"
fi

# Parse-check locally BEFORE posting: a malformed body should die here
# with the parse position named (json.tool reports line/column/char),
# not travel to the server and come back as a vaguer refusal.
if ! ERR="$(printf '%s' "$BODY" | python3 -m json.tool 2>&1 >/dev/null)"; then
  echo "glass.sh: body is not valid JSON — $ERR" >&2
  exit 2
fi

# The Content-Type header is load-bearing, not decoration: the server
# 403s anything else. That requirement is its cross-site write defense
# (a browser can fire a cross-origin text/plain POST without asking;
# application/json it must preflight first, and the server never
# answers preflights). curl states it honestly and sails through.
# --noproxy '*' keeps the localhost POST out of any http_proxy/all_proxy
# in the environment — a down proxy would otherwise read as "the face
# isn't running", and every diagnosis line below would mislead.
REPLY="$(curl -s --noproxy '*' --max-time 10 \
              -H 'Content-Type: application/json' \
              --data-binary "$BODY" "$URL")"
RC=$?
if [ "$RC" -ne 0 ]; then
  # Nothing answered at all. Connection refused gets its own sentence
  # because it has its own fix; anything rarer keeps curl's exit code
  # so the trail stays warm.
  if [ "$RC" -eq 7 ]; then
    echo "glass.sh: the face isn't running (connection refused on $URL) — start it and I'll put this up." >&2
  else
    echo "glass.sh: could not reach $URL (curl exit $RC)." >&2
  fi
  exit 1
fi

# Something answered — but was it the glass? A reply without an "ok"
# key is not a server error, it is a stranger on the face's port, and
# that deserves a different sentence than a refusal does.
printf '%s' "$REPLY" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    reply = json.loads(raw)
    ok = reply["ok"]
except (ValueError, KeyError, TypeError):
    sys.stderr.write("glass.sh: something else is on the face'\''s port"
                     " (%s replied, but not with glass JSON)\n" % sys.argv[1])
    if raw.strip():
        sys.stderr.write("reply began: %s\n" % raw.strip()[:200])
    sys.exit(1)
print(json.dumps(reply, indent=2))
sys.exit(0 if ok else 1)
' "$URL"
