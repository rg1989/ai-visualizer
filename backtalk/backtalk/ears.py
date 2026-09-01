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
"""The ears — mic capture with VAD endpointing, transcribed in-process
by faster-whisper. Local, free, no server, no API key.

record_held() is the hold-to-talk capture (the button is the VAD).
Ears.listen_once() is the legacy open-mic mode: blocks until one
complete utterance is heard, then returns its transcript. Endpointing:
an utterance opens after ~120ms of sustained speech, closes after
`silence_ms` of trailing quiet. A `gate` callable can suppress
listening (so the open mic ignores the speakers unless barge-in is on).
"""
import platform
import re
import sys
import threading

import numpy as np
import sounddevice as sd
import webrtcvad

from backtalk.config import CFG
from backtalk.vlog import log

RATE = 16000
FRAME_MS = 30
FRAME_LEN = RATE * FRAME_MS // 1000  # samples per frame
OPEN_FRAMES = 4        # ~120ms speech to open an utterance
MAX_UTTER_S = 30
# ponytail: whisper's own two confidence numbers, at whisper's own defaults.
# A 240ms cough clears the VAD, and whisper will always hand back SOMETHING
# for it -- "Beep.", "Thank you.", its own initial_prompt. These are the
# knobs for how hard to disbelieve it. Every drop is logged with both scores,
# so tune from the log, not from guesswork: raise NO_SPEECH_MAX / lower
# LOGPROB_MIN if real short replies get eaten, tighten them if ghosts pass.
NO_SPEECH_MAX = 0.6      # scored non-speech on EVERY segment -> noise
LOGPROB_MIN = -1.0       # whisper itself did not believe the words
GHOST_WORDS = 3          # min words to believe a transcript with no logprob
LOOP_MIN_REPS = 3        # a fragment repeated this often is a decoder loop
LOOP_MIN_WORDS = 6       # ...and long enough that nobody said it on purpose
LOOP_UNIT_MAX = 8        # longest repeating unit worth looking for

_NONSPEECH = re.compile(r"[\[(][^\])]*[\])]")

_model = None
_model_lock = threading.Lock()
_backend = None          # "mlx" once the GPU path loads, else "faster-whisper"


def _apple_gpu_available() -> bool:
    """Apple Silicon only. CTranslate2, the runtime under faster-whisper,
    has no Metal backend, so on every Mac it transcribes on the CPU while
    the GPU sits idle. mlx-whisper runs the SAME model on the GPU.

    Measured on an M4 Max, small.en, a 6.5s clip, warm: 0.88s on the CPU
    path against 0.12s on the GPU, with a character-identical transcript
    on three of four test clips and a two-comma difference on the fourth.

    Not a second product and not a user-facing choice: same model name
    from the same config key, same text out, one platform finally running
    it properly. Anything that is not an Apple Silicon Mac keeps
    faster-whisper, which already uses CUDA wherever it exists."""
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return False
    try:
        import mlx_whisper                       # noqa: F401
    except ImportError:
        return False
    return True


def _mlx_repo(model_name: str) -> str:
    """A faster-whisper model name -> its MLX conversion on the Hub."""
    return f"mlx-community/whisper-{model_name}-mlx"


_mic_checked = False


_mic_device_warned = False


def _mic_index():
    """Resolve mic_device (a device NAME) to an index, or None for the default.

    A NAME and never an index, because indices shift every time a device
    connects or disconnects, which is the exact event this setting exists
    to survive. Measured on a real machine: plugging a USB microphone in
    moved the default pair from [-1, 1] to [1, 3], silently changing the
    OUTPUT device too.

    Re-resolved on every stream open rather than cached at startup, for
    the same reason. Exact name wins, then the first case-insensitive
    substring, so a precise name can never be beaten by a loose one.
    """
    global _mic_device_warned
    want = str(CFG.get("mic_device", "") or "").strip()
    if not want:
        return None
    try:
        devices = sd.query_devices()
    except Exception as e:
        log(f"[ears] could not list audio devices ({e}) -- using the "
            f"default mic")
        return None
    ins = [(i, d) for i, d in enumerate(devices)
           if d.get("max_input_channels", 0) > 0]
    for i, d in ins:
        if d["name"] == want:
            _mic_device_warned = False
            return i
    low = want.lower()
    for i, d in ins:
        if low in d["name"].lower():
            _mic_device_warned = False
            return i
    if not _mic_device_warned:          # once per disappearance, not per press
        _mic_device_warned = True
        log(f"[ears] mic_device {want!r} not found -- using the system "
            f"default. Inputs I can see: {[d['name'] for _, d in ins]}")
    return None


def _open_mic():
    """Open the capture stream on the configured mic.

    Degrades to the system default if that device will not open --
    unplugged between the lookup and the open, busy, or refusing the
    sample rate. The mic gets worse; it never goes mute.
    """
    dev = _mic_index()
    opts = dict(samplerate=RATE, channels=1, dtype="int16",
                blocksize=FRAME_LEN)
    try:
        return sd.InputStream(device=dev, **opts)
    except Exception as e:
        if dev is not None:
            log(f"[ears] could not open mic_device {CFG.get('mic_device')!r} "
                f"({e}) -- using the system default")
            try:
                return sd.InputStream(**opts)
            except Exception:
                pass                   # fall through to the rebuild below
        return _reopen_after_device_change(opts)


def _reopen_after_device_change(opts):
    """Last resort: rebuild the audio system, then open the mic once more.

    PortAudio caches the device list when it initialises, so a device that
    disappears afterwards leaves a stale entry behind. A Bluetooth headset
    flipping between listening and call modes does this every time the mic
    opens, and from then on EVERY capture fails while the voice line looks
    perfectly healthy and simply never hears another word.

    Rebuilding refreshes the list. It also closes every open stream, the
    speaking one included, which is why Mouth._get_out rebuilds a stream
    it finds dead rather than trusting the one it is holding. Do not
    remove that guard without removing this.
    """
    log("[ears] the audio devices changed -- rebuilding and reopening")
    try:
        sd._terminate()
    except Exception:
        pass                           # already down; re-initialising is the point
    sd._initialize()
    return sd.InputStream(**opts)


_mic_warned = False

# Substrings PortAudio uses when the problem is the DEVICE rather than the
# audio. Matched on the message because the exception TYPE is the same
# PortAudioError whether a device vanished or a stream merely glitched.
_DEVICE_ERROR_HINTS = ("error querying device", "invalid device",
                       "device unavailable", "no default input",
                       "invalid number of channels", "device not found")


def _mic_message(detail: str) -> list[str]:
    """The one explanation, so startup and mid-session say the same thing."""
    return [
        "[ears] NO WORKING MICROPHONE. Nothing can be recorded on this "
        "machine, so the talk key will have nothing to send.",
        f"[ears] the audio system said: {detail}",
        "[ears] plug one in and start the voice line again. If one IS "
        "plugged in, check it is allowed in this system's microphone "
        "privacy settings -- and if you have several, put part of the "
        "one you want in \"mic_device\" in backtalk.json.",
    ]


def explain_audio_failure(exc) -> bool:
    """Turn a device-level audio failure into plain words. Returns True
    when it handled the message, so the caller can skip the raw repr.

    The startup pre-flight cannot cover a microphone that is unplugged or
    dies MID-SESSION, and that person gets the worst version of this:
    no warning at all, and a raw PortAudioError on every single press,
    forever. The key hook keeps working throughout, so it still looks
    like it is listening. This says the same sentences the pre-flight
    would have said, at the moment it becomes true.

    Said in full once, then briefly, because a message repeated on every
    key press stops being information and becomes noise.
    """
    global _mic_warned
    text = str(exc).lower()
    # THE TWO HALVES OF THIS TEST ARE NOT DOING THE SAME JOB. Do not
    # simplify it to one. Measured on Windows: the SAME missing microphone
    # produces "Error querying device -1" when it is absent at startup and
    # "A device ID has been used that is out of range for your system
    # [MME error 2]" when it is unplugged mid-stream. The second matches
    # not one hint below, and was caught only by the type check -- on the
    # very first real test of the case this function exists for. The
    # hints catch device failures raised as something other than a
    # PortAudioError; the type catches PortAudio wording nobody predicted.
    if type(exc).__name__ != "PortAudioError" and \
            not any(h in text for h in _DEVICE_ERROR_HINTS):
        return False
    if _mic_warned:
        log("[ears] still no working microphone.")
        return True
    _mic_warned = True
    for line in _mic_message(f"{type(exc).__name__}: {exc}"):
        log(line)
    return True


def check_microphone() -> bool:
    """Say whether recording is possible at all, BEFORE the greeting.

    Without this the voice line boots on a machine with no microphone,
    warms, speaks its greeting and presents a working push-to-talk
    prompt. The key hook works perfectly throughout, so the user is
    given every impression it is listening -- and the only sign of
    trouble is a raw PortAudioError AFTER they have held the key and
    spoken. It then repeats forever, because holding a key again cannot
    conjure a device.
    """
    global _mic_checked
    if _mic_checked:
        return True
    _mic_checked = True
    try:
        sd.check_input_settings(device=_mic_index(), channels=1,
                                samplerate=RATE, dtype="int16")
        return True
    except Exception as e:
        global _mic_warned
        _mic_warned = True      # said it here; do not repeat on first press
        for line in _mic_message(str(e)):
            log(line)
        return False


def _probe(model):
    """Run a tenth of a second of silence through the real path.

    faster-whisper is lazy: transcribe() returns a generator and does no
    work until it is iterated, so the list() is what actually exercises
    the backend and is not redundant.
    """
    segments, _ = model.transcribe(np.zeros(RATE // 10, dtype=np.float32),
                                   language="en")
    list(segments)


def warm():
    """Load the STT model (first call downloads it to the HF cache).
    Called at startup while the greeting plays, so the first real
    utterance doesn't pay the load."""
    global _model, _backend
    check_microphone()
    with _model_lock:
        if _model is None:
            if _apple_gpu_available():
                import mlx_whisper
                repo = _mlx_repo(CFG["stt_model"])
                log(f"[ears] loading {CFG['stt_model']} on the Apple GPU...")
                # This API has no separate load call: the first transcribe
                # pulls and caches the weights. Warm on a beat of silence so
                # the first real utterance does not pay for it.
                mlx_whisper.transcribe(np.zeros(RATE // 10, dtype=np.float32),
                                       path_or_hf_repo=repo, language="en",
                                       verbose=None)
                _model, _backend = repo, "mlx"
            else:
                from faster_whisper import WhisperModel
                want = CFG["stt_device"]
                log(f"[ears] loading {CFG['stt_model']} "
                    f"({want}/{CFG['stt_compute']})...")
                _model = WhisperModel(CFG["stt_model"], device=want,
                                      compute_type=CFG["stt_compute"])
                # PROVE the device before the greeting, not at the first
                # spoken sentence. WhisperModel CONSTRUCTS perfectly well
                # against a GPU it cannot actually use: "auto" picks CUDA
                # on any NVIDIA machine, and the CUDA runtime is not
                # loaded until the first inference. So warm-up logged
                # "model ready", startup reported healthy, and a missing
                # cublas DLL only surfaced when the user finally spoke --
                # long after the greeting, in a place they could not
                # connect to a setting. The Apple-GPU branch above has
                # always done this; this one never did.
                try:
                    _probe(_model)
                except Exception as e:
                    if want == "cpu":
                        raise
                    log(f"[ears] {want!r} does not work on this machine "
                        f"({type(e).__name__}: {e}).")
                    log("[ears] falling back to the CPU. Set "
                        "\"stt_device\": \"cpu\" in backtalk.json to skip "
                        "this check in future.")
                    _model = WhisperModel(CFG["stt_model"], device="cpu",
                                          compute_type=CFG["stt_compute"])
                    _probe(_model)
                _backend = "faster-whisper"
            log(f"[ears] model ready ({_backend})")
    return _model


# ponytail: whisper has never heard of "SHODAN", so small.en renders the
# wake word as "Show that." and the gate drops every attempt. Both backends
# take initial_prompt -- whisper's own vocabulary hint -- so we spend one
# short line of context instead of guessing at mishears forever. Keep it
# SHORT: a long prompt makes whisper echo it back as hallucinated speech.
_PROMPT = None      # the hint sentence whisper actually receives
_NAME = None        # the bare name inside it


def set_prompt(name):
    """Bias the decoder toward a name it cannot know (the live persona).

    Takes the BARE name and builds the hint here, as a GLOSSARY LINE and
    never a sentence. Measured on noise clips, small.en hands its hint
    back verbatim at a rate that depends on the name -- 1 clip in 5 for
    "SHODAN", 5 in 5 for "JARVIS" -- so there is no wording that reliably
    never echoes, and the echo cannot be told from real speech by score
    (the ghosts score BETTER than 0.6 because the model is quoting itself).

    So the hint is chosen to be un-SAYABLE instead: nobody utters
    "SHODAN, voice log, transcript.", which makes dropping it on sight
    safe no matter how often it comes back. Two forms that look tidier
    are traps, both measured: the bare name collides with the wake word
    itself, and a repeated name ("NAME. NAME. NAME.") sets off whisper's
    repetition loop and returns the name fifty times.
    """
    global _PROMPT, _NAME
    _NAME = (name or "").strip() or None
    _PROMPT = f"{_NAME}, voice log, transcript." if _NAME else None


_LETTERS = re.compile(r"[a-z0-9]", re.I)
_WORDS = re.compile(r"[^a-z0-9 ]+")


def _norm(t):
    """Loose match key: 'SHODAN, voice log.' == 'shodan voice log'."""
    return " ".join(_WORDS.sub(" ", (t or "").lower()).split())


def _fmt(ns, lp) -> str:
    return ("no scores" if ns is None
            else f"no_speech {ns:.2f} logprob {lp:.2f}")


# How many leading tokens may stand in for the name and still count as the
# hint coming back. One covers "Shoredan"; two covers the two-word renderings
# small.en is already known to produce ("show dan", "show that"). Three would
# start swallowing real questions like "what is the voice log transcript".
_HINT_NAME_SLOT = 2


def _strip_hint_echo(text) -> tuple[str, str]:
    """Cut whisper's own initial_prompt off the FRONT of a transcript.

    THE FIRST TRAP: the hint exists because small.en cannot spell the
    persona's name, and the original echo test demanded that same name come
    back spelled PERFECTLY (_norm(text) == _norm(_PROMPT)) -- weakest at
    exactly the token it was built around. Field-caught: whisper returned
    "Shoredan, voice log, transcript." on room noise, the compare missed it,
    the WAKE matcher then forgave the mangled name (it carries an alias list
    for precisely this), and the ghost barged in and killed two turns.

    THE SECOND TRAP, and why this TRIMS instead of answering yes/no: matching
    the whole string -- even name-forgivingly -- still demanded the echo be
    the only thing in the clip. Whisper does not oblige. It emits the hint and
    then keeps going into one of its stock silence ghosts, so
    "<name>, voice log, transcript. Thank you." (or "Bye.", or "You" -- the
    three this repo's own gate test already lists as measured ghosts) sailed
    straight through, woke on the name, and interrupted a live reply. An echo
    with litter stapled to it is still an echo.

    So: find the name-free tail as a contiguous run, require it to START the
    clip (at most _HINT_NAME_SLOT tokens of mangled name before it), and cut
    everything up to its end. Whatever whisper drifted into afterwards is
    handed back to be judged on its own merits -- same trim-don't-drop shape
    as _unloop, and it is what keeps the WAKE WORD out of the remainder,
    which is the half that actually did the damage.

    Requiring the run to START the clip is what makes this safe for real
    speech: "what is the voice log transcript" has three tokens in front of
    the run, so it is left completely alone.

    Returns (kept_text, note); note is "" when nothing was cut.

    ponytail: exact tokens, so a morphological variant ("...transcripts")
    still slips. If one ever shows up in a log, compare tail tokens by their
    first five characters rather than adding another guard.
    """
    if not _NAME or not _PROMPT:
        return text, ""
    tail = _norm(_PROMPT).split()[1:]        # name-free: the stable part
    if not tail:
        return text, ""
    toks = text.split()
    cuts = 0
    while True:
        key = [_norm(t) for t in toks]
        for i in range(min(_HINT_NAME_SLOT, max(len(key) - len(tail), 0)) + 1):
            if key[i:i + len(tail)] == tail:
                toks = toks[i + len(tail):]  # drop the name slot AND the run
                cuts += 1
                break
        else:
            break                            # no run at the front any more
    if not cuts:
        return text, ""
    return " ".join(toks).strip(), f"{cuts}x its own hint"


_HINT_ECHO_NOTE = "its own hint"


def _scores(segs):
    """(worst-case no_speech, mean logprob) over whisper's segments, for
    either backend. min() on no_speech and the mean on logprob: one bad
    segment in a real sentence must not sink the whole utterance."""
    get = (lambda g, k: g.get(k, 0.0)) if isinstance(segs[0], dict) \
        else (lambda g, k: getattr(g, k, 0.0))
    ns = [get(g, "no_speech_prob") for g in segs]
    lp = [get(g, "avg_logprob") for g in segs]
    # NaN IS ABSORBING UNDER ADDITION, so one scoreless segment used to make
    # the whole mean NaN -- and NaN is the escape hatch _no_speech reads as
    # "nothing to judge on", which silently turned LOGPROB_MIN off for the
    # entire utterance. A confidently-disbelieved sentence then walked out
    # past the gate because ONE of its segments carried no number. The
    # escape hatch was written for a whole-utterance NaN (mlx leaves it so
    # on short clips); keep exactly that meaning by averaging the segments
    # that DID score, and going NaN only when none of them did.
    real = [v for v in lp if v == v]
    return min(ns), (sum(real) / len(real)) if real else float("nan")


def _no_speech(ns, lp) -> str:
    """Whisper's own verdict that the clip was not speech -- "" if it was."""
    if ns is None:
        return ""                        # no scores -> nothing to judge on
    if ns > NO_SPEECH_MAX:
        return f"scored non-speech (no_speech {ns:.2f})"
    # lp is NaN when the backend returns no token probabilities (mlx does
    # this routinely on short clips). NaN is NO evidence, not bad
    # evidence: firing the gate on it threw away real speech that whisper
    # itself scored no_speech 0.01 -- "SHODAN, show me a map of Tel Aviv"
    # arrived as '! Show me a map of Tel Aviv.' and was dropped, which is
    # what "it never answers to its name" looks like from the outside.
    # With no logprob, no_speech above is the whole verdict.
    if lp != lp:                         # NaN
        return ""                        # judged on length, in _junk
    if lp < LOGPROB_MIN:
        return f"whisper did not believe it (logprob {lp:.2f})"
    return ""


def _junk(text, ns, lp) -> str:
    """Why this transcript is not speech -- "" when it is. Whisper never
    returns nothing, so somebody has to say no."""
    if not _LETTERS.search(text):
        return "no words in it"          # "!", "...", "\u266a"
    why = _no_speech(ns, lp)
    if why:
        return why
    if lp is not None and lp != lp and len(text.split()) < GHOST_WORDS:
        # No logprob at all (mlx leaves it NaN on short clips), so the
        # only evidence left is length. What whisper emits for silence is
        # always a stock fragment -- "You", "Thank you.", "Bye." -- and
        # what it emits for a real request is a sentence.
        return "no logprob and too short to trust"
    return ""


def _unloop(text) -> tuple[str, str]:
    """Strip whisper's degenerate repeat off the tail of a transcript.

    The decoder gets stuck and emits one fragment forever -- 27x "I don't
    know.", 56x "I'm sorry" -- on room noise, on its own voice bleeding
    back, on a clip that ran long. It scores like ordinary speech (the
    words ARE confidently predicted; that is the whole failure), so no
    logprob or no_speech threshold can catch it. Only its shape can.

    The loop is always the TAIL: whatever real speech opened the clip
    still deserves to reach the agent, so this trims rather than drops.
    "What's the name of the people? The people? I don't know. I don't
    know. ..." is a real question with a seizure stapled to it.

    Returns (kept_text, note); note is "" when nothing was trimmed.
    """
    toks = text.split()
    key = [_norm(t) for t in toks]
    # Shortest unit first, because that is the true period: a unit that
    # is only PART of the loop cannot match what precedes it. Starting
    # long instead picks up a double copy ("i don t know i don t know")
    # and leaves one stray repetition behind on odd counts.
    for n in range(1, LOOP_UNIT_MAX + 1):
        if len(key) < n * LOOP_MIN_REPS:
            continue
        unit = key[-n:]
        if not any(unit):                # trailing punctuation-only tokens
            continue
        reps, i = 1, len(key) - 2 * n
        while i >= 0 and key[i:i + n] == unit:
            reps += 1
            i -= n
        span = reps * n
        if reps >= LOOP_MIN_REPS and span >= LOOP_MIN_WORDS:
            kept = " ".join(toks[:len(toks) - span]).strip()
            if not kept and n == 1:
                # THE LOOP WAS THE WHOLE UTTERANCE, and its unit is a single
                # word. That is a person saying a word hard -- "no no no no
                # no no", "stop stop stop stop stop stop" -- and returning
                # "" swallowed it whole, at the exact moments someone repeats
                # themselves because they mean it. Keep one copy.
                # Multi-word units are NOT let through the same way: "I don't
                # know. I don't know." repeated is the decoder's own seizure,
                # never a person, and one copy of it is a sentence nobody
                # said.
                return toks[0], f"{reps}x {' '.join(unit)!r} (kept one)"
            return kept, f"{reps}x {' '.join(unit)!r}"
    return text, ""


def _finish(text, segs) -> str:
    """Every gate a transcript passes on its way out: the bracketed-marker
    scrub, the prompt-echo drop, and _junk.

    segs is empty when the backend hands back no per-segment scores, and
    then ns/lp are None. That is not a special case: _no_speech returns
    "" on None (nothing to judge on) and _junk falls through to its
    does-it-contain-letters test, so the text is judged on the only
    evidence there is. test_ears_gate pins both behaviours.
    """
    text = _NONSPEECH.sub("", text or "").strip()
    if not text:
        return ""
    ns, lp = _scores(segs) if segs else (None, None)
    text, echoed = _strip_hint_echo(text)
    if echoed:
        # Whisper handed its own hint back: always a ghost, because the hint
        # is a glossary line no one says out loud. NOT score-gated -- an echo
        # is the model quoting itself, so it scores like clean speech, and
        # trusting the score here turned every quiet moment into a spoken
        # wake word and woke her in a loop. Anything the model drifted into
        # AFTER the hint falls through to _junk on its own merits; what
        # matters is that the NAME is gone, so it can no longer wake her.
        log(f"[ears] (noise, cut {echoed}; kept {text!r}; {_fmt(ns, lp)})")
        if not text:
            return ""
    text, looped = _unloop(text)
    if looped:
        log(f"[ears] (whisper looped, trimmed {looped})")
        if not text:
            return ""
    why = _junk(text, ns, lp)
    if why:
        # Loud on purpose, and ALWAYS with both scores: a dropped transcript
        # is the only evidence for tuning the thresholds above. Silent drops
        # are how a mic "just stops hearing you" with nothing to go on.
        log(f"[ears] (noise, dropped {text!r} -- {why}; {_fmt(ns, lp)})")
        return ""
    return text


# The capture VAD runs at aggressiveness 2 (it has to be forgiving, or it
# clips the front off real speech). This second one is the STRICTEST setting
# and it only ever decides whether to prime the decoder -- never whether you
# were heard -- so it can afford to be harsh.
_HINT_VAD = webrtcvad.Vad(3)
_HINT_MIN_VOICED = 0.35        # share of 30ms frames that must be speech


def _voiced_ratio(pcm: np.ndarray) -> float:
    """Share of 30ms frames the strict VAD calls speech. 1.0 on any error:
    unsure must behave exactly as before, never suppress the hint.

    On its own this is NOT enough: webrtcvad scores energy and spectral
    shape, so it calls loud broadband hiss a confident 1.00, exactly like
    speech, and no threshold separates 1.00 from 1.00. _flatness() is the
    second half that does; the two are combined in _speechlike().
    """
    n = len(pcm) // FRAME_LEN
    if n < 1:
        return 0.0
    buf = pcm[:n * FRAME_LEN].astype(np.int16).tobytes()
    try:
        return sum(_HINT_VAD.is_speech(buf[i * FRAME_LEN * 2:
                                           (i + 1) * FRAME_LEN * 2], RATE)
                   for i in range(n)) / n
    except Exception:
        return 1.0


_FLAT_MAX = 0.05        # above this the clip is hiss, not a voice
_FLAT_WIN = 512


def _flatness(pcm: np.ndarray) -> float:
    """Wiener entropy: geometric mean over arithmetic mean of the power
    spectrum, median across frames. 1.0 is perfectly flat (white noise);
    a voice is peaky -- formants and a harmonic stack -- so it lands orders
    of magnitude lower. This is what tells a fan from a person when the VAD
    cannot: measured on this machine, white noise 0.56 against speech 0.0008
    to 0.0022, a 250x gap that any threshold in between splits cleanly.

    Silent frames are skipped rather than scored: a digitally silent frame
    has no spectrum to be flat or peaky about. 1.0 on any error -- unsure
    means "not speechlike", and the hint is merely withheld, never the words.
    """
    try:
        x = pcm.astype(np.float64) / 32768.0
        n = len(x) // _FLAT_WIN
        if n < 1:
            return 1.0
        win = np.hanning(_FLAT_WIN)
        vals = []
        for i in range(n):
            ps = np.abs(np.fft.rfft(x[i * _FLAT_WIN:(i + 1) * _FLAT_WIN]
                                    * win)) ** 2 + 1e-12
            if ps.sum() < 1e-9:
                continue
            vals.append(float(np.exp(np.log(ps).mean()) / ps.mean()))
        return float(np.median(vals)) if vals else 1.0
    except Exception:
        return 1.0


def _speechlike(pcm: np.ndarray) -> bool:
    """Is this clip worth priming the decoder for? Both halves must agree:
    the VAD rules out silence and clicks, the flatness rules out hiss."""
    return (_voiced_ratio(pcm) >= _HINT_MIN_VOICED
            and _flatness(pcm) <= _FLAT_MAX)


def transcribe(pcm: np.ndarray) -> str:
    """int16 mono 16kHz -> text. Bracketed non-speech markers that
    whisper emits ([BLANK_AUDIO], [SIGHS], (coughs)...) are stripped;
    if nothing remains, it was silence."""
    model = warm()
    try:
        from backtalk import signals
        if signals.unsummoned():
            # ponytail: cold wake mode -- the gate is about to drop this,
            # so the face must not flash TRANSCRIBING at every sentence
            # spoken near the mic. Looked exactly like a false wake.
            signals = None
        else:
            signals.set_stage("transcribing")
    except Exception:
        signals = None
    audio = pcm.astype(np.float32) / 32768.0
    lang = "en" if CFG["stt_model"].endswith(".en") else None
    # PRIME ON SPEECH ONLY. The hint's benefit and its cost land on disjoint
    # inputs: on a real utterance it is the only reason small.en spells the
    # persona's name (measured on this machine, 3 clips in 4 with it, 0 in 4
    # without) -- and on a non-speech clip it is the very thing whisper hands
    # back as a phantom sentence, because a prompt is context the decoder is
    # free to repeat. The wider whisper community's first answer to
    # hallucination is simply not to run the model on non-speech; backtalk
    # still runs it (the clip already cleared the capture VAD, and _junk is
    # the backstop), but there is no reason to PRIME a clip that is about to
    # be thrown away. Costs one VAD pass and one FFT over audio in hand.
    hint = _PROMPT if _speechlike(pcm) else None
    if _backend == "mlx":
        import mlx_whisper
        r = mlx_whisper.transcribe(audio, path_or_hf_repo=model,
                                   temperature=0.0, language=lang,
                                   initial_prompt=hint, verbose=None)
        text, segs = r["text"].strip(), r.get("segments") or []
    else:
        segs = list(model.transcribe(audio, temperature=0.0, language=lang,
                                     initial_prompt=hint)[0])
        text = "".join(g.text for g in segs).strip()
    if signals:
        signals.set_stage("")
    return _finish(text, segs)


class Ears:
    def __init__(self, aggressiveness: int = 2, silence_ms: int = 480):
        self.vad = webrtcvad.Vad(aggressiveness)
        self.silence_frames = silence_ms // FRAME_MS

    def listen_once(self, gate=None, timeout_s: float | None = None,
                    abort=None, want_audio: bool = False,
                    on_speech=None):
        """Block until one utterance completes; return transcript
        (or None on timeout). An `abort` callable is checked every
        frame; returning True closes the mic and returns None, which
        is how a live switch back to push-to-talk shuts the open mic
        down promptly instead of after one more utterance.
        want_audio=True returns (transcript, pcm) instead — the same
        int16 buffer the transcriber saw, for the speaker identifier
        ((None, None) on timeout/abort).

        `on_speech(True)` fires the frame the VAD opens and
        `on_speech(False)` if that open turns out to be a noise blip.
        This is the ONLY listening signal that can be on time: every
        other one in the pipeline waits on whisper, which waits on the
        utterance ending. Runs on the audio thread — keep it cheap and
        it must not raise (we swallow anyway; a throwing callback would
        otherwise kill the mic)."""
        opened = False

        def _sig(on):
            # Deduped and symmetric: one True, one False, always. The
            # blip path signals explicitly and the finally below is the
            # backstop, so a transcript, a timeout, an abort and a
            # device blowup all put the light back where they found it.
            nonlocal opened
            if on == opened:
                return
            opened = on
            if on_speech:
                try:
                    on_speech(on)
                except Exception:
                    pass
        frames: list[np.ndarray] = []
        ring: list[np.ndarray] = []   # pre-roll so the first syllable survives
        speech_run = 0
        silence_run = 0
        speech_total = 0
        in_utterance = False
        elapsed = 0.0

        try:
            with _open_mic() as stream:
                while True:
                    block, _ = stream.read(FRAME_LEN)
                    elapsed += FRAME_MS / 1000
                    if abort and abort():
                        return (None, None) if want_audio else None
                    if timeout_s and elapsed > timeout_s and not in_utterance:
                        return (None, None) if want_audio else None
                    mono = block[:, 0].copy()
                    # THE GATE MUST BE ABLE TO END AN UTTERANCE, not just
                    # suppress one. This used to `continue` above the
                    # in_utterance branch, which meant that when the gate
                    # rose mid-sentence NOTHING ran: no frames.append, so
                    # MAX_UTTER_S could never trip; no silence_run, so it
                    # could never endpoint; and _sig(True) had already
                    # fired, so the listening light stayed on. The
                    # utterance was frozen for the whole gated stretch, and
                    # when the gate dropped the two halves were handed to
                    # whisper as ONE buffer with seconds cut out of the
                    # middle -- a sentence spliced to whatever was said
                    # after the reply finished.
                    #
                    # Gated frames are the SPEAKERS, never the owner, so
                    # they are still not recorded. They simply count as
                    # silence, which lets the utterance close on its own
                    # terms: enough speech already captured and it is
                    # transcribed, too little and the blip path drops it.
                    gated = bool(gate and gate())
                    if gated and not in_utterance:
                        ring.clear()
                        continue
                    is_speech = (False if gated
                                 else self.vad.is_speech(mono.tobytes(),
                                                         RATE))
                    if not in_utterance:
                        ring.append(mono)
                        # 360ms of pre-roll: whisper needs the attack of the
                        # first word, and a fast talker beats a smaller ring
                        # (the old 240ms clipped sentence openings).
                        if len(ring) > 12:
                            ring.pop(0)
                        speech_run = speech_run + 1 if is_speech else 0
                        if speech_run >= OPEN_FRAMES:
                            in_utterance = True
                            frames = ring[:]
                            silence_run = 0
                            _sig(True)
                    else:
                        if not gated:
                            frames.append(mono)
                        if is_speech:
                            speech_total += 1
                            silence_run = 0
                        else:
                            silence_run += 1
                        if silence_run >= self.silence_frames or \
                           len(frames) * FRAME_MS / 1000 > MAX_UTTER_S:
                            if speech_total < 8:
                                # <240ms of actual speech: a noise blip, not
                                # a sentence — keep listening
                                in_utterance = False
                                frames, ring = [], []
                                speech_run = speech_total = 0
                                _sig(False)
                                continue
                            # Mic shut BEFORE whisper runs: the light is
                            # "I hear you", not "I am busy", and transcribe
                            # is the better part of a second.
                            _sig(False)
                            pcm = np.concatenate(frames)
                            text = transcribe(pcm)
                            return (text, pcm) if want_audio else text
        finally:
            _sig(False)


def record_held(is_held, max_s: float = 60.0, min_s: float = 0.25,
                want_audio: bool = False):
    """Hold-to-talk capture: record raw audio while is_held() is True,
    then transcribe. The button is the VAD — no endpointing. Returns
    None for taps shorter than min_s (accidental presses).
    want_audio=True returns (transcript, pcm) / (None, None)."""
    frames: list[np.ndarray] = []
    with _open_mic() as stream:
        while is_held() and len(frames) * FRAME_MS / 1000 < max_s:
            block, _ = stream.read(FRAME_LEN)
            frames.append(block[:, 0].copy())
        # HOW LONG THE KEY WAS ACTUALLY DOWN, measured before the tail is
        # added. The tap guard below used to measure held-time PLUS tail,
        # and the tail is a fixed 180ms -- so on the stated min_s of 250ms
        # it was really only rejecting presses under ~70ms, and a 90ms
        # brush of the key sailed through as a 270ms clip of near-silence.
        # That is exactly the length whisper answers with a stock ghost.
        held_s = len(frames) * FRAME_MS / 1000
        # a small tail so the last word isn't clipped at release
        for _ in range(6):
            block, _ = stream.read(FRAME_LEN)
            frames.append(block[:, 0].copy())
    if held_s < min_s:
        return (None, None) if want_audio else None
    pcm = np.concatenate(frames)
    text = transcribe(pcm)
    return (text, pcm) if want_audio else text


if __name__ == "__main__":
    import time
    print("[ears] listening — say something...", flush=True)
    ears = Ears()
    start = time.time()
    while time.time() - start < 30:
        text = ears.listen_once(timeout_s=30 - (time.time() - start))
        if text:
            print(f"[ears] heard: {text!r}", flush=True)
            break
        if text is None:
            print("[ears] timed out with no speech", flush=True)
            break
        print("[ears] (noise/empty — still listening)", flush=True)
