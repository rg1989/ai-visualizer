#!/usr/bin/env python3
"""watch.py -- "watch over X": keep an eye on the camera OR the screen cheaply; wake the assistant only on change.

The model never sees a live stream (no API takes one). What it can see is a
few frames. So this keeps the MODEL out of the loop: a 1 fps grayscale
thumbnail (ffmpeg for the camera, screencapture for the screen), a numpy
frame difference decides whether the picture changed, and only then a burst
of full frames is saved and ONE event line is dropped into the voice line's
typed channel (.voice_typed), which the assistant handles like any spoken
turn: it Reads the frames, decides whether anything is worth saying, and
says it -- or stays quiet.

Every event costs model calls, so a watch cannot be forgotten open: it stops
ITSELF after --for minutes (default 30) or --max-events events (default 40),
whichever comes first, tells the voice line that it did, and a pinned timer
card on the glass counts down while it runs. The person extends it by saying
so; nothing here re-arms on its own.

Opt-in, loud, and forgetful: the camera LED is on the whole time a camera
watch runs, it announces nothing by itself (the assistant does, once, when
told to start), it keeps only the last burst on disk, and it never records.
The first screen watch asks macOS for Screen Recording permission for the app
running the voice line; without it the frames show only the wallpaper.

  watch.py start "what to watch for" [--source camera|screen] [--for MINUTES] [--max-events N]
                 [--interval 1] [--threshold 12] [--cooldown 45] [--dry-run]
  watch.py stop | status

ponytail: mean absolute pixel difference on a 160x90 grayscale frame is the
whole detector. A lamp switching on trips it; a cat walking by trips it; a
window switch trips it; that is fine, the assistant is the second filter.
Swap in a tiny person detector (ONNX) if the false wakes annoy anyone.
"""
import os, sys, time, json, signal, subprocess, argparse, pathlib, urllib.request
import numpy as np

W, H = 160, 90
HERE = pathlib.Path(__file__).resolve().parent
STATE = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "watch"
PIDF = STATE / "watch.pid"
INFOF = STATE / "watch.json"
try:
    CFG = json.load(open(HERE.parent / "ai-visualizer.json"))
except Exception:
    CFG = {}
BUS = pathlib.Path(os.environ.get("WATCH_BUS") or CFG.get("bus_dir") or os.path.expanduser("~/my-agent/backtalk"))
GLASS = f"http://127.0.0.1:{int(CFG.get('port', 8790))}/cmd"
BURST = 3          # full frames saved per event
BURST_GAP = 0.7    # seconds between them
FULL_WIDTH = 1400  # a screen frame is downscaled to this: a vision model needs no more


# --- frame sources: a generator of 160x90 grayscale thumbnails, and a full-frame grab for events

def camera_frames(interval):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "fatal", "-nostdin", "-f", "avfoundation", "-framerate", "30",
           "-pixel_format", "uyvy422", "-video_size", "1280x720", "-i", "0",
           "-vf", f"fps={1/interval},scale={W}:{H}", "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    try:
        while True:
            buf = proc.stdout.read(W * H)
            if len(buf) < W * H:
                return
            yield np.frombuffer(buf, dtype=np.uint8).astype(np.int16)
    finally:
        proc.kill()


def camera_grab(path):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "avfoundation",
                    "-framerate", "30", "-pixel_format", "uyvy422", "-video_size", "1280x720",
                    "-i", "0", "-frames:v", "1", "-q:v", "3", str(path)],
                   stdin=subprocess.DEVNULL, check=True, timeout=15)


def screen_frames(interval):
    from PIL import Image
    shot = STATE / "screen.jpg"
    while True:
        subprocess.run(["screencapture", "-x", "-t", "jpg", str(shot)], check=True, timeout=15)
        yield np.asarray(Image.open(shot).convert("L").resize((W, H)), dtype=np.int16).ravel()
        time.sleep(interval)


def screen_grab(path):
    from PIL import Image
    subprocess.run(["screencapture", "-x", "-t", "jpg", str(path)], check=True, timeout=15)
    im = Image.open(path); im.thumbnail((FULL_WIDTH, FULL_WIDTH)); im.save(path, quality=85)


SOURCES = {"camera": (camera_frames, camera_grab), "screen": (screen_frames, screen_grab)}


# --- the two ways out: a line to the voice line, a card on the glass

def notify(line):
    """One JSON string per line on the typed channel: one line, one turn."""
    with open(BUS / ".voice_typed", "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def glass(body):
    """One verb to the glass. None when no face is running -- the watch works without it."""
    try:
        req = urllib.request.Request(GLASS, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None


def run(a, frames, grab):
    STATE.mkdir(parents=True, exist_ok=True)
    PIDF.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # `stop` must reach the finally below
    log = lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)
    ends = time.time() + a.minutes * 60
    ends_hm = time.strftime("%H:%M", time.localtime(ends))
    info = {"what": a.what, "source": a.source, "ends": ends, "events": 0, "max_events": a.max_events}
    INFOF.write_text(json.dumps(info))
    card = glass({"a": "show", "type": "timer", "new": True, "pin": True,
                  "until": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ends)),
                  "label": f"watching the {a.source}: {a.what}"})
    card_id = card.get("id") if card and card.get("ok") else None
    log(f"the {a.source} is watching for: {a.what!r} until {ends_hm} or {a.max_events} events "
        f"({a.interval}s frames, threshold {a.threshold}, cooldown {a.cooldown}s{', DRY RUN' if a.dry_run else ''})")
    prev, last_event, n, reason, told = None, 0.0, 0, None, False
    own = (" Changes that are only the assistant's own face, glass or cards are not it."
           if a.source == "screen" else "")
    try:
        for cur in frames:
            n += 1
            if time.time() >= ends:
                reason = f"its {a.minutes:g}-minute limit"; break
            if prev is not None:
                diff = float(np.abs(cur - prev).mean())
                if diff >= a.threshold and time.time() - last_event >= a.cooldown:
                    last_event = time.time(); info["events"] += 1; INFOF.write_text(json.dumps(info))
                    last = info["events"] >= a.max_events
                    stamp = time.strftime("%H:%M:%S")
                    log(f"CHANGE diff={diff:.1f} -> event {info['events']}/{a.max_events}")
                    saved = []
                    if not a.dry_run:
                        for i in range(BURST):
                            f = STATE / f"event-{i}.jpg"
                            try: grab(f); saved.append(str(f))
                            except Exception as e: log(f"frame {i} failed: {e}")
                            time.sleep(BURST_GAP)
                        notify(f"[watch] you were asked to watch over: {a.what}. The {a.source} saw a change at {stamp} "
                               f"(motion score {diff:.0f}). Frames: {', '.join(saved)}. Read them. If what you were asked to "
                               f"watch for is happening, say so in one short line. If it is not, reply with exactly "
                               f"[silent] and nothing else -- that reply is swallowed and nobody hears it.{own}"
                               + (f" This was event {info['events']} of {a.max_events}, so the watch is now OFF: say so in "
                                  f"the same breath, and start it again only if the person asks." if last else ""))
                        told = last
                        log("event sent to the voice line")
                    if last:
                        reason = f"its limit of {a.max_events} events"; break
                elif diff >= a.threshold:
                    log(f"change diff={diff:.1f} (in cooldown)")
                elif n % 30 == 0:
                    log(f"quiet (diff {diff:.1f})")
            prev = cur
        reason = reason or "the picture stopped coming"
        if not told:
            notify(f"[watch] the watch over {a.what} ({a.source}) switched itself off at {time.strftime('%H:%M')} "
                   f"after reaching {reason}, as every watch does unless given a longer --for. Tell the person in one "
                   f"short line; start it again only if they ask.")
    finally:
        getattr(frames, "close", lambda: None)()
        if card_id: glass({"a": "dismiss", "id": card_id})
        PIDF.unlink(missing_ok=True); INFOF.unlink(missing_ok=True)
        log(f"watch off ({reason or 'stopped'})")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["start", "stop", "status"]); ap.add_argument("what", nargs="?", default="anything unusual")
    ap.add_argument("--source", choices=sorted(SOURCES), default="camera")
    ap.add_argument("--for", dest="minutes", type=float, default=30.0, help="stop by itself after this many minutes")
    ap.add_argument("--max-events", type=int, default=40, help="stop by itself after this many events")
    ap.add_argument("--interval", type=float, default=1.0); ap.add_argument("--threshold", type=float, default=12.0)
    ap.add_argument("--cooldown", type=float, default=45.0); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    pid = int(PIDF.read_text()) if PIDF.exists() else None
    alive = pid and subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode == 0
    if a.cmd == "status":
        if not alive: print("watch off"); return
        i = json.loads(INFOF.read_text()) if INFOF.exists() else {}
        print(f"watch ON (pid {pid}): the {i.get('source', '?')} is watching for: {i.get('what', '?')}; stops by itself at "
              f"{time.strftime('%H:%M', time.localtime(i.get('ends', 0)))} or after {i.get('max_events', '?')} events "
              f"({i.get('events', 0)} so far)"); return
    if a.cmd == "stop":
        if alive: os.kill(pid, signal.SIGTERM); print("watch stopping")
        else: print("watch was not running")
        return
    if alive: print(f"watch already running (pid {pid}); stop it first"); return
    frames, grab = SOURCES[a.source]
    run(a, frames(a.interval), grab)


if __name__ == "__main__":
    main()
