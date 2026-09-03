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
"""The mouth — streaming sentence-chunked TTS, played through one
long-lived output stream.

Default engine: Kokoro, in-process. Local, free, no server, no API key,
~0.2s to first audio once warm. Optional premium engine: ElevenLabs on
YOUR key — read from the system keychain, never from a file (see
_get_elevenlabs_key) — with Kokoro as the automatic fallback: the voice
degrades instead of going mute if the cloud fails.

Sentences are synthesized one at a time and queued for playback, so the
first sentence is audible while later ones are still rendering. Playback
is cancellable mid-word: set the stop event and the speaker goes silent
within one audio block plus the device buffer (~0.15s).

HARD-WON AUDIO LAW #1 — ONE long-lived OutputStream, reused for every
sentence for the life of the process. A fresh stream per sentence gives
an audible onset blip or a beat of dead air on plenty of audio setups
(USB interfaces, Bluetooth, streaming mixers that latch onto each new
stream late). Proven by A/B test; do not "simplify" this away.

HARD-WON AUDIO LAW #2 — buffer ~0.75s of synthesized audio before a
sentence starts playing, so a slower machine never underruns into
slow-motion garble.
"""
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time

import numpy as np
import sounddevice as sd

from backtalk import signals
from backtalk.config import CFG
from backtalk.ears import PA_LOCK
from backtalk.vlog import log

KOKORO_RATE = 24000
EL_RATE = 44100
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

_pipe = None
_pipe_lang = None
_pipe_lock = threading.Lock()


def _ensure_espeak():
    """kokoro phonemizes through system espeak-ng (its bundled loader
    ships a broken build path — found the hard way; upstream's own docs
    say install the system package). Help phonemizer find it in the
    usual homes when the env isn't already set."""
    if os.environ.get("PHONEMIZER_ESPEAK_LIBRARY"):
        return
    candidates = (
        "/opt/homebrew/lib/libespeak-ng.dylib",       # macOS arm64 (brew)
        "/usr/local/lib/libespeak-ng.dylib",          # macOS intel (brew)
        "/usr/lib/x86_64-linux-gnu/libespeak-ng.so.1",  # debian/ubuntu
        "/usr/lib/libespeak-ng.so.1",                 # other linux
        "C:\\Program Files\\eSpeak NG\\libespeak-ng.dll",       # windows
        "C:\\Program Files (x86)\\eSpeak NG\\libespeak-ng.dll",
    )
    for lib in candidates:
        if os.path.exists(lib):
            os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = lib
            break


# Every espeak library filename phonemizer might copy, on any platform. A
# directory holding exactly one of these and nothing else is a phonemizer
# scratch dir and is not plausibly anything else.
_ESPEAK_LIB_NAMES = (
    "espeak-ng.dll",
    "libespeak-ng.dll",
    "libespeak-ng.so",
    "libespeak-ng.so.1",
    "libespeak-ng.dylib",
)


def _is_orphan_espeak_tempdir(path: str) -> bool:
    """True only for a directory whose ENTIRE contents are one espeak
    library. That signature is what makes it safe to point a delete at a
    shared temp folder: one file, and its name is one of five."""
    try:
        entries = os.listdir(path)
    except OSError:
        return False
    return len(entries) == 1 and entries[0] in _ESPEAK_LIB_NAMES


def _sweep_orphan_espeak_tempdirs():
    """Delete espeak scratch dirs left behind by previous runs.

    phonemizer copies the espeak shared library into a fresh temp dir for
    every backend it builds, because espeak-ng keeps its state in globals
    and the loader refuses the same file twice. Kokoro builds several
    backends, so ONE start leaves several behind.

    On POSIX that cleanup rides a finalizer and usually happens. On
    Windows phonemizer can only register it with atexit, and atexit does
    not run when a process is KILLED rather than exited -- so anything
    stopping the voice line by terminating it, which is most launchers and
    every supervisor, leaks every directory it ever made. Sixty had piled
    up on the machine where this was found, and fifteen were sitting on
    the author's own Mac when it was reviewed: the POSIX path is not as
    reliable as it looks either. The count only ever grows.

    Patching phonemizer where it is installed is not a fix, because the
    launcher runs a dependency sync that would overwrite it. Sweeping at
    our own startup bounds the total at one run's worth instead.

    Two things make deleting from a shared temp folder safe, and only the
    first is ours: the signature above is narrow enough that nothing else
    matches it, and anything we are not permitted to remove raises and is
    skipped. On Windows a loaded library cannot be deleted at all, so a
    live instance is protected by the OS rather than by us noticing it.
    POSIX does not work that way, but a process that has already mapped
    the library keeps it after the unlink, so a running instance is
    unharmed either way.
    """
    root = tempfile.gettempdir()
    swept = 0
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isdir(path) or not _is_orphan_espeak_tempdir(path):
            continue
        try:
            shutil.rmtree(path)
            swept += 1
        except OSError:
            pass          # in use, or not ours. Leaving it is correct.
    if swept:
        log(f"[mouth] swept {swept} orphaned espeak temp dir(s)")


def warm():
    """Load the Kokoro pipeline (first call downloads the model to the
    HF cache). Called at startup while the greeting text is composed.

    The voice itself is passed per sentence, so switching voices WITHIN
    a language is free; only the language pipeline is cached here. When
    CFG["voice"] moves to another language letter (a theme-voice switch,
    say af_bella -> bm_george), the stale pipeline is dropped and
    reloaded — a few seconds, behind the face's thinking state."""
    global _pipe, _pipe_lang
    with _pipe_lock:
        # The voice name's first letter IS the language pipeline:
        # a=American English, b=British English, e/f/h/i/j/p/z = the
        # other shipped languages. bm_lewis -> 'b'.
        lang = (CFG["voice"] or "bm_lewis")[0]
        if _pipe is not None and lang != _pipe_lang:
            log(f"[mouth] voice language '{_pipe_lang}' -> '{lang}' — "
                f"reloading kokoro")
            _pipe = None
        if _pipe is None:
            if _pipe_lang is None:
                # First load of this process only: the sweep must never
                # run again mid-session — a live pipeline's scratch dir
                # matches the orphan signature too.
                _ensure_espeak()
                _sweep_orphan_espeak_tempdirs()
            from kokoro import KPipeline
            log(f"[mouth] loading kokoro (lang '{lang}', "
                f"voice {CFG['voice']})...")
            _pipe = KPipeline(lang_code=lang)
            _pipe_lang = lang
            log("[mouth] voice ready")
    return _pipe


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_RE.split(text.strip()) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


# ponytail: kokoro phonemises through espeak, which reads an ALL-CAPS word
# as an initialism. Measured: "SHODAN" -> /ˌɛsˌAʧˌOdˌiˌAˈɛn/ ("ess-aitch-oh-
# dee-ay-enn"), "JARVIS" -> /ʤˌAˌAˌɑɹvˌiˌIˈɛs/. The persona says its OWN NAME
# constantly, so it mangles it constantly. Kokoro takes inline IPA in
# markdown-link form, which is exact and costs nothing at runtime.
#
# Only names go here. Do NOT "fix" all-caps generally: "API" and "CPU" are
# spelled out because that is how they are said, and this table must never
# start guessing which is which.
SAY_AS = {
    "shodan": "ʃoʊˈdæn",
    "jarvis": "ˈdʒɑɹvɪs",
}
_SAY_AS_RE = re.compile(r"\b(" + "|".join(SAY_AS) + r")\b", re.IGNORECASE)


def _phonemize_names(text: str) -> str:
    """Pin the pronunciation of persona names before kokoro guesses."""
    return _SAY_AS_RE.sub(
        lambda m: f"[{m.group(0).title()}](/{SAY_AS[m.group(0).lower()]}/)",
        text)


def _stream_kokoro(text: str):
    """One sentence -> int16 PCM chunks at 24kHz, in-process."""
    # kokoro only -- ElevenLabs would read the markup out loud.
    text = _phonemize_names(text)
    pipe = warm()
    try:
        speed = float(CFG.get("speed") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    for _, _, audio in pipe(text, voice=CFG["voice"], speed=speed):
        a = np.asarray(audio, dtype=np.float32)
        if a.size:
            yield (np.clip(a, -1.0, 1.0) * 32767).astype(np.int16)


def _stream_elevenlabs(text: str, timeout: float):
    """ElevenLabs -> ffmpeg streaming decode -> int16 PCM at 44.1kHz.

    THE ELEVENLABS DOCTRINE, learned the expensive way:
    - fetch mp3_44100_128 and decode locally (raw 44.1k PCM needs their
      Pro tier; the mp3 decode hides inside network wait anyway)
    - turbo model, stability 0.5, similarity 0.75
    - never the multilingual model for English, never style above 0 —
      both make delivery slow and dull
    - their site previews are MASTERED demo clips; raw API output never
      matches them, so master locally (the ffmpeg chain in config)
    ffmpeg reads stdin as we feed it, so playback still starts before
    synthesis finishes."""
    import subprocess

    import httpx

    el = CFG["elevenlabs"]
    key = _get_elevenlabs_key()
    url = (f"https://api.elevenlabs.io/v1/text-to-speech/"
           f"{el['voice_id']}/stream?output_format=mp3_44100_128")
    proc = subprocess.Popen(
        ["ffmpeg", "-loglevel", "quiet", "-i", "pipe:0",
         "-af", el["master"],
         "-f", "s16le", "-ar", str(EL_RATE), "-ac", "1", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    feed_error: list = []

    def _feed():
        try:
            with httpx.stream("POST", url, headers={"xi-api-key": key},
                              json={"text": text, "model_id": el["model"],
                                    "voice_settings": {
                                        "stability": 0.5,
                                        "similarity_boost": 0.75}},
                              timeout=timeout) as r:
                r.raise_for_status()
                for chunk in r.iter_bytes(chunk_size=4096):
                    proc.stdin.write(chunk)
        except Exception as e:
            feed_error.append(e)
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    t = threading.Thread(target=_feed, daemon=True)
    t.start()
    carry = b""
    got_audio = False
    while True:
        data = proc.stdout.read(8820)
        if not data:
            break
        data = carry + data
        usable = len(data) - (len(data) % 2)
        carry = data[usable:]
        if usable:
            got_audio = True
            yield np.frombuffer(data[:usable], dtype=np.int16)
    proc.wait(timeout=10)
    if feed_error and not got_audio:
        raise feed_error[0]


_el_key_cache: str | None = None


def _key_slot() -> str:
    """The credential-store entry name, so someone who already keeps a key
    under their own name points at it instead of storing a second copy."""
    return str(CFG["elevenlabs"].get("key_slot") or "backtalk-elevenlabs")


def _get_elevenlabs_key() -> str:
    """The API key, from the most secure store available — NEVER from a
    file in this repo. Lookup order:
      1. macOS Keychain, item `backtalk-elevenlabs` by default (change it
         with elevenlabs.key_slot) — seed it once with:
         security add-generic-password -a "$USER" -s backtalk-elevenlabs -T /usr/bin/security -w
         (it prompts for the secret; -T lets this code read it without a
         GUI prompt every launch)
      2. Linux secret-tool (libsecret):
         secret-tool store --label backtalk service backtalk-elevenlabs
      3. the ELEVENLABS_API_KEY environment variable — the last-resort
         fallback, and the only option on Windows for now. Know the
         tradeoff: an export line in a shell profile is a plaintext key
         on disk, which is exactly what the keychain path avoids."""
    global _el_key_cache
    if _el_key_cache is not None:
        return _el_key_cache
    import subprocess
    key = ""
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["security", "find-generic-password",
                                "-s", _key_slot(), "-w"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                key = r.stdout.strip()
        elif sys.platform.startswith("linux"):
            from shutil import which
            if which("secret-tool"):
                r = subprocess.run(["secret-tool", "lookup", "service",
                                    _key_slot()],
                                   capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    key = r.stdout.strip()
    except Exception:
        pass
    _el_key_cache = key or os.environ.get("ELEVENLABS_API_KEY", "")
    return _el_key_cache


def _elevenlabs_ready() -> bool:
    el = CFG["elevenlabs"]
    return bool(el.get("enabled") and el.get("voice_id")
                and _get_elevenlabs_key())


def synth_stream(text: str, timeout: float = 30.0):
    """One sentence -> yields (sample_rate, pcm_chunk) as the TTS
    renders. ElevenLabs when configured, Kokoro otherwise — and Kokoro
    as the fallback on ANY ElevenLabs failure. Degrade, never mute.

    An optional character effect sits on top of either engine, picked
    by "voice_fx" in backtalk.json: "shodan" runs every line through
    backtalk/shodanfx.py (System Shock's SHODAN), "jarvis" through
    backtalk/jarvisfx.py (Iron Man's JARVIS), "lara" through
    backtalk/larafx.py (Lara Croft, the booth-professional Brit) —
    see those modules."""
    gen = _engine_stream(text, timeout)
    fx = str(CFG.get("voice_fx") or "").strip().lower()
    if fx == "shodan":
        from backtalk.shodanfx import shodanize_stream
        gen = shodanize_stream(gen, text)
    elif fx == "jarvis":
        from backtalk.jarvisfx import jarvisize_stream
        gen = jarvisize_stream(gen, text)
    elif fx == "lara":
        from backtalk.larafx import laraize_stream
        gen = laraize_stream(gen, text)
    yield from gen


def _engine_stream(text: str, timeout: float):
    if _elevenlabs_ready():
        try:
            for pcm in _stream_elevenlabs(text, timeout):
                yield EL_RATE, pcm
            return
        except Exception as e:
            log(f"[mouth] elevenlabs failed ({str(e)[:60]}) — "
                f"falling back to {CFG['voice']}")
    for pcm in _stream_kokoro(text):
        yield KOKORO_RATE, pcm


class Mouth:
    def __init__(self):
        from backtalk.ducking import Ducker
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._speaking = threading.Event()
        # The one persistent output stream (audio law #1).
        # Worker-thread-only — never touch from other threads.
        self._out: sd.OutputStream | None = None
        self._out_rate: int | None = None
        self.ducker = Ducker()  # public: PTT ducks for the USER's voice too
        self.last_done = 0.0    # monotonic time the voice last went quiet
        # True while a reply is still being generated. The queue draining
        # does NOT mean the turn ended: the agent says "one moment" and
        # then spends ten seconds in a tool call. Without this the face
        # dropped to idle in that gap and the turn looked dead.
        self.turn_live = False
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    @property
    def speaking(self) -> bool:
        return self._speaking.is_set()

    def say(self, text: str):
        """Queue text (split to sentences) for speech."""
        # Everything the agent says belongs in the conversation crawl —
        # but nothing shows before it is HEARD: each sentence rides its
        # queue item and is published when its audio starts, typed out
        # at speaking pace (greetings, enrollment prompts, verb
        # confirmations, asks — streamed replies go via say_chunk).
        mid = f"m{time.time():.3f}"
        for s in split_sentences(text):
            self._q.put((s, None, (mid, s)))

    def say_chunk(self, text: str, directions=None, chat=None):
        """Queue text as ONE TTS request, no sentence splitting — fuller
        chunks get livelier prosody (single short sentences come out
        dull).

        `directions` are the stage directions this chunk carried. They are
        published on the signal bus when this chunk's audio STARTS, which
        is why they travel with it instead of firing at parse time.
        `chat` is (mid, text) for the conversation crawl, published at
        that same moment with the chunk's audio duration — the crawl
        types along with the voice instead of racing ahead of it."""
        text = text.strip()
        if text:
            self._q.put((text, directions or None, chat))

    def shut_up(self):
        """Barge-in: stop current playback and flush everything queued.

        turn_live goes down HERE, not only in speak_reply's finally. The
        worker's _settle() runs about 50ms after the drain and parks the
        face on "thinking" -- and restarts the thinking SOUND -- whenever
        turn_live is still set. On the talk-key path that teardown had not
        run yet, so interrupting a reply silenced the voice and then
        immediately began playing 36 seconds of static into the microphone
        the same keypress had just opened."""
        self.turn_live = False
        self._stop.set()
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def shutdown(self):
        """Exit path: stop playback and restore the music SYNCHRONOUSLY
        (the debounced restore timer dies with the process otherwise)."""
        self.shut_up()
        self.ducker.restore_now()

    def wait_done(self, timeout: float | None = None):
        """Block until the queue is drained and playback finished."""
        import time
        deadline = None if timeout is None else time.time() + timeout
        while (not self._q.empty()) or self._speaking.is_set():
            time.sleep(0.05)
            if deadline and time.time() > deadline:
                return

    def _synth_full(self, sentence: str):
        """Render one chunk fully to memory — the prefetch path. The
        character fx buffer whole sentences anyway, so this is the same
        work a beat earlier. Returns (rate, [pcm]); rate None on error
        (the chunk then re-synthesizes live, degrading to the old
        serial behavior instead of going mute)."""
        rate, bufs = None, []
        # Only announce the wait the person is actually waiting on. This
        # also runs as a PREFETCH under live playback (the next sentence
        # rendered while this one plays), and there the honest label is
        # "speaking" — saying "generating speech" over her own voice for
        # the whole utterance is just wrong.
        announce = not self._speaking.is_set()
        if announce:
            signals.set_stage("generating speech")
        try:
            for r, pcm in synth_stream(sentence):
                rate = r
                bufs.append(pcm)
        except Exception as e:
            log(f"[mouth] prefetch synth error: {e}")
            if announce:
                signals.set_stage("")
            return None, []
        if announce:
            signals.set_stage("")
        return rate, bufs

    def _run(self):
        from backtalk import signals
        pre = None    # (item, rate, bufs): the next chunk, pre-rendered
        while True:
            if pre is not None:
                item, rate, bufs = pre
                pre = None
                if self._stop.is_set():
                    # Barge-in in the gap between chunks: the pre-render
                    # dies with the flushed queue — and the voice must
                    # settle to QUIET here, or _speaking stays latched
                    # and gates the mic deaf.
                    if self._q.empty():
                        self._speaking.clear()
                        self.last_done = time.monotonic()
                        signals.reply_done()
                        self.ducker.speech_end()
                        self._settle()
                    continue
            else:
                item = self._q.get()
                rate, bufs = None, None
            if isinstance(item, tuple):
                sentence, directions, chat = (item + (None, None))[:3]
            else:
                sentence, directions, chat = item, None, None
            if not sentence:
                continue
            self._stop.clear()
            self._speaking.set()
            self.ducker.speech_start()
            signals.static_stop()     # thinking sound dies when speech starts
            # "speaking" is raised at AUDIO START, in _play_stream. A chunk
            # that is not prefetched has its whole synthesis ahead of it
            # (~0.7 s warm, 3 s+ on the first cold load) and the face used
            # to animate a voice that was not there yet. While that wait is
            # real, say what it is; audio start clears it.
            if rate is None:
                signals.set_stage("generating speech")
            # THE PERIOD FIX: while this chunk plays, render the next
            # queued one in parallel. Serially, every sentence boundary
            # cost the entire synthesis of the following sentence (the
            # fx chains buffer whole sentences) — a long dead pause at
            # each period, with the face dropping to idle in the gap.
            # Playback is pure I/O, so the one background synth never
            # runs against another synth.
            # ...and it has to WAIT for that next chunk, not glance once.
            # A single get_nowait() here could only ever see a chunk that
            # had ALREADY arrived, and in a streaming reply the next chunk
            # lands WHILE this one plays — so the common case missed, and
            # the boundary paid a full synth plus the head-start buffer.
            # Measured: 2.45s of dead air at the first period of a reply,
            # 0.15s once the queue ran ahead. Now the wait happens off the
            # critical path, in the thread, for as long as playback lasts.
            got = {}
            done = threading.Event()

            def _prefetch():
                while not done.is_set() and not self._stop.is_set():
                    try:
                        n = self._q.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    got["nxt"] = n
                    nsent = n[0] if isinstance(n, tuple) else n
                    try:
                        got["pre"] = self._synth_full(nsent)
                    except Exception as e:
                        # Keep the chunk, lose only the head start: the
                        # main loop replays it through the streaming path
                        # rather than dropping a sentence on the floor.
                        log(f"[mouth] prefetch synth failed: {e}")
                    return
            th = threading.Thread(target=_prefetch, daemon=True,
                                  name="mouth-prefetch")
            th.start()
            try:
                self._play_stream(sentence, directions, chat,
                                  pre=(rate, bufs) if rate else None)
            except Exception as e:
                log(f"[mouth] synth/play error: {e}")
            finally:
                # Stop waiting for a chunk that may never come. A synth
                # already begun still finishes on the join below — that is
                # work the next iteration needs anyway.
                done.set()
                # ponytail: joined even after a barge-in (Kokoro has
                # no cancel; the engine must go quiet before the next
                # utterance's synth anyway)
                th.join()
                nxt = got.get("nxt")
                if nxt is None:
                    pre = None
                else:
                    r, b = got.get("pre", (None, []))
                    pre = (nxt, r, b) if r else (nxt, None, None)
                if self._stop.is_set():
                    pre = None    # barge-in flushed the queue; ours dies too
                if self._q.empty() and pre is None:
                    self._speaking.clear()
                    # When the voice ACTUALLY went quiet — _speaking can
                    # clear a beat before the device buffer drains, and
                    # the mic hangover / wake follow-up window key off
                    # this, not the flag.
                    self.last_done = time.monotonic()
                    # The reply has genuinely stopped talking, as opposed to
                    # the gap between two sentences of the same reply.
                    signals.reply_done()
                    self.ducker.speech_end()
                    self._settle()

    def _settle(self):
        """Where the face goes when the queue empties: back to thinking
        if the turn is still running (a tool call between sentences),
        idle only when the reply is genuinely over."""
        from backtalk import signals
        if self.turn_live:
            signals.set_state("thinking")
            signals.static_start()
        else:
            signals.set_state("idle")

    def _get_out(self, rate: int) -> sd.OutputStream:
        """The long-lived stream (audio law #1). Reopened only when the
        sample rate changes (ElevenLabs 44.1k <-> Kokoro 24k fallback:
        rare, costs at most one blip on the switch)."""
        if self._out is not None and self._out_rate == rate:
            # Guarded, because the stream can die UNDER us: the ears
            # rebuild the whole audio system to recover from a device
            # change (see ears._reopen_after_device_change), and that
            # closes every open stream including this one. Touching a
            # dead stream raises rather than returning False, so the
            # check has to be the try, not an `if`. Falling through
            # rebuilds it, which is what the rest of this method does.
            try:
                if not self._out.active:
                    with PA_LOCK:
                        self._out.start()
                return self._out
            except Exception:
                log("[mouth] the output stream went away, reopening")
        self._drop_out()
        # Under the ears' device lock: an output open racing a mic stop is
        # the PortAudio collision that wedged the mic (ears.PA_LOCK).
        with PA_LOCK:
            self._out = sd.OutputStream(samplerate=rate, channels=1,
                                        dtype="int16")
            self._out_rate = rate
            self._out.start()
        return self._out

    def _cut(self):
        """Barge-in cut: stop feeding audio and pad the line with a beat
        of silence — the stream itself NEVER stops (an abort+restart here
        re-triggers the onset blip on latch-happy audio setups). Cost:
        the device buffer (~0.1s) plays out after the kill order — half a
        syllable of tail."""
        try:
            zeros = np.zeros(2205, dtype=np.int16)
            for _ in range(3):
                self._out.write(zeros)
        except Exception:
            self._drop_out()

    def _drop_out(self):
        """Close and forget the stream — the next sentence reopens
        fresh. The self-heal path for device errors (interface
        unplugged, audio mixer restarted)."""
        if self._out is not None:
            try:
                with PA_LOCK:
                    self._out.close(ignore_errors=True)
            except Exception:
                pass
        self._out = None
        self._out_rate = None

    # Engine padding trimmed at chunk EDGES only (never inside a
    # sentence): a period should be a beat, not a wait. The kept quiet
    # is what makes consecutive chunks join naturally.
    _KEEP_LEAD_S = 0.06
    _KEEP_TAIL_S = 0.12

    @staticmethod
    def _quiet(pcm):
        return 0.008 if np.issubdtype(pcm.dtype, np.floating) else 250

    def _trim_lead(self, pcm, rate):
        loud = np.abs(pcm) > self._quiet(pcm)
        if not loud.any():
            return pcm
        keep = max(0, int(np.argmax(loud)) - int(rate * self._KEEP_LEAD_S))
        return pcm[keep:]

    def _trim_tail(self, pcm, rate):
        loud = np.abs(pcm) > self._quiet(pcm)
        if not loud.any():
            return pcm
        last = len(pcm) - 1 - int(np.argmax(loud[::-1]))
        return pcm[:min(len(pcm), last + 1 + int(rate * self._KEEP_TAIL_S))]

    def _play_stream(self, sentence: str, directions=None, chat=None,
                     pre=None, block: int = 2205, prebuffer_s: float = 0.75):
        """Stream-synthesize and play with the head-start buffer (audio
        law #2) — or play a prefetched render straight from memory
        (pre=(rate, bufs)). stop() reacts ~50ms. The sample rate comes
        from whichever engine actually answered."""
        from backtalk import signals
        gen = None
        if pre:
            rate, head = pre[0], [b for b in pre[1] if len(b)]
        else:
            gen = synth_stream(sentence)
            head: list = []
            banked = 0
            rate = None
            for rate_, pcm in gen:
                rate = rate_
                head.append(pcm)
                banked += len(pcm)
                if banked >= int(rate * prebuffer_s):
                    break
        if rate is None or not head:
            # The ONLY place a queued chunk dies without a sound. Say so:
            # silence here is indistinguishable from "it printed the reply
            # but never read it aloud".
            log(f"[mouth] no audio rendered, chunk not spoken: {sentence[:80]!r}")
            signals.set_stage("")     # never leave "generating speech" up
            return
        head[0] = self._trim_lead(head[0], rate)
        if pre:
            head[-1] = self._trim_tail(head[-1], rate)
        try:
            out = self._get_out(rate)
            # AUDIO STARTS HERE: the head buffer is full and the first write
            # is next. Publishing now is what puts a screen cue on the spoken
            # word rather than seconds ahead of it.
            signals.set_state("speaking")
            signals.set_stage("")
            if directions:
                signals.direction(directions)
            if chat:
                # The crawl types this segment out over the audio's real
                # length (exact when prefetched; estimated for the first,
                # streamed chunk of a reply).
                dur = (sum(len(b) for b in head) / rate if pre
                       else max(1.0, len(chat[1]) / 14.0))
                signals.chat_add("agent", None, chat[1], mid=chat[0],
                                 dur=dur)

            def _write(pcm):
                for i in range(0, len(pcm), block):
                    if self._stop.is_set():
                        return False
                    out.write(pcm[i:i + block])
                    # Re-check after the blocking write: a barge-in
                    # landing mid-block must not let feed_waveform
                    # re-assert "speaking" over a fresh "listening".
                    if self._stop.is_set():
                        return False
                    signals.feed_waveform(pcm[i:i + block])
                return True
            for pcm in head:
                if not _write(pcm):
                    self._cut()
                    return
            if gen is not None:
                # One-buffer lag so the final buffer is known when it
                # arrives — its trailing engine silence gets trimmed.
                held = None
                for _, pcm in gen:
                    if held is not None and not _write(held):
                        self._cut()
                        return
                    held = pcm
                if held is not None and not _write(self._trim_tail(held,
                                                                   rate)):
                    self._cut()
                    return
        except Exception:
            self._drop_out()
            raise


if __name__ == "__main__":
    m = Mouth()
    m.say(sys.argv[1] if len(sys.argv) > 1 else
          "Voice check. The mouth is alive, and it is very good to be heard.")
    m.wait_done(timeout=60)
