#!/usr/bin/env python3
# ai-visualizer: give your AI agent a face.
# Copyright (C) 2026 Jared Rhodenizer
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ai-visualizer server. Python standard library only, nothing to install.

Serves the face gallery at http://127.0.0.1:8790/ and exposes:

  /state   polled by the faces (~8x/sec):
           {"state":  "idle|listening|thinking|speaking",
            "level":  0.0-1.0,       voice loudness while speaking
            "samples": [64 floats],  raw waveform snapshot (0s when quiet)
            "alert":  bool,          optional attention signal
            "loading": bool}         true while the voice line plays its
                                     own thinking sound (we stay quiet)
  /config  the merged ai-visualizer.json plus the list of installed
           faces, discovered by scanning the faces/ folder. Drop a new
           folder with an index.html into faces/ and it appears in the
           gallery. That is the whole plugin system.
  /cmd     POST, application/json only: the glass verbs (show, update,
           move, pin, unpin, dismiss, clear, state). The glass is an
           agent-controlled overlay grid drawn over the face;
           GLASS-SPEC.md in the repo root is the contract. While it is
           enabled ("glass" in ai-visualizer.json), the /state payload
           gains a "glass" object the faces render -- there is no second
           read endpoint and no second poll loop.

READ-ONLY on the signal bus. The bus is three tiny files written by a
voice line (backtalk writes them natively, github.com/jaredrhod/backtalk):

  .voice_state        idle | listening | thinking | speaking
  .voice_waveform     JSON {ts, samples: [64 floats]} while audio plays
  .voice_loading_pid  exists while the voice line plays a thinking sound
  .voice_alert        optional: non-empty file = attention needed

Where the bus lives comes from "bus_dir" in ai-visualizer.json (default:
this folder). Point it at your backtalk folder, or point backtalk's
"signals_dir" here. Either direction works.

Run:
  python3 server.py             the real bus
  python3 server.py --mock speaking
                                no voice line needed: /state synthesizes
                                the chosen state (idle|listening|thinking
                                |speaking) so you can see a face perform
  python3 server.py --no-open   do not auto-open the browser
  python3 server.py --selfcheck
                                prove the glass out in-process (placement,
                                conflicts, singleton targeting, resize
                                re-anchoring, ttl, persistence, origin
                                guards, thread races); prints pass/fail
                                per group and exits non-zero on any FAIL
Ctrl-C stops.
"""
import functools
import ipaddress
import json
import math
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
import urllib.parse
import urllib.request
import errno
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATES = {"idle", "listening", "thinking", "speaking"}
WAVEFORM_STALE_S = 0.6

DEFAULTS = {
    "name": "JARVIS",       # shown on the chip / headers, yours to change
    "badge": "",            # optional handle shown in some faces' chrome
    "face": "board",        # the default face the root URL opens
    "port": 8790,
    "bus_dir": "",          # where the .voice_* files live ("" = here)
    "thinking_sound": True, # play assets/thinking.wav while thinking
    "glass": True,          # the agent-controlled overlay grid (GLASS-SPEC.md);
                            # false makes every /cmd verb refuse in plain English
    "chat": True,           # the conversation crawl on the face (chat.js):
                            # what was said and what was answered, rolling up
    "orbs": [],             # the hub's folders (hub.js): one entry per
                            # folder that blooms off the face, each
                            # {"title": ..., "path": ..., "kind": "notes"}.
                            # Empty = no hub. Same shape as barehands.json.
    "chat_area": {"cell": "D6", "span": [6, 3]},  # its grid home (bottom
                            # center, right under the face); auto-
                            # placement stays clear of it (explicit placements
                            # may cover it, flagged over_reserve as usual)
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        user = json.loads((HERE / "ai-visualizer.json").read_text())
        if not isinstance(user, dict):
            print("[config] ai-visualizer.json must be a JSON object, "
                  "using defaults")
            return cfg
        for k, v in user.items():
            cfg[k] = v
    except FileNotFoundError:
        pass
    except ValueError as e:
        print(f"[config] ai-visualizer.json is not valid JSON ({e}), "
              f"using defaults")
    # Wrong-SHAPE values crash far from here (int(port) at module load,
    # Path(bus_dir) one line above it) while bin/glass.sh quietly falls
    # back to 8790 -- the two "same precedence" port readers must agree
    # on every input. So coerce here, where the tolerance already lives:
    # one printed line and a default, never a traceback.
    try:
        cfg["port"] = int(cfg.get("port", 8790))
    except (TypeError, ValueError):    # null AND non-numeric strings
        print('[config] "port" in ai-visualizer.json is not a number, '
              'using 8790')
        cfg["port"] = 8790
    if cfg.get("bus_dir") and not isinstance(cfg["bus_dir"], str):
        print('[config] "bus_dir" in ai-visualizer.json is not a string, '
              'using this folder')
        cfg["bus_dir"] = ""
    return cfg


CFG = load_config()


def orb_root(i):
    """Resolve a notes orb's jail root, or None. Ported from barehands."""
    try:
        orb = CFG["orbs"][int(i)]
        assert orb.get("kind", "notes") == "notes"
        p = Path(str(orb["path"])).expanduser()
        return (p if p.is_absolute() else HERE / p).resolve()
    except Exception:
        return None


BUS = Path(CFG["bus_dir"]).expanduser() if CFG.get("bus_dir") else HERE

# The glass-wide theme. localStorage alone cannot carry it: every browser
# (the glass display, a laptop, a phone) keeps its own copy, so a theme
# picked on one display never reached the others. The server remembers it
# here and /state carries it; theme.js posts changes to /theme.
THEME_FILE = HERE / ".face_theme"

# ponytail: a face is just a browser tab, and closing it is silent -- which
# is what made "I closed the tab" look exactly like "the face crashed". The
# tab's /state poll is the heartbeat; when it stops, print the way back in.
LAST_POLL = [0.0]

# ---------------------------------------------------------------------------
# The brain: which model answers. Picked on the face (the BRAIN tab of the
# settings picker) instead of by which launcher was double-clicked.
#
#   POST /brain     -> BUS/.voice_brain_pick   the request, one bare token
#   BUS/.voice_brain                            what the voice line published
#   POST /brainkey  -> the OS keyring          never a file, never a log
#
# Same shape as the mic picker one screen over (/pick -> .voice_mode_pick,
# .voice_mic): a token beside the bus, consumed by whoever is waiting.
PROVIDERS = ("claude", "zai")
# The tiers the BRAIN tab may ask for, across both brains: Claude's three
# presets and the two GLMs. Kept beside PROVIDERS for the same reason:
# this file must never be able to name a tier backtalk would refuse
# (backtalk/backtalk/provider.py owns the truth -- PROVIDERS[p]
# ["variants"] -- and an id missing from THIS tuple is dropped to "",
# which silently answers on the provider's default instead).
BRAIN_MODELS = ("glm-5.3", "glm-5.3-flash", "fast", "balanced", "think")
ZAI_KEYCHAIN_ITEM = "jarvis-glm"        # what the GLM launcher already reads
CLAUDE_HOME = Path("~/jarvis-config").expanduser()   # the Anthropic profile
# A plausible API token: printable ASCII, no whitespace, bounded. Deliberately
# not a z.ai-shaped regex -- this only has to stop obvious garbage (a pasted
# sentence, an empty box, a whole file) from reaching the keyring.
KEY_RE = re.compile(r"[!-~]{8,512}")


def _zai_key_status(item=ZAI_KEYCHAIN_ITEM):
    """(stored?, last four characters). The rest of the secret is dropped
    on the next line and never leaves this function."""
    from shutil import which

    def run(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None

    if which("security"):
        meta = run("security", "find-generic-password", "-s", item)
        if meta is None or meta.returncode != 0:
            return False, ""
        # Attributes are free; only reading the SECRET can meet a keychain
        # ACL prompt. So a failure on the second call means "stored, tail
        # unknown" -- answering "no key" there would be a lie that sends
        # the owner off to paste a key he already has.
        got = run("security", "find-generic-password", "-s", item, "-w")
    elif which("secret-tool"):
        got = run("secret-tool", "lookup", "service", item)
        if got is None or got.returncode != 0:
            return False, ""
    else:
        return False, ""
    secret = (got.stdout or "").strip() if got and got.returncode == 0 else ""
    return True, secret[-4:]


def _claude_signed_in():
    """Does the Anthropic side have something to log in with? Soft on
    purpose -- "looks signed in", not proof. Claude Code keeps its login
    either in CLAUDE_CONFIG_DIR/.credentials.json or, on macOS, in the
    login keychain under a "Claude Code-credentials" service. Only
    attributes are asked for, never the secret, so this never raises a
    keychain prompt."""
    if (CLAUDE_HOME / ".credentials.json").exists():
        return True
    from shutil import which
    if not which("security"):
        return False
    try:
        return subprocess.run(
            ["security", "find-generic-password", "-s",
             "Claude Code-credentials"],
            capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def brain_status():
    """What the BRAIN tab renders: whether each provider can actually be
    switched to. NEVER the key -- a bool and four characters is the whole
    contract, and four characters is what lets someone tell "the key I
    pasted" from "some older key".

    THE TWIN: backtalk/backtalk/provider.py answers these same two
    questions for the voice line (it is the side that actually swaps
    os.environ and restarts the brain). Two readers, one truth: keychain
    item "jarvis-glm" for z.ai, ~/jarvis-config for the Claude login.
    Change either rule and change it in both files -- they are kept this
    small precisely so they can be compared by eye."""
    stored, tail = _zai_key_status()
    return {"zai_key": stored, "zai_tail": tail,
            "claude_signed_in": _claude_signed_in()}


def store_zai_key(key, item=ZAI_KEYCHAIN_ITEM):
    """Put the z.ai token in the OS keyring and hand back its last four.
    Returns (True, tail) or (False, error-slug). The key is not written
    to any file, not logged, not echoed -- note that nothing here ever
    prints the child's stderr, because a failing `security` likes to
    quote back the command line it was given.

    ponytail: `security ... -w <key>` puts the token in THIS process's
    argv for as long as the command runs, so a `ps` on a multi-user box
    could catch it in that window. That is the ceiling of the one-line
    version and it is worth naming out loud. The upgrade, when this ever
    runs somewhere shared: a helper that reads the key on stdin
    (`security -i`, or a keyring binding) so it never reaches the
    process table. The Linux fallback below already has that property --
    `secret-tool store` reads stdin -- so the hole is macOS-only, and on
    a single-user Mac the exposure is to yourself."""
    from shutil import which
    if which("security"):
        cmd = ["security", "add-generic-password", "-U",   # -U: update in place
               "-a", os.environ.get("USER") or "jarvis", "-s", item,
               "-w", key]
        stdin = None
    elif which("secret-tool"):
        cmd = ["secret-tool", "store", "--label=" + item, "service", item]
        stdin = key
    else:
        return False, "no_keychain"
    try:
        r = subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                           timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False, "keychain_failed"
    if r.returncode != 0:
        return False, "keychain_failed"
    return True, key[-4:]


def watch_face(url, gone_after=5.0):
    """Say 'reopen: <url>' the moment the last face stops polling, once."""
    had = False
    while True:
        time.sleep(1.0)
        seen = LAST_POLL[0]
        if seen and time.time() - seen < 3.0:
            if not had:
                print("face connected", flush=True)
            had = True
        elif had and time.time() - seen >= gone_after:
            print(f"face closed  reopen: {url}", flush=True)
            had = False


def read_theme():
    try:
        t = THEME_FILE.read_text().strip()
        return t if t and len(t) <= 32 and t.isalnum() else None
    except OSError:
        return None

MOCK = None
NO_OPEN = "--no-open" in sys.argv
if "--mock" in sys.argv:
    i = sys.argv.index("--mock")
    MOCK = sys.argv[i + 1] if len(sys.argv) > i + 1 else "speaking"
    if MOCK not in STATES:
        MOCK = "speaking"
PORT = int(CFG.get("port", 8790))
if "--port" in sys.argv:
    i = sys.argv.index("--port")
    PORT = int(sys.argv[i + 1])


def list_faces():
    faces = []
    fdir = HERE / "faces"
    if fdir.is_dir():
        for p in sorted(fdir.iterdir()):
            if p.is_dir() and (p / "index.html").exists():
                meta = {"id": p.name, "title": p.name.title(), "tagline": ""}
                try:
                    meta.update(json.loads((p / "face.json").read_text()))
                except (OSError, ValueError):
                    pass
                meta["id"] = p.name
                faces.append(meta)
    return faces


def list_themes():
    """Theme ids: every themes/<id>/ holding a theme.js. Same drop-in rule
    as list_faces -- add the folder, it appears; delete it, it is gone, and
    nothing tracked lists the ones you keep private. Ids are [A-Za-z0-9]
    because POST /theme validates them that way and because the name goes
    straight into a <script src> the browser executes."""
    tdir = HERE / "themes"
    if not tdir.is_dir():
        return []
    return [p.name for p in sorted(tdir.iterdir())
            if p.is_dir() and (p / "theme.js").is_file()
            and re.fullmatch(r"[A-Za-z0-9]{1,32}", p.name)]


def mock_bus():
    t = time.time()
    level = 0.0
    samples = [0.0] * 64
    if MOCK == "speaking":
        level = abs(math.sin(t * 6.0)) * 0.85
        samples = [
            (math.sin(i * 0.55 + t * 9.0) * 0.6
             + math.sin(i * 1.7 - t * 13.0) * 0.4)
            * 9000.0 * (0.35 + 0.65 * abs(math.sin(t * 2.6)))
            for i in range(64)
        ]
    return {"state": MOCK, "level": level, "samples": samples,
            "alert": False, "loading": MOCK == "thinking",
            # Faked so the usage readout can be looked at without
            # spending a real session to make it appear.
            "rate_limits": {
                "five_hour": {"utilization": 0.34, "resets_at": t + 9200},
                "seven_day": {"utilization": 0.61, "resets_at": t + 288000},
            }}


def read_bus():
    if MOCK:
        return mock_bus()
    try:
        state = (BUS / ".voice_state").read_text().strip().lower()
        if state not in STATES:
            state = "idle"
    except OSError:
        state = "idle"
    level = 0.0
    samples = [0.0] * 64
    try:
        payload = json.loads((BUS / ".voice_waveform").read_text())
        age = time.time() - float(payload.get("ts", 0))
        raw = payload.get("samples") or []
        if raw and age < WAVEFORM_STALE_S:
            # A fresh waveform IS speech, whatever the state file says.
            state = "speaking"
            samples = [float(s) for s in raw[:64]]
            mean = sum(abs(s) for s in samples) / len(samples)
            level = min(1.0, mean / 3000.0)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        alert = (BUS / ".voice_alert").stat().st_size > 0
    except OSError:
        alert = False
    loading = (BUS / ".voice_loading_pid").exists()
    # Absent unless the voice line was told to publish it, which is the
    # normal case: it is the account holder's own spend and it stays off
    # until asked for. An empty dict simply means no readout.
    rate_limits = {}
    try:
        rate_limits = json.loads((BUS / ".voice_rate_limits").read_text())
    except (OSError, ValueError):
        pass
    # The mic mode ("ptt" | "open" | "wake", plus "hot" while a wake
    # follow-up window is live), so faces can show at a glance whether
    # the room is being listened to. Absent file = no badge.
    mic = None
    try:
        parts = (BUS / ".voice_mic").read_text().split()
        if parts and parts[0] in ("ptt", "open", "wake", "select"):
            mic = {"mode": parts[0], "hot": "hot" in parts[1:]}
    except (OSError, ValueError):
        pass
    # Which brain is answering ("claude" | "zai"), published by the voice
    # line the same way the mic mode is. Absent file = the key is left out
    # entirely and the picker shows nothing selected rather than guessing
    # on the owner's behalf. The KEY never comes through here.
    brain = None
    try:
        token = (BUS / ".voice_brain").read_text()[:64].strip()
        name, _, model = token.partition(" ")
        if name in PROVIDERS:
            brain = {"provider": name}
            if model in BRAIN_MODELS:
                brain["model"] = model
    except (OSError, ValueError):
        pass
    # Sub-step detail inside a state ("transcribing", "generating
    # speech"): the two real waits, which otherwise look like a hang.
    stage = ""
    try:
        stage = (BUS / ".voice_stage").read_text().strip()[:64]
    except OSError:
        pass
    ready = (BUS / ".voice_ready").exists()
    out = {"state": state, "level": level, "samples": samples,
           "stage": stage, "ready": ready, "alert": alert,
           "loading": loading, "rate_limits": rate_limits}
    if mic:
        out["mic"] = mic
    if brain:
        out["brain"] = brain
    theme = read_theme()
    if theme:
        out["theme"] = theme
    return out


# ---------------------------------------------------------------------------
# The glass (GLASS-SPEC.md): a 12x8 overlay grid the agent draws on via
# POST /cmd. The model lives in memory; every access -- mutations, lazy TTL
# pruning, and /state serialization -- happens under ONE threading.Lock,
# because this is a ThreadingHTTPServer: without it, two auto-placements can
# race each other into the same cells and json.dumps can serialize a
# half-applied mutation. Pinned items and the id counters are mirrored to
# glass-state.json so a restart restores what the person asked to keep and
# never reissues an id a page may still be holding.

GRID_COLS, GRID_ROWS = 12, 8
COLS = "ABCDEFGHIJKL"
DEFAULT_TTL = 180.0     # one default for every type; pass "ttl" to linger
TIMER_GRACE = 30.0      # a timer never expires before its own end + this,
                        # so a 10-minute timer cannot die at 3:00
VIEWER_WINDOW_S = 3.0   # a face "is watching" for this long after a poll
DEFAULT_RESERVE = [{"cell": "D2", "span": [6, 6]}]  # the center block, where
                        # a face with no measured reserve draws its subject
# Auto-placement tries anchors in this column order -- right rail, left rail,
# then the middle, each top to bottom. Hugging the rails keeps the center
# clear even for a face that defines no reserve at all.
SCAN_COLS = [COLS.index(c) for c in "JKLABCDEFGHI"]
# The probe spans /state's "free" readout answers for. It reuses the real
# placement scanner, so "will my next show fit" has exactly one answer.
FREE_PROBES = [(2, 2), (3, 2), (3, 3), (3, 4)]

# Per-type contract: default span (an opening, not a limit -- any item may be
# moved to any span), the content fields the type accepts, and which of them
# are required ("needs" lists alternatives; exactly one alternative must be
# fully present, and mixing two is refused rather than guessed at).
TYPE_SPECS = {
    "note":     {"span": (3, 2), "fields": {"body"}, "needs": [["body"]]},
    "image":    {"span": (3, 3), "fields": {"src", "caption"},
                 "needs": [["src"]]},
    "map":      {"span": (3, 3), "fields": {"q", "lat", "lon", "zoom"},
                 "needs": [["q"], ["lat", "lon"]]},
    "calendar": {"span": (3, 4), "fields": {"events", "view"},
                 "needs": [["events"]]},
    "timer":    {"span": (2, 2), "fields": {"until", "seconds", "label"},
                 "needs": [["until"], ["seconds"]]},
    "list":     {"span": (3, 3), "fields": {"items"}, "needs": [["items"]]},
    "iframe":   {"span": (3, 3), "fields": {"src"}, "needs": [["src"]]},
    "html":     {"span": (3, 3), "fields": {"html"}, "needs": [["html"]]},
    "player":   {"span": (4, 4),
                 "fields": {"q", "count", "tracks", "playlist", "mode"},
                 "needs": [["q"], ["tracks"], ["playlist"]]},
}
SHOW_KEYS = {"a", "type", "id", "new", "cell", "span", "ttl", "pin", "title"}
VERBS = ("show", "update", "move", "pin", "unpin", "dismiss", "clear",
         "state")
STR_FIELDS = {"title", "body", "caption", "label", "html", "q", "src",
              "until", "view", "mode", "playlist"}
NUM_FIELDS = {"zoom", "lat", "lon", "seconds", "count"}


def parse_cell(s):
    """"J4" -> (9, 3), zero-based (col, row); None for anything else."""
    if not isinstance(s, str):
        return None
    m = re.fullmatch(r"([A-La-l])([1-8])", s.strip())
    if not m:
        return None
    return (COLS.index(m.group(1).upper()), int(m.group(2)) - 1)


def cell_name(col, row):
    return COLS[col] + str(row + 1)


def parse_span(v):
    # bool is an int subclass, so [true, 2] must be caught explicitly.
    if (isinstance(v, list) and len(v) == 2
            and all(isinstance(n, int) and not isinstance(n, bool) and n >= 1
                    for n in v)):
        return (v[0], v[1])
    return None


def parse_rect(d):
    """A reserve rect {"cell","span"} -> (col, row, w, h), or None. Garbage
    in a face.json must never wedge the server, so bad rects just vanish."""
    if not isinstance(d, dict):
        return None
    cr = parse_cell(d.get("cell"))
    sp = parse_span(d.get("span"))
    if not cr or not sp:
        return None
    c, r = cr
    w, h = sp
    if c + w > GRID_COLS or r + h > GRID_ROWS:
        return None
    return (c, r, w, h)


def overlap(a, b):
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def item_rect(it):
    c, r = parse_cell(it["cell"])
    return (c, r, it["span"][0], it["span"][1])


def sec(x):
    """Seconds the way the spec's worked examples write them: 180, not
    180.0, but 142.5 stays a float."""
    if x is None:
        return None
    x = round(x, 1)
    return int(x) if float(x).is_integer() else x


def _err(slug, message, **extra):
    out = {"ok": False, "error": slug, "message": message}
    out.update(extra)
    return out


def coerce_bool(obj, key):
    """JSON booleans, plus the strings "true"/"false" -- the one common LLM
    emission slip. Anything else is refused naming both accepted spellings."""
    v = obj.get(key, False)
    if isinstance(v, bool):
        return v, None
    if v == "true":
        return True, None
    if v == "false":
        return False, None
    return None, ('"%s" must be a JSON boolean true or false (the strings '
                  '"true"/"false" are also accepted)' % key)


def check_ttl(obj):
    if "ttl" not in obj:
        return None, None
    v = obj["ttl"]
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
        return float(v), None
    return None, '"ttl" must be a positive number of seconds'


def _points_at_local(host):
    """True when `host` denotes this machine or its local network under
    browser (WHATWG) URL canonicalization, not just the canonical
    spellings: browsers resolve http://2130706433/, 0x7f000001,
    0177.0.0.1, 127.1 and [::ffff:127.0.0.1] straight to loopback, so a
    string-prefix check alone leaves the §4 guard bypassable. No live DNS
    here -- a name can always rebind, so the enforceable floor is the
    numeric literals a browser resolves without a lookup. `host` arrives
    lowercased with brackets already stripped by urlsplit; a trailing dot
    is the same name to DNS and the same address to a browser."""
    host = host.rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost"):
        return True

    def bad4(ip):
        # Private and link-local ride along with loopback: the always-on
        # face would otherwise GET agent-chosen LAN/metadata addresses.
        return (ip.is_loopback or ip.is_unspecified or ip.is_private
                or ip.is_link_local)

    try:
        ip = ipaddress.ip_address(host)
        # An IPv4-mapped IPv6 literal (::ffff:127.0.0.1) reports
        # is_loopback False until the mapped address is unwrapped.
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            return bad4(mapped)
        if isinstance(ip, ipaddress.IPv4Address):
            return bad4(ip)
        return ip.is_loopback or ip.is_unspecified or ip.is_link_local
    except ValueError:
        pass
    # WHATWG numeric IPv4 (what ipaddress refuses but browsers accept):
    # 1-4 dot parts, each decimal, hex (0x...) or octal (leading 0), the
    # last part spanning the remaining bytes. Anything else is a real DNS
    # name and passes.
    parts = host.split(".")
    if not (1 <= len(parts) <= 4):
        return False
    nums = []
    for p in parts:
        if not p:
            return False
        try:
            if p[:2] in ("0x", "0X"):
                n = int(p, 16)
            elif len(p) > 1 and p[0] == "0":
                n = int(p, 8)
            else:
                n = int(p, 10)
        except ValueError:
            return False
        nums.append(n)
    if any(n < 0 for n in nums) or any(n > 255 for n in nums[:-1]):
        return False
    if nums[-1] >= (1 << (8 * (4 - len(nums) + 1))):
        return False
    addr = nums[-1] + sum(n << (8 * (3 - i)) for i, n in enumerate(nums[:-1]))
    return bad4(ipaddress.IPv4Address(addr))


def bad_src(src, relative_ok):
    """The iframe sandbox premise (§4) is that embedded content is
    CROSS-origin: a same-origin embed with allow-same-origin would be a
    textbook sandbox escape into this server's origin. So loopback hosts and
    non-http(s) schemes are refused at /cmd time, before anything renders.
    Images get the same rule for their absolute URLs (an img src is still an
    agent-controlled GET), but repo-relative paths are their one exception --
    that is how a face asset gets on a card."""
    if not isinstance(src, str) or not src.strip():
        return '"src" must be a non-empty string'
    u = urllib.parse.urlsplit(src)
    if not u.scheme and not u.netloc:
        return None if relative_ok else (
            "an iframe src must be an absolute http(s) URL: a relative one "
            "would be same-origin with this server")
    if u.scheme not in ("http", "https"):
        return ('src must be http or https, not "%s"'
                % (u.scheme or "(no scheme)"))
    if _points_at_local((u.hostname or "").strip().lower()):
        return ("src must not point back at this machine or its network: "
                "loopback, private and link-local hosts are refused")
    return None


# --------------------------- the player (§YouTube) ---------------------------
# The one place the glass reaches the public internet. No API key and no
# account: the search reads the same public results page a logged-out browser
# gets, and the card plays through a normal cross-origin YouTube embed. That
# buys "play me a song" with zero setup; it does NOT buy the user's library,
# their likes, or their Premium ad-free stream, and it never will from here --
# those need an OAuth flow this server deliberately does not have.
YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YT_LIST = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
YT_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TRACK_KEYS = {"id", "title", "artist", "dur"}


def bad_tracks(v):
    """Agent-supplied tracks land in the page's DOM and in an embed URL, so
    every id is charset-checked here rather than trusted downstream."""
    if not isinstance(v, list) or not v:
        return '"tracks" must be a non-empty list of {"id", ...} objects'
    if len(v) > 200:
        return '"tracks" takes at most 200 entries'
    for t in v:
        if not isinstance(t, dict):
            return 'each track must be an object with an "id"'
        if not (isinstance(t.get("id"), str) and YT_ID.match(t["id"])):
            return ('each track needs "id": an 11-character YouTube video id '
                    '(the v= part of a URL); got %r' % (t.get("id"),))
        for k in ("title", "artist", "dur"):
            if k in t and (not isinstance(t[k], str) or has_ctl(t[k])):
                return '"%s" on a track must be plain text' % k
        extra = sorted(set(t) - TRACK_KEYS)
        if extra:
            return ("a track takes %s -- not %s"
                    % (", ".join(sorted(TRACK_KEYS)), ", ".join(extra)))
    return None


def _yt_walk(node, out):
    """Collect every videoRenderer in ytInitialData wherever it nests. A
    recursive sweep, not a fixed path: YouTube reshuffles that tree often,
    and a path walk would break on a layout change a sweep sails through."""
    if isinstance(node, dict):
        vr = node.get("videoRenderer")
        if isinstance(vr, dict) and isinstance(vr.get("videoId"), str):
            out.append(vr)
        for v in node.values():
            _yt_walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _yt_walk(v, out)


def _yt_text(d):
    """YouTube writes text as either {"simpleText"} or {"runs":[...]}."""
    if not isinstance(d, dict):
        return ""
    if isinstance(d.get("simpleText"), str):
        return d["simpleText"]
    r = d.get("runs")
    if isinstance(r, list):
        return "".join(x.get("text", "") for x in r if isinstance(x, dict))
    return ""


# Repeating a search must not repeat the fetch: "play that again" is the
# most common second command there is.
@functools.lru_cache(maxsize=128)
def yt_search(q):
    """Resolve a phrase to YouTube tracks. Returns a JSON string (lru_cache
    wants a hashable, and a list is not one). Raises on a network failure --
    resolve_player() owns the refusal wording."""
    url = ("https://www.youtube.com/results?search_query="
           + urllib.parse.quote(q))
    req = urllib.request.Request(url, headers={
        "User-Agent": YT_UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=10) as r:
        html = r.read(4_000_000).decode("utf-8", "replace")
    m = re.search(r"ytInitialData\s*=\s*(\{.*?\})\s*;\s*</script>", html, re.S)
    if not m:
        return "[]"
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return "[]"
    vids = []
    _yt_walk(data, vids)
    # "<artist> - Topic" channels are the auto-generated music uploads that
    # YouTube Music itself serves, so they sort ahead of everything else: a
    # song request lands on the track, not on a live cover or a reaction.
    music, other, seen = [], [], set()
    for v in vids:
        vid = v["videoId"]
        if not YT_ID.match(vid) or vid in seen:
            continue
        seen.add(vid)
        who = _yt_text(v.get("ownerText")) or _yt_text(v.get("longBylineText"))
        t = {"id": vid, "title": _yt_text(v.get("title"))[:120],
             "artist": re.sub(r"\s*-\s*Topic$", "", who)[:80],
             "dur": _yt_text(v.get("lengthText"))[:12]}
        (music if who.endswith("- Topic") else other).append(t)
    return json.dumps(music + other)


def resolve_player(obj):
    """Turn a player show's "q" into "tracks" by asking YouTube.

    Runs BEFORE the model lock is taken: this is the only blocking network
    call in the whole server, and the faces' 8 Hz /state poll must never
    queue behind it. Edits obj in place; returns an error reply or None."""
    q = obj.get("q")
    if q is None:
        return None
    if not isinstance(q, str) or not q.strip():
        return _err("bad_field", '"q" must be a non-empty search phrase',
                    field="q")
    if "tracks" in obj or "playlist" in obj:
        return _err("bad_field",
                    'player takes "q" or "tracks" or "playlist" -- one of '
                    'them, not several')
    n = obj.pop("count", 1)
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 50:
        return _err("bad_field", '"count" must be a whole number 1-50',
                    field="count")
    try:
        found = json.loads(yt_search(q.strip()[:200]))
    except Exception as e:
        return _err("search_failed",
                    "could not reach YouTube to search for %r: %s" % (q, e))
    if not found:
        return _err("no_results",
                    "YouTube returned nothing for %r -- try different words"
                    % q)
    obj.pop("q")
    obj["tracks"] = found[:n]
    obj.setdefault("title", q.strip()[:80])
    return None


def has_ctl(s):
    """C0 controls and DEL have no place in a title or an id: they exist
    only to forge extra rows or inject escape sequences into the terminal
    readout glass-state.sh prints, the exact surface the agent trusts."""
    return any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in s)


def check_fields(t, fields):
    """Shape-check the content fields present in `fields` (a show body or an
    update patch). Returns a plain-English error line, or None."""
    for k, v in fields.items():
        if k in STR_FIELDS and not isinstance(v, str):
            return '"%s" must be a string' % k
        if k in NUM_FIELDS and (isinstance(v, bool)
                                or not isinstance(v, (int, float))):
            return '"%s" must be a number' % k
    if fields.get("view") not in (None, "month", "week"):
        return '"view" must be "month" or "week"'
    if "seconds" in fields and fields["seconds"] <= 0:
        return ('that timer is already over: "seconds" must be greater '
                'than zero')
    if "events" in fields:
        ev = fields["events"]
        if not isinstance(ev, list) or not all(
                isinstance(x, dict) and isinstance(x.get("date"), str)
                and isinstance(x.get("label"), str) for x in ev):
            return ('"events" must be a list of {"date", "label", optional '
                    '"time"} objects')
    if "items" in fields:
        li = fields["items"]
        if not isinstance(li, list) or not all(
                isinstance(x, dict) and isinstance(x.get("text"), str)
                for x in li):
            return '"items" must be a list of {"text", optional "done"} objects'
    if "src" in fields:
        return bad_src(fields["src"], relative_ok=(t == "image"))
    if "tracks" in fields:
        return bad_tracks(fields["tracks"])
    if "playlist" in fields and not YT_LIST.match(fields["playlist"]):
        return ('"playlist" must be a YouTube playlist id (the list= part '
                'of a YouTube URL), not a whole URL')
    if fields.get("mode") not in (None, "audio", "video"):
        return '"mode" must be "audio" or "video"'
    if "count" in fields and not (1 <= fields["count"] <= 50):
        return '"count" must be between 1 and 50'
    return None


def needs_err(t, fields, creating):
    """Required-field alternatives (§4). On a show every alternative counts;
    an update only needs the mixing check, since the item was born valid."""
    alts = TYPE_SPECS[t]["needs"]
    if len(alts) > 1:
        touched = [a for a in alts if any(f in fields for f in a)]
        if len(touched) > 1:
            return ("%s takes %s -- one of them, not both"
                    % (t, " or ".join("/".join(a) for a in alts)))
    if not creating:
        return None
    if not any(all(f in fields for f in a) for a in alts):
        want = " or ".join(" + ".join('"%s"' % f for f in a) for a in alts)
        return "%s needs %s" % (t, want)
    return None


def timer_remaining(content):
    """Seconds until the timer's end, from "seconds" or "until". A timer that
    is already over is a refused show: the agent almost certainly mis-built
    the payload, and rendering a dead countdown helps no one."""
    if "seconds" in content:
        return float(content["seconds"]), None
    s = content.get("until", "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None, ('"until" needs a time of day, not just a date '
                      '(e.g. "2026-08-31T18:00:00+02:00")')
    try:
        # fromisoformat cannot take a trailing Z before 3.11; an offset-less
        # value means the server's local time, exactly what timestamp() does
        # with a naive datetime.
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None, '"until" is not an ISO 8601 date-time'
    remaining = dt.timestamp() - time.time()
    if remaining <= 0:
        return None, 'that "until" is already in the past'
    return remaining, None


def face_reserve_from_disk(face_id):
    """The reserve rects a face declares in its face.json ("reserve": one
    rect or a list). Missing face, missing key, or unreadable JSON all fall
    back to the default center block -- a face must never be able to break
    placement by shipping a bad manifest."""
    if not isinstance(face_id, str) \
            or not re.fullmatch(r"[A-Za-z0-9_-]+", face_id):
        return DEFAULT_RESERVE
    try:
        meta = json.loads((HERE / "faces" / face_id / "face.json").read_text())
    except (OSError, ValueError):
        return DEFAULT_RESERVE
    r = meta.get("reserve")
    if isinstance(r, dict):     # a single object is normalized to a list
        r = [r]
    if not isinstance(r, list):
        return DEFAULT_RESERVE
    return r


class GlassModel:
    """The in-memory glass. Callers use handle() / payload() / note_viewer();
    everything else assumes the lock is already held (one lock, taken once
    per request, never re-entered)."""

    def __init__(self, state_path, reserve_for=None, default_face=None,
                 clock=time.monotonic, chat_rect="cfg"):
        self.state_path = Path(state_path)
        self.reserve_for = reserve_for or face_reserve_from_disk
        # The conversation crawl's home. "cfg" late-binds to the config
        # (the live server); --selfcheck injects None so its placement
        # worlds stay exactly the reserves each test declares.
        self.chat_rect = chat_rect
        # Late-bound so a config edit + restart is the only thing that can
        # change it, but --selfcheck can inject its own.
        self.default_face = default_face or (lambda: CFG.get("face", ""))
        # monotonic, never wall time: expiry must be immune to clock jumps.
        self.clock = clock
        self.lock = threading.Lock()
        self.items = {}       # id -> item; insertion order IS age order,
                              # and a replace keeps its slot in that order
        self.counters = {}    # type -> last issued number, persisted
        self.viewers_map = {} # face id -> last /state?face= poll (monotonic)
        self.rev = 0
        self._persist_failed = False   # gates _persist's one-line complaint
        self._load()

    # -- public entry points (take the lock) --------------------------------

    def handle(self, obj):
        # The player's search talks to YouTube, so it happens out here with
        # NO lock held -- see resolve_player(). A refusal from it is carried
        # inside and returned like any other, so it still gets "viewers".
        pre = None
        if obj.get("a") == "show" and obj.get("type") == "player":
            pre = resolve_player(obj)
        with self.lock:
            self._prune()
            a = obj.get("a")
            if pre is not None:
                reply = pre
            elif a == "show":
                reply = self._show(obj)
            elif a == "update":
                reply = self._update(obj)
            elif a == "move":
                reply = self._move(obj)
            elif a == "pin":
                reply = self._setpin(obj, True)
            elif a == "unpin":
                reply = self._setpin(obj, False)
            elif a == "dismiss":
                reply = self._dismiss(obj)
            elif a == "clear":
                reply = self._clear(obj)
            elif a == "state":
                reply = self._state(obj)
            else:
                reply = _err("unknown_verb",
                             '"a" must be one of: %s' % ", ".join(VERBS))
            # Every reply carries viewers so the agent knows when it is
            # narrating to an empty room (0 = nobody has a glass open).
            reply["viewers"] = self._viewers()
            if a == "state":
                # The state verb also names WHICH faces are looking, so
                # glass-state.sh can print "faces: board" and the agent
                # knows whose reserve shapes the free map.
                now = self.clock()
                reply["faces"] = sorted(
                    f for f, t in self.viewers_map.items()
                    if now - t <= VIEWER_WINDOW_S)
            return reply

    def payload(self):
        """The §3.5 "glass" object /state embeds. Pruning here too: the 8 Hz
        poll is what makes lazy expiry land within ~125 ms of anyone being
        able to observe it, no background thread needed."""
        with self.lock:
            self._prune()
            return self._payload(self._effective_reserve())

    def note_viewer(self, face_id):
        with self.lock:
            now = self.clock()
            self.viewers_map[face_id] = now
            # Opportunistic sweep so a stream of one-off ids cannot grow the
            # map without bound.
            for f in [f for f, t in self.viewers_map.items()
                      if now - t > VIEWER_WINDOW_S]:
                del self.viewers_map[f]

    # -- lifecycle ----------------------------------------------------------

    def _prune(self):
        now = self.clock()
        dead = [i for i, it in self.items.items()
                if it["_expires_at"] is not None and it["_expires_at"] <= now]
        for i in dead:
            del self.items[i]
        if dead:
            # Pruning is a model change like any other: the pages must see
            # the rev move or the exit animation never plays.
            self.rev += 1

    def _left(self, it):
        if it["_expires_at"] is None:
            return None
        return max(0.0, it["_expires_at"] - self.clock())

    def _arm(self, it):
        """(Re)start the countdown from the item's own effective ttl: the
        value last set on it (or the default), stretched by the timer
        invariant -- an item whose content encodes its own end time never
        expires before that time."""
        if it["pin"]:
            it["_expires_at"] = None
            return
        eff = it["_ttl"]
        if it["_ends_at"] is not None:
            eff = max(eff, max(0.0, it["_ends_at"] - self.clock())
                      + TIMER_GRACE)
        it["_expires_at"] = self.clock() + eff

    # -- geometry -----------------------------------------------------------

    def _effective_reserve(self):
        """Union of the reserves of every face seen in the last 3 s: with
        two tabs open the placer stays clear of both, deterministically.
        Nobody polling (fresh boot, headless, selfcheck) falls back to the
        configured default face -- the one a newly opened browser will show
        -- never the union of all installed faces."""
        now = self.clock()
        live = sorted(f for f, t in self.viewers_map.items()
                      if now - t <= VIEWER_WINDOW_S)
        rects, seen = [], set()
        for f in (live or [self.default_face()]):
            for d in self.reserve_for(f):
                rect = parse_rect(d)
                if rect and rect not in seen:
                    seen.add(rect)
                    rects.append(rect)
        # The conversation crawl's home is reserved the same way the
        # face is: auto-placement never covers the dialogue, explicit
        # placement may (over_reserve), and whatever does is temporary
        # by the glass's own lifecycle.
        chat = self.chat_rect
        if chat == "cfg":
            chat = (CFG.get("chat_area") or {}) if CFG.get("chat", True) \
                else None
        if chat:
            rect = parse_rect(chat) if isinstance(chat, dict) else chat
            if rect and rect not in seen:
                rects.append(rect)
        return rects

    def _viewers(self):
        now = self.clock()
        return sum(1 for t in self.viewers_map.values()
                   if now - t <= VIEWER_WINDOW_S)

    def _fits(self, box, reserve, ignore=None):
        c, r, w, h = box
        if c + w > GRID_COLS or r + h > GRID_ROWS:
            return False
        if any(overlap(box, rv) for rv in reserve):
            return False
        return not any(overlap(box, item_rect(o))
                       for o in self.items.values() if o["id"] != ignore)

    def _scan(self, span, ignore=None, reserve=None):
        if reserve is None:
            reserve = self._effective_reserve()
        w, h = span
        for c in SCAN_COLS:
            for r in range(GRID_ROWS):
                if self._fits((c, r, w, h), reserve, ignore):
                    return (c, r)
        return None

    def _occ(self, reserve):
        """8 strings of 12 chars: '.' free, '#' effective reserve, a
        lowercase letter per item in listing order. Items paint over the
        reserve marks -- an over_reserve item's letter is the useful fact."""
        grid = [["."] * GRID_COLS for _ in range(GRID_ROWS)]
        for (c, r, w, h) in reserve:
            for rr in range(r, r + h):
                for cc in range(c, c + w):
                    grid[rr][cc] = "#"
        for n, it in enumerate(self.items.values()):
            letter = chr(ord("a") + n % 26)
            c, r, w, h = item_rect(it)
            for rr in range(r, r + h):
                for cc in range(c, c + w):
                    grid[rr][cc] = letter
        return ["".join(row) for row in grid]

    def _free(self, reserve):
        out = {}
        for (w, h) in FREE_PROBES:
            a = self._scan((w, h), reserve=reserve)
            out["%dx%d" % (w, h)] = cell_name(*a) if a else None
        return out

    def _board(self):
        """What every placement refusal carries: the agent always learns
        what actually happened and what would fit instead."""
        reserve = self._effective_reserve()
        return {"map": self._occ(reserve), "free": self._free(reserve)}

    def _fit_refusal(self, c, r, w, h, ignore=None):
        """-> (refusal | None, over_reserve). Never a silent re-place,
        clamp, or shrink. The reserve does NOT refuse here: explicit
        geometry only arrives when the person asked for it, and the
        grid is the only law — covering part of the face on request is
        allowed, flagged over_reserve so the agent can say so. Items
        still never stack."""
        if c + w > GRID_COLS or r + h > GRID_ROWS:
            return _err("out_of_bounds",
                        "%s with span %dx%d runs past column L or row 8; "
                        "nothing was clamped or wrapped" % (cell_name(c, r),
                                                            w, h),
                        bounds={"cell": cell_name(c, r), "span": [w, h]},
                        **self._board()), False
        box = (c, r, w, h)
        by = [it["id"] for it in self.items.values()
              if it["id"] != ignore and overlap(box, item_rect(it))]
        if by:
            return _err("occupied", "that spot is occupied by %s"
                        % ", ".join(by), by=by, **self._board()), False
        over = any(overlap(box, rv) for rv in self._effective_reserve())
        return None, over

    def _no_room(self, w, h, exclude=None):
        cands = [it["id"] for it in self.items.values()
                 if not it["pin"] and it["id"] != exclude][:3]
        return _err("no_room",
                    "no free %dx%d slot; dismiss something (candidates are "
                    "oldest first) or pass a smaller span, then retry"
                    % (w, h),
                    dismiss_candidates=cands, **self._board())

    # -- verbs (lock held) --------------------------------------------------

    def _show(self, obj):
        t = obj.get("type")
        if t not in TYPE_SPECS:
            return _err("unknown_type", "unknown type %r; valid types: %s"
                        % (t, ", ".join(sorted(TYPE_SPECS))))
        newf, e = coerce_bool(obj, "new")
        if e:
            return _err("bad_field", e, field="new")
        pinf, e = coerce_bool(obj, "pin")
        if e:
            return _err("bad_field", e, field="pin")
        cr = sp = None
        if "cell" in obj:
            cr = parse_cell(obj["cell"])
            if cr is None:
                return _err("bad_field", 'cell must be a column A-L plus a '
                            'row 1-8, like "J4"', field="cell")
        if "span" in obj:
            sp = parse_span(obj["span"])
            if sp is None:
                return _err("bad_field", "span must be [width, height], two "
                            "whole numbers >= 1", field="span")
        ttl, e = check_ttl(obj)
        if e:
            return _err("bad_field", e, field="ttl")
        if "title" in obj and not isinstance(obj["title"], str):
            return _err("bad_field", '"title" must be a string', field="title")
        if "title" in obj and has_ctl(obj["title"]):
            return _err("bad_field",
                        '"title" must not contain control characters',
                        field="title")
        if "id" in obj and (not isinstance(obj["id"], str) or not obj["id"]):
            return _err("bad_field", '"id" must be a non-empty string',
                        field="id")
        if "id" in obj and has_ctl(obj["id"]):
            return _err("bad_field",
                        '"id" must not contain control characters',
                        field="id")
        content = {k: v for k, v in obj.items() if k not in SHOW_KEYS}
        unknown = sorted(set(content) - TYPE_SPECS[t]["fields"])
        if unknown:
            return _err("unknown_field",
                        "%s does not take %s; its fields are: %s"
                        % (t, ", ".join(unknown),
                           ", ".join(sorted(TYPE_SPECS[t]["fields"]))),
                        fields=unknown)
        e = needs_err(t, content, creating=True)
        if e:
            return _err("bad_field", e)
        e = check_fields(t, content)
        if e:
            return _err("bad_field", e)
        remaining = None
        if t == "timer":
            remaining, e = timer_remaining(content)
            if e:
                return _err("bad_field", e)

        # Targeting: singleton per type by default, so "actually, show Rome
        # instead" is just another show -- no dismiss+show dance, no
        # server-assigned slug to remember across a topic shift.
        target = None
        if "id" in obj:
            target = self.items.get(obj["id"])
        elif not newf:
            same = [it for it in self.items.values() if it["type"] == t]
            if len(same) == 1:
                target = same[0]
            elif len(same) > 1:
                # The server never guesses which card to destroy.
                return _err("ambiguous",
                            'there are %d %s cards up; pass "id" (or "new": '
                            'true) to say which' % (len(same), t),
                            ids=[it["id"] for it in same])

        if sp is None:
            sp = tuple(target["span"]) if target else TYPE_SPECS[t]["span"]
        w, h = sp
        if cr is None and target is not None:
            cr = parse_cell(target["cell"])
        over = False
        if cr is not None:
            c, r = cr
            # An unchanged footprint on a replace skips only the REFUSAL
            # check: an in-place replace must not self-collide, and must
            # keep working even when a face switch has left the item over
            # the new reserve (never evicted -- §2). over_reserve is
            # computed unconditionally, though: §2 promises the REPLY says
            # the card covers the face, not just the /state flags.
            if target is None or (c, r, w, h) != item_rect(target):
                ref, _ = self._fit_refusal(c, r, w, h,
                                           ignore=target["id"] if target
                                           else None)
                if ref:
                    return ref
            over = any(overlap((c, r, w, h), rv)
                       for rv in self._effective_reserve())
        else:
            anchor = self._scan((w, h))
            if anchor is None:
                return self._no_room(w, h)
            c, r = anchor

        if target is not None:
            iid, rev, replaced = target["id"], target["rev"] + 1, True
        elif "id" in obj:
            iid, rev, replaced = obj["id"], 1, False
            # Keep the auto counters ahead of hand-picked ids so they can
            # never alias one later -- for ANY type's id shape, not just
            # the shown type's: a note named "map-2" must still spend the
            # map counter's 2, or a later auto map would silently clobber
            # the note (and, once it was dismissed, reissue "map-2" to a
            # different item a slow-polling page could body-swap).
            for t2 in TYPE_SPECS:
                m = re.fullmatch(re.escape(t2) + r"-(\d+)", iid)
                if m:
                    self.counters[t2] = max(self.counters.get(t2, 0),
                                            int(m.group(1)))
                    break
        else:
            # Auto-issue skips any live id as a hard guarantee: no path may
            # hand out an id an existing item holds. The loop terminates --
            # items are bounded by the 12x8 grid.
            n = self.counters.get(t, 0)
            while True:
                n += 1
                iid = "%s-%d" % (t, n)
                if iid not in self.items:
                    break
            self.counters[t] = n
            rev, replaced = 1, False

        pin = pinf if "pin" in obj else (target["pin"] if target else False)
        base_ttl = ttl if ttl is not None else (target["_ttl"] if target
                                                else DEFAULT_TTL)
        title = obj.get("title")
        if title is None:
            # The chrome must never be blank where it can be derived: a map
            # titles itself with its query, a timer with its label (§3.5's
            # worked payload shows the map's q as its title).
            if t == "map":
                title = content.get("q") or "%s,%s" % (content.get("lat"),
                                                       content.get("lon"))
            elif t == "timer":
                title = content.get("label", "")
            else:
                title = ""

        item = dict(content)
        item.update({"id": iid, "type": t, "title": title,
                     "cell": cell_name(c, r), "span": [w, h],
                     "pin": bool(pin), "rev": rev,
                     "_ttl": float(base_ttl), "_ends_at": None,
                     "_expires_at": None})
        if t == "timer":
            item["_ends_at"] = self.clock() + remaining
        self._arm(item)
        self.items[iid] = item
        self.rev += 1
        self._persist()
        reply = {"ok": True, "id": iid, "replaced": replaced,
                 "cell": item["cell"], "span": item["span"],
                 "expires_in": sec(self._left(item))}
        if over:
            reply["over_reserve"] = True
        return reply

    def _target(self, obj):
        iid = obj.get("id")
        if not isinstance(iid, str) or not iid:
            return None, _err("bad_field", '"id" (a string) is required',
                              field="id")
        it = self.items.get(iid)
        if it is None:
            return None, _err("not_found", "no item %r on the glass" % iid,
                              ids=sorted(self.items))
        return it, None

    def _update(self, obj):
        it, ref = self._target(obj)
        if ref:
            return ref
        immutable = sorted({"type", "cell", "span", "pin", "new"} & set(obj))
        if immutable:
            return _err("bad_field",
                        "update is content-only and may not touch %s; use "
                        "show (replace), move, or pin/unpin"
                        % ", ".join(immutable), fields=immutable)
        t = it["type"]
        allowed = TYPE_SPECS[t]["fields"]
        patch = {k: v for k, v in obj.items()
                 if k not in ("a", "id", "ttl", "title")}
        unknown = sorted(set(patch) - allowed)
        if unknown:
            return _err("unknown_field",
                        "%s content fields are: %s"
                        % (t, ", ".join(sorted(allowed))), fields=unknown)
        if not patch and "ttl" not in obj and "title" not in obj:
            return _err("bad_field", "that update carries nothing to change")
        ttl, e = check_ttl(obj)
        if e:
            return _err("bad_field", e, field="ttl")
        if "title" in obj and not isinstance(obj["title"], str):
            return _err("bad_field", '"title" must be a string', field="title")
        if "title" in obj and has_ctl(obj["title"]):
            return _err("bad_field",
                        '"title" must not contain control characters',
                        field="title")
        e = needs_err(t, patch, creating=False)
        if e:
            return _err("bad_field", e)
        e = check_fields(t, patch)
        if e:
            return _err("bad_field", e)
        remaining = None
        if t == "timer" and ("until" in patch or "seconds" in patch):
            remaining, e = timer_remaining(patch)
            if e:
                return _err("bad_field", e)
        if t == "map" and ("lat" in patch or "lon" in patch):
            if (patch.get("lat", it.get("lat")) is None
                    or patch.get("lon", it.get("lon")) is None):
                return _err("bad_field",
                            'a map by coordinates needs both "lat" and "lon"')
        # Every check passed; only now does anything change ("an error ...
        # changes nothing").
        it.update(patch)
        if t == "map":
            # q and lat/lon are alternatives, so switching to one clears the
            # other -- an update never leaves a card with two subjects.
            if "q" in patch:
                it.pop("lat", None)
                it.pop("lon", None)
            elif "lat" in patch or "lon" in patch:
                it.pop("q", None)
        if remaining is not None:
            it["_ends_at"] = self.clock() + remaining
            it.pop("seconds" if "until" in patch else "until", None)
        if ttl is not None:
            it["_ttl"] = ttl
        if "title" in obj:
            it["title"] = obj["title"]
        it["rev"] += 1      # content changed: the page rebuilds this body
        self._arm(it)       # update resets the clock to the effective ttl
        self.rev += 1
        self._persist()
        return {"ok": True, "id": it["id"], "rev": it["rev"],
                "expires_in": sec(self._left(it))}

    def _move(self, obj):
        it, ref = self._target(obj)
        if ref:
            return ref
        unknown = sorted(set(obj) - {"a", "id", "cell", "span"})
        if unknown:
            return _err("unknown_field",
                        "move takes cell and/or span, nothing else",
                        fields=unknown)
        if "cell" not in obj and "span" not in obj:
            return _err("bad_field", "move needs a cell, a span, or both")
        cr = None
        if "cell" in obj:
            cr = parse_cell(obj["cell"])
            if cr is None:
                return _err("bad_field", 'cell must be a column A-L plus a '
                            'row 1-8, like "J4"', field="cell")
        if "span" in obj:
            sp = parse_span(obj["span"])
            if sp is None:
                return _err("bad_field", "span must be [width, height], two "
                            "whole numbers >= 1", field="span")
        else:
            sp = tuple(it["span"])
        w, h = sp
        over = False
        if cr is not None:
            # Explicit cell: items never stack, bounds never bend, but
            # the reserve is the person's to override (over flagged).
            c, r = cr
            ref, over = self._fit_refusal(c, r, w, h, ignore=it["id"])
            if ref:
                return ref
        else:
            # span-only is the "enlarge it / shrink it" verb: resize at the
            # current anchor, and when the grown footprint no longer fits
            # there, re-anchor via auto-placement instead of refusing -- the
            # person asked for a size, not a position. A size the rails
            # cannot hold at all goes over the face reserve rather than
            # being refused: the grid is the only law (§2), and the reply
            # says over_reserve so the agent can mention it.
            c, r = parse_cell(it["cell"])
            reserve = self._effective_reserve()
            cur_over = any(overlap(item_rect(it), rv) for rv in reserve)
            if cur_over and self._fits((c, r, w, h), [], ignore=it["id"]):
                # Grandfather: this anchor already sits over the reserve
                # (placed there on request, or stranded by a face switch --
                # either way never auto-moved, §2), so a resize keeps it
                # rather than teleporting the card to a reserve-clear spot.
                # The flag stays honest: computed from the NEW footprint,
                # so a shrink that clears the reserve drops it.
                over = any(overlap((c, r, w, h), rv) for rv in reserve)
            elif not self._fits((c, r, w, h), reserve, ignore=it["id"]):
                anchor = self._scan((w, h), ignore=it["id"], reserve=reserve)
                if anchor is None:
                    # ignore the reserve: current anchor first, then scan
                    if self._fits((c, r, w, h), [], ignore=it["id"]):
                        over = True
                    else:
                        anchor = self._scan((w, h), ignore=it["id"],
                                            reserve=[])
                        if anchor is None:
                            return self._no_room(w, h, exclude=it["id"])
                        c, r = anchor
                        over = True
                else:
                    c, r = anchor
        it["cell"], it["span"] = cell_name(c, r), [w, h]
        self._arm(it)       # move resets the clock like update does
        self.rev += 1       # but item rev is untouched: a cell/span change
        self._persist()     # drives the FLIP move, not a body rebuild
        reply = {"ok": True, "id": it["id"], "cell": it["cell"],
                 "span": it["span"], "expires_in": sec(self._left(it))}
        if over:
            reply["over_reserve"] = True
        return reply

    def _setpin(self, obj, flag):
        it, ref = self._target(obj)
        if ref:
            return ref
        unknown = sorted(set(obj) - {"a", "id"})
        if unknown:
            return _err("unknown_field", "pin/unpin take only an id",
                        fields=unknown)
        it["pin"] = flag
        # pin clears the countdown; unpin restarts the item's own effective
        # ttl, however long it sat pinned.
        self._arm(it)
        self.rev += 1
        self._persist()
        return {"ok": True, "id": it["id"], "pin": flag,
                "expires_in": sec(self._left(it))}

    def _dismiss(self, obj):
        unknown = sorted(set(obj) - {"a", "id", "ids"})
        if unknown:
            return _err("unknown_field", 'dismiss takes "id" or "ids"',
                        fields=unknown)
        if ("id" in obj) == ("ids" in obj):
            return _err("bad_field",
                        'dismiss takes exactly one of "id" or "ids"')
        ids = [obj["id"]] if "id" in obj else obj["ids"]
        if not isinstance(ids, list) or not ids \
                or not all(isinstance(i, str) for i in ids):
            return _err("bad_field",
                        '"ids" must be a non-empty list of item ids')
        # A duplicated id is one dismissal, not two: without the dedupe the
        # second del raised KeyError AFTER the first had already mutated the
        # model, so the item was gone but rev never moved -- pages kept
        # rendering a card that no longer existed (§3.5 rule 1).
        ids = list(dict.fromkeys(ids))
        missing = sorted(set(i for i in ids if i not in self.items))
        if missing:
            # All or nothing: a partial dismiss would leave the agent's
            # picture of the glass wrong.
            return _err("not_found", "not on the glass: %s"
                        % ", ".join(missing), ids=sorted(self.items))
        for i in ids:
            del self.items[i]
        self.rev += 1
        self._persist()
        return {"ok": True, "dismissed": ids}

    def _clear(self, obj):
        unknown = sorted(set(obj) - {"a", "include_pinned"})
        if unknown:
            return _err("unknown_field",
                        'clear takes only "include_pinned"', fields=unknown)
        inc, e = coerce_bool(obj, "include_pinned")
        if e:
            return _err("bad_field", e, field="include_pinned")
        cleared = [i for i, it in self.items.items()
                   if inc or not it["pin"]]
        kept = [i for i in self.items if i not in set(cleared)]
        for i in cleared:
            del self.items[i]
        if cleared:
            self.rev += 1
            self._persist()
        return {"ok": True, "cleared": cleared, "kept_pinned": kept}

    def _state(self, obj):
        reserve = self._effective_reserve()
        return {"ok": True, "glass": self._payload(reserve),
                "map": self._occ(reserve), "free": self._free(reserve)}

    # -- serialization ------------------------------------------------------

    def _payload(self, reserve):
        return {"rev": self.rev,
                "reserve": [{"cell": cell_name(c, r), "span": [w, h]}
                            for (c, r, w, h) in reserve],
                "items": [self._public(it, reserve)
                          for it in self.items.values()]}

    def _public(self, it, reserve):
        out = {k: v for k, v in it.items() if not k.startswith("_")}
        # Always relative seconds, computed at serialization: no epoch
        # timestamps, no client clock math, and a second-browser glass
        # stays correct for free.
        out["expires_in"] = sec(self._left(it))
        out["flags"] = (["over_reserve"]
                        if any(overlap(item_rect(it), rv) for rv in reserve)
                        else [])
        if it["_ends_at"] is not None:
            out["ends_in"] = sec(max(0.0, it["_ends_at"] - self.clock()))
        return out

    # -- persistence (pinned items + id counters only) ----------------------

    def _persist(self):
        pinned = []
        for it in self.items.values():
            if not it["pin"]:
                continue
            rec = {k: v for k, v in it.items() if not k.startswith("_")}
            rec["ttl_base"] = it["_ttl"]
            if it["_ends_at"] is not None:
                # Monotonic deadlines die with the process; wall time is the
                # only clock that can carry a pinned timer across a restart.
                rec["ends_wall"] = time.time() + (it["_ends_at"]
                                                  - self.clock())
            pinned.append(rec)
        tmp = Path(str(self.state_path) + ".tmp")
        # Every verb mutates the model FIRST and mirrors to disk second, so
        # a failed write must not turn an applied change into a 500 that
        # glass.sh misreads as a stranger on the port: the in-memory model
        # is authoritative, and the next successful write rewrites the whole
        # file from it. Only OSError is tolerated -- a json.dumps TypeError
        # would be a real bug that should still surface. The complaint is
        # gated so a persistently broken disk speaks once, not once per
        # agent command.
        try:
            tmp.write_text(json.dumps({"counters": self.counters,
                                       "pinned": pinned}, indent=1))
            os.replace(tmp, self.state_path)
        except OSError as e:
            tmp.unlink(missing_ok=True)   # no half-written .tmp droppings
            if not self._persist_failed:
                self._persist_failed = True
                print("[glass] could not write %s (%s); the glass keeps "
                      "running in memory and will retry on the next change"
                      % (self.state_path.name, e))
            return
        if self._persist_failed:
            self._persist_failed = False
            print("[glass] %s is writable again" % self.state_path.name)

    def _load(self):
        try:
            raw = self.state_path.read_text()
        except OSError:
            return      # first boot: nothing to restore, nothing to say
        try:
            data = json.loads(raw)
            self.counters = {str(k): int(v)
                             for k, v in data.get("counters", {}).items()}
            for rec in data.get("pinned", []):
                it = {k: v for k, v in rec.items()
                      if k not in ("ttl_base", "ends_wall")}
                # parse_rect, not parse_cell+parse_span: it adds the 12x8
                # bounds check, without which a hand-edited K7 [3,3] record
                # loads fine and then every _occ (state verb, every refusal
                # board) dies with IndexError while GET /state keeps
                # working -- a wedged server that looks alive.
                if (not isinstance(it.get("id"), str)
                        or it.get("type") not in TYPE_SPECS
                        or parse_rect(it) is None):
                    raise ValueError("bad pinned record")
                # rev feeds the singleton-replace path (target["rev"] + 1),
                # so a missing or damaged one is repaired, not fatal:
                # _persist always writes it, so only a hand-edited file can
                # lack it, and one bad field should not discard every other
                # pinned record. bool is excluded the way parse_span does.
                rv = rec.get("rev")
                if not (isinstance(rv, int) and not isinstance(rv, bool)
                        and rv >= 1):
                    it["rev"] = 1
                it["pin"] = True
                it["_ttl"] = float(rec.get("ttl_base", DEFAULT_TTL))
                it["_expires_at"] = None
                it["_ends_at"] = None
                if "ends_wall" in rec:
                    it["_ends_at"] = self.clock() + (float(rec["ends_wall"])
                                                     - time.time())
                self.items[it["id"]] = it
        except Exception as e:
            # Tolerated the way load_config() tolerates a corrupt
            # ai-visualizer.json: one plain-English line, empty glass, and
            # the server always comes up.
            self.items = {}
            self.counters = {}
            print("[glass] %s is unreadable (%s), starting with an empty "
                  "glass" % (self.state_path.name, e))


GLASS = GlassModel(HERE / "glass-state.json")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path, _, query = self.path.partition("?")
        try:
            if not self._host_ok():
                self._send(b"forbidden: unexpected Host header",
                           "text/plain", 403)
            elif path == "/state":
                LAST_POLL[0] = time.time()
                payload = read_bus()
                if CFG.get("glass", True):
                    # Faces report themselves (?face=<id>) because config
                    # alone cannot know which face a browser is showing: the
                    # gallery switches faces client-side, and two faces can
                    # be open in two tabs. The id feeds the viewers count
                    # and the effective-reserve union. Real and --mock paths
                    # both gain the glass object.
                    face = urllib.parse.parse_qs(query).get("face", [""])[0]
                    if face and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", face):
                        GLASS.note_viewer(face)
                    payload["glass"] = GLASS.payload()
                if CFG.get("chat", True):
                    # Cheap rev pointer only; the crawl fetches /chat
                    # when it moves.
                    try:
                        payload["chat_rev"] = json.loads(
                            (BUS / ".voice_chat").read_text()).get("rev", 0)
                    except (OSError, ValueError):
                        pass
                self._send(json.dumps(payload).encode(), "application/json")
            elif path == "/chat":
                try:
                    body = (BUS / ".voice_chat").read_text()
                    json.loads(body)          # never relay a torn write
                except (OSError, ValueError):
                    body = '{"rev": 0, "msgs": []}'
                self._send(body.encode(), "application/json")
            elif path == "/themes.js":
                # The theme loader theme.js pulls in: one document.write
                # per themes/<id>/theme.js, so the theme scripts stay
                # parser-blocking and every theme is registered before a
                # face script reads AVTheme.overrides. Generated per
                # request -- drop a folder into themes/ and it is here on
                # the next reload, with no manifest to keep in step.
                js = "".join(
                    'document.write(\'<script src="/themes/%s/theme.js">'
                    '<\\/script>\');\n' % t for t in list_themes())
                self._send(js.encode(), "text/javascript")
            elif path == "/config":
                out = {"name": CFG["name"], "badge": CFG["badge"],
                       "face": CFG["face"],
                       "thinking_sound": bool(CFG["thinking_sound"]),
                       "glass": bool(CFG["glass"]),
                       "chat": bool(CFG.get("chat", True)),
                       "chat_area": CFG.get("chat_area"),
                       "theme": read_theme(),
                       # The BRAIN tab's key STATUS -- a bool, four
                       # characters, and whether Claude has a login.
                       # It rides /config, not /state, for two reasons:
                       # the picker already fetches /config once at
                       # init (core.js A.init), so it costs no new
                       # request; and /state is polled ~8x/sec, which
                       # would mean eight `security` subprocesses a
                       # second for a value that changes about twice a
                       # year. After POST /brainkey the reply itself
                       # carries the new tail, so the picker never has
                       # to re-fetch to repaint.
                       "brain_status": brain_status(),
                       "orbs": [{"title": o.get("title", "?"),
                                 "kind": o.get("kind", "notes")}
                                for o in CFG.get("orbs", [])],
                       "faces": list_faces()}
                self._send(json.dumps(out).encode(), "application/json")
            elif path == "/tree":
                # a folder's tree, jailed to that orb's configured root.
                # .md only, CLAUDE.md (AI config, not a note) excluded.
                idx = urllib.parse.parse_qs(query).get("orb", ["0"])[0]
                root = orb_root(idx)
                if root is None or not root.is_dir():
                    self._send(json.dumps(
                        {"name": "?", "notes": [], "dirs": []}).encode(),
                        "application/json", 404)
                else:
                    def walk(d):
                        out = {"name": d.name, "notes": [], "dirs": []}
                        for f in sorted(d.iterdir()):
                            if f.name.startswith("."):
                                continue
                            if f.is_dir():
                                sub = walk(f)
                                if sub["notes"] or sub["dirs"]:
                                    out["dirs"].append(sub)
                            elif f.suffix == ".md" and f.name != "CLAUDE.md":
                                # "<orb>/<relpath>" so /note knows the jail
                                out["notes"].append({
                                    "title": f.stem,
                                    "file": f"{int(idx)}/"
                                            f"{f.relative_to(root).as_posix()}",
                                    "size": f.stat().st_size})
                        return out
                    tree = walk(root)
                    tree["name"] = CFG["orbs"][int(idx)].get(
                        "title", tree["name"])
                    self._send(json.dumps(tree).encode(), "application/json")
            elif path == "/note":
                # one note's text: f=<orb>/<relpath>, resolved against that
                # orb's jail. Inside the root, .md only, must exist.
                rel = urllib.parse.parse_qs(query).get("f", [""])[0]
                idx, _, rel = rel.partition("/")
                root = orb_root(idx)
                target = (root / rel).resolve() if root else None
                if target is None or root not in target.parents \
                        or target.suffix != ".md" or not target.is_file():
                    self._send(b"not found", "text/plain", 404)
                else:
                    self._send(target.read_text(encoding="utf-8",
                                                errors="replace").encode(),
                               "text/plain; charset=utf-8")
            else:
                self._static(path)
        except ConnectionError:
            # THE WHOLE FAMILY, not one member of it. A tab closed or
            # reloaded mid-response raises ConnectionResetError, which is a
            # SIBLING of BrokenPipeError rather than a subclass -- so
            # catching only BrokenPipeError sent it to the generic branch
            # below, which then wrote a 500 back down the socket that had
            # just died and raised a SECOND, uncaught error from inside
            # flush_headers(). One disconnect, two tracebacks. ConnectionError
            # is the common parent of Reset, Broken, Aborted and Refused.
            pass
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
            try:
                self._send(body, "application/json", 500)
            except ConnectionError:
                # A real error AND the client already gone. There is nobody
                # left to tell; saying so twice helps no one.
                pass

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if not self._host_ok():
                self._send(b"forbidden: unexpected Host header",
                           "text/plain", 403)
                return
            if path not in ("/cmd", "/pick", "/theme", "/brain",
                            "/brainkey", "/stop", "/say", "/media"):
                self._send(b"not found", "text/plain", 404)
                return
            ctype = (self.headers.get("Content-Type") or "") \
                .split(";")[0].strip().lower()
            if ctype != "application/json":
                # The whole cross-site write defense: a text/plain or form
                # POST is a "simple request" browsers send without asking,
                # so any web page open on this machine could draw on the
                # glass. application/json forces an OPTIONS preflight this
                # server never answers (no do_OPTIONS, no CORS headers), and
                # the POST is never sent. curl is unaffected.
                self._send(b"forbidden: /cmd takes Content-Type: "
                           b"application/json only", "text/plain", 403)
                return
            if path == "/theme":
                # theme.js posts the picked theme here so every display
                # converges on it (same JSON-only defense as /pick).
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 < length <= 4096:
                        raise ValueError("bad length")
                    obj = json.loads(self.rfile.read(length))
                    theme = obj.get("theme") if isinstance(obj, dict) \
                        else None
                except (ValueError, TypeError):
                    theme = None
                if not (isinstance(theme, str) and theme.isalnum()
                        and len(theme) <= 32):
                    self._send(json.dumps(
                        {"ok": False, "error": "bad_theme"}).encode(),
                        "application/json", 200)
                    return
                try:
                    tmp = HERE / ".face_theme.tmp"
                    tmp.write_text(theme)
                    tmp.replace(THEME_FILE)
                    ok = True
                except OSError:
                    ok = False
                self._send(json.dumps({"ok": ok}).encode(),
                           "application/json")
                return
            if path == "/stop":
                # ESC on a face page: drop whatever the voice line is
                # doing. No body to validate -- the request IS the whole
                # message, so the file is a bare touch and the voice
                # line's poller deletes it as it acts. Same one-write
                # return lane as /pick, and the same JSON-only defense
                # keeps a stray web page from pressing it.
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if 0 < n <= 4096:
                        self.rfile.read(n)      # drain, nothing to read
                except ValueError:
                    pass
                try:
                    (BUS / ".voice_stop").write_text("1")
                    ok = True
                except OSError:
                    ok = False
                self._send(json.dumps({"ok": ok}).encode(),
                           "application/json", 200)
                return
            if path == "/say":
                # P on a face page: the prompt box's line, handed to the
                # voice line as a typed turn. Same one-write return lane
                # as /stop, and the same JSON-only defense -- this one
                # SPEAKS to the agent, so a stray page must never reach it.
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 < length <= 8192:
                        raise ValueError("bad length")
                    obj = json.loads(self.rfile.read(length))
                    text = obj.get("text") if isinstance(obj, dict) else None
                except (ValueError, TypeError):
                    text = None
                text = text.strip() if isinstance(text, str) else ""
                # 2000 chars is a long prompt and a short paste; the voice
                # line joins the lines, so what arrives is one message.
                if not text or len(text) > 2000:
                    self._send(json.dumps(
                        {"ok": False, "error": "bad_text"}).encode(),
                        "application/json", 200)
                    return
                try:
                    tmp = BUS / ".voice_typed.tmp"
                    tmp.write_text(text)
                    tmp.replace(BUS / ".voice_typed")
                    ok = True
                except OSError:
                    ok = False
                self._send(json.dumps({"ok": ok}).encode(),
                           "application/json", 200)
                return
            if path == "/media":
                # The face's player reporting whether sound is coming out
                # of it. The voice line requires the wake word while this
                # is set, because the mic is on the HOST and the music is
                # inside a cross-origin YouTube iframe: there is no
                # reference signal anywhere in this stack, so nothing can
                # subtract the music from what she hears. The name is the
                # whole separation between a command and a lyric.
                # ponytail: a touched file read by MTIME -- no protocol, no
                # state machine. The page re-posts while it plays, so a
                # closed tab or a crashed renderer expires in seconds
                # instead of leaving her deaf until somebody notices.
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 < length <= 4096:
                        raise ValueError("bad length")
                    obj = json.loads(self.rfile.read(length))
                    playing = (bool(obj.get("playing"))
                               if isinstance(obj, dict) else None)
                except (ValueError, TypeError):
                    playing = None
                if playing is None:
                    self._send(json.dumps(
                        {"ok": False, "error": "bad_body"}).encode(),
                        "application/json", 200)
                    return
                try:
                    f = BUS / ".voice_media"
                    if playing:
                        f.write_text("1")
                    elif f.exists():
                        f.unlink()
                    ok = True
                except OSError:
                    ok = False
                self._send(json.dumps({"ok": ok}).encode(),
                           "application/json", 200)
                return
            if path == "/pick":
                # The launch-time mode picker: the face's buttons post
                # here, and the choice crosses to the WAITING voice
                # line as a file beside the bus (the one write this
                # otherwise read-only-on-the-bus server makes — it is
                # the return lane the picker needs).
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 < length <= 4096:
                        raise ValueError("bad length")
                    obj = json.loads(self.rfile.read(length))
                    mode = obj.get("mode") if isinstance(obj, dict) \
                        else None
                except (ValueError, TypeError):
                    mode = None
                if mode not in ("open", "wake", "ptt"):
                    self._send(json.dumps(
                        {"ok": False, "error": "bad_mode"}).encode(),
                        "application/json", 200)
                    return
                try:
                    tmp = BUS / ".voice_mode_pick.tmp"
                    tmp.write_text(mode)
                    tmp.replace(BUS / ".voice_mode_pick")
                    ok = True
                except OSError:
                    ok = False
                self._send(json.dumps({"ok": ok}).encode(),
                           "application/json", 200)
                return
            if path == "/brain":
                # The BRAIN tab: which model answers. Exactly the /pick
                # lane one screen over -- a hard allowlist, then one bare
                # token beside the bus for the voice line to consume.
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 < length <= 4096:
                        raise ValueError("bad length")
                    obj = json.loads(self.rfile.read(length))
                    provider = obj.get("provider") if isinstance(obj, dict) \
                        else None
                    model = obj.get("model") if isinstance(obj, dict) else ""
                except (ValueError, TypeError):
                    provider, model = None, ""
                # The tier rides along as a second token, the way
                # .voice_mic carries "wake hot". Allowlisted like every
                # other value that crosses this line; anything else is
                # dropped to "" rather than refused, so a picker from a
                # newer build cannot lock the owner out of switching.
                if model not in BRAIN_MODELS:
                    model = ""
                if provider not in PROVIDERS:
                    self._send(json.dumps(
                        {"ok": False, "error": "bad_provider"}).encode(),
                        "application/json", 200)
                    return
                try:
                    tmp = BUS / ".voice_brain_pick.tmp"
                    tmp.write_text(f"{provider} {model}".strip())
                    tmp.replace(BUS / ".voice_brain_pick")
                    ok = True
                except OSError:
                    ok = False
                self._send(json.dumps({"ok": ok}).encode(),
                           "application/json", 200)
                return
            if path == "/brainkey":
                # The z.ai token, on its way to the OS keyring and nowhere
                # else. Nothing below this line writes it to a file, logs
                # it, or puts it in a reply: what comes back out is a bool
                # and the last four characters.
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 < length <= 4096:
                        raise ValueError("bad length")
                    obj = json.loads(self.rfile.read(length))
                    if not isinstance(obj, dict):
                        raise ValueError("not an object")
                    provider, key = obj.get("provider"), obj.get("key")
                except (ValueError, TypeError):
                    provider, key = None, None
                # A key pasted out of a terminal or an email arrives with a
                # newline on it about half the time, and "bad_key" on a
                # perfectly good token reads as "my key is wrong". Trimming
                # the ends is never wrong for a token; whitespace in the
                # MIDDLE still fails below, because that is a real paste
                # accident and not one to store silently.
                if isinstance(key, str):
                    key = key.strip()
                if provider != "zai":
                    # Claude signs in with a browser login and has no key
                    # to paste. Refusing every other provider here keeps
                    # the one code path that touches a secret as narrow
                    # as it can be.
                    err = "bad_provider"
                elif not (isinstance(key, str) and KEY_RE.fullmatch(key)):
                    err = "bad_key"
                else:
                    err = None
                if err:
                    self._send(json.dumps({"ok": False, "error": err})
                               .encode(), "application/json", 200)
                    return
                stored, res = store_zai_key(key)
                out = {"ok": True, "tail": res} if stored \
                    else {"ok": False, "error": res}
                self._send(json.dumps(out).encode(),
                           "application/json", 200)
                return
            reply = None
            try:
                length = int(self.headers.get("Content-Length") or "")
            except ValueError:
                length = -1
            if not 0 < length <= 1_000_000:
                reply = _err("bad_json",
                             "the request body is missing or over 1MB")
            else:
                try:
                    obj = json.loads(self.rfile.read(length))
                    if not isinstance(obj, dict):
                        raise ValueError("the top level must be a JSON "
                                         "object")
                except ValueError as e:
                    reply = _err("bad_json",
                                 "the body is not a JSON object (%s)" % e)
            if reply is None:
                if not CFG.get("glass", True):
                    # Refused up front so the agent never announces content
                    # on a layer that cannot exist.
                    reply = _err("disabled",
                                 'the glass is turned off: set "glass": '
                                 'true in ai-visualizer.json and restart '
                                 'the server')
                else:
                    reply = GLASS.handle(obj)
            self._send(json.dumps(reply).encode(), "application/json")
        except ConnectionError:
            # Same family-wide catch as do_GET, same reason.
            pass
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
            try:
                self._send(body, "application/json", 500)
            except ConnectionError:
                pass

    def _host_ok(self):
        # The DNS-rebinding defense, on EVERY request: a rebound page
        # becomes same-origin with this server and could otherwise read
        # /state -- the usage readout and whatever is on the glass -- so the
        # guard covers reads, not just /cmd. The port is asked from the
        # socket, not the config, so --selfcheck's ephemeral server guards
        # itself the same way.
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        return host in ("127.0.0.1:%d" % port, "localhost:%d" % port,
                        "[::1]:%d" % port)

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        target = (HERE / path.lstrip("/")).resolve()
        if target != HERE and HERE not in target.parents:
            self._send(b"not found", "text/plain", 404)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._send(b"not found", "text/plain", 404)
            return
        ctype = mimetypes.guess_type(str(target))[0] or \
            "application/octet-stream"
        self._send(target.read_bytes(), ctype)

    def _send(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def selfcheck():
    """In-process proof of the glass contract, group by group. Model groups
    drive GlassModel directly (deterministic clock, injected reserves); the
    guards group runs the real Handler on an ephemeral port because a 403 is
    only real if an actual HTTP request earns it."""
    import contextlib
    import http.client
    import io
    import tempfile

    groups = []

    def group(name):
        def deco(fn):
            groups.append((name, fn))
            return fn
        return deco

    class Clock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            return self.t

    def model(tmp, name, reserve=DEFAULT_RESERVE, clock=time.monotonic,
              face="board"):
        return GlassModel(Path(tmp) / (name + ".json"),
                          reserve_for=lambda f, r=reserve: r,
                          default_face=lambda: face, clock=clock,
                          chat_rect=None)

    def ok(reply):
        assert reply.get("ok") is True, "expected ok:true, got %r" % (reply,)
        return reply

    def refused(reply, slug):
        assert reply.get("ok") is False and reply.get("error") == slug, \
            "expected %r refusal, got %r" % (slug, reply)
        return reply

    @group("place")
    def _(tmp):
        m = model(tmp, "place")
        # Default reserve (center block): the scanner hugs the right rail.
        r1 = ok(m.handle({"a": "show", "type": "note", "body": "hi"}))
        assert r1["id"] == "note-1" and r1["cell"] == "J1", r1
        assert r1["span"] == [3, 2] and r1["replaced"] is False
        assert r1["expires_in"] == 180 and r1["viewers"] == 0
        r2 = ok(m.handle({"a": "show", "type": "map", "q": "Aleppo",
                          "zoom": 8}))
        assert r2["cell"] == "J3" and r2["span"] == [3, 3], r2
        # Every type opens at its §4 default span.
        m2 = model(tmp, "place2", reserve=[])
        fixtures = {"image": {"src": "https://x.example/a.png"},
                    "calendar": {"events": [{"date": "2026-09-01",
                                             "label": "x"}]},
                    "timer": {"seconds": 60},
                    "list": {"items": [{"text": "x"}]},
                    "iframe": {"src": "https://x.example/"},
                    "html": {"html": "<b>x</b>"},
                    # "tracks" needs no network; only "q" reaches YouTube,
                    # so the selfcheck stays offline.
                    "player": {"tracks": [{"id": "dQw4w9WgXcQ"}]}}
        for t, extra in fixtures.items():
            r = ok(m2.handle(dict({"a": "show", "type": t}, **extra)))
            assert r["span"] == list(TYPE_SPECS[t]["span"]), (t, r)
        # Explicit cells are honored verbatim.
        r = ok(m.handle({"a": "show", "type": "timer", "seconds": 60,
                         "cell": "A7"}))
        assert r["cell"] == "A7"
        # The occupancy map matches the item list and the reserve, cell by
        # cell (the one implementation of the grid math, asserted).
        st = ok(m.handle({"a": "state"}))
        reserve = [parse_rect(d) for d in st["glass"]["reserve"]]
        order = list(m.items.values())
        for rr in range(GRID_ROWS):
            for cc in range(GRID_COLS):
                ch = st["map"][rr][cc]
                here = [n for n, it in enumerate(order)
                        if overlap((cc, rr, 1, 1), item_rect(it))]
                if here:
                    assert ch == chr(ord("a") + here[0] % 26), (rr, cc, ch)
                elif any(overlap((cc, rr, 1, 1), rv) for rv in reserve):
                    assert ch == "#", (rr, cc, ch)
                else:
                    assert ch == ".", (rr, cc, ch)
        # A free probe's answer is a spot a show would actually get.
        probe = st["free"]["2x2"]
        r = ok(m.handle({"a": "show", "type": "timer", "seconds": 5,
                         "new": True}))
        assert r["cell"] == probe, (probe, r)
        # Reserve union of every face seen in 3 s, then default-face
        # fallback once they go quiet.
        ck = Clock()
        reserves = {"a": [{"cell": "A1", "span": [3, 8]}],
                    "b": [{"cell": "J1", "span": [3, 8]}]}
        m3 = GlassModel(Path(tmp) / "union.json",
                        reserve_for=lambda f: reserves.get(f,
                                                           DEFAULT_RESERVE),
                        default_face=lambda: "a", clock=ck,
                        chat_rect=None)
        m3.note_viewer("a")
        m3.note_viewer("b")
        st = ok(m3.handle({"a": "state"}))
        assert len(st["glass"]["reserve"]) == 2 and st["viewers"] == 2
        r = ok(m3.handle({"a": "show", "type": "note", "body": "x"}))
        assert r["cell"] == "D1", r     # both rails reserved -> the middle
        ck.t += 4.0
        st = ok(m3.handle({"a": "state"}))
        assert st["viewers"] == 0
        assert st["glass"]["reserve"] == [{"cell": "A1", "span": [3, 8]}]

    @group("conflict")
    def _(tmp):
        m = model(tmp, "conflict", reserve=[])
        a = ok(m.handle({"a": "show", "type": "note", "body": "1",
                         "cell": "A1", "span": [2, 2]}))
        b = ok(m.handle({"a": "show", "type": "note", "body": "2",
                         "new": True, "cell": "C1", "span": [2, 2]}))
        # A multi-cell span reports EVERY overlapping id, plus the map and
        # the free probes.
        r = refused(m.handle({"a": "show", "type": "list",
                              "items": [{"text": "x"}],
                              "cell": "B1", "span": [2, 2]}), "occupied")
        assert sorted(r["by"]) == sorted([a["id"], b["id"]]), r
        assert len(r["map"]) == 8 and all(len(row) == 12 for row in r["map"])
        assert "free" in r and "viewers" in r
        # An explicit cell over the reserve is ALLOWED and flagged: the
        # grid is the only law, and explicit geometry means the person
        # asked. The payload flags it too, so the agent can move it.
        m2 = model(tmp, "conflict2")
        r = ok(m2.handle({"a": "show", "type": "note", "body": "x",
                          "cell": "E4"}))
        assert r.get("over_reserve") is True, r
        st = ok(m2.handle({"a": "state"}))
        assert st["glass"]["items"][0]["flags"] == ["over_reserve"], st
        # A replace with the SAME footprint must still say over_reserve in
        # the reply: the agent reading only replies (the glass.sh flow, §7)
        # is otherwise never told the card still covers the face.
        r = ok(m2.handle({"a": "show", "type": "note", "body": "y",
                          "cell": "E4"}))
        assert r.get("over_reserve") is True, r
        # ... but auto-placement never touches the reserve on its own:
        # the same note without a cell lands clear of it, unflagged.
        r2 = ok(m2.handle({"a": "show", "type": "note", "body": "y",
                           "new": True}))
        assert "over_reserve" not in r2, r2
        # Out of bounds is refused with the offending bounds, never clamped.
        r = refused(m.handle({"a": "show", "type": "note", "body": "x",
                              "new": True, "cell": "K7", "span": [3, 3]}),
                    "out_of_bounds")
        assert r["bounds"] == {"cell": "K7", "span": [3, 3]}
        # no_room names the oldest unpinned items as dismissal candidates.
        m3 = model(tmp, "conflict3", reserve=[])
        ids = [ok(m3.handle({"a": "show", "type": "note", "body": "x",
                             "new": True, "span": [6, 4]}))["id"]
               for _ in range(4)]
        ok(m3.handle({"a": "pin", "id": ids[0]}))
        r = refused(m3.handle({"a": "show", "type": "note", "body": "x",
                               "new": True}), "no_room")
        assert r["dismiss_candidates"][0] == ids[1], r
        assert ids[0] not in r["dismiss_candidates"]
        # Bad booleans name the field and the two accepted spellings.
        r = refused(m.handle({"a": "show", "type": "note", "body": "x",
                              "new": "yes"}), "bad_field")
        assert "true" in r["message"] and "false" in r["message"]
        # Unknown verb / type / field are structured errors, not guesses;
        # include_pinned without a clear is one of them.
        refused(m.handle({"a": "blorp"}), "unknown_verb")
        refused(m.handle({"a": "show", "type": "chart"}), "unknown_type")
        refused(m.handle({"a": "show", "type": "note", "body": "x",
                          "bodyy": "x"}), "unknown_field")
        refused(m.handle({"a": "show", "type": "note", "body": "x",
                          "include_pinned": True}), "unknown_field")
        refused(m.handle({"a": "show", "type": "map"}), "bad_field")
        refused(m.handle({"a": "show", "type": "map", "q": "x", "lat": 1,
                          "lon": 2}), "bad_field")

    @group("singleton")
    def _(tmp):
        m = model(tmp, "singleton")
        r1 = ok(m.handle({"a": "show", "type": "map", "q": "Aleppo"}))
        assert (r1["id"], r1["replaced"]) == ("map-1", False)
        # A show of a type replaces that type's card in place: same cell,
        # same span, ttl restarted, content swapped, rev bumped.
        r2 = ok(m.handle({"a": "show", "type": "map", "q": "Rome"}))
        assert (r2["id"], r2["replaced"]) == ("map-1", True)
        assert r2["cell"] == r1["cell"] and r2["span"] == r1["span"]
        it = m.items["map-1"]
        assert it["q"] == "Rome" and it["rev"] == 2 and it["title"] == "Rome"
        # "new": true (string spelling coerced) makes a second instance.
        r3 = ok(m.handle({"a": "show", "type": "map", "q": "Kyiv",
                          "new": "true"}))
        assert r3["id"] == "map-2" and r3["replaced"] is False
        # Two cards of the type and no id: refused, never guessed.
        r = refused(m.handle({"a": "show", "type": "map", "q": "Oslo"}),
                    "ambiguous")
        assert sorted(r["ids"]) == ["map-1", "map-2"]
        ok(m.handle({"a": "show", "type": "map", "q": "Oslo",
                     "id": "map-2"}))
        # An unseen explicit id creates with that id, and the auto counter
        # stays ahead of it forever after.
        r5 = ok(m.handle({"a": "show", "type": "note", "id": "note-7",
                          "body": "x"}))
        assert r5["replaced"] is False
        r6 = ok(m.handle({"a": "show", "type": "note", "body": "y",
                          "new": True}))
        assert r6["id"] == "note-8"
        # update is content-only and strict.
        ok(m.handle({"a": "update", "id": "note-7", "body": "z"}))
        assert m.items["note-7"]["rev"] == 2
        refused(m.handle({"a": "update", "id": "note-7", "cell": "A1"}),
                "bad_field")
        refused(m.handle({"a": "update", "id": "note-7", "q": "x"}),
                "unknown_field")
        refused(m.handle({"a": "update", "id": "nope", "body": "x"}),
                "not_found")
        # A replace keeps the pin state unless pin is passed.
        ok(m.handle({"a": "pin", "id": "note-7"}))
        r7 = ok(m.handle({"a": "show", "type": "note", "id": "note-7",
                          "body": "q"}))
        assert m.items["note-7"]["pin"] is True and r7["expires_in"] is None
        # dismiss and clear.
        r = ok(m.handle({"a": "dismiss", "id": "map-2"}))
        assert r["dismissed"] == ["map-2"] and "map-2" not in m.items
        refused(m.handle({"a": "dismiss", "id": "map-2"}), "not_found")
        # A duplicated id in "ids" (a classic LLM emission stutter) is one
        # dismissal, deduped and reported once -- never a KeyError 500 with
        # the model half-mutated and rev unmoved.
        rev_before = m.rev
        r = ok(m.handle({"a": "dismiss", "ids": ["note-8", "note-8"]}))
        assert r["dismissed"] == ["note-8"] and "note-8" not in m.items
        assert m.rev == rev_before + 1
        r = ok(m.handle({"a": "clear"}))
        assert "note-7" in r["kept_pinned"] and "map-1" in r["cleared"]
        assert "note-7" in m.items
        r = ok(m.handle({"a": "clear", "include_pinned": True}))
        assert m.items == {} and "note-7" in r["cleared"]
        # A foreign-typed id that LOOKS like an auto id advances that
        # type's counter anyway: an auto-issued id must never clobber a
        # live item, nor be reissued after that item is gone -- a
        # slow-polling page diffing by id would body-swap, not exit+enter.
        m2 = model(tmp, "singleton2")
        ok(m2.handle({"a": "show", "type": "map", "q": "x"}))
        ok(m2.handle({"a": "show", "type": "note", "id": "map-2",
                      "body": "not a map"}))
        r = ok(m2.handle({"a": "show", "type": "map", "q": "y",
                          "new": True}))
        assert r["id"] == "map-3", r
        assert m2.items["map-2"]["type"] == "note"      # the note survived
        ok(m2.handle({"a": "dismiss", "id": "map-2"}))
        r = ok(m2.handle({"a": "show", "type": "map", "q": "z",
                          "new": True}))
        assert r["id"] == "map-4", r    # map-2 is spent, never reissued

    @group("resize-reanchor")
    def _(tmp):
        m = model(tmp, "resize", reserve=[])
        a = ok(m.handle({"a": "show", "type": "note", "body": "a",
                         "cell": "J1", "span": [3, 2]}))
        ok(m.handle({"a": "show", "type": "note", "body": "b", "new": True,
                     "cell": "J3", "span": [3, 2]}))
        # A shrink stays at its anchor.
        r = ok(m.handle({"a": "move", "id": a["id"], "span": [2, 1]}))
        assert r["cell"] == "J1" and r["span"] == [2, 1]
        # A grow that no longer fits at the anchor is re-anchored via
        # auto-placement, never refused: the person asked for a size.
        r = ok(m.handle({"a": "move", "id": a["id"], "span": [3, 3]}))
        assert r["cell"] == "J5" and r["span"] == [3, 3], r
        # An explicit cell keeps the refuse-on-conflict rule.
        refused(m.handle({"a": "move", "id": a["id"], "cell": "J3"}),
                "occupied")
        # cell-only move keeps the span.
        r = ok(m.handle({"a": "move", "id": a["id"], "cell": "A1"}))
        assert r["cell"] == "A1" and r["span"] == [3, 3]
        refused(m.handle({"a": "move", "id": a["id"]}), "bad_field")
        refused(m.handle({"a": "move", "id": a["id"], "cell": "Z9"}),
                "bad_field")
        # A grow with nowhere at all to go is a no_room refusal that never
        # proposes dismissing the item itself.
        big = ok(m.handle({"a": "show", "type": "html", "html": "x",
                           "cell": "D1", "span": [6, 8]}))
        r = refused(m.handle({"a": "move", "id": big["id"],
                              "span": [12, 8]}), "no_room")
        assert big["id"] not in r["dismiss_candidates"]
        # A grow the rails cannot hold goes OVER the face reserve rather
        # than refusing — the grid is the only law — and says so.
        m2 = model(tmp, "resize2")   # default center-block reserve
        it = ok(m2.handle({"a": "show", "type": "map", "q": "x"}))
        r = ok(m2.handle({"a": "move", "id": it["id"], "span": [6, 4]}))
        assert r.get("over_reserve") is True, r
        # A card deliberately placed over the face is grandfathered: a
        # span-only resize keeps the anchor the person chose (still
        # flagged while it overlaps) instead of teleporting to the rail.
        m3 = model(tmp, "resize3")
        it = ok(m3.handle({"a": "show", "type": "map", "q": "y",
                           "cell": "D2", "span": [6, 4]}))
        assert it.get("over_reserve") is True, it
        r = ok(m3.handle({"a": "move", "id": it["id"], "span": [3, 3]}))
        assert r["cell"] == "D2" and r.get("over_reserve") is True, r
        # ...and the flag is honest: a shrink whose new footprint clears
        # the reserve keeps its anchor AND drops over_reserve.
        m4 = model(tmp, "resize4")
        it = ok(m4.handle({"a": "show", "type": "note", "body": "z",
                           "cell": "B1", "span": [4, 4]}))
        assert it.get("over_reserve") is True, it
        r = ok(m4.handle({"a": "move", "id": it["id"], "span": [2, 2]}))
        assert r["cell"] == "B1" and "over_reserve" not in r, r

    @group("ttl")
    def _(tmp):
        ck = Clock()
        m = model(tmp, "ttl", reserve=[], clock=ck)
        ok(m.handle({"a": "show", "type": "note", "body": "x"}))
        ck.t += 179
        st = ok(m.handle({"a": "state"}))
        assert len(st["glass"]["items"]) == 1
        assert 0 < st["glass"]["items"][0]["expires_in"] <= 1.01
        rev = st["glass"]["rev"]
        ck.t += 2
        # Lazy pruning at the top of the next request, and it bumps the
        # glass rev so pages animate the exit.
        st = ok(m.handle({"a": "state"}))
        assert st["glass"]["items"] == [] and st["glass"]["rev"] == rev + 1
        # An explicit ttl always wins over the 180 s default.
        r = ok(m.handle({"a": "show", "type": "note", "body": "x",
                         "ttl": 10}))
        assert r["expires_in"] == 10
        nid = r["id"]
        # update and move reset the clock to the item's own effective ttl.
        ck.t += 7
        assert ok(m.handle({"a": "update", "id": nid,
                            "body": "y"}))["expires_in"] == 10
        ck.t += 7
        assert ok(m.handle({"a": "move", "id": nid,
                            "cell": "A5"}))["expires_in"] == 10
        # pin clears the countdown; unpin restarts the last-set ttl, no
        # matter how long the item sat pinned.
        assert ok(m.handle({"a": "pin", "id": nid}))["expires_in"] is None
        ck.t += 5000
        assert nid in m.items
        assert ok(m.handle({"a": "unpin", "id": nid}))["expires_in"] == 10
        # The timer invariant: effective ttl is max(ttl, remaining + 30).
        r = ok(m.handle({"a": "show", "type": "timer", "seconds": 600}))
        assert r["expires_in"] == 630
        tid = r["id"]
        ck.t += 500
        st = ok(m.handle({"a": "state"}))
        tit = [i for i in st["glass"]["items"] if i["id"] == tid][0]
        assert tit["ends_in"] == 100 and tit["expires_in"] == 130, tit
        # ...and an explicit longer ttl still wins.
        r = ok(m.handle({"a": "show", "type": "timer", "seconds": 60,
                         "ttl": 1000, "new": True}))
        assert r["expires_in"] == 1000
        # Dead or malformed timers are refused shows.
        refused(m.handle({"a": "show", "type": "timer", "seconds": 0,
                          "new": True}), "bad_field")
        refused(m.handle({"a": "show", "type": "timer", "seconds": -5,
                          "new": True}), "bad_field")
        refused(m.handle({"a": "show", "type": "timer",
                          "until": "2001-01-01T00:00:00", "new": True}),
                "bad_field")
        refused(m.handle({"a": "show", "type": "timer",
                          "until": "2999-01-01", "new": True}), "bad_field")
        refused(m.handle({"a": "show", "type": "timer",
                          "until": "eventually", "new": True}), "bad_field")
        refused(m.handle({"a": "show", "type": "timer", "seconds": 5,
                          "until": "2999-01-01T00:00:00", "new": True}),
                "bad_field")

    @group("persist")
    def _(tmp):
        p = Path(tmp) / "persist.json"

        def mk():
            return GlassModel(p, reserve_for=lambda f: [],
                              default_face=lambda: "x")

        m = mk()
        ok(m.handle({"a": "show", "type": "note", "body": "keep me",
                     "title": "K", "pin": True}))
        ok(m.handle({"a": "show", "type": "map", "q": "Rome"}))
        ok(m.handle({"a": "show", "type": "timer", "seconds": 3600,
                     "pin": True}))
        # tmp + os.replace leaves no droppings behind.
        assert p.exists() and not Path(str(p) + ".tmp").exists()
        m2 = mk()   # "restart": pinned survive, ephemerals died
        assert set(m2.items) == {"note-1", "timer-1"}, m2.items
        assert m2.items["note-1"]["body"] == "keep me"
        assert m2.items["note-1"]["pin"] is True
        st = ok(m2.handle({"a": "state"}))
        tim = [i for i in st["glass"]["items"] if i["id"] == "timer-1"][0]
        # The countdown crossed the restart via wall time.
        assert 3500 < tim["ends_in"] <= 3600 and tim["expires_in"] is None
        # The id counter persisted: map-1 is never reissued to a page that
        # may still be holding it.
        r = ok(m2.handle({"a": "show", "type": "map", "q": "Oslo"}))
        assert r["id"] == "map-2", r
        # A corrupt state file is tolerated with one plain-English line.
        p.write_text("{this is not json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m3 = mk()
        assert m3.items == {} and m3.counters == {}
        out = buf.getvalue()
        assert out.count("\n") == 1 and p.name in out, out
        # Valid JSON, bad RECORD: a rect past the grid edge takes the same
        # tolerant path -- it used to load fine and then wedge every _occ
        # (state verb, refusal boards) with IndexError forever after.
        p.write_text(json.dumps({"counters": {}, "pinned": [
            {"id": "note-1", "type": "note", "title": "", "cell": "K7",
             "span": [3, 3], "pin": True, "rev": 1, "body": "x"}]}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m4 = mk()
        assert m4.items == {} and p.name in buf.getvalue()
        st = ok(m4.handle({"a": "state"}))   # the grid math still answers
        assert len(st["map"]) == 8
        # A hand-deleted rev is repaired to 1, not fatal: the record loads
        # and the first singleton replace works (it used to KeyError).
        p.write_text(json.dumps({"counters": {}, "pinned": [
            {"id": "note-1", "type": "note", "title": "", "cell": "A1",
             "span": [3, 2], "pin": True, "body": "keep"}]}))
        m5 = mk()
        assert m5.items["note-1"]["rev"] == 1
        ok(m5.handle({"a": "show", "type": "note", "body": "swap"}))
        assert m5.items["note-1"]["rev"] == 2

    @group("guards")
    def _(tmp):
        global GLASS
        old_glass, old_flag = GLASS, CFG.get("glass", True)
        GLASS = GlassModel(Path(tmp) / "guards.json",
                           reserve_for=lambda f: [],
                           default_face=lambda: "x")
        CFG["glass"] = True
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        def req(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request(method, path, body=body, headers=headers or {})
                resp = conn.getresponse()
                return resp.status, resp.read()
            finally:
                conn.close()

        me = {"Host": "127.0.0.1:%d" % port}
        js = dict(me, **{"Content-Type": "application/json"})
        try:
            # DNS rebinding: an unexpected Host is 403 on reads too.
            assert req("GET", "/state",
                       headers={"Host": "evil.example:%d" % port})[0] == 403
            assert req("GET", "/state", headers=me)[0] == 200
            s, body = req("GET", "/state",
                          headers={"Host": "localhost:%d" % port})
            assert s == 200 and json.loads(body)["glass"]["rev"] == 0
            assert req("POST", "/cmd", body=b'{"a":"state"}',
                       headers={"Host": "evil.example:%d" % port,
                                "Content-Type": "application/json"})[0] == 403
            # The preflight rule: /cmd without application/json is 403.
            assert req("POST", "/cmd", body=b'{"a":"state"}',
                       headers=dict(me, **{"Content-Type": "text/plain"})
                       )[0] == 403
            s, body = req("POST", "/cmd", body=b'{"a":"state"}',
                          headers=dict(me, **{"Content-Type":
                                              "application/json; "
                                              "charset=utf-8"}))
            assert s == 200 and json.loads(body)["ok"] is True
            # Face self-reporting reaches the viewers count on every reply.
            req("GET", "/state?face=selfcheck", headers=me)
            s, body = req("POST", "/cmd", body=b'{"a":"state"}', headers=js)
            assert json.loads(body)["viewers"] == 1
            # Malformed JSON is a structured refusal, not a traceback.
            s, body = req("POST", "/cmd", body=b"{nope", headers=js)
            assert s == 200 and json.loads(body)["error"] == "bad_json"
            # iframe/image src guard: loopback and non-http(s) die at /cmd
            # time, server-side.
            for src in ("http://127.0.0.1:8790/", "http://localhost/x",
                        "ftp://x.example/", "assets/x.html",
                        "http://a.localhost/x"):
                refused(GLASS.handle({"a": "show", "type": "iframe",
                                      "src": src, "new": True}), "bad_field")
            for src in ("http://[::1]/x.png", "javascript:alert(1)",
                        "data:image/png;base64,AAAA"):
                refused(GLASS.handle({"a": "show", "type": "image",
                                      "src": src, "new": True}), "bad_field")
            # Non-canonical loopback spellings a browser still resolves
            # (decimal, hex, octal, shorthand, IPv4-mapped IPv6, trailing
            # dot) -- plus private and link-local, the SSRF next door.
            for src in ("http://2130706433/", "http://0x7f000001/",
                        "http://0177.0.0.1/", "http://127.1/",
                        "http://[::ffff:127.0.0.1]/", "http://0/",
                        "http://localhost./x", "http://10.0.0.5/",
                        "http://169.254.0.1/"):
                refused(GLASS.handle({"a": "show", "type": "iframe",
                                      "src": src, "new": True}), "bad_field")
            # ...while real public hosts, numeric or named, still pass
            # (0x08080808 canonicalizes to 8.8.8.8, not to loopback).
            ok(GLASS.handle({"a": "show", "type": "iframe",
                             "src": "https://8.8.8.8/", "new": True}))
            # Control characters in a title or id are refused at /cmd time:
            # glass-state.sh prints both into a terminal readout the agent
            # trusts, and a crafted title could forge rows there.
            refused(GLASS.handle({"a": "show", "type": "note", "body": "x",
                                  "title": "ok\x1b[2Jnope", "new": True}),
                    "bad_field")
            refused(GLASS.handle({"a": "show", "type": "note", "body": "x",
                                  "id": "note\nx", "new": True}),
                    "bad_field")
            # ...repo-relative images are the one exception.
            ok(GLASS.handle({"a": "show", "type": "image",
                             "src": "assets/face.png"}))
            ok(GLASS.handle({"a": "show", "type": "iframe",
                             "src": "https://example.com/"}))
            # Glass off: every verb refuses in plain English, and /state
            # drops the glass object entirely.
            CFG["glass"] = False
            s, body = req("POST", "/cmd", body=b'{"a":"state"}', headers=js)
            rep = json.loads(body)
            assert rep["ok"] is False and "ai-visualizer.json" in \
                rep["message"]
            s, body = req("GET", "/state", headers=me)
            assert "glass" not in json.loads(body)
        finally:
            srv.shutdown()
            srv.server_close()
            GLASS = old_glass
            CFG["glass"] = old_flag

    @group("brain")
    def _(tmp):
        # The provider picker's two endpoints, end to end. The rule this
        # group exists to hold down is the last assert: a key that goes
        # in never comes back out.
        global BUS, MOCK, store_zai_key, _zai_key_status
        old = (BUS, MOCK, store_zai_key, _zai_key_status)
        BUS = Path(tmp) / "brainbus"
        BUS.mkdir()
        MOCK = None                     # read_bus must read real files here
        seen = {}

        def fake_store(key, item=None):
            # NEVER the real keyring in a selfcheck: this runs on the
            # owner's machine and his jarvis-glm item is not a fixture.
            seen["key"] = key
            return True, key[-4:]

        store_zai_key = fake_store
        _zai_key_status = lambda item=None: (True, "ab12")   # noqa: E731
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        def req(path, body, ctype="application/json", host=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("POST", path, body=body.encode(), headers={
                    "Host": host or "127.0.0.1:%d" % port,
                    "Content-Type": ctype})
                r = conn.getresponse()
                return r.status, r.read().decode()
            finally:
                conn.close()

        try:
            # /brain: the allowlist, exactly as /pick validates mic modes.
            for good in PROVIDERS:
                s, b = req("/brain", json.dumps({"provider": good}))
                assert s == 200 and json.loads(b)["ok"] is True, b
                assert (BUS / ".voice_brain_pick").read_text() == good
            for bad in ("openai", "", "CLAUDE", " zai", None, 7,
                        ["zai"], {"provider": "zai"}):
                s, b = req("/brain", json.dumps({"provider": bad}))
                assert json.loads(b) == {"ok": False,
                                         "error": "bad_provider"}, b
            s, b = req("/brain", "{nope")
            assert json.loads(b)["error"] == "bad_provider", b
            # ...and the new paths inherit every existing defense.
            assert req("/brain", '{"provider":"zai"}',
                       ctype="text/plain")[0] == 403
            assert req("/brainkey", '{"provider":"zai"}',
                       ctype="text/plain")[0] == 403
            assert req("/brain", '{"provider":"zai"}',
                       host="evil.example:%d" % port)[0] == 403
            assert req("/brainkey", '{"provider":"zai"}',
                       host="evil.example:%d" % port)[0] == 403

            # /brainkey refuses claude outright: it logs in, it has no key.
            s, b = req("/brainkey", json.dumps(
                {"provider": "claude", "key": "sk-ant-notakey-000000"}))
            assert json.loads(b) == {"ok": False, "error": "bad_provider"}, b
            assert "key" not in seen, "claude reached the keyring"
            # Malformed keys never reach the keyring either.
            for bad in ("", "   ", "has space", "two\nlines", "short",
                        "tab\there", "x" * 513, "cafeébeef", 42, None):
                s, b = req("/brainkey", json.dumps(
                    {"provider": "zai", "key": bad}))
                assert json.loads(b) == {"ok": False, "error": "bad_key"}, \
                    "%r accepted: %s" % (bad, b)
            assert "key" not in seen, "a malformed key reached the keyring"

            # The happy path: stored, and the reply is a bool and a tail.
            # A fixture, not a key: 40 characters of nothing, built
            # in two halves so no reader mistakes it for a real one.
            secret = "d2f9c1a4b7e60355aa11" + "Z" * 20  # gitleaks:allow
            s, b = req("/brainkey", json.dumps(
                {"provider": "zai", "key": secret}))
            assert seen.get("key") == secret, "the key never arrived"
            assert json.loads(b) == {"ok": True, "tail": secret[-4:]}, b
            assert secret not in b and secret[:-4] not in b, \
                "the key came back out of /brainkey"
            # ...and the newline a paste drags along is trimmed, not
            # treated as a wrong key.
            seen.clear()
            s, b = req("/brainkey", json.dumps(
                {"provider": "zai", "key": "  " + secret + "\n"}))
            assert seen.get("key") == secret, seen
            assert json.loads(b) == {"ok": True, "tail": secret[-4:]}, b

            # read_bus surfaces .voice_brain, filtered like .voice_mic,
            # and omits the whole key when the file is not there.
            assert "brain" not in read_bus()
            # Two tokens now: provider, then the GLM tier when there is
            # one. An unknown tier drops to nothing rather than voiding
            # the whole line -- the provider half is still the truth.
            for token, want in (
                    ("zai", {"provider": "zai"}),
                    ("claude", {"provider": "claude"}),
                    ("claude\n", {"provider": "claude"}),
                    ("zai glm-5.3-flash",
                     {"provider": "zai", "model": "glm-5.3-flash"}),
                    ("zai glm-5.3", {"provider": "zai",
                                     "model": "glm-5.3"}),
                    ("zai extra", {"provider": "zai"}),
                    ("openai", None), ("", None),
                    ("openai glm-5.3", None)):
                (BUS / ".voice_brain").write_text(token)
                got = read_bus().get("brain")
                assert got == want, "%r -> %r" % (token, got)
            (BUS / ".voice_brain").unlink()
            assert "brain" not in read_bus()

            # /config carries the STATUS the BRAIN tab renders -- and only
            # ever a bool, four characters, and the Claude login flag.
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/config",
                         headers={"Host": "127.0.0.1:%d" % port})
            cfg = json.loads(conn.getresponse().read())
            conn.close()
            st = cfg["brain_status"]
            assert set(st) == {"zai_key", "zai_tail", "claude_signed_in"}, st
            assert st["zai_key"] is True and st["zai_tail"] == "ab12"
            assert len(st["zai_tail"]) <= 4
            assert isinstance(st["claude_signed_in"], bool)
        finally:
            srv.shutdown()
            srv.server_close()
            BUS, MOCK, store_zai_key, _zai_key_status = old

    @group("threads")
    def _(tmp):
        # Two shows racing auto-placement while a loop hammers the /state
        # serialization: the one lock is what keeps every placement unique.
        m = model(tmp, "threads", reserve=[])
        errors = []

        def shower(n):
            try:
                for _ in range(n):
                    r = m.handle({"a": "show", "type": "note", "body": "x",
                                  "new": True, "span": [1, 1]})
                    assert r["ok"] is True, r
            except Exception as e:    # noqa: BLE001 -- reported via assert
                errors.append(e)

        stop = threading.Event()

        def poller():
            try:
                while not stop.is_set():
                    m.payload()
            except Exception as e:
                errors.append(e)

        pt = threading.Thread(target=poller)
        ts = [threading.Thread(target=shower, args=(40,)) for _ in range(2)]
        pt.start()
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        stop.set()
        pt.join()
        assert not errors, errors
        assert len(m.items) == 80
        st = ok(m.handle({"a": "state"}))
        occupied = sum(ch != "." for row in st["map"] for ch in row)
        assert occupied == 80, occupied     # no two placements share a cell

    @group("quiet")
    def _(tmp):
        # No socket needed: handle_error touches no instance state, so a
        # bare instance is enough to prove which exceptions reach stderr.
        srv = QuietHTTPServer.__new__(QuietHTTPServer)
        out = io.StringIO()
        for exc in (ConnectionResetError(54, "Connection reset by peer"),
                    BrokenPipeError(32, "Broken pipe"),
                    ValueError("a real handler bug")):
            try:
                raise exc
            except Exception:
                with contextlib.redirect_stderr(out):
                    srv.handle_error(None, ("127.0.0.1", 0))
        seen = out.getvalue()
        assert "a real handler bug" in seen, "a real error went silent:\n" + seen
        assert "ConnectionResetError" not in seen, seen
        assert "BrokenPipeError" not in seen, seen

    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name, fn in groups:
            try:
                fn(tmp)
                print("[selfcheck] %-16s pass" % name)
            except Exception as e:
                failed += 1
                print("[selfcheck] %-16s FAIL  %s: %s"
                      % (name, e.__class__.__name__, e))
    print("[selfcheck] %s" % ("all groups passed" if not failed
                              else "%d group(s) FAILED" % failed))
    return 1 if failed else 0


class QuietHTTPServer(ThreadingHTTPServer):
    """The same server, minus the traceback when a browser drops a socket.

    A browser opens spare keep-alive/preconnect connections and closes the
    ones it never needs, without ever sending a request line. socketserver
    counts that as an unhandled error and dumps a forty-line traceback per
    hangup, which buries every real log line under noise nobody can act on.
    Only the client-hangup errors go quiet here; a genuine handler crash
    still prints exactly as loudly as before."""

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1],
                          (ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, client_address)


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        sys.exit(selfcheck())
    mode = f"MOCK={MOCK}" if MOCK else f"bus: {BUS}"
    root = f"http://127.0.0.1:{PORT}/"
    # The browser opens on the configured face; the gallery stays at "/" for switching.
    face = CFG.get("face", "")
    url = f"{root}faces/{face}/" if face and (HERE / "faces" / face / "index.html").exists() else root
    # ALREADY RUNNING IS NOT AN ERROR, and treating it as one was the whole
    # bug. Closing the browser tab does not stop this server; it keeps going
    # headless. Relaunching then failed to bind, died before the line that
    # opens the browser, and took the traceback with it when the launcher
    # window closed. The end-user symptom was "I can hear my agent but the
    # face never shows up", with the face running perfectly the entire time.
    try:
        srv = QuietHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        if e.errno not in (errno.EADDRINUSE, errno.EACCES):
            raise
        # Something holds the port. Ask it whether it is us before claiming
        # anything: a stranger on this port is a different problem and
        # deserves a different sentence.
        mine = False
        try:
            with urllib.request.urlopen(root + "state", timeout=2) as r:
                mine = r.status == 200
        except Exception:
            mine = False
        if mine:
            print(f"already running at {root}  opening it instead", flush=True)
            if not NO_OPEN:
                webbrowser.open(url)
            sys.exit(0)
        print(f"port {PORT} is taken by something that is not this server.",
              flush=True)
        print("Close whatever is using it, or set a different \"port\" in "
              "ai-visualizer.json.", flush=True)
        sys.exit(1)
    srv.allow_reuse_address = True
    print(f"ai-visualizer on {root}  opening {url}  ({mode})  Ctrl-C stops", flush=True)
    if not NO_OPEN:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    threading.Thread(target=watch_face, args=(url,), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
