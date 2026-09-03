# backtalk: talk to your Claude Code agent out loud.
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
"""The signal bus — tiny files any other program can watch.

The voice line leaves notes; faces read the notes. That one dumb trick
is the whole integration surface:

  .voice_state        idle | listening | thinking | speaking
  .voice_waveform     JSON {ts, samples: [64 floats]} while audio plays
  .voice_loading_pid  exists while the thinking sound is playing
  .voice_rate_limits  JSON {window: {utilization, resets_at}} — only
                      written when show_usage is on

Written to signals_dir (default: the repo root). Visualizers built on
this contract just work.

THE BAREHANDS SEAM: set barehands_state_dir in backtalk.json to a
barehands checkout's state/ folder and the same signals are mirrored in
its format (state/state as a bare word, state/wave.json normalized
0..1) — the on-screen ring becomes your agent's face with zero glue.

Every write is wrapped: the bus must never crash the voice line.
"""
import json
import os
import subprocess
import sys

from backtalk.vlog import log
import time

import numpy as np

from backtalk.config import CFG

_DIR = CFG["signals_dir"]
_STATE_FILE = os.path.join(_DIR, ".voice_state")
_WAVEFORM_FILE = os.path.join(_DIR, ".voice_waveform")
_LOADING_PID_FILE = os.path.join(_DIR, ".voice_loading_pid")
_DIRECTION_FILE = os.path.join(_DIR, ".voice_direction")
_REPLY_DONE_FILE = os.path.join(_DIR, ".voice_reply_done")
_RATE_LIMIT_FILE = os.path.join(_DIR, ".voice_rate_limits")
# ponytail: the four states say WHAT she is doing, never how far along.
# Transcribing and synthesising are the two real waits inside "listening"
# and "thinking", and to a person they look like a hang. One more dumb
# file, same contract as the rest of the bus; empty means "no detail".
_STAGE_FILE = os.path.join(_DIR, ".voice_stage")
# Absent until the voice line has finished warming and is about to speak.
# The face draws a curtain while it is missing: the server comes up first,
# so without this the face looks alive a good while before anything is.
_READY_FILE = os.path.join(_DIR, ".voice_ready")

_BH = CFG.get("barehands_state_dir") or ""
_BH_STATE = os.path.join(_BH, "state") if _BH else ""
_BH_WAVE = os.path.join(_BH, "wave.json") if _BH else ""

_THINKING_SOUND = CFG.get("thinking_sound") or ""

_WAVEFORM_MIN_INTERVAL = 1.0 / 15   # ~15 writes/sec is plenty for 60fps reads
_last_waveform_write = 0.0
_static_proc: subprocess.Popen | None = None


# Written by the FACE, not by us -- the one inbound line on this bus.
# Non-empty and fresh = the glass player is making sound. See
# media_playing() for why it is read by mtime and why it does not simply
# close the mic like every other sound on this bus does.
_MEDIA_FILE = os.path.join(_DIR, ".voice_media")
MEDIA_STALE_S = 30.0

_MIC_FILE = os.path.join(_DIR, ".voice_mic")
# Last thing published on each of these two lines, so unsummoned()
# below can answer without re-reading the bus.
_mic_mode = ""
_state = "idle"


def unsummoned() -> bool:
    """True while the mic is in wake mode and nothing has summoned her.

    Both ways of being summoned -- the window a bare wake word opens,
    and the follow-up window after she speaks -- publish state
    "listening", so idle here means the room is simply talking. Every
    utterance the VAD catches in that condition is a speculative gate
    check the room's own conversation will almost always lose, and
    nothing about it should reach the face. Same shape as
    cue_playing(): a bit the bus already knows, exposed for the one
    caller who cares."""
    return _mic_mode == "wake" and _state == "idle"


PTT_STALE_S = 1.0


def set_ptt_key(name: str = ""):
    """Publish the talk key's name for the face page to bind (config
    ptt_scope "face"). Empty = the page binds nothing. Never raises."""
    try:
        with open(os.path.join(_DIR, ".voice_ptt_key"), "w") as f:
            f.write(str(name or "")[:32])
    except OSError:
        pass


def face_ptt() -> tuple[bool, int]:
    """(held, n): the face page's talk key, as the visualizer dropped it
    in .voice_ptt. n counts presses. held reads False once the page's
    re-post is older than PTT_STALE_S -- a tab closed mid-hold must not
    leave the mic open. Path resolved per call so a test can move _DIR."""
    try:
        with open(os.path.join(_DIR, ".voice_ptt")) as f:
            d = json.load(f)
        held = (bool(d.get("held"))
                and time.time() - float(d.get("t", 0)) < PTT_STALE_S)
        return held, int(d.get("n", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return False, 0


def set_mic(mode: str, hot: bool = False, hush: bool = False):
    """Publish the microphone mode ("ptt" | "open" | "wake") so a face
    can show at a glance whether the room is being listened to. hot
    marks a live wake-mode follow-up window (no wake word needed right
    now). hush marks "stop listening": the room is off the record until
    the person comes back, and the overlay window hides itself on it
    (facewin fades at once and ignores her "Stopped."). Never raises."""
    global _mic_mode
    _mic_mode = mode
    try:
        with open(_MIC_FILE, "w") as f:
            f.write(mode + (" hot" if hot else "") + (" hush" if hush else ""))
    except OSError:
        pass


_CHAT_FILE = os.path.join(_DIR, ".voice_chat")
_chat = {"rev": 0, "msgs": []}      # rolling window, newest last
_CHAT_MAX = 120
# say() publishes from whatever thread holds the mouth; the asyncio
# loop publishes turns — one lock keeps appends and writes whole.
import threading as _threading
_chat_lock = _threading.Lock()


def _chat_locked():
    return _chat_lock


def _chat_write():
    _chat["rev"] += 1
    try:
        tmp = _CHAT_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_chat, f)
        os.replace(tmp, _CHAT_FILE)
    except OSError:
        pass


def chat_add(role, who, text, mid=None, dur=None):
    """Append a line of the conversation for the face's chat crawl.
    role: "you" | "agent". who: the identified speaker or None. mid:
    a message id — passing the SAME mid appends to that message's text
    instead of adding a new bubble, which is how a streaming reply
    grows in place sentence by sentence. dur: seconds of audio this
    text spans — the crawl types the fresh segment out over exactly
    that long, so nobody reads ahead of the voice ("seg" below marks
    where the fresh text starts). Never raises."""
    try:
        text = str(text).strip()
        if not text:
            return
        with _chat_locked():
            if mid and _chat["msgs"] \
                    and _chat["msgs"][-1].get("mid") == mid:
                m = _chat["msgs"][-1]
                off = len(m["text"]) + 1
                m["text"] += " " + text
            else:
                m = {"role": role, "who": who,
                     "text": text, "t": time.time(), "mid": mid}
                off = 0
                _chat["msgs"].append(m)
                del _chat["msgs"][:-_CHAT_MAX]
            if dur:
                m["seg"] = {"off": off, "dur": round(float(dur), 2),
                            "t": time.time()}
            else:
                m.pop("seg", None)
            _chat_write()
    except Exception:
        pass


_BRAIN_FILE = os.path.join(_DIR, ".voice_brain")


def set_brain(provider: str):
    """Publish which brain is answering ("claude" | "zai") so a face can
    show it and the settings picker can open on the choice that is
    actually live. One bare token, same contract as .voice_mic — the
    picker's own request goes the other way, as .voice_brain_pick.
    Never raises."""
    try:
        tmp = _BRAIN_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(provider)
        os.replace(tmp, _BRAIN_FILE)
    except OSError:
        pass


def clear_brain():
    """Remove the brain file on shutdown — same reason as clear_mic: a
    dead voice line must not keep claiming whose model is thinking."""
    try:
        os.remove(_BRAIN_FILE)
    except OSError:
        pass


def clear_mic():
    """Remove the mic file on shutdown so a dead voice line never
    leaves a stale 'OPEN MIC' badge lying about the room."""
    try:
        os.remove(_MIC_FILE)
    except OSError:
        pass
    # Every shutdown path already goes through here (the Ctrl-C out of
    # the launch picker, the brain-connect failure, and the finally in
    # run()), so the brain badge dies with the mic badge and no caller
    # has to remember a second line. clear_brain() is idempotent, so an
    # explicit call alongside a clear_mic() call is harmless.
    clear_brain()


def set_state(name: str):
    """Write the state. Never raises — the show must go on."""
    global _state
    if name != _state:
        # Every transition, with its caller: the face's animations follow
        # this file and nothing else, so a blink nobody meant is found here.
        try:
            _f = sys._getframe(1)
            _who = f"{_f.f_code.co_name}:{_f.f_lineno}"
        except Exception:
            _who = "?"
        log(f"[state] {_state} -> {name}  ({_who})")
    _state = name
    try:
        with open(_STATE_FILE, "w") as f:
            f.write(name)
    except OSError:
        pass
    if _BH_STATE:
        try:
            with open(_BH_STATE, "w") as f:
                f.write(name)
        except OSError:
            pass


def state() -> str:
    """The last state written. In-process only; good enough for the
    snapshot/restore around a mic-open blink."""
    return _state


def set_stage(name: str = ""):
    """Sub-step detail for the current state (""=none). Never raises."""
    try:
        with open(_STAGE_FILE, "w") as f:
            f.write(name or "")
    except OSError:
        pass


def set_ready(on: bool):
    """Flip the "warmed up and about to speak" flag. Never raises."""
    try:
        if on:
            with open(_READY_FILE, "w") as f:
                f.write("1")
        else:
            os.remove(_READY_FILE)
    except OSError:
        pass


def feed_waveform(pcm: np.ndarray):
    """Feed one PCM block (int16) — throttled, downsampled to 64 points.

    Also re-asserts state="speaking" on the same throttle: this only runs
    while the mouth is audibly playing, so the bus self-heals within
    ~70ms if a stray writer stomps the state mid-speech. (That self-heal
    rule once closed a bug that took a whole evening to find.)"""
    global _last_waveform_write
    if pcm.size == 0:
        return
    now = time.time()
    if now - _last_waveform_write < _WAVEFORM_MIN_INTERVAL:
        return
    _last_waveform_write = now
    try:
        idx = np.linspace(0, pcm.size - 1, 64).astype(int)
        raw = pcm[idx].astype(float)
        with open(_WAVEFORM_FILE, "w") as f:
            f.write(json.dumps({"ts": now, "samples": raw.tolist()}))
        if _BH_WAVE:
            norm = np.clip(np.abs(raw) / 32768.0, 0.0, 1.0)
            with open(_BH_WAVE, "w") as f:
                f.write(json.dumps({"ts": now, "samples": norm.tolist()}))
    except (OSError, ValueError):
        pass
    set_state("speaking")


def direction(items):
    """Stage directions the agent wrote into its reply, published at the
    moment the audio carrying them starts playing.

    Your agent can emit `<<anything>>` inline and backtalk will never speak
    it. What the tag MEANS is deliberately not backtalk's business: it
    publishes the raw strings and something else decides. That is the whole
    reason this is a file and not a plugin API.

    The timing is the point, and it is the one part a watcher cannot do for
    itself: these fire when the sentence becomes AUDIBLE, not when the model
    generated it. A screen cue lands on the spoken word instead of seconds
    early. Never raises."""
    if not items:
        return
    try:
        with open(_DIRECTION_FILE, "w") as f:
            f.write(json.dumps({"ts": time.time(), "directions": list(items)}))
    except OSError:
        pass


def reply_done():
    """One reply has finished speaking and its audio has fully drained.

    Distinct from the state going idle, which also happens in the gaps
    BETWEEN sentences of the same reply. Anything waiting for the agent to
    genuinely stop talking wants this rather than a state flicker. Never
    raises."""
    try:
        with open(_REPLY_DONE_FILE, "w") as f:
            f.write(json.dumps({"ts": time.time()}))
    except OSError:
        pass


_rate_limits: dict = {}


def set_rate_limit(window: str, utilization, resets_at):
    """One usage window's reading — how much of the plan is spent.

    Merged rather than replaced, because the reading arrives one window
    at a time and a face wants to draw both at once. `utilization` is a
    0..1 fraction (or None when the window has not reported a number
    yet, which is a real state and not an error); `resets_at` is a unix
    epoch.

    NOTHING CALLS THIS UNLESS show_usage IS ON. That is a privacy
    default, not a performance one: this is the account holder's own
    spend, and it renders on a face that may well be pointed at a
    camera. It never appears without being asked for. (Community fix,
    ai-visualizer issue #1.)

    Never raises."""
    if not window:
        return
    _rate_limits[window] = {"utilization": utilization,
                            "resets_at": resets_at}
    try:
        with open(_RATE_LIMIT_FILE, "w") as f:
            f.write(json.dumps(_rate_limits))
    except OSError:
        pass


def _player_cmd(path: str) -> list[str] | None:
    if sys.platform == "darwin":
        return ["afplay", "-v", "0.35", path]
    for cand in ("ffplay", "aplay", "paplay"):
        from shutil import which
        if which(cand):
            if cand == "ffplay":
                return ["ffplay", "-nodisp", "-autoexit", "-loglevel",
                        "quiet", "-volume", "35", path]
            return [cand, path]
    return None


# ponytail: the chimes come out of the SPEAKERS, so the mic hears them, and
# a 240ms chirp clears the VAD and reaches whisper -- which dutifully writes
# it down as "Beep." and sends it to the brain as something the owner said.
# Nothing stops these sounds (they are fire-and-forget), so instead we
# remember when each one goes quiet and let the mic gate skip that stretch.
_cue_until = 0.0        # monotonic time the last cue stops being audible
_cue_secs = {}          # path -> duration, read once
CUE_TAIL = 0.15         # speaker -> mic travel, plus one VAD frame


def _cue_duration(path) -> float:
    """The wav's real length, so a longer cue is covered and a short one
    costs no more silence than it has to. Unreadable -> assume the
    longest cue that ships."""
    if path not in _cue_secs:
        try:
            import wave
            with wave.open(path, "rb") as w:
                _cue_secs[path] = w.getnframes() / float(w.getframerate())
        except Exception:
            _cue_secs[path] = 0.5
    return _cue_secs[path]


def cue_playing() -> bool:
    """True while a chime we fired is still in the air. The mic gate asks
    this so our own sounds never come back as the owner's words."""
    return time.monotonic() < _cue_until


def static_playing() -> bool:
    """True while the thinking sound is coming out of the speakers.

    cue_playing()'s docstring promises "our own sounds never come back as
    the owner's words", but it only ever knew about the 160-240ms chimes.
    The thinking sound is 36 SECONDS, and it plays through the same
    speakers during exactly the stretch when the mic is open and the agent
    is otherwise silent -- the loudest, longest noise the system makes was
    the one thing no mic gate could see."""
    p = _static_proc
    return p is not None and p.poll() is None


def media_playing() -> bool:
    """True while the face's player is making sound.

    The third speaker this gate has had to learn about, and the only one
    we do not fire ourselves. The music comes out of a cross-origin
    YouTube iframe on the face page; the mic is sounddevice on the host.
    Nothing joins them: no reference signal, no echo canceller anywhere in
    this stack, so the music CANNOT be subtracted from what she hears.

    So unlike cue_playing() and static_playing(), this one must NOT close
    the mic -- deafness while music plays is worse than the problem, and
    talking over your own music is the entire point of a voice assistant.
    It makes the WAKE WORD REQUIRED instead (see main.py), which is the
    carve-out Alexa's follow-up mode and Google's continued conversation
    both ship for exactly this case.

    Read by MTIME and expired, because the writer is a browser tab: a
    closed tab, a crashed renderer or a killed server would otherwise
    leave the file behind and demand the name forever. The page re-posts
    every 10s while it plays, so the staleness window is the failure
    mode's whole cost."""
    try:
        return time.time() - os.path.getmtime(_MEDIA_FILE) < MEDIA_STALE_S
    except OSError:
        return False


def play_cue(path):
    """Fire-and-forget one-shot sound (the wake-mode chimes). Short
    files only — nothing tracks or stops these. Never raises."""
    global _cue_until
    if not path or not os.path.exists(path):
        return
    _cue_until = time.monotonic() + _cue_duration(path) + CUE_TAIL
    if sys.platform == "win32":
        # No afplay and usually no ffplay on Windows; winsound ships
        # with Python and plays a wav asynchronously.
        try:
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME
                               | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:
            pass
        return
    cmd = _player_cmd(path)
    if not cmd:
        return
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError:
        pass


def static_start():
    """Optional thinking sound — plays while the brain works."""
    global _static_proc
    if not _THINKING_SOUND or not os.path.exists(_THINKING_SOUND):
        return
    static_stop()
    cmd = _player_cmd(_THINKING_SOUND)
    if not cmd:
        return
    try:
        _static_proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(_LOADING_PID_FILE, "w") as f:
            f.write(str(_static_proc.pid))
    except OSError:
        _static_proc = None


def static_stop():
    global _static_proc
    if _static_proc is not None:
        try:
            _static_proc.terminate()
        except OSError:
            pass
        _static_proc = None
    try:
        os.remove(_LOADING_PID_FILE)
    except OSError:
        pass
