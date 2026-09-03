#!/usr/bin/env python3
"""task.py — long work as a child process, so the voice stays free.

  task.py agent "<label>" "<brief>"      a second brain (claude -p: same
                                          config, tools and permissions as
                                          the assistant) does the brief
  task.py start "<label>" -- <command>   a plain long command
  task.py stop  <id|label>               end it, whole process group
  task.py list                           running + recently ended
  task.py --selfcheck                    prove the flow in a temp bus

Each task runs under a small supervisor in its own session, detached
from the shell that started it, so the assistant's tool call returns at
once and she can keep listening. Records are <bus>/.tasks/<id>.json,
output <id>.log; the face's /tasks serves them to the tasks card. When
a task ends the supervisor posts "[task finished: label] ..." to the
face's /say, which the voice line hands the assistant as a turn -- she
says the outcome. The bus dir is resolved exactly as server.py does
(ai-visualizer.json "bus_dir", else the ai-visualizer folder).

ponytail: files and a poll, no daemon, no queue, no IPC.
"""
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # ai-visualizer/
try:
    CFG = json.loads((HERE / "ai-visualizer.json").read_text())
except (OSError, ValueError):
    CFG = {}
BUS = Path(os.environ.get("TASKS_BUS") or CFG.get("bus_dir") or HERE).expanduser()
DIR = BUS / ".tasks"
PORT = int(os.environ.get("GLASS_PORT") or CFG.get("port") or 8790)
QUIET = os.environ.get("TASK_NO_GLASS") == "1"     # selfcheck: no face, no /say
WORKER = ("You are a background task started by the voice assistant for the "
          "person. Do the brief completely and autonomously: decide sensibly, "
          "never ask a question, never wait for input. Never start or open the "
          "face, its server, or the voice line. End with a plain-text result "
          "under 1200 characters the assistant can say out loud: what was done, "
          "what was found, where any files are.")


def _post(path, obj):
    """Tell the face. It may be down; a task is not about the face."""
    if QUIET:
        return
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/{path}", data=json.dumps(obj).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass


def _load(tid):
    return json.loads((DIR / f"{tid}.json").read_text())


def _save(rec):
    tmp = DIR / f"{rec['id']}.json.tmp"
    tmp.write_text(json.dumps(rec))
    tmp.replace(DIR / f"{rec['id']}.json")


def _all():
    out = []
    for p in DIR.glob("t*.json"):
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            pass
    return sorted(out, key=lambda r: r["started"])


def _claude():
    """The assistant's own claude first (same version, same behaviour),
    then whatever claude is on PATH."""
    for pat in (BUS / ".venv/lib/python*/site-packages/claude_agent_sdk/_bundled/claude",
                HERE.parent / "backtalk/.venv/lib/python*/site-packages/claude_agent_sdk/_bundled/claude"):
        hit = glob.glob(str(pat))
        if hit:
            return hit[0]
    return shutil.which("claude") or "claude"


def _perm():
    """Headless cannot ask out loud: the assistant's bypass carries over,
    anything else becomes acceptEdits (gated calls just fail, in the log)."""
    try:
        mode = json.loads((BUS / "backtalk.json").read_text()).get("permission_mode", "ask")
    except (OSError, ValueError):
        mode = "ask"
    return "bypassPermissions" if mode == "bypassPermissions" else "acceptEdits"


def start(label, kind, cmd):
    DIR.mkdir(parents=True, exist_ok=True)
    tid = "t" + uuid.uuid4().hex[:6]
    rec = {"id": tid, "label": str(label)[:60], "kind": kind, "cmd": cmd,
           "status": "running", "started": time.time(), "ended": None,
           "exit": None, "pid": None, "result": ""}
    _save(rec)
    # the supervisor: its own session, no tty, outlives the caller's shell
    subprocess.Popen([sys.executable, __file__, "_run", tid],
                     start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     env=os.environ.copy())
    _post("cmd", {"a": "show", "type": "tasks", "pin": True})
    print(f"task {tid} started: {rec['label']}  (log: {DIR / (tid + '.log')})")
    return tid


def _run(tid):
    rec = _load(tid)
    # a nested claude refuses to start inside a Claude Code shell unless
    # these are gone; everything else (config dir, provider) carries over
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    if rec["kind"] == "agent":
        argv = [_claude(), "-p", rec["cmd"], "--output-format", "text",
                "--permission-mode", _perm(), "--append-system-prompt", WORKER]
        cwd = str(HERE.parent)        # the agent dir: her CLAUDE.md and skills
    else:
        argv = rec["cmd"] if isinstance(rec["cmd"], list) and len(rec["cmd"]) > 1 \
            else ["/bin/bash", "-lc", rec["cmd"] if isinstance(rec["cmd"], str) else rec["cmd"][0]]
        cwd = os.path.expanduser("~")
    with open(DIR / f"{tid}.log", "ab") as log:
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                             stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True)
        rec["pid"] = p.pid
        _save(rec)
        code = p.wait()
    rec = _load(tid)                   # stop() may have marked it meanwhile
    rec["ended"] = time.time()
    rec["exit"] = code
    if rec["status"] == "running":
        rec["status"] = "done" if code == 0 else "failed"
    try:
        out = (DIR / f"{tid}.log").read_text(errors="replace").strip()
    except OSError:
        out = ""
    rec["result"] = out[-1500:]
    _save(rec)
    if rec["status"] in ("done", "failed"):
        mins = (rec["ended"] - rec["started"]) / 60
        head = (f"[task finished: {rec['label']}] "
                + ("done" if rec["status"] == "done" else f"FAILED (exit {code})")
                + f" after {mins:.1f} min. ")
        body = rec["result"] if rec["kind"] == "agent" else \
            ("last output: " + rec["result"][-600:] if rec["result"] else "no output")
        _post("say", {"text": (head + body)[:1900]})


def stop(key):
    for r in reversed(_all()):
        if r["status"] == "running" and (r["id"] == key or key.lower() in r["label"].lower()):
            r["status"] = "stopped"
            r["ended"] = time.time()
            _save(r)
            if r["pid"]:
                try:
                    os.killpg(r["pid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass
            print(f"task {r['id']} stopped: {r['label']}")
            return 0
    print(f"no running task matches {key!r}")
    return 1


def listing():
    now, shown = time.time(), 0
    for r in reversed(_all()):
        if r["status"] != "running" and now - (r["ended"] or now) > 600:
            continue
        mins = ((r["ended"] or now) - r["started"]) / 60
        print(f"{r['id']}  {r['status']:8} {mins:5.1f} min  {r['label']}")
        shown += 1
    if not shown:
        print("no tasks")


def selfcheck():
    """A shell task runs to done with its output kept; a long one stops
    on request and its process is gone. Temp bus, no face."""
    tmp = tempfile.mkdtemp(prefix="tasks-")
    env = dict(os.environ, TASKS_BUS=tmp, TASK_NO_GLASS="1")
    me = [sys.executable, __file__]
    out = subprocess.run(me + ["start", "echo test", "--", "sh", "-c", "echo hi; sleep 1"],
                         env=env, capture_output=True, text=True).stdout
    tid = out.split()[1]
    for _ in range(40):
        rec = json.loads(Path(tmp, ".tasks", tid + ".json").read_text())
        if rec["status"] != "running":
            break
        time.sleep(0.25)
    assert rec["status"] == "done" and "hi" in rec["result"], rec
    out = subprocess.run(me + ["start", "long sleep", "--", "sleep", "30"],
                         env=env, capture_output=True, text=True).stdout
    tid = out.split()[1]
    time.sleep(0.8)
    assert subprocess.run(me + ["stop", "long"], env=env).returncode == 0
    time.sleep(0.5)
    rec = json.loads(Path(tmp, ".tasks", tid + ".json").read_text())
    assert rec["status"] == "stopped", rec
    try:
        os.kill(rec["pid"], 0)
        assert False, "sleep still alive after stop"
    except ProcessLookupError:
        pass
    print("task.py selfcheck: pass")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
    elif a[0] == "--selfcheck":
        selfcheck()
    elif a[0] == "agent" and len(a) == 3:
        start(a[1], "agent", a[2])
    elif a[0] == "start" and len(a) >= 4 and a[2] == "--":
        start(a[1], "shell", a[3:] if len(a) > 4 else a[3])
    elif a[0] == "stop" and len(a) == 2:
        sys.exit(stop(a[1]))
    elif a[0] == "list":
        listing()
    elif a[0] == "_run" and len(a) == 2:
        _run(a[1])
    else:
        print(__doc__)
        sys.exit(2)
