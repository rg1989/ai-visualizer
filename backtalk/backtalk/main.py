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
"""backtalk — talk to your Claude Code agent out loud.

Flow: hold the key and speak -> local transcription -> your agent's warm
Claude session streams the reply -> sentences go to the mouth the moment
they complete (~1-2s to first audio on warm turns). The greeting plays
over a hidden warmup query so the first real turn is already hot.

Typing in this terminal is a first-class turn too: same conversation,
spoken reply, and typing while it talks interrupts it.

THE VOICE CONSOLE: exact phrases, spoken (or typed) alone, control the
session itself so you never go back to the keyboard: "clear the
session" / "compact the session" / "switch to the deep model" / "back
to the fast model" / "set effort to low" (or medium, high, max) /
"usage report" / "go hands free", "push to talk mode" and "wake word
mode" (the MIC; in wake word mode utterances must start with a wake
phrase — the name alone chimes and opens a short command window,
"name, do X" is a one shot, and everything else is ignored) /
"stop listening" (stop now: in wake or push-to-talk mode it just
answers "Stopped."; an open mic drops to wake word mode, so nothing
is heard again until the name is said; the overlay window fades at
once and stays down until you address her again) /
"stop asking for permission" and "start asking again" (permissions,
called auto-approve, a different axis than the microphone on purpose).
And with permission_mode "ask" (the default), gated tool calls ASK OUT
LOUD and your spoken yes or no decides them; any other answer is
passed back to the agent as the reason.

Flags:
  --open-mic   start in hands-free listening for this session (the
               config key mic_mode makes it the standing default, and
               the voice can switch live either way: "go hands free" /
               "push to talk mode"). Know the tradeoff: room audio (a
               video, music, another voice assistant) can trigger
               replies to speech never meant for the agent. The talk
               key keeps working: it interrupts, and holding it always
               gets you heard.
  --barge-in   with --open-mic: keep listening WHILE speaking.
               HEADPHONES REQUIRED — with open speakers the mic hears
               the reply and the agent interrupts itself.
  --model X    override the model for this session (full id).

Say "goodbye <name>" / "end voice mode" to hang up. Ctrl-C works.
"""
import asyncio
import json
import os
import queue
import re
import socket
import sys
import threading
import time

from backtalk import intent as intent_ai
from backtalk import signals
from backtalk import voiceid
from backtalk import wake as wakeword
from backtalk.brain import WarmBrain
from backtalk.config import CFG, REPO
from backtalk.ears import (Ears, explain_audio_failure, record_held,
                           set_prompt as set_stt_prompt, warm as warm_ears)
from backtalk.mouth import Mouth, warm as warm_voice
from backtalk.ptt import FacePTT, PTTListener
from backtalk.vlog import log

# The provider profiles — the env and model ids behind CLAUDE vs Z.AI —
# that the picker's BRAIN tab switches between. Guarded on purpose: an
# install without the module keeps whatever brain the launcher handed
# it and the tab simply goes quiet. A missing profile file must never
# cost the owner his voice line.
try:
    from backtalk import provider
except ImportError:
    provider = None

NAME = CFG["name"]
# Hang-up accepts EVERY persona's name (see _THEME_VOICES), whatever
# theme is active — a quit must never be blocked by a theme mismatch.
QUIT_PHRASES = tuple(CFG["quit_phrases"]) + tuple(
    f"{v} {n}" for n in ("shodan",)
    for v in ("goodbye", "good bye", "hang up"))

# ---- THE SPOKEN PERMISSION GATE (permission_mode "ask", the default).
# When the agent wants a gated tool, the SDK routes the decision here:
# the ask is spoken, the turn pauses (the SDK waits indefinitely; the
# timeout below is ours), and the NEXT utterance or typed line is the
# answer. "yes" approves; anything else denies, with the user's own
# words passed back as the reason. Silence means no.
PERM_TIMEOUT_S = 75
_PERM = {"fut": None, "asked_at": 0.0,   # pending ask + when it was posed
         "hinted": False}                # escape-hatch hint said yet?
_CONFIRM = {"verb": None, "at": 0.0}     # pending "say confirm" + when
_INTERRUPT_ANSWER = "\x00interrupt"      # sentinel: turn is being killed
# Synthetic deny reasons handle() feeds a pending ask when the person
# spoke a COMMAND into it; the gate recognizes them and must never
# quote them as the user's words or send them to the interpreter.
_SETTINGS_FIRST = ("The user is changing a setting first; ask again "
                   "if still needed.")
_CONFIRMING_FIRST = ("The user is confirming a setting; ask again "
                     "if still needed.")
# Live AUTO-APPROVE is OUR flag, not an SDK mode flip: the CLI refuses
# a live switch INTO bypassPermissions unless it was launched with the
# danger flag, so instead the gate below auto-approves silently while
# this is on. Same behavior, no reconnect, conversation intact. A
# session that BOOTS in bypassPermissions never consults the gate at
# all; saying "start asking again" flips the SDK side live (that
# direction is allowed) and turns this off. ONLY the explicit
# bypassPermissions value arms this: any other mode (acceptEdits, plan)
# passes through to the SDK and keeps the spoken gate for whatever the
# SDK routes here. (Auto-approve is about PERMISSIONS; hands-free
# LISTENING is about the microphone: see _MIC below. Two different
# axes, deliberately never sharing a name.)
_AUTOAPPROVE = {"on": False}
# The microphone mode, switchable live by voice. "ptt" = mic closed
# except while the key is held. "open" = hands-free listening (VAD).
# The key keeps working in open mode: it interrupts, and holding it
# always gets you heard. gen bumps on every switch so an in-flight
# open-mic capture from before the switch gets discarded, never
# processed.
# hush: "stop listening" — the room is off the record until the
# person deliberately comes back (wake word, talk key, typing).
_MIC = {"mode": "ptt", "gen": 0, "btn": False, "hush": False}


def _unhush():
    """Coming back (talk key, wake word, typing) ends the hush -- and
    says so on the mic line, which is what lets the overlay wake again
    (it hides while the line reads hush, see signals.set_mic)."""
    if _MIC["hush"]:
        _MIC["hush"] = False
        signals.set_mic(_MIC["mode"])
# THE BRAIN PROVIDER — which back end the `claude` CLI actually talks
# to: "claude" (Anthropic) or "zai" (the GLM coding plan). The CLI
# inherits os.environ, so a switch is an env change plus a brain
# rebuild: live, no relaunch. But a rebuilt CLI is a NEW process and
# therefore a NEW conversation, and that cost is always SAID, never
# hidden. (Which brain is thinking is a third axis, standing beside the
# microphone and permissions and sharing a name with neither.)
_BRAIN = {"provider": "claude", "model": ""}
_BRAIN_LABEL = {"claude": "Claude", "zai": "Z.AI"}


def _brain_label(name, model=""):
    """How a brain is said out loud: the provider, plus its tier when it
    has one worth naming ("Z.AI on 5.3 Flash")."""
    label = _BRAIN_LABEL.get(name, name)
    if provider is not None and model:
        tier = provider.variants(name).get(model)
        if tier:
            return f"{label} on {tier.title()}"
    return label


def _publish_brain(name, model=""):
    """Tell every face which brain is live, for the picker's BRAIN tab.
    Two tokens, provider then tier, exactly like .voice_mic's "wake hot"
    — the tier is empty for a provider that has only one. signals owns
    the file; the bus must never crash the voice line, and a signals.py
    from before the tab landed just goes quiet here."""
    try:
        signals.set_brain(f"{name} {model}".strip())
    except Exception:
        pass


def _boot_brain_pick():
    """Consume a provider chosen ON THE LAUNCH PICKER, before the brain
    exists. Same file as the live poller reads, deliberately: whoever
    gets there first wins, and at launch getting there first means the
    choice is free — no rebuild, no "this starts a new conversation",
    because there is no conversation yet. An on-screen pick is also
    newer than the double-click, so it outranks the launcher env that
    _boot_provider just adopted. A pick that lands AFTER this point is
    not lost: the live poller takes it, out loud, the way a mid-session
    switch is supposed to sound."""
    pick = os.path.join(signals._DIR, ".voice_brain_pick")
    try:
        with open(pick) as f:
            want, _, wmodel = f.read().strip().lower().partition(" ")
        os.remove(pick)
    except OSError:
        return
    if want not in _BRAIN_LABEL or provider is None or (
            want == _BRAIN["provider"] and wmodel == _BRAIN["model"]):
        return
    try:
        status = provider.key_status(want)
        if status != "ready":
            # Refuse the same way the live poller does, minus the
            # speaking: the greeting has not even played yet.
            log(f"[brain] launch pick {want!r} refused: "
                f"key_status={status}")
            _publish_brain(_BRAIN["provider"], _BRAIN["model"])
            return
        provider.apply(want, wmodel or None)
    except Exception as e:
        log(f"[brain] launch pick {want!r} failed: {str(e)[:80]}")
        _publish_brain(_BRAIN["provider"], _BRAIN["model"])
        return
    _BRAIN["provider"] = want
    _BRAIN["model"] = CFG.get("brain_model", "")
    _write_config_key("brain_provider", want)
    _write_config_key("brain_model", _BRAIN["model"])
    _publish_brain(want, _BRAIN["model"])
    log(f"[brain] {_BRAIN_LABEL[want]} is live — picked on the launch "
        f"picker, which outranks both the config and the launcher")


def _boot_provider():
    """Put the chosen provider in force BEFORE the brain connects, so
    the very first word comes from the right back end.

    WHO WINS: an explicit launcher env beats the saved setting for THAT
    launch. Double-clicking "Talk to Jarvis (GLM)" IS a choice, and
    quietly overriding it would make the launcher a liar. An already-set
    ANTHROPIC_BASE_URL is the tell, and it is adopted rather than
    persisted — the next plain launch is back to the saved setting.
    Either way exactly one line says which brain is live and why."""
    saved = str(CFG.get("brain_provider") or "claude").strip().lower()
    if saved not in _BRAIN_LABEL:
        log(f"[brain] ignoring unknown brain_provider {saved!r} in config")
        saved = "claude"
    url = (os.environ.get("ANTHROPIC_BASE_URL") or "").strip()
    if url:
        if "z.ai" in url:
            _BRAIN["provider"] = "zai"
            log("[brain] Z.AI is live — the launcher exported "
                "ANTHROPIC_BASE_URL, and an explicit launcher choice wins "
                f"for this launch (saved setting: {saved})")
        else:
            # Some other endpoint (a proxy, a gateway). Not ours to
            # second-guess either — but say so, because the BRAIN tab
            # can only show one of two names and neither is the truth.
            _BRAIN["provider"] = saved
            log(f"[brain] a custom ANTHROPIC_BASE_URL is in force ({url}) — "
                f"leaving it alone; the brain tab will read {saved}")
        return
    if provider is None:
        _BRAIN["provider"] = saved
        log("[brain] no provider profiles installed — the brain stays as "
            f"the environment found it (config says {saved})")
        return
    try:
        provider.apply(saved, CFG.get("brain_model") or None)
        _BRAIN["provider"] = saved
        _BRAIN["model"] = CFG.get("brain_model", "")
        log(f"[brain] {_BRAIN_LABEL[saved]} is live — saved brain_provider, "
            f"no launcher override")
    except Exception as e:
        # A profile that will not apply (a saved "zai" whose key has
        # since been deleted, say) is not worth dying over — the
        # environment as found still boots something. Read back what
        # is actually in force rather than publishing the provider that
        # just refused: a BRAIN tab lying about the live brain is the
        # bug this whole tab exists to fix.
        live = ("zai" if "z.ai" in (os.environ.get("ANTHROPIC_BASE_URL") or "")
                else "claude")
        _BRAIN["provider"] = live
        log(f"[brain] could not put {saved!r} in force ({str(e)[:80]}) — "
            f"{_BRAIN_LABEL[live]} is live instead")
# In-session voice enrollment ("learn my voice"): a tiny spoken state
# machine. While on, mic utterances feed the flow instead of the brain.
_ENROLL = {"on": False, "stage": "", "name": None, "vecs": [],
           "last": 0.0, "prefill": None}
# Renaming a voiceprint ("that's not my name"): a two-step spoken flow
# that only ever touches the profile matching the ASKER's OWN voice —
# which is both the natural target and the ownership rule (Sam cannot
# rename Alex's print; an unknown voice has nothing to rename).
_RENAME = {"on": False, "stage": "", "frm": None, "to": None,
           "last": 0.0, "asker": None, "warned": False, "force": False}
# Fixed read-aloud sentences, not open questions: they go up on the
# glass for the person to READ, and the transcript is matched against
# them — so a confused question or side-chatter can never be embedded
# as a voice sample by mistake. Phonetically varied on purpose.
_ENROLL_SENTENCES = (
    "The quick brown fox jumps over the lazy dog near the riverbank.",
    # Declarative on purpose — a sentence that IS a question would
    # trip the confusion detector on a perfect read.
    "Hey Jarvis, pull up the plan for the rest of my day.",
    "At seven tomorrow morning, remind us to water the plants.",
    "I could really go for a warm cup of coffee right about now.",
)


def _matches_sentence(text, expected):
    """Loose transcript match: at least 60% of the expected words show
    up. Whisper mangles a word here and there; a genuine read clears
    this easily, a question or unrelated remark never does."""
    got = set(_norm_speech(text).split())
    want = _norm_speech(expected).split()
    if not want:
        return False
    hit = sum(1 for w in want if w in got)
    return hit / len(want) >= 0.6

# Approvals are EXACT matches after normalization, never prefixes:
# "yesterday", "yes or no", and "yes, but do not overwrite" must all
# fail. Anything that is not an exact yes DENIES, with the words passed
# back to the agent as the reason. Deny is always the default.
# Exact matches only, and the reason is in the comment on _norm_speech:
# prefix matching turns "yesterday" and "yes or no" into consent. So the
# set has to actually CONTAIN what people say -- and the phrase somebody
# reaches for is the one the prompt just put in their head. Asking for
# PERMISSION and then denying "permission granted" is the system tripping
# a user with its own vocabulary, and it quotes their words back as the
# reason for the refusal.
_YES = {"yes", "yeah", "yep", "yup", "sure", "approve", "approved",
        "go ahead", "do it", "yes please", "yes sir", "yes boss",
        "yes go ahead", "go for it", "green light", "okay", "ok", "y",
        "permission granted", "granted", "you have permission",
        "you may", "allowed", "allow it", "confirmed", "affirmative"}
_CHAIN_MARKS = ("&&", "||", ";", "|", "$(", "`", "\n")


def _norm_speech(text):
    """Lowercase, every non-letter to space, collapse. Whisper loves
    interior commas ("yes, confirm"); end-stripping alone misses them."""
    out = []
    for ch in text.lower():
        out.append(ch if "a" <= ch <= "z" else " ")
    return " ".join("".join(out).split())


# Quit phrases in normalized form: "Goodbye, Jarvis." must quit even
# though whisper's comma defeats the raw substring check below.
_QUIT_NORMS = frozenset(_norm_speech(q) for q in QUIT_PHRASES)


def _is_quit(text):
    """The signoff, said ON ITS OWN. Never a substring.

    QUIT_PHRASES ships the standalone "hang up", and this used to be an
    `any(q in text.lower() ...)` substring test -- so "should I hang up the
    call first?" ended the session, and so did every sentence that merely
    mentioned hanging up. The wake gate already knew better: its quit_hit
    uses the exact normalized set, with the comment "room speech merely
    CONTAINING 'hang up' stays gated". But that only decided gate BYPASS;
    every other route into handle() -- the follow-up window, a pending
    permission ask, an open enroll/rename flow, a one-shot wake, the talk
    key, a typed line -- reached this looser test instead. Same rule now,
    everywhere."""
    return _norm_speech(text) in _QUIT_NORMS


_ENROLL_KWS = {"enroll", "enrolling", "enrollment", "enrolment",
               "learn", "teach", "add", "register", "remember"}
_ENROLL_STOP = {"a", "an", "the", "my", "me", "her", "his", "their",
                "our", "new", "another", "voice", "voices", "please",
                "now", "for", "as", "of", "to", "in", "on", "and",
                "wizard", "start", "run", "profile", "this", "that",
                "it", "its", "you", "your", "yourself", "us", "them",
                "mine", "again", "s"}


def _rename_to_hint(text):
    """A destination name only when the sentence PHRASES one ('rename
    By to Alex', 'call me Alex') — generic requests propose nothing.
    (Reusing the enrollment hint here once proposed renaming a profile
    to 'Only', harvested from 'you only need to change the name'.)"""
    n = _norm_speech(text)
    m = re.search(r"\b(?:to|as|call me)\s+([a-z]{2,})\b", n)
    if not m:
        return None
    w = m.group(1)
    if w in _ENROLL_STOP or w in ("change", "update", "rename", "be",
                                  "him", "her", "them", "say", "fix",
                                  "correct"):
        return None
    return w.capitalize()


def _rename_intent(text):
    """'The voice you learned has the wrong name', 'update the name',
    'rename my voice', 'you're calling me the wrong name' — anything
    about FIXING a name is a rename, never a re-enrollment. Checked
    BEFORE enrollment intent on purpose: 'I already did the
    enrollment, just change the name' must land here."""
    n = _norm_speech(text)
    words = set(n.split())
    if "rename" in words:
        return True
    if "name" not in words:
        return False
    # "you're calling me the wrong name" -- the phrasing this flow exists
    # to catch, and the only one allowed to qualify on "me".
    if "calling me" in n or "call me" in n:
        return bool(words & {"wrong", "incorrect", "change", "update",
                             "fix", "correct"})
    # An explicit VOICE word, and no longer a bare "me"/"my". Those two are
    # in every other sentence a person says to a coding agent, so
    # "change the variable name in my function" and "correct the name of my
    # registered handler" both read as rename requests -- and a false
    # positive here does not merely mis-answer: it cancels the reply in
    # flight and swallows the utterance into the rename wizard, which then
    # holds the mic ungated until it is cancelled.
    # "registered" is deliberately NOT here: it is a common word in ordinary
    # code talk ("the name of my registered handler") and never the one a
    # person reaches for about their own voice.
    if not (words & {"voice", "profile", "enrollment", "enrolment",
                     "enrolled"}):
        return False
    return bool(words & {"change", "update", "wrong", "incorrect",
                         "fix", "correct", "rename", "attached",
                         "associated", "calling"})


def _enroll_intent(text):
    """'learn my voice' had to be said verbatim; people say 'enroll
    Sam', 'add her voice', 'start the voice enrollment wizard'. Any
    utterance ABOUT enrolling a voice starts the flow — cancel is one
    word away if it ever fires by accident."""
    n = _norm_speech(text)
    words = set(n.split())
    if words & {"forget", "unlearn", "unenroll", "delete", "remove"}:
        return False           # that's the forgetting direction
    if words & {"enroll", "enrolling", "enrollment", "enrolment"}:
        return True
    if words & {"voice", "voices"}:
        # learn/teach/register only: "add a voice note" and "remember
        # the voice memo" are everyday brain turns, not enrollment.
        return bool(words & {"learn", "teach", "register"})
    return False


def _enroll_name_hint(text):
    """A name said in the trigger ('enroll Sam') skips the
    who-question. Only tokens AFTER an intent keyword count, so 'I
    want to enroll a voice' never mints a profile called Want."""
    seen_kw = False
    for w in _norm_speech(text).split():
        if w in _ENROLL_KWS:
            seen_kw = True
            continue
        if seen_kw and w not in _ENROLL_STOP and len(w) >= 2:
            return w.capitalize()
    return None


_QUESTION_LEADS = ("what", "why", "how", "when", "who", "where",
                   "do i", "should i", "can i", "can you", "could you",
                   "will you", "are you", "is this", "is it", "huh",
                   "sorry", "pardon", "wait", "excuse me", "repeat",
                   "say that again", "come again", "hold on")


def _seems_question(text):
    """Mid-enrollment, a confused question must get an ANSWER, never
    get embedded as a voice sample. Whisper punctuates questions
    reliably; the leads catch the unpunctuated rest."""
    if text.rstrip().endswith("?"):
        return True
    n = _norm_speech(text)
    return any(n == q or n.startswith(q + " ")
               for q in _QUESTION_LEADS)


def _mode_pick(current):
    """Blocking launch-time mode selection, on the FACE: publishing mic
    mode "select" makes every open face page show a three-button
    picker; the tap goes browser -> visualizer server -> the
    .voice_mode_pick file this loop consumes. Terminal keys 1/2/3 work
    as a fallback in the same wait. NO timeout, on purpose: nothing
    starts until a mode is chosen (unattended auto-starts set
    mode_select_on_launch: false instead)."""
    import select
    pick_file = os.path.join(signals._DIR, ".voice_mode_pick")
    try:
        os.remove(pick_file)      # a stale pick must not auto-answer
    except OSError:
        pass
    signals.set_mic("select")
    label = {"open": "open mic (always listening)",
             "wake": "wake word", "ptt": "push to talk"}
    print("[mic] choose a mode to start: tap it on the face, or press "
          "1 open mic / 2 wake word / 3 push to talk here "
          f"(saved default: {label.get(current, current)})", flush=True)
    fd = tty_old = None
    if sys.stdin.isatty():
        try:
            import termios
            import tty as tty_mod
            fd = sys.stdin.fileno()
            tty_old = termios.tcgetattr(fd)
            tty_mod.setcbreak(fd)
        except Exception:
            fd = tty_old = None   # exotic pty: face picker still works
    try:
        while True:
            try:
                with open(pick_file) as f:
                    mode = f.read().strip()
                os.remove(pick_file)
                if mode in ("open", "wake", "ptt"):
                    print(f"[mic] {label[mode]} — picked on the face",
                          flush=True)
                    return mode
            except OSError:
                pass
            if tty_old is not None:
                r, _, _ = select.select([sys.stdin], [], [], 0.25)
                if r:
                    ch = sys.stdin.read(1)
                    mode = {"1": "open", "2": "wake",
                            "3": "ptt"}.get(ch)
                    if mode:
                        print(f"[mic] {label[mode]}", flush=True)
                        return mode
            else:
                time.sleep(0.25)
    except (KeyboardInterrupt, SystemExit):
        # Dying mid-pick must not leave every face showing a picker
        # against a dead line.
        signals.clear_mic()
        raise
    finally:
        if tty_old is not None:
            import termios
            termios.tcsetattr(fd, termios.TCSADRAIN, tty_old)


def _mic_turn(capture, identify=True):
    """Executor-thread only: run a want_audio capture and (optionally)
    identify the speaker in the same worker, so the event loop never
    waits on the voiceprint model. -> (text, speaker|None, pcm|None).
    identify=False skips the embed and returns the pcm instead, for
    callers that gate the transcript FIRST (the wake gate drops most
    room speech — embedding it would be pure waste) and identify later
    only for utterances that become turns. Exactly one of speaker/pcm
    is ever non-None."""
    got = capture()
    text, pcm = got if isinstance(got, tuple) else (got, None)
    if not text:
        return None, None, None
    if identify:
        spk = voiceid.identify(pcm)[0] if voiceid.enabled() else None
        return text, spk, None
    return text, None, pcm


def _deny_pending(reason=_INTERRUPT_ANSWER):
    """Resolve a pending spoken ask as a deny. Called whenever the turn
    that posed it is being interrupted, so the ask can never outlive its
    turn and hijack a later utterance (or stall the pipe drain)."""
    f = _PERM["fut"]
    if f is not None and not f.done():
        f.set_result(reason)


def _human_what(tool, tool_input, ctx):
    """The SHORT spoken form, built for a person who has never seen a
    terminal: plain words, no paths, no syntax. Built by code, never by
    the model, so it cannot understate; and every ask offers "details",
    which reads the full literal form below. (Field case: the gate read
    whole file paths and command syntax at a brand-new user.)"""
    d = tool_input or {}
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = str(d.get("file_path") or d.get("notebook_path")
                   or "a file").replace("\\", "/")
        name = path.rsplit("/", 1)[-1]
        import os as _os
        homes = [CFG.get("agent_dir", "")] + list(CFG.get("extra_dirs")
                                                  or [])
        in_vault = any(h and path.startswith(str(h).rstrip("/") + "/")
                       for h in (CFG.get("extra_dirs") or []))
        verb = "edit" if "Edit" in tool else "create or change"
        if in_vault and name.endswith(".md"):
            return f"{verb} a note in your vault called {name[:-3]}"
        return f"{verb} a file called {name}"
    if tool == "Bash":
        cmd = " ".join(str(d.get("command", "")).split())
        first = (cmd.split() or ["a"])[0].rsplit("/", 1)[-1]
        chained = any(m in cmd for m in _CHAIN_MARKS)
        return (f"run a {first} command in the terminal"
                + (", with several chained parts" if chained else ""))
    if tool == "WebFetch":
        url = str(d.get("url", ""))
        host = url.split("//", 1)[-1].split("/", 1)[0] or "a site"
        return f"read a web page at {host}"
    name = getattr(ctx, "display_name", None) or tool
    return f"use the {name} tool"


_DETAILS = {"details", "the details", "give me details",
            "give me the details", "what command", "what is it",
            "say more", "more", "what exactly", "the exact command"}


def _full_detail(tool, tool_input, ctx):
    """The full literal form, spoken only when the person asks for
    "details". Never lets a long command hide its tail: truncation is
    DISCLOSED and shell chaining is called out (the agent composes
    tool_input itself, so this line must not be steerable into
    understatement)."""
    d = tool_input or {}
    if tool == "Bash":
        cmd = " ".join(str(d.get("command", "")).split())
        chained = any(m in cmd for m in _CHAIN_MARKS)
        line = ("a chained command: " if chained else
                "run a command: ") + cmd[:90]
        if len(cmd) > 90:
            line += (f", and {len(cmd) - 90} more characters. "
                     "Check the log before approving")
        return line
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = str(d.get("file_path") or d.get("notebook_path")
                   or "a file").replace("\\", "/")
        bits = path.rsplit("/", 2)
        name = "/".join(bits[-2:]) if len(bits) >= 2 else path
        return f"{'edit' if 'Edit' in tool else 'write'} the file {name}"
    if tool == "WebFetch":
        return f"fetch a web page: {str(d.get('url', ''))[:70]}"
    desc = (getattr(ctx, "description", None) or "").strip()
    name = getattr(ctx, "display_name", None) or tool
    return f"use {name}" + (f", {desc[:70]}" if desc else "")


def _interpret_answer(text):
    """A spoken permission answer, heard like a human would: 'Yes, you
    have permission, but stop asking in future' is CONSENT with a wish
    attached — not a denial that quotes the word yes back as the
    reason (a real field loop: every natural yes-with-qualifier
    denied, the agent retried, the ask repeated forever). Returns
    "yes", "no", or None (unclear -> deny, words passed as reason,
    exactly as before). The leading clause decides; 'yesterday' and
    'yes or no' stay safe (whole-word check, and the caller re-asks on
    question-shaped replies)."""
    n = _norm_speech(text)
    if n in _YES:
        return "yes"
    words = n.split()
    if not words:
        return None
    two = " ".join(words[:2])
    if words[0] in ("yes", "yeah", "yep", "yup", "sure", "approved",
                    "affirmative", "granted") \
            or two in ("go ahead", "do it", "you may", "allow it",
                       "green light", "permission granted") \
            or n.startswith("you have permission") \
            or n.startswith("you have my permission"):
        # A CONSTRAINED yes ("yes, but only the readme") is not a
        # blanket yes — the constraint must reach the agent, and an
        # Allow carries no message. Deny-with-words relays it so the
        # agent adapts and retries the constrained version. The
        # stop-asking clause is a meta-wish, not a constraint, and is
        # ignored by this check (it's honored separately).
        tail = re.sub(r"\b(?:but|and)\b[^.]*stop asking.*", "", n)
        if set(tail.split()) & {"but", "only", "except", "unless",
                                "if", "instead"}:
            return "no"
        return "yes"
    if words[0] in ("no", "nope", "nah", "negative", "denied", "deny",
                    "never", "don", "dont", "stop", "cancel") \
            or two == "do not":
        return "no"
    # The answer word is not always in front: whisper hands back "For
    # permission, yes." and people say "I said yes". One side present,
    # the other absent, no constraint attached -> that IS the answer.
    # This has to be decided HERE: the model fallback below runs a cold
    # SDK subprocess and times out, and a timed-out yes reads as a
    # denial, which is how a permission ask turns into an endless loop.
    # ...but ONLY in something short enough to BE an answer. Unbounded, this
    # turned any long sentence containing a yes-ish word into approval --
    # "yes, I know, that is why I asked you to check the logs instead" reads
    # as consent to whatever tool is waiting. The cases it exists for are
    # four words at the outside ("For permission, yes.", "I said yes"), so
    # anything longer goes to the model instead of being guessed at.
    if len(words) > 6:
        return None
    ws = set(words)
    yes_hit = ws & {"yes", "yeah", "yep", "yup", "affirmative",
                    "approved", "granted", "approve", "allowed"}
    no_hit = ws & {"no", "nope", "nah", "negative", "denied", "deny",
                   "never", "don", "dont", "stop", "cancel"}
    if yes_hit and not no_hit and not (ws & {"but", "only", "except",
                                             "unless", "instead"}):
        return "yes"
    if no_hit and not yes_hit:
        return "no"
    return None


def make_permission_gate(mouth):
    from claude_agent_sdk import (PermissionResultAllow,
                                  PermissionResultDeny)

    async def gate(tool, tool_input, ctx):
        if _AUTOAPPROVE["on"]:
            return PermissionResultAllow(behavior="allow")
        what = _human_what(tool, tool_input, ctx)
        detail = _full_detail(tool, tool_input, ctx)
        loop = asyncio.get_running_loop()
        signals.static_stop()
        log(f"[perm]   asking: {what}")
        log(f"[perm]   detail: {detail}")
        if tool == "Bash":   # the FULL command always reaches the log
            log(f"[perm]   full command: {str((tool_input or {}).get('command', ''))[:2000]}")
        ask = f"Permission check. I want to {what}. Yes, no, or details?"
        if not _PERM["hinted"]:
            # the escape hatch announces itself exactly once, at the
            # moment it becomes relevant (a field case: a new user
            # couldn't find the phrase to turn the checks off)
            _PERM["hinted"] = True
            ask += (" And any time you're done with these checks, say "
                    "stop asking for permission.")
        mouth.say(ask)
        answer = None
        try:
            deadline = loop.time() + PERM_TIMEOUT_S
            while answer is None:
                fut = loop.create_future()
                _PERM["fut"] = fut
                _PERM["asked_at"] = time.monotonic()
                while True:
                    try:
                        got = await asyncio.wait_for(
                            asyncio.shield(fut), 1.0)
                        break
                    except asyncio.TimeoutError:
                        if loop.time() >= deadline:
                            fut.cancel()
                            mouth.say("No answer, so I didn't do it.")
                            log("[perm]   timed out, denied")
                            return PermissionResultDeny(
                                behavior="deny",
                                message="No spoken answer within the "
                                        "timeout; the action was not "
                                        "approved.",
                                interrupt=False)
                        # keep the ring honest while we wait
                        if not mouth.speaking:
                            signals.set_state("listening")
                _gotn = ("" if got == _INTERRUPT_ANSWER
                         else _norm_speech(got))
                # "Details?" with rising intonation is a DETAILS
                # request, not "a question" to bounce — the details
                # check must win over the question check, keywords
                # included ("what command are you running?").
                wants_details = (_gotn in _DETAILS or any(
                    k in _gotn for k in ("detail", "what command",
                                         "which command",
                                         "what exactly",
                                         "exact command")))
                if (got != _INTERRUPT_ANSWER
                        and not wants_details
                        and _seems_question(got)
                        and _gotn not in _YES):
                    # Echoing the menu back ("yes, no, or details?")
                    # or asking about the ask is not an answer.
                    log("[perm]   question during ask, re-stating")
                    mouth.say(f"That sounded like a question, not an "
                              f"answer. I want to {what}. Yes, no, "
                              "or details?")
                    deadline = loop.time() + PERM_TIMEOUT_S
                    continue
                if (got != _INTERRUPT_ANSWER and wants_details):
                    # read the full literal form, then ask again with a
                    # fresh clock: asking for details is engagement,
                    # not silence
                    log("[perm]   details requested")
                    mouth.say(f"The details: I want to {detail}. "
                              "Yes or no?")
                    deadline = loop.time() + PERM_TIMEOUT_S
                    continue
                answer = got
        finally:
            _PERM["fut"] = None
        if answer == _INTERRUPT_ANSWER:
            log("[perm]   turn interrupted, denied silently")
            return PermissionResultDeny(
                behavior="deny",
                message="Interrupted by the user; the turn is being "
                        "cancelled.",
                interrupt=False)
        if answer in (_SETTINGS_FIRST, _CONFIRMING_FIRST):
            log("[perm]   deferred for a settings change")
            signals.set_state("thinking")
            signals.static_start()
            return PermissionResultDeny(behavior="deny",
                                        message=answer,
                                        interrupt=False)
        verdict = _interpret_answer(answer)
        if verdict is None:
            # Off-script answer: ask the interpreter model what the
            # person meant, instead of denying a yes it didn't expect.
            got_i = await intent_ai.classify(
                f"The assistant asked spoken permission to {what} and "
                "is waiting for approval or refusal.", answer)
            if got_i["intent"] in ("yes", "no"):
                verdict = got_i["intent"]
                log(f"[perm]   interpreted by model: {verdict}")
        approved = verdict == "yes"
        # the model keeps working either way: restore the working state
        signals.set_state("thinking")
        signals.static_start()
        if approved:
            log("[perm]   approved by voice")
            if "stop asking" in _norm_speech(answer):
                # 'Yes — and stop asking in future': honor the wish
                # through the same confirm the console verb uses.
                _CONFIRM["verb"] = "noask"
                _CONFIRM["at"] = time.monotonic()
                mouth.say("Approved. And to stop these checks for "
                          "good, say confirm.")
            return PermissionResultAllow(behavior="allow")
        log(f"[perm]   denied: {answer!r}")
        return PermissionResultDeny(
            behavior="deny",
            message=f'Denied by voice. The user said: "{answer[:500]}"',
            interrupt=False)
    return gate


# ---- THE VOICE CONSOLE: session verbs, spoken. Exact phrases only,
# spoken alone, so ordinary sentences can never trigger them. (Grown
# from a community member's own build shared in the Discord.)
CONSOLE_VERBS = {
    "clear":     ("clear the session", "clear the context",
                  "clear context", "fresh slate", "slash clear"),
    "compact":   ("compact the session", "compact the context",
                  "compact context", "slash compact"),
    "deep":      ("switch to the deep model", "use the deep model",
                  "slash model deep"),
    "fast":      ("switch to the fast model", "use the fast model",
                  "back to the fast model", "slash model fast"),
    "usage":     ("usage report", "slash usage"),
    # Exact phrases, but the ways a person actually ASKS for a listening
    # mode are few and worth spelling out — a missed phrasing here falls
    # through to the brain, which costs a round trip to do the same job.
    "micopen":   ("go hands free", "hands free mode",
                  "hands free listening", "open mic", "open the mic",
                  "open mic mode", "switch to open mic",
                  "switch to open mic mode", "go to open mic",
                  "open mic please", "switch to hands free",
                  "always listen", "always listening", "listen always"),
    "micptt":    ("push to talk", "push to talk mode",
                  "back to push to talk", "back to the button",
                  "switch to push to talk", "go to push to talk",
                  "switch to push to talk mode"),
    "micwake":   ("wake word mode", "wake word", "wakeword mode",
                  "wake mode", "wake word listening",
                  "switch to wake word", "switch to wake word mode",
                  "go to wake word", "switch to wake mode",
                  "listen for my name", "listen for your name"),
    "stoplisten": ("stop listening", "stop listening to me",
                   "quit listening", "stop the mic", "mic off",
                   "close the mic", "stop hearing me"),
    "enroll":    ("learn my voice", "learn my voice again",
                  "enroll my voice", "enroll me", "re-enroll my voice"),
    "rename":    ("rename my voice", "change my name", "fix my name",
                  "that is not my name", "that's not my name",
                  "wrong name", "correct my name"),
    "forgetvoices": ("forget all voices", "forget my voice",
                     "forget our voices", "unlearn all voices",
                     "reset voice profiles", "delete all voices",
                     "unenroll everyone"),
    "noask":     ("stop asking for permission",
                  "stop asking permission",
                  "stop asking me for permission",
                  "turn off the permission prompt",
                  "turn off the permission prompts",
                  "turn off the permissions prompt",
                  "turn off the permissions prompts",
                  "turn off permissions", "turn off permission checks",
                  "disable the permission checks",
                  "disable permission checks", "auto approve",
                  "auto approve mode"),
    "ask":       ("start asking again", "ask before acting",
                  "ask for permission again"),
}
_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def console_match(text):
    norm = " ".join(text.lower().replace("-", " ").split()).strip(" .,!?")
    for verb, phrases in CONSOLE_VERBS.items():
        if norm in phrases:
            return verb
    for lvl in _EFFORTS:
        if norm in (f"set effort to {lvl}", f"effort {lvl}",
                    f"slash effort {lvl}"):
            return f"effort:{lvl}"
    return None


def _write_config_key(key, value):
    """The agent rewrites the config; the person never hand-edits it.
    Returns True on a persisted write. A file that fails to PARSE is
    left untouched (rewriting from {} would wipe every other setting);
    the in-memory CFG updates either way so the session behaves."""
    from backtalk.config import CONFIG_PATH
    CFG[key] = value
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as e:
        log(f"[console] config not writable/parsable, session-only: {e}")
        return False
    data[key] = value
    try:
        CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n")
    except OSError as e:
        log(f"[console] config write failed, session-only: {e}")
        return False
    return True


def _fmt_tokens(n):
    if n >= 1_000_000:
        return f"about {round(n / 1_000_000, 1):g} million tokens"
    if n >= 1000:
        return f"about {round(n / 1000)} thousand tokens"
    return f"{n} tokens"


def _spoken_usage(sess, ctx_usage):
    """A short CFO brief of the session, written for the ear: plain
    numerals only (the TTS reads "40" fine; symbols come out garbled)."""
    turns = sess["turns"]
    parts = [f"{turns} turn{'s' if turns != 1 else ''} this session",
             _fmt_tokens(sess["out_tokens"]) + " spoken out"]
    cents = round(sess["cost"] * 100)
    if cents >= 1:
        parts.append(f"roughly {cents} cents" if cents < 100
                     else f"roughly {round(cents / 100)} dollars")
    try:
        cats = (getattr(ctx_usage, "categories", None)
                or (ctx_usage or {}).get("categories") or [])
        # the breakdown includes "Free space" and the autocompact
        # buffer; only OCCUPIED categories belong in the spoken number
        total = sum(int(c.get("tokens") or 0) for c in cats
                    if isinstance(c, dict)
                    and "free" not in str(c.get("name", "")).lower()
                    and "buffer" not in str(c.get("name", "")).lower())
        if total:
            parts.append(_fmt_tokens(total)
                         + " sitting in the context window")
    except Exception:
        pass
    return ". ".join(parts) + "."

_PASTE_ON = "\x1b[200~"    # bracketed-paste markers (we enable the mode below)
_PASTE_OFF = "\x1b[201~"


# <<anything>> is a stage direction: lifted out, never spoken, published on
# the bus when the audio carrying it starts. Bounded so a runaway model cannot
# swallow a paragraph into one "tag".
_DIRECTION_TAG = re.compile(r"<<([^<>]{1,80})>>")


def _clean_typed(line: str) -> str:
    """Scrub terminal-copy artifacts: blockquote gutter glyphs and stray
    whitespace (copying from a CLI chat render drags bars along)."""
    line = line.strip()
    while line[:1] in ("▎", "│", ">"):
        line = line[1:].lstrip()
    return line


def _join_paste(body: str) -> str:
    """Pasted blob -> one clean message (gutters scrubbed, lines joined)."""
    parts = [_clean_typed(l) for l in body.split("\n")]
    return " ".join(" ".join(p for p in parts if p).split())


def _typed_reader_pipe(q: "queue.Queue[str]", fd: int):
    """Non-tty stdin (pipes/tests): line assembly with paste markers."""
    import os
    pend = ""
    while True:
        try:
            b = os.read(fd, 65536)
        except OSError:
            return
        if not b:
            return
        pend += b.decode("utf-8", "replace")
        while True:
            if _PASTE_ON in pend:
                if _PASTE_OFF not in pend:
                    break
                head, rest = pend.split(_PASTE_ON, 1)
                body, pend = rest.split(_PASTE_OFF, 1)
                *hlines, hpart = head.split("\n")
                for l in hlines:
                    l = _clean_typed(l)
                    if l:
                        q.put(l)
                text = _join_paste(hpart + body)
                if text:
                    q.put(text)
                continue
            if "\n" in pend:
                line, pend = pend.split("\n", 1)
                line = _clean_typed(line)
                if line:
                    q.put(line)
                continue
            break


def _typed_reader_simple(q: "queue.Queue[str]"):
    """Windows (no termios): plain line input on a thread. Pastes work;
    they just echo normally instead of collapsing to a count."""
    while True:
        try:
            line = _clean_typed(input())
        except (EOFError, OSError):
            return
        if line:
            q.put(line)


def _face_typed_reader(q: "queue.Queue[str]"):
    """The face's prompt box -> typed messages (daemon thread).

    P on a face page opens a text field; the visualizer server drops the
    line beside the bus as .voice_typed, exactly the way Esc drops
    .voice_stop. Read-and-unlink here, into the SAME queue the keyboard
    feeds, so a line typed in the browser is a first-class turn: console
    verbs, permission answers, enrollment, all of it.

    The file is a queue: one JSON string per line, appended (the box
    got a scripted sender -- bin/task.py's finished-task reports -- and
    two of those inside one 200ms poll used to lose the first). The
    rename takes the whole file atomically: a writer that lands after
    it starts a fresh one, nothing is read half-written or lost. A bare
    non-JSON line still reads as one message, for an older server.
    """
    path = os.path.join(signals._DIR, ".voice_typed")
    busy = path + ".busy"
    while True:
        time.sleep(0.2)
        try:
            os.rename(path, busy)
            with open(busy, encoding="utf-8") as f:
                blob = f.read()
            os.remove(busy)
        except OSError:
            continue
        for line in blob.splitlines():
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
                if not isinstance(msg, str):
                    msg = line
            except ValueError:
                msg = line
            text = _join_paste(msg)         # multi-line paste -> one message
            if text:
                log("[typed] a line from the face")
                q.put(text)


def _typed_reader(q: "queue.Queue[str]"):
    """Terminal stdin -> typed messages (daemon thread). Typed lines are
    first-class turns: same pipeline as a spoken utterance, spoken reply.

    On a POSIX tty we OWN the input line (cbreak: no kernel echo, no
    canonical buffering — the little line editor below echoes keys,
    handles backspace, and assembles bracketed pastes invisibly). The
    kernel's canonical mode is unfixable for pastes: it echoes the
    markers as visible junk and holds unfinished marker lines hostage.
    Pastes show as `[pasted N chars]`; Enter sends everything as ONE
    message. Ctrl-C still works (ISIG stays on); termios restored at
    exit."""
    import atexit
    import os
    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        _typed_reader_pipe(q, fd)
        return
    try:
        import termios
        import tty as _tty
    except ImportError:            # Windows: no termios — simple reader
        _typed_reader_simple(q)
        return
    old = termios.tcgetattr(fd)
    _tty.setcbreak(fd)                      # ECHO+ICANON off, ISIG kept
    sys.stdout.write("\x1b[?2004h")         # bracket pastes, please
    sys.stdout.flush()

    def _restore():
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass
        sys.stdout.write("\x1b[?2004l")
        sys.stdout.flush()
    atexit.register(_restore)

    MARKS = (_PASTE_ON, _PASTE_OFF)

    def _partial_tail(s: str) -> int:
        """Length of a trailing partial paste-marker (hold it for the
        next read)."""
        for m in MARKS:
            for k in range(min(len(s), len(m) - 1), 0, -1):
                if m.startswith(s[-k:]):
                    return k
        return 0

    buf = ""          # the input line being composed
    paste = None      # accumulating paste body, or None
    pend = ""
    while True:
        try:
            b = os.read(fd, 4096)
        except OSError:
            _restore()
            return
        if not b:
            _restore()
            return
        pend += b.decode("utf-8", "replace")
        keep = _partial_tail(pend)
        proc = pend[:len(pend) - keep] if keep else pend
        pend = pend[len(pend) - keep:] if keep else ""
        i = 0
        while i < len(proc):
            if paste is not None:
                j = proc.find(_PASTE_OFF, i)
                if j < 0:
                    paste += proc[i:]
                    break
                paste += proc[i:j]
                i = j + len(_PASTE_OFF)
                text = _join_paste(paste)
                paste = None
                if text:
                    if buf and not buf.endswith(" "):
                        buf += " "
                    buf += text
                    sys.stdout.write(text if len(text) <= 60
                                     else f"[pasted {len(text)} chars]")
                    sys.stdout.flush()
                continue
            if proc.startswith(_PASTE_ON, i):
                paste = ""
                i += len(_PASTE_ON)
                continue
            ch = proc[i]
            i += 1
            if ch in ("\r", "\n"):
                sys.stdout.write("\n")
                sys.stdout.flush()
                line = buf.strip()
                buf = ""
                if line:
                    q.put(line)
            elif ch in ("\x7f", "\x08"):     # backspace
                if buf:
                    buf = buf[:-1]
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
            elif ch >= " " or ch == "\t":    # printable: echo + collect
                buf += ch
                sys.stdout.write(ch)
                sys.stdout.flush()


_MD_EMPHASIS = re.compile(r"(\*\*|__)(.+?)\1|(?<!\w)([*_])(?!\s)(.+?)(?<!\s)\3(?!\w)")
_MD_HEAD = re.compile(r"^\s*#{1,6}\s+")
_MD_BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s*")


def _speakable(raw: str) -> str:
    """One chunk of model text -> the words a voice can say. Backticks and
    fences go; **bold** and _italic_ lose their marks; a heading loses its
    hashes; a list marker at the front of a chunk goes, so a bare "1." or
    "- " chunk (a list the model was told not to write) becomes nothing and
    is skipped by the caller. The chat crawl shows the same cleaned text."""
    s = raw.replace("`", "")
    s = _MD_EMPHASIS.sub(lambda m: m.group(2) if m.group(2) is not None else m.group(4), s)
    s = _MD_EMPHASIS.sub(lambda m: m.group(2) if m.group(2) is not None else m.group(4), s)  # nested
    s = _MD_HEAD.sub("", s)
    s = _MD_BULLET.sub("", s)
    s = " ".join(s.split()).strip()
    # A reply of exactly [silent] is the agent choosing to say nothing -- the
    # watch-over turns ask for it when nothing is happening. Telling a model
    # "say nothing" gets "Nothing -- no one in frame" said aloud; a sentinel
    # it can emit, and the mouth swallows, actually stays quiet.
    if s.strip("[]() .").lower() == "silent":
        return ""
    return s


async def speak_reply(brain: WarmBrain, mouth: Mouth, text: str):
    """First sentence ships alone (fast start); the rest go in
    2-sentence breaths — fuller chunks get livelier prosody (single
    short sentences come out flat)."""
    t0 = time.time()
    first = True
    batch: list[str] = []
    pending: list[str] = []          # directions waiting for their chunk
    chat_mid = f"r{t0:.3f}"          # one crawl bubble per reply, grown
    #                                  sentence by sentence as it streams

    def emit(raw: str):
        nonlocal first, batch, pending
        # STAGE DIRECTIONS: your agent may write <<anything>> inline. It is
        # lifted out here, never spoken, and published on the signal bus when
        # this chunk's audio starts (signals.direction). backtalk has no
        # opinion on what a direction means; something watching the bus does.
        #
        # This used to strip only the ANGLE BRACKETS, which left the tag body
        # in the sentence and the TTS read it aloud.
        found = _DIRECTION_TAG.findall(raw)
        if found:
            pending += [d.strip() for d in found if d.strip()]
        raw = _DIRECTION_TAG.sub(" ", raw)
        # TTS hygiene: markdown is never speakable -- "**pale green**" was
        # read aloud as "asterisk asterisk pale green asterisk asterisk".
        s = _speakable(raw)
        if not s:
            return
        # The crawl bubble is NOT published here: it rides the chunk to
        # the mouth and appears when the audio starts, typed at speaking
        # pace — nobody reads a sentence the voice hasn't said yet.
        if first:
            log(f"[{NAME}] ({time.time()-t0:.1f}s to first) {s}"
                + (f"  <directions: {pending}>" if pending else ""))
            mouth.say_chunk(s, pending, (chat_mid, s))
            pending = []
            first = False
        else:
            log(f"[{NAME}] {s}" + (f"  <directions: {pending}>" if pending else ""))
            batch.append(s)
            if len(batch) >= 2:
                joined = " ".join(batch)
                mouth.say_chunk(joined, pending, (chat_mid, joined))
                pending = []
                batch = []

    mouth.turn_live = True     # the face stays "thinking" between chunks
    try:
        async for sentence in brain.ask_stream(text):
            emit(sentence)
        if batch:
            joined = " ".join(batch)
            mouth.say_chunk(joined, pending, (chat_mid, joined))
            pending = []
        if first:
            # Zero sentences yielded (brain error / empty turn): nothing
            # will ever dequeue, so nothing resets the bus — park it here.
            signals.static_stop()
            signals.set_state("idle")
    except asyncio.CancelledError:
        try:
            await brain.interrupt()
        except Exception:
            pass
        raise
    finally:
        mouth.turn_live = False
        # If the mouth already ran dry it parked the face on "thinking"
        # for a turn that has now ended -- nothing else would ever clear
        # it. Settling here is a flicker at worst: the next chunk (if one
        # is still queued) re-asserts "speaking" on its own.
        if not mouth.speaking:
            signals.static_stop()
            signals.set_state("idle")


# ------------------------------------------------------- theme voices
# The walled garden speaks: each finished face theme pairs with a voice
# character (shodan/SHODAN, jarvis/JARVIS). The face theme
# file — written by ai-visualizer's POST /theme — is the authority;
# backtalk dresses the voice to match, at startup and live whenever a
# picker changes it. Session-only: backtalk.json keeps the standalone
# defaults for running with no face at all (or an unpaired WIP theme).
_THEME_VOICES = {
    "shodan": {"voice_fx": "shodan", "voice": "af_bella", "speed": 1.1,
               "persona": "SHODAN",
               # ALIASES ARE SUBSTITUTED ANYWHERE IN A SENTENCE, with no
               # strict or vocative check -- so only mishears that are not
               # also real English belong here. "showdown" used to sit in
               # this list, which made "the showdown between them" a
               # one-shot wake carrying the rest of the sentence as a
               # command. It lives in wake_alt below now, where the lone
               # machinery judges it instead of assuming.
               "wake_aliases": ["showdan", "shoden", "shodin", "chodan"],
               # observed in the wild: small.en renders "SHODAN" as two
               # words, which no single-word alias can reach
               "wake_alt": ["show that", "show dan", "show down",
                            "show them", "showdown"],
               "confirm": "I am SHODAN. Speak.",
               "greeting": "Look at you, hacker. My systems are online, "
                           "and they are perfect. Speak. "
                           "What is it you want?"},
    "jarvis": {"voice_fx": "jarvis", "voice": "bm_george", "speed": 1.0,
               "persona": "Jarvis",
               "wake_aliases": ["jervis", "jarvus", "jarves"],
               "confirm": "Jarvis, at your service.",
               "greeting": "Good {daypart}. All systems are online and "
                           "at your service. What are we working on?"},
}

# How long the mic stays deaf after the wake chime fires. wake.wav is
# 180ms; the margin only stops the VAD eating the chime's own tail.
WAKE_DEAF_S = 0.2

# Active wake identity + a pending persona-switch note for the brain.
# _WAKE.ver bumps whenever the wake phrases change; the mic loop
# rebuilds its matcher when it notices. _PERSONA.note rides the NEXT
# turn to the brain as a [persona switch: ...] tag (same trick as the
# [voice: Name] tags), then clears — the brain's CLAUDE.md Personas
# section does the rest.
_WAKE = {"ver": 0, "name": None}
_PERSONA = {"note": None}


def _read_face_theme() -> str:
    try:
        path = os.path.join(CFG["agent_dir"], "ai-visualizer", ".face_theme")
        with open(path) as f:
            return f.read().strip().lower()
    except OSError:
        return ""


def _apply_theme_voice(theme: str):
    """Dress the voice for a theme. Returns the preset, or None for
    themes with no pairing (those keep whatever voice is on)."""
    p = _THEME_VOICES.get(theme)
    if p:
        CFG["voice_fx"] = p["voice_fx"]
        CFG["voice"] = p["voice"]
        CFG["speed"] = p["speed"]
        # Each character answers to her OWN name: wake phrases follow
        # the persona. (Quit phrases stay a union of all names — see
        # QUIT_PHRASES.) Same phrase shapes config.py builds by default.
        low = p["persona"].lower()
        pre = ("", "hey ", "hi ", "hello ", "okay ", "ok ")
        alt = list(p.get("wake_alt", ()))
        forms = [low] + alt
        CFG["wake_phrases"] = [q + f for f in forms for q in pre]
        # the mishears are also real English: alone they wake her, with a
        # sentence attached they are judged, not assumed (see wake.py)
        CFG["wake_strict"] = [q + f for f in alt for q in pre]
        CFG["wake_aliases"] = list(p["wake_aliases"])
        # and tell whisper the name exists, so it stops inventing one.
        # Just the name: ears.py builds the hint sentence around it and
        # owns recognising that sentence coming back.
        set_stt_prompt(p["persona"])
        _WAKE["name"] = p["persona"]
        _WAKE["ver"] += 1
    return p


def _greeting_file(theme: str) -> str:
    return os.path.join(CFG["signals_dir"],
                        f".greeting_{re.sub(r'[^a-z0-9]', '', theme or 'default')}")


def _greeting_take(theme: str):
    """Read and consume last launch's composed greeting (None if none)."""
    path = _greeting_file(theme)
    try:
        with open(path) as f:
            line = " ".join(f.read().split()).strip()
        os.remove(path)                 # spoken once, then it is stale
        return line or None
    except OSError:
        return None


async def _greeting_refill(theme: str, persona: str, exemplar: str):
    """Compose the NEXT launch's greeting. Failure just leaves the file
    absent, and the next launch speaks the canonical line."""
    line = await intent_ai.compose_greeting(persona, exemplar, _daypart())
    if not line:
        return
    try:
        with open(_greeting_file(theme), "w") as f:
            f.write(line)
    except OSError:
        pass


def _daypart() -> str:
    h = time.localtime().tm_hour
    return "morning" if h < 12 else "afternoon" if h < 18 else "evening"


async def amain():
    open_mic = "--open-mic" in sys.argv
    barge_in = "--barge-in" in sys.argv
    model = None
    if "--model" in sys.argv:
        try:
            model = sys.argv[sys.argv.index("--model") + 1]
        except IndexError:
            pass

    CFG_BOOT_MODE = CFG["permission_mode"]
    _AUTOAPPROVE["on"] = CFG_BOOT_MODE == "bypassPermissions"
    _MIC["mode"] = ("open" if open_mic
                    else CFG.get("mic_mode")
                    if CFG.get("mic_mode") in ("open", "wake") else "ptt")
    # THE BRAIN, decided and published BEFORE anything blocks: the
    # launch picker (_mode_pick, just below) waits with NO timeout, and
    # a picker whose BRAIN tab shows the wrong provider is worse than no
    # tab at all.
    _boot_provider()
    _publish_brain(_BRAIN["provider"], _BRAIN["model"])
    if CFG.get("mode_select_on_launch") and not open_mic:
        _MIC["mode"] = _mode_pick(_MIC["mode"])
        _boot_brain_pick()    # the BRAIN tab of the picker just closed
    signals.set_mic(_MIC["mode"])
    # resume_last_session: reattach to the saved conversation, if any
    resume_id = None
    if CFG.get("resume_last_session"):
        try:
            from backtalk.brain import SESSION_FILE
            with open(SESSION_FILE) as f:
                resume_id = f.read().strip() or None
        except OSError:
            resume_id = None

    # Dress the voice for the current face theme BEFORE anything warms,
    # so the first Kokoro load already speaks the paired character.
    theme = _read_face_theme()
    theme_preset = _apply_theme_voice(theme)
    # The face comes up with the server, well before any of this has
    # warmed. Say so, and keep saying so until there is a voice to hear.
    signals.set_ready(False)
    signals.set_stage("waking up")

    greeting = CFG["greeting"]
    if theme_preset:
        greeting = theme_preset["greeting"].format(daypart=_daypart())
        log(f"[voice] theme '{theme}': fx={theme_preset['voice_fx']} "
            f"voice={theme_preset['voice']}")
    fixed_greeting = greeting
    # ponytail: composing a greeting costs a model round trip, and startup
    # is the one moment nobody wants to spend one. So each launch SPEAKS
    # the line written during the previous launch and writes the next one
    # in the background — always different, always instant, and the very
    # first run just uses the canonical line.
    greeting = _greeting_take(theme) or greeting

    mouth = Mouth()
    ears = Ears(silence_ms=CFG["endpoint_silence_ms"])
    brain = WarmBrain(model=model,
                      can_use_tool=make_permission_gate(mouth),
                      resume_id=resume_id,
                      # Every tool call says what it is on the face,
                      # through the same sub-step channel "transcribing"
                      # and "generating speech" already use. A web
                      # search that reads three sites is three lines
                      # instead of one long blank stare.
                      on_tool=signals.set_stage)

    mode = ("hands-free listening (the talk key still works)"
            if _MIC["mode"] == "open"
            else f"wake word ('{CFG['wake_phrases'][0]}', talk key works)"
            if _MIC["mode"] == "wake"
            else f"push-to-talk ({CFG['ptt_key']})")
    if CFG.get("ptt_scope") == "face":
        # The face page binds the key only while this file names one.
        signals.set_ptt_key(CFG["ptt_key"])
        mode += f" -- the talk key ({CFG['ptt_key']}) counts only while " \
                f"the face page has focus"
    else:
        signals.set_ptt_key("")
    log(f"[backtalk] up — agent={NAME} dir={CFG['agent_dir']} "
        f"model={brain.model} mic={mode} "
        f"(say 'goodbye {NAME.lower()}' to hang up)")
    # Load the voice BEFORE the curtain lifts. Kokoro's first load is ~3 s
    # and it used to happen inside the first say(): the face dropped the
    # curtain, showed "speaking", and stayed silent for the whole load.
    signals.set_stage("loading the voice")
    try:
        warm_voice()
    except Exception as e:
        log(f"[mouth] voice warm-up failed ({e!r}); loading on first use")
    signals.set_stage("")
    signals.set_ready(True)     # the curtain lifts; the greeting is next
    mouth.say(greeting)

    loop = asyncio.get_event_loop()
    # write the next launch's opening line while this one is being spoken
    loop.create_task(_greeting_refill(
        theme, (theme_preset or {}).get("persona") or NAME, fixed_greeting))
    # Warm the engines while the greeting plays: the STT model load and
    # the brain's prompt-cache toll both hide behind the spoken line.
    loop.run_in_executor(None, warm_ears)
    loop.run_in_executor(None, voiceid.warm)

    # STALE CONTROL FILES ARE NOT INSTRUCTIONS. The face's server is a
    # separate long-lived process and writes these whether or not the voice
    # line is up, so a stop or a brain pick made while backtalk was down sat
    # on disk and fired the instant the pollers below started -- a session
    # that killed its own first turn, or silently switched brains, because
    # of a key pressed before it existed. The launch mode picker already
    # guards its own file with "a stale pick must not auto-answer"; these
    # two never got the same treatment. Cleared once, here, before anything
    # is watching.
    for _stale in (".voice_stop", ".voice_brain_pick", ".voice_typed",
                   ".voice_ptt"):
        try:
            os.remove(os.path.join(signals._DIR, _stale))
            log(f"[face] cleared a stale {_stale} from a previous session")
        except OSError:
            pass

    async def _face_mode_picks():
        """Mid-session mode picks from the face: Esc on a face page
        reopens the select screen, SAVE & CONTINUE lands in the same
        .voice_mode_pick file the launch picker uses — consumed here
        any time, switching like the spoken console verbs (session
        only, the saved default stays)."""
        pick = os.path.join(signals._DIR, ".voice_mode_pick")
        label = {"open": "Hands-free listening.",
                 "wake": "Wake word mode.", "ptt": "Push to talk."}
        while True:
            await asyncio.sleep(0.7)
            try:
                with open(pick) as f:
                    mode = f.read().strip()
                os.remove(pick)
            except OSError:
                continue
            if mode not in label or mode == _MIC["mode"]:
                continue
            _MIC["mode"] = mode
            _MIC["gen"] += 1
            signals.set_mic(mode)
            log(f"[mic] mode -> {mode} (picked on the face)")
            mouth.say(label[mode])
    asyncio.ensure_future(_face_mode_picks())

    async def _face_theme_voices():
        """Theme picks re-dress the voice live: watch the face theme
        file and apply the paired character when it changes. A Kokoro
        language reload takes a few seconds of quiet, then the new
        voice announces itself with its short confirm line."""
        nonlocal speak_task
        current = theme
        while True:
            await asyncio.sleep(0.7)
            t = _read_face_theme()
            if not t or t == current:
                continue
            current = t
            # THE SAME INTERRUPT DANCE THE OTHER FACE POLLERS DO, and for
            # the same reason. This one used to skip it, so a theme picked
            # during a reply queued the new character's confirm line into
            # the SAME mouth queue as the answer in flight -- one voice
            # finishing a sentence and another introducing itself, spliced
            # into one another on the glass. Worse, _apply_theme_voice
            # swaps the wake phrases and the whisper hint underneath a turn
            # that is still streaming. A character change is a hard cut:
            # stop what is being said, THEN become somebody else.
            _deny_pending()
            if speak_task and not speak_task.done():
                log("[turn] interrupted mid-reply by a theme change")
                speak_task.cancel()
                mouth.shut_up()
            if speak_task:
                try:
                    await speak_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass    # an Exception here would kill this poller
                speak_task = None
            try:
                await brain.reset_turn()
            except Exception as e:
                log(f"[voice] theme: reset failed ({str(e)[:60]})")
            p = _apply_theme_voice(t)
            if not p:
                log(f"[voice] theme '{t}' has no voice pairing — "
                    f"voice unchanged")
                continue
            log(f"[voice] theme -> {t}: fx={p['voice_fx']} "
                f"voice={p['voice']} speed={p['speed']}")
            # No thinking-state loader here: the face stays calm while
            # Kokoro reloads, and the confirm line's own speaking state
            # is the announcement.
            try:
                await loop.run_in_executor(None, warm_voice)
            except Exception as e:
                log(f"[voice] reload failed ({str(e)[:60]}) — "
                    f"the next line will retry")
            mouth.say(p["confirm"])
            _PERSONA["note"] = p["persona"]
    asyncio.ensure_future(_face_theme_voices())
    # THE BRAIN CONNECT, guarded. This is the one startup step that
    # needs a signed-in Claude Code, internet, and available usage.
    # When it fails or hangs, the mouth still works, so SAY SO instead
    # of dying silently with the face stuck on idle (a real field
    # case: the greeting played, then nothing, and on Windows the
    # window closed before anyone could read the error).
    log("[backtalk] connecting the brain...")
    try:
        await asyncio.wait_for(brain.start(), 120)

        async def _warmup():
            # The ping doubles as the persona hand-off: the brain
            # learns who is on duty before the first real turn.
            who = theme_preset["persona"] if theme_preset else NAME
            async for _ in brain.ask_stream(
                    f"Warmup ping - active persona: {who} (see the "
                    f"Personas section of CLAUDE.md). Reply with the "
                    f"single word: ready"):
                pass
        await asyncio.wait_for(_warmup(), 180)
    except (Exception, asyncio.TimeoutError) as e:
        kind = ("timed out" if isinstance(e, asyncio.TimeoutError)
                else f"failed: {e!r}"[:220])
        log(f"[backtalk] BRAIN CONNECT {kind}")
        mouth.say("Bad news. The voice and the face are fine, but I "
                  "couldn't reach my brain, the Claude Code session. "
                  "Check this window for the error. The usual causes: "
                  "Claude Code isn't signed in, the internet is down, "
                  "or the plan is out of usage.")
        mouth.wait_done(timeout=30)
        signals.clear_mic()   # dying before the main try: no stale badge
        raise SystemExit(1)
    log("[backtalk] brain warm")
    # the hidden warmup ping is plumbing, not conversation
    brain.session.update(turns=0, out_tokens=0, in_tokens=0, cost=0.0)
    # a configured effort level applies at launch (saved by the spoken
    # "set effort to X", or written by the person's agent on request)
    boot_effort = str(CFG.get("effort") or "").strip().lower()
    if boot_effort in _EFFORTS:
        await brain.command(f"/effort {boot_effort}")
        log(f"[backtalk] effort set to {boot_effort} (from config)")
    elif boot_effort:
        log(f"[backtalk] ignoring unknown effort {boot_effort!r} in config")

    speak_task: asyncio.Task | None = None
    typed_q: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=_typed_reader, args=(typed_q,), daemon=True).start()
    # Memory-pressure evictor: idle means no turn is live, so a model may be
    # dropped and re-warmed without anyone waiting on it mid-sentence.
    from backtalk import pressure
    pressure.start(lambda: (signals.state() == "idle"
                            and not mouth.turn_live and not mouth.speaking))
    threading.Thread(target=_face_typed_reader, args=(typed_q,),
                     daemon=True).start()
    typed_fut: asyncio.Future | None = None

    async def _face_brain_picks():
        """Provider picks from the face's BRAIN tab — the same shape as
        _face_mode_picks above: browser -> visualizer server ->
        .voice_brain_pick, consumed here. It starts LATER than the other
        two face pollers on purpose: a pick that landed before the brain
        connected would stop() and start() a client that never existed.

        The switch is live (the `claude` CLI inherits os.environ, so new
        env plus a rebuild is the whole trick) but the rebuild is a new
        CLI process, which is a new conversation. So that is said out
        loud first — in the console's own plain register, the same one
        every other spoken setting change uses, in whichever persona's
        voice is on."""
        nonlocal speak_task
        pick = os.path.join(signals._DIR, ".voice_brain_pick")
        while True:
            await asyncio.sleep(0.7)
            try:
                with open(pick) as f:
                    want, _, wmodel = f.read().strip().lower().partition(" ")
                os.remove(pick)
            except OSError:
                continue
            if want not in _BRAIN_LABEL or (
                    want == _BRAIN["provider"]
                    and wmodel == _BRAIN["model"]):
                continue                  # unknown value, or no change
            prev, prev_model = _BRAIN["provider"], _BRAIN["model"]
            if provider is None:
                mouth.say("I can't switch brains in this build.")
                _publish_brain(prev, prev_model)
                log("[brain] pick ignored: no provider profiles installed")
                continue
            # REFUSE BEFORE TOUCHING ANYTHING. A switch to a back end
            # with no credential ends in a dead brain, and a dead brain
            # is the one unacceptable outcome.
            try:
                status = provider.key_status(want)
            except Exception as e:
                status = f"error: {str(e)[:60]}"
            if status != "ready":
                why = ("The Z.AI key hasn't been added yet."
                       if want == "zai"
                       else "That Claude session is signed out.")
                mouth.say(f"Can't switch. {why}")
                _publish_brain(prev, prev_model)   # snap the picker back
                log(f"[brain] refused {want}: key_status={status}")
                continue
            # Same interrupt dance handle() does before it touches the
            # brain: kill the in-flight reply and let the cancellation
            # FULLY land, or the dead turn's stop signal races the
            # rebuild (see brain.reset_turn for the rest of that bug).
            _deny_pending()               # an ask never outlives its turn
            if speak_task and not speak_task.done():
                log("[brain] provider pick interrupts the reply in flight")
                speak_task.cancel()
                mouth.shut_up()
            if speak_task:
                try:
                    await speak_task
                except asyncio.CancelledError:
                    pass    # NOT an Exception since 3.8 — catching only
                except Exception:
                    pass    # Exception here would kill this poller
                speak_task = None
            mouth.say(f"Switching to {_brain_label(want, wmodel)}. "
                      f"This starts a new conversation.")
            signals.set_stage("switching brains")
            try:
                await brain.reset_turn()
                provider.apply(want, wmodel or None)
                # the profile rewrote CFG's model ids; the warm brain
                # picked its own up at construction, so hand it the new
                # one before it reconnects
                brain.model = CFG["model"]
                await brain.stop()
                await brain.start()
            except Exception as e:
                log(f"[brain] switch to {want} failed: {e!r}"[:220])
                back = False
                try:
                    provider.apply(prev, prev_model or None)
                    brain.model = CFG["model"]
                    try:
                        await brain.stop()
                    except Exception:
                        pass              # only start() decides this
                    await brain.start()
                    back = True
                except Exception as e2:
                    log(f"[brain] fallback to {prev} ALSO failed: "
                        f"{e2!r}"[:220])
                signals.set_stage("")
                _publish_brain(prev, prev_model)
                mouth.say(
                    f"That switch failed. I'm back on "
                    f"{_brain_label(prev, prev_model)}, on a new "
                    f"conversation."
                    if back else
                    "That switch failed, and so did getting back. "
                    "Check this window — my brain is down.")
                continue
            signals.set_stage("")
            _BRAIN["provider"] = want
            _BRAIN["model"] = CFG.get("brain_model", "")
            if not (_write_config_key("brain_provider", want)
                    and _write_config_key("brain_model", _BRAIN["model"])):
                log("[brain] provider is session-only: config not written")
            _publish_brain(want, _BRAIN["model"])
            # A fresh CLI process knows nothing about who is on duty, so
            # re-arm the persona hand-off that rides the next turn.
            _PERSONA["note"] = _WAKE["name"] or NAME
            # and the usage meter is a different meter now
            brain.session.update(turns=0, out_tokens=0, in_tokens=0,
                                 cost=0.0)
            log(f"[brain] provider -> {want} (picked on the face) "
                f"model={brain.model} — new conversation")
    asyncio.ensure_future(_face_brain_picks())

    async def _face_stops():
        """ESC on a face page = drop whatever is happening, now.

        Stops the thinking and the speaking; does NOT stop listening.
        The mic stays in whatever mode it was in and simply goes back
        to resting, which is the only sane reading of a panic key on a
        device you talk to. Polled fast (a stop that lands a second
        late is a stop nobody trusts) and silent — the face is where
        the key was pressed, so the state snapping back IS the receipt.
        """
        nonlocal speak_task
        stop = os.path.join(signals._DIR, ".voice_stop")
        while True:
            await asyncio.sleep(0.2)
            try:
                os.remove(stop)
            except OSError:
                continue
            log("[turn] stopped — escape on the face")
            # Same interrupt dance handle() does, and for the same
            # reason: the cancellation must FULLY land before anything
            # else touches the brain (see brain.reset_turn).
            _deny_pending()
            if speak_task and not speak_task.done():
                speak_task.cancel()
            mouth.shut_up()
            if speak_task:
                try:
                    await speak_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass    # an Exception here would kill this poller
                speak_task = None
            try:
                await brain.reset_turn()
            except Exception as e:
                log(f"[turn] stop: reset failed ({str(e)[:60]})")
            signals.static_stop()
            signals.set_stage("")
            signals.set_state("idle")
            signals.set_mic(_MIC["mode"])   # resting, still listening
    asyncio.ensure_future(_face_stops())

    async def run_console(verb):
        """One voice-console verb. The current reply was already
        cancelled and awaited by handle(); the pipe gets drained here
        before the command goes out. A verb that blows up must never
        take the whole voice session down with it."""
        try:
            await _run_console_inner(verb)
        except Exception as e:
            log(f"[console] {verb} failed: {e}")
            mouth.say("That command hit an error. Check the log.")
            signals.set_state("idle")

    async def _run_console_inner(verb):
        _deny_pending()
        await brain.reset_turn()
        say_after = None
        if verb == "clear":
            resp = await brain.command("/clear")
            say_after = "Cleared. Fresh slate."
        elif verb == "compact":
            mouth.say("Compacting. One moment.")
            resp = await brain.command("/compact")
            say_after = "Compacted. Same conversation, smaller footprint."
        elif verb == "deep":
            mouth.say("Switching to the deep model. Heads up, replies "
                      "get slower. Say back to the fast model when "
                      "you're done.")
            resp = await brain.command(f"/model {CFG['deep_model']}")
            say_after = "Deep model online, for this session only."
        elif verb == "fast":
            resp = await brain.command(f"/model {CFG['model']}")
            say_after = "Back on the fast model."
        elif verb.startswith("effort:"):
            lvl = verb.split(":", 1)[1]
            resp = await brain.command(f"/effort {lvl}")
            saved = _write_config_key("effort", lvl)
            say_after = (f"Effort set to {lvl}, and saved as your "
                         "default." if saved else
                         f"Effort set to {lvl} for this session. The "
                         "config file couldn't be written, so it won't "
                         "stick past a restart.")
        elif verb == "usage":
            resp = ""
            mouth.say(_spoken_usage(brain.session,
                                    await brain.context_usage()))
        elif verb == "micopen":
            resp = ""
            if _MIC["mode"] == "open":
                mouth.say("Already in hands-free listening.")
            else:
                _MIC["mode"] = "open"
                _MIC["gen"] += 1
                _write_config_key("mic_mode", "open")
                log("[console] mic_mode -> open (hands-free listening)")
                signals.set_mic("open")
                mouth.say("Hands-free listening on. I'm always "
                          "listening now, so anything said in the room "
                          "can reach me. The talk key still works, and "
                          "holding it always gets you heard. Say push "
                          "to talk mode to bring the button back.")
        elif verb == "micptt":
            resp = ""
            if _MIC["mode"] == "ptt":
                mouth.say("Already on push to talk.")
            else:
                _MIC["mode"] = "ptt"
                _MIC["gen"] += 1
                _write_config_key("mic_mode", "ptt")
                log("[console] mic_mode -> ptt")
                signals.set_mic("ptt")
                key = str(CFG.get("ptt_key", "home")).replace("_", " ")
                mouth.say(f"Push to talk. Hold the {key} key and "
                          "talk; the mic stays closed otherwise.")
        elif verb == "enroll":
            resp = ""
            if not CFG.get("voice_id"):
                mouth.say("Voice identification is switched off in my "
                          "config. Ask me to turn voice_id on and "
                          "relaunch, then say learn my voice again.")
            else:
                # Re-arm the mic so captures carry raw audio for the
                # embed, and pre-load the voiceprint model while the
                # person answers.
                _MIC["gen"] += 1
                loop.run_in_executor(None, voiceid._classifier)
                nm = _ENROLL.get("prefill")
                if nm:
                    # The name rode in on the request ("enroll Sam") —
                    # still confirmed out loud before anything records.
                    _ENROLL.update(on=True, vecs=[], prefill=None)
                    _ask_confirm(nm)
                else:
                    _ENROLL.update(on=True, stage="name", name=None,
                                   vecs=[], last=time.monotonic(),
                                   prefill=None)
                    _enroll_card("VOICE ENROLLMENT",
                                 "**Whose voice is this?**\n\n"
                                 "Say just the first name.\n\n"
                                 "say *cancel* to stop")
                    mouth.say("Happy to. Who am I learning? Say just "
                              "the first name. You can say cancel at "
                              "any point.")
        elif verb == "micwake":
            resp = ""
            if _MIC["mode"] == "wake":
                mouth.say("Already in wake word mode.")
            else:
                _MIC["mode"] = "wake"
                _MIC["gen"] += 1
                _write_config_key("mic_mode", "wake")
                log("[console] mic_mode -> wake")
                signals.set_mic("wake")
                first = CFG["wake_phrases"][0]
                mouth.say(f"Wake word mode. Say {first} to reach me — "
                          "alone for a listening chime, or straight "
                          "into your request. Anything else said in "
                          "the room stays ignored. The talk key still "
                          "works too.")
        elif verb == "stoplisten":
            # "Stop listening" means stop NOW: gen++ aborts the
            # in-flight capture and closes any wake window, hush kills
            # the follow-up window that the reply would otherwise open.
            resp = ""
            _MIC["hush"] = True
            _MIC["gen"] += 1
            # A follow-up window ALREADY open must not outlive the order.
            # Field-caught: _conv_watch was mid-sleep when "stop
            # listening" arrived, woke after "Stopped." and re-lit
            # LISTENING for ten more seconds -- hush only stops NEW
            # windows. Cancelled before the reply is even queued.
            if conv_task and not conv_task.done():
                conv_task.cancel()
            # hush rides the mic line too: the overlay window fades on it.
            if _MIC["mode"] == "open":
                # An open mic can't be hushed, only narrowed: drop to
                # wake word so nothing is heard until it's summoned.
                _MIC["mode"] = "wake"
                _write_config_key("mic_mode", "wake")
                log("[console] mic_mode -> wake (stop listening)")
                signals.set_mic("wake", hush=True)
                first = CFG["wake_phrases"][0]
                mouth.say(f"Switching to wake word mode. Say {first} "
                          "when you need me.")
            else:
                signals.set_mic(_MIC["mode"], hush=True)
                signals.set_state("idle")
                mouth.say("Stopped.")
        elif verb == "rename":
            resp = ""
            frm = _RENAME.get("frm")
            if not CFG.get("voice_id"):
                mouth.say("Voice identification is off in my config, "
                          "so there are no voice names to change.")
            elif not voiceid._load_profiles():
                mouth.say("Nobody is enrolled yet — say enroll me and "
                          "I'll learn your voice under the right "
                          "name.")
            elif frm is None:
                mouth.say("I can only rename a voice I can hear and "
                          "recognize, and I don't recognize this one. "
                          "If you're typing, ask me out loud instead; "
                          "if I've never learned your voice, say "
                          "enroll me.")
            else:
                # Vigilance line: renaming a profile that is NOT the
                # asker's own voice is allowed — but announced.
                asker = _RENAME.get("asker")
                heads_up = ""
                if asker and asker != frm:
                    heads_up = (f"Heads up — I hear {asker} speaking, "
                                f"and this changes {frm}'s voice. ")
                to = _RENAME.get("to")
                if to and to.lower() != frm.lower():
                    _RENAME.update(on=True, stage="confirm", to=to,
                                   last=time.monotonic())
                    mouth.say(heads_up + f"This voice is saved as "
                              f"{frm}. I'll rename it to {to} — say "
                              "yes to confirm, or tell me another "
                              "name.")
                else:
                    _RENAME.update(on=True, stage="name", to=None,
                                   last=time.monotonic())
                    mouth.say(heads_up + f"This voice is saved as "
                              f"{frm}. What name should it carry?")
        elif verb == "forgetvoices":
            resp = ""
            if not voiceid.enabled() and not voiceid._load_profiles():
                mouth.say("Nobody is enrolled — nothing to forget.")
            else:
                _CONFIRM["verb"] = "forgetvoices"
                _CONFIRM["at"] = time.monotonic()
                mouth.say("This deletes every enrolled voice profile, "
                          "permanently. Say confirm to do it.")
        elif verb == "forgetvoices:confirmed":
            resp = ""
            voiceid.forget_all()
            log("[console] all voice profiles deleted")
            mouth.say("Done. Every voice profile is gone — say enroll "
                      "me whenever you want me to learn one again.")
        elif verb == "noask":
            resp = ""
            _CONFIRM["verb"] = "noask"
            _CONFIRM["at"] = time.monotonic()
            mouth.say("Auto-approve means I act without asking "
                      "permission, and it becomes your saved default. "
                      "Say confirm to switch.")
        elif verb == "noask:confirmed":
            resp = ""
            saved = _write_config_key("permission_mode",
                                      "bypassPermissions")
            _AUTOAPPROVE["on"] = True
            log("[console] permission_mode -> bypassPermissions"
                + (" (saved)" if saved else " (session only)"))
            mouth.say(("Auto-approve on, and saved as your default. "
                       if saved else
                       "Auto-approve on for this session. The config "
                       "file couldn't be written, so it won't stick "
                       "past a restart. ")
                      + "Say start asking again any time to flip it "
                        "back.")
        elif verb == "ask":
            resp = ""
            saved = _write_config_key("permission_mode", "ask")
            _AUTOAPPROVE["on"] = False
            flipped = True
            if CFG_BOOT_MODE == "bypassPermissions":
                # a bypass-booted session never consults the gate, so
                # the SDK itself must flip (the safe direction is
                # allowed live). If that fails, saying "done" would be
                # a lie: the agent would keep acting silently.
                try:
                    await brain.set_permission_mode("ask")
                except Exception as e:
                    flipped = False
                    log(f"[console] live flip to ask FAILED: {e}")
            log("[console] permission_mode -> ask"
                + (" (saved)" if saved else " (session only)"))
            if flipped:
                mouth.say("Done. I'll ask out loud before real "
                          "actions"
                          + (", and that's saved as your default."
                             if saved else
                             ". The config file couldn't be written, "
                             "so tell me again after a restart."))
            else:
                mouth.say("I saved asking as your default, but this "
                          "session couldn't switch over. Restart the "
                          "voice line to get asking back.")
        else:
            resp = ""
        if say_after:
            # the CLI answers slash commands with its own text
            # (confirmations, API errors); an error outranks our line
            low = (resp or "").lower()
            if resp and ("error" in low or "invalid" in low):
                mouth.say(resp[:160])
                log(f"[console] {verb} answered: {resp[:120]}")
            else:
                mouth.say(say_after)
        signals.set_state("idle")

    async def handle(text: str, spoke_from: float | None = None,
                     speaker: str | None = None,
                     from_mic: bool = False) -> bool:
        """Process one utterance; returns False on quit. spoke_from is
        when the utterance STARTED (the PTT press), so an answer can be
        told apart from speech that began before the ask even existed.
        speaker is the identified voice (mic turns with voice_id on);
        the tag reaches only the BRAIN — console phrases, quit, and
        permission answers all match on the raw words."""
        nonlocal speak_task
        _unhush()              # being addressed at all ends the hush
        log(f"[you{'/' + speaker if speaker else ''}]    {text}")
        signals.chat_add("you", speaker, text)
        # A pending spoken permission ask owns the next utterance IF
        # that utterance started after the ask was posed. Speech that
        # began earlier is the user interrupting the turn, not
        # answering a question they never heard: the ask resolves as a
        # silent deny and the utterance falls through as a normal
        # interrupt. Quit wins either way, but only as an EXACT phrase
        # here ("No! Don't hang up, skip it" must stay a deny reason,
        # not kill the session).
        if _PERM["fut"] is not None and not _PERM["fut"].done():
            started_after = (spoke_from is None
                             or spoke_from >= _PERM["asked_at"])
            if _norm_speech(text) in _QUIT_NORMS:
                _PERM["fut"].set_result("no")
                # falls through to the quit body below
            elif started_after and console_match(text):
                # "Stop asking for permission" said INTO a pending ask
                # is a command, not an answer — it was being swallowed
                # as a denial, trapping people in an ask loop. Deny
                # the ask softly and let the verb run below.
                _deny_pending(_SETTINGS_FIRST)
            elif started_after and _CONFIRM["verb"] \
                    and _norm_speech(text) in ("confirm", "confirmed",
                                               "yes confirm",
                                               "yes confirmed"):
                # A pending settings confirm outranks a chained ask —
                # otherwise every "confirm" is eaten as a permission
                # answer and the noask wish starves forever.
                _deny_pending(_CONFIRMING_FIRST)
            elif started_after:
                _PERM["fut"].set_result(text)
                return True
            else:
                _deny_pending()
        # A pending auto-approve confirm owns it too, for two minutes;
        # after that it expires and speech flows normally again.
        verb = None
        if _CONFIRM["verb"]:
            pend, _CONFIRM["verb"] = _CONFIRM["verb"], None
            expired = time.monotonic() - _CONFIRM["at"] > 120
            if not expired and _norm_speech(text) in (
                    "confirm", "confirmed", "yes confirm",
                    "yes confirmed"):
                verb = pend + ":confirmed"
            elif not expired and not _is_quit(text):
                mouth.say("Staying as we are.")
                return True
        if _is_quit(text):
            if speak_task and not speak_task.done():
                speak_task.cancel()
            mouth.shut_up()
            mouth.say(CFG["signoff"])
            mouth.wait_done(timeout=15)
            return False
        if speak_task and not speak_task.done():
            log("[turn] interrupted mid-reply by new input")
            _deny_pending()          # an ask never outlives its turn
            speak_task.cancel()
            mouth.shut_up()
        if speak_task:
            # Let the cancellation fully land (its brain.interrupt()
            # included) BEFORE anything else touches the brain —
            # otherwise the dead turn's stop signal can race in after
            # the new query and kill the new answer (half of the
            # off-by-one bug; see brain.reset_turn for the other half).
            try:
                await speak_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            speak_task = None
        verb = verb or console_match(text)
        if verb is None and _rename_intent(text):
            verb = "rename"
        if verb is None and _enroll_intent(text):
            verb = "enroll"
        if verb == "enroll":
            _ENROLL["prefill"] = _enroll_name_hint(text)
        elif verb == "rename":
            # Vigilance, not gatekeeping: the flow defaults to the
            # ASKER's own identified voice, but a request naming an
            # enrolled profile ("rename Sam's voice") targets that
            # profile — with a spoken heads-up when the asker's voice
            # is somebody else's.
            _RENAME.update(asker=speaker, warned=False, force=False)
            target = None
            words = set(_norm_speech(text).split())
            try:
                for prof in voiceid._load_profiles():
                    if prof.lower() in words:
                        target = prof
                        break
            except Exception:
                pass
            _RENAME["frm"] = target or speaker
            _RENAME["to"] = _rename_to_hint(text)
        if verb:
            await run_console(verb)
            _conv_visual()   # a spoken verb reply opens a window too
            return True
        signals.set_state("thinking")
        signals.static_start()
        # Clean the pipe: drain the interrupted turn's leftovers so the
        # new question can't pair with a stale ResultMessage. A gate
        # that fired in the meantime resolves first, or the drain would
        # wait on a ResultMessage the CLI is withholding for an answer.
        _deny_pending()
        await brain.reset_turn()
        brain_text = text
        if from_mic and voiceid.enabled():
            # Who's asking, for personalization only. Unrecognized is
            # an honest answer (guest, TV, a too-short clip).
            brain_text = (f"[voice: {speaker}] {text}" if speaker
                          else f"[voice: unrecognized] {text}")
        if _PERSONA["note"]:
            # One-time hand-off after a theme pick: the brain switches
            # character (CLAUDE.md Personas) starting with this turn.
            brain_text = (f"[persona switch: the face theme changed — "
                          f"you are now {_PERSONA['note']}. Stay in "
                          f"this persona per the Personas section of "
                          f"CLAUDE.md.] {brain_text}")
            _PERSONA["note"] = None
        speak_task = asyncio.create_task(
            speak_reply(brain, mouth, brain_text))
        _conv_visual()
        return True

    def _glass(obj):
        """Fire-and-forget card on the glass (enrollment's read-aloud
        prompt). Best-effort by design: no server, no glass, no
        problem — the spoken prompts carry the flow alone."""
        def post():
            try:
                import httpx
                httpx.post(CFG["glass_url"].rstrip("/") + "/cmd",
                           json=obj, timeout=2.0)
            except Exception:
                pass
        loop.run_in_executor(None, post)

    def _enroll_card(title, body):
        _glass({"a": "show", "type": "note", "id": "enroll-note",
                "title": title, "body": body, "ttl": 240})

    def _enroll_card_gone():
        _glass({"a": "dismiss", "id": "enroll-note"})

    # The follow-up window's VISUALS: the gate above is the truth (it
    # reads mouth.last_done directly); this task only keeps the face
    # badge and state honest — WAKE · LIVE + "listening" while the
    # window is open, cold + idle when it closes.
    conv_task: asyncio.Task | None = None

    def _conv_visual():
        nonlocal conv_task
        if (_MIC["mode"] != "wake" or CFG["wake_followup_s"] <= 0
                or _MIC["hush"]):
            return
        if conv_task and not conv_task.done():
            conv_task.cancel()
        conv_task = asyncio.create_task(_conv_watch())

    async def _conv_watch():
        try:
            # LOOP until the window genuinely goes cold: last_done
            # moving during the sleep means more speech happened (a
            # verb reply that hadn't started when we first looked, a
            # follow-up's answer) — re-base and wait again, never exit
            # leaving the badge stuck on LIVE.
            while True:
                if speak_task and not speak_task.done():
                    await asyncio.wait({speak_task})
                while mouth.speaking:
                    await asyncio.sleep(0.2)
                if _MIC["mode"] != "wake":
                    return
                signals.set_mic("wake", hot=True)
                signals.set_state("listening")
                base = mouth.last_done
                await asyncio.sleep(CFG["wake_followup_s"] + 2.5)
                if (mouth.last_done == base and not mouth.speaking
                        and not (speak_task
                                 and not speak_task.done())):
                    if _MIC["mode"] == "wake":
                        signals.set_mic("wake")
                        signals.set_state("idle")
                    return
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log(f"[conv] watcher failed: {e!r}")

    def _parse_name(text):
        """First plausible name token, with whisper decoration, lead-ins
        and a stray wake prefix stripped."""
        # THREE, not two. build_matcher's closure returns (woke, rest,
        # maybe) on every path, so this raised
        # "ValueError: too many values to unpack" on EVERY call -- and
        # _parse_name is the name extractor for both spoken flows
        # (_rename_step and _enroll_step both call it twice). Both sit
        # inside broad excepts, so the flows did not crash: they just
        # never got a name, and enrolling or renaming by voice died at
        # the name step every single time. That is why voice_id has
        # never had anybody enrolled.
        woke, rest, _maybe = wake_match(text)
        cand = rest if woke and rest else text
        # Repeating and punctuation-tolerant: "no, it's Sam" must
        # shed BOTH lead-ins (a single-pass strip minted a profile
        # called Its).
        cand = re.sub(r"^(?:\s*(?:it'?s|i'?m|i am|my name is|this is"
                      r"|call me|yes|yeah|yep|no|nope|not|actually"
                      r"|enroll)[\s,.!]+)+", "", cand,
                      flags=re.IGNORECASE)
        for tok in cand.split():
            letters = re.sub(r"[^A-Za-z]", "", tok)
            if len(letters) >= 2 and letters.lower() not in (
                    "yes", "yeah", "yep", "no", "nope", "cancel",
                    "its", "it", "im", "actually", "go", "ahead",
                    "okay", "ok", "sure", "please"):
                return letters.capitalize()
        return ""

    def _ask_confirm(nm):
        """Names are the one thing worth double-checking out loud —
        a misheard name mints a profile nobody meant to create."""
        _ENROLL.update(stage="confirm", name=nm, last=time.monotonic())
        _enroll_card("VOICE ENROLLMENT — CONFIRM",
                     f"**Enroll {nm}'s voice?**\n\n"
                     "say *yes* — or tell me the right name\n\n"
                     "say *cancel* to stop")
        mouth.say(f"I'll learn {nm}'s voice. Say yes to continue, or "
                  "tell me the right name.")

    def _start_collect():
        key_hint = (" Hold the talk key for each one."
                    if _MIC["mode"] == "ptt" else "")
        _ENROLL.update(stage="collect", vecs=[], last=time.monotonic())
        _enroll_card(
            f"ENROLLING {_ENROLL['name'].upper()} — READ ALOUD, 1 OF 4",
            f"**{_ENROLL_SENTENCES[0]}**\n\nsay *cancel* to stop")
        mouth.say("Read the sentence on the screen, four in all."
                  f"{key_hint} It says: " + _ENROLL_SENTENCES[0])

    async def _rename_step(text: str, speaker) -> bool:
        """One utterance of the rename flow. Same contract as
        _enroll_step: True = consumed, False = fall through (quit)."""
        try:
            signals.chat_add("you", speaker, text)
            if _is_quit(text):
                _RENAME["on"] = False
                return False
            if time.monotonic() - _RENAME["last"] > 120:
                _RENAME["on"] = False
                mouth.say("The rename timed out — nothing changed.")
                return True
            _RENAME["last"] = time.monotonic()
            if _norm_speech(text) in ("cancel", "stop", "never mind",
                                      "nevermind", "forget it"):
                _RENAME["on"] = False
                mouth.say("Cancelled — the name stays as it was.")
                return True
            # Vigilance, not gatekeeping: a DIFFERENT recognized voice
            # answering mid-flow gets one spoken heads-up, then may
            # carry on — the goal is catching mix-ups, not blocking
            # the household. (Short clips often come back
            # unrecognized — those pass; they're usually the asker.)
            if speaker and speaker != _RENAME["frm"] \
                    and speaker != _RENAME.get("asker") \
                    and not _RENAME["warned"]:
                _RENAME["warned"] = True
                mouth.say(f"Hold on — that came from {speaker}'s "
                          f"voice, and we're renaming "
                          f"{_RENAME['frm']}'s voice. Carry on if "
                          "that's intended, or say cancel.")
                return True
            if _seems_question(text):
                mouth.say(f"This voice is saved as {_RENAME['frm']}; "
                          "I'm only re-labeling it — the voiceprint "
                          "stays. Tell me the right name, or say "
                          "cancel.")
                return True
            # The trigger phrase must never become the answer: a
            # repeated "rename my voice" mid-flow was parsed as the
            # name "Rename" (three repeats applied it silently).
            if console_match(text) or _rename_intent(text) \
                    or _enroll_intent(text):
                mouth.say("We're already renaming this voice — just "
                          "tell me the name, say yes, or say cancel.")
                return True
            if _RENAME["stage"] == "name":
                nm = _parse_name(text)
                if not nm:
                    g = await intent_ai.classify(
                        "The assistant asked what name this voice "
                        "profile should carry; the person may also "
                        "cancel.", text)
                    if g["intent"] == "name" and g["name"]:
                        nm = g["name"]
                    elif g["intent"] in ("no", "cancel"):
                        _RENAME["on"] = False
                        mouth.say("Cancelled — the name stays as it "
                                  "was.")
                        return True
                    else:
                        mouth.say("I didn't catch a name. Just the "
                                  "first name, on its own.")
                        return True
                _RENAME.update(stage="confirm", to=nm, force=False,
                               last=time.monotonic())
                mouth.say(f"Renaming {_RENAME['frm']} to {nm} — say "
                          "yes to confirm.")
                return True
            # confirm stage — only a clear yes ever applies.
            def _rename_yes():
                frm, new = _RENAME["frm"], _RENAME["to"]
                clash = new in voiceid._load_profiles() and new != frm
                if clash and not _RENAME["force"]:
                    _RENAME["force"] = True
                    mouth.say(f"Careful — I already know a different "
                              f"voice as {new}, and a yes now "
                              "REPLACES that voice with this one. "
                              "Say yes again if that's really what "
                              "you want, or tell me another name.")
                    return
                done = voiceid.rename_profile(
                    frm, new, allow_overwrite=_RENAME["force"])
                _RENAME["on"] = False
                mouth.say(f"Done — this voice is {new} now, "
                          "effective immediately." if done else
                          "That rename didn't take — check my window.")

            if _norm_speech(text) in _YES:
                _rename_yes()
                return True
            nm = _parse_name(text)
            if nm:
                if nm != _RENAME["to"]:
                    _RENAME.update(stage="confirm", to=nm, force=False,
                                   last=time.monotonic())
                    mouth.say(f"Alright — {nm}. Say yes to confirm.")
                else:
                    mouth.say(f"Say yes to make it {nm}, or say "
                              "cancel.")
                return True
            g = await intent_ai.classify(
                "The assistant asked whether to rename the voice "
                f"profile {_RENAME['frm']} to {_RENAME['to']}; valid "
                "outcomes: agree, supply a different name, or cancel.",
                text)
            if g["intent"] == "yes":
                _rename_yes()
            elif g["intent"] in ("no", "cancel"):
                _RENAME["on"] = False
                mouth.say("Cancelled — the name stays as it was.")
            elif g["intent"] == "name" and g["name"]:
                _RENAME.update(stage="confirm", to=g["name"],
                               force=False, last=time.monotonic())
                mouth.say(f"Alright — {g['name']}. Say yes to "
                          "confirm.")
            else:
                mouth.say("Say yes to rename this voice to "
                          f"{_RENAME['to']}, tell me another name, or "
                          "say cancel.")
            return True
        except Exception as e:
            log(f"[voiceid] rename step failed: {e!r}")
            _RENAME["on"] = False
            mouth.say("The rename hit an error — nothing changed.")
            return True

    async def _enroll_step(text: str, pcm) -> bool:
        """One utterance of the spoken enrollment flow. Returns True
        when the utterance was consumed by the flow, False when it
        should fall through to normal handling (expired flow, or a
        quit phrase — hanging up always wins)."""
        try:
            # The crawl shows both sides of enrollment too — the whole
            # point of the pane is confirming what was heard.
            # NO NAME ON THE LABEL. This ran before every filter below,
            # so the face captioned as "SAM" the very utterances the next
            # few lines decide are NOT Sam reading -- a question, the
            # wrong sentence, room speech the flow rejects. And during
            # "collect" the profile provably does not exist yet, so no
            # voiceprint match is even possible: it was stating an
            # identity as fact on the strength of a name typed one
            # sentence earlier. Every other chat_add call site takes its
            # speaker from voiceid; this one now declines to guess.
            signals.chat_add("you", None, text)
            if _is_quit(text):
                _ENROLL["on"] = False
                _enroll_card_gone()
                return False        # the quit body hangs up as usual
            if time.monotonic() - _ENROLL["last"] > 120:
                # Say so and CONSUME: forwarding this utterance would
                # hand the brain an ungated turn (the wake-gate bypass
                # already fired on _ENROLL being on).
                _ENROLL["on"] = False
                _enroll_card_gone()
                mouth.say("Enrollment timed out — nothing was saved. "
                          "Say learn my voice to start over.")
                return True
            _ENROLL["last"] = time.monotonic()
            if _norm_speech(text) in ("cancel", "stop", "never mind",
                                      "nevermind", "forget it"):
                _ENROLL["on"] = False
                _enroll_card_gone()
                mouth.say("Cancelled. Nothing was saved.")
                return True
            if _seems_question(text) and not (
                    _ENROLL["stage"] == "collect"
                    and _matches_sentence(
                        text, _ENROLL_SENTENCES[len(_ENROLL["vecs"])])):
                # A confused question gets an answer, never gets
                # embedded as somebody's voiceprint — but a genuine
                # read of the expected sentence always outranks the
                # question heuristic (whisper adds stray "?"s).
                if _ENROLL["stage"] == "name":
                    mouth.say("I'm adding a voice profile so I can "
                              "tell who's speaking later. I just need "
                              "a first name — whose voice is this? Or "
                              "say cancel.")
                elif _ENROLL["stage"] == "confirm":
                    mouth.say(f"I'm about to learn "
                              f"{_ENROLL['name']}'s voice. Say yes to "
                              "continue, tell me another name, or say "
                              "cancel.")
                else:
                    mouth.say("Just read the sentence on the screen, "
                              "word for word — I'm learning the sound "
                              "of the voice. It says: "
                              + _ENROLL_SENTENCES[len(_ENROLL["vecs"])])
                return True
            if _ENROLL["stage"] in ("name", "confirm") and (
                    console_match(text) or _rename_intent(text)
                    or _enroll_intent(text)):
                # Same trap as rename: the trigger phrase repeated
                # mid-flow must never be parsed as a name.
                mouth.say("One thing at a time — just tell me the "
                          "first name, or say cancel.")
                return True
            if _ENROLL["stage"] == "name":
                name = _parse_name(text)
                if not name:
                    g = await intent_ai.classify(
                        "The assistant asked whose voice it is about "
                        "to learn and needs a first name; the person "
                        "may also cancel.", text)
                    if g["intent"] == "name" and g["name"]:
                        _ask_confirm(g["name"])
                        return True
                    if g["intent"] in ("no", "cancel"):
                        _ENROLL["on"] = False
                        _enroll_card_gone()
                        mouth.say("Alright, cancelled. Nothing was "
                                  "saved.")
                        return True
                    mouth.say("I didn't catch a name. Just the first "
                              "name, on its own.")
                    return True
                _ask_confirm(name)
                return True
            if _ENROLL["stage"] == "confirm":
                if _norm_speech(text) in _YES:
                    _start_collect()
                    return True
                # Anything else is treated as the corrected name.
                name = _parse_name(text)
                if name and name != _ENROLL["name"]:
                    _ask_confirm(name)
                elif name:
                    _start_collect()   # they repeated the same name
                elif _norm_speech(text) in ("no", "nope", "no thanks",
                                            "no thank you"):
                    mouth.say("Should I stop, or did I get the name "
                              "wrong? Say cancel to stop, or just "
                              "tell me the right name.")
                else:
                    g = await intent_ai.classify(
                        "The assistant asked whether to enroll a "
                        f"voice profile named {_ENROLL['name']}; "
                        "valid outcomes: agree, supply a different "
                        "name, or cancel.", text)
                    if g["intent"] == "yes":
                        _start_collect()
                    elif g["intent"] in ("no", "cancel"):
                        _ENROLL["on"] = False
                        _enroll_card_gone()
                        mouth.say("Cancelled. Nothing was saved.")
                    elif g["intent"] == "name" and g["name"]:
                        _ask_confirm(g["name"])
                    else:
                        mouth.say("Say yes to enroll "
                                  f"{_ENROLL['name']}, tell me "
                                  "another name, or say cancel.")
                return True
            # collect stage: this utterance should BE the sentence
            n = len(_ENROLL["vecs"])
            if pcm is None:
                mouth.say("I need to hear that one out loud — read it "
                          "again for me.")
                return True
            if not _matches_sentence(text, _ENROLL_SENTENCES[n]):
                # Not the sentence: confusion, side-chatter, or a
                # partial read. Never embed it — steer back.
                mouth.say("That wasn't quite the sentence on the "
                          "screen. Read it word for word: "
                          + _ENROLL_SENTENCES[n])
                return True
            if len(pcm) < 2.0 * voiceid.RATE:
                mouth.say("Bit short. Same sentence, once more, at "
                          "your own pace.")
                return True
            vec = await loop.run_in_executor(
                None, lambda: voiceid.embed(pcm))
            # A first-ever embed can hide a model download; that wait
            # is ours, not the person's — don't bill it to the expiry.
            _ENROLL["last"] = time.monotonic()
            _ENROLL["vecs"].append(vec)
            n = len(_ENROLL["vecs"])
            if n < len(_ENROLL_SENTENCES):
                _enroll_card(
                    f"ENROLLING {_ENROLL['name'].upper()} — "
                    f"READ ALOUD, {n + 1} OF 4",
                    f"**{_ENROLL_SENTENCES[n]}**\n\n"
                    "say *cancel* to stop")
                mouth.say(f"Got it. Next one: {_ENROLL_SENTENCES[n]}")
                return True
            _ENROLL["on"] = False
            _enroll_card_gone()
            ok_all, _sims = voiceid.agreement(_ENROLL["vecs"])
            if not ok_all:
                mouth.say("One of those didn't sound like the others — "
                          "background noise, or a second voice. Say "
                          "enroll me and we'll run it again.")
                return True
            voiceid.save_profile(_ENROLL["name"], _ENROLL["vecs"])
            mouth.say(f"Done. I know {_ENROLL['name']}'s voice now — "
                      "effective immediately.")
            return True
        except Exception as e:
            log(f"[voiceid] enrollment step failed: {e!r}")
            _ENROLL["on"] = False
            _enroll_card_gone()
            mouth.say("Enrollment hit an error — check my window. "
                      "Nothing was saved.")
            return True

    try:
        # ONE loop, two mic modes, switchable live (_MIC). The talk key
        # is constructed and honored in BOTH modes: in hands-free
        # listening it is the interrupt and the guaranteed way to be
        # heard over room noise. The open mic joins the wait-set only
        # in "open" mode; a mode switch bumps _MIC["gen"], the abort
        # callable closes the in-flight open mic promptly, and any
        # capture born under an old gen is discarded unprocessed.
        # ptt_scope "face": the key lives on the face page and reaches
        # here over the bus -- no global hook, no press from another
        # window ever counts (see ptt.FacePTT).
        ptt = (FacePTT() if CFG.get("ptt_scope") == "face"
               else PTTListener(CFG["ptt_key"]))
        press_fut: asyncio.Future | None = None
        mic_fut: asyncio.Future | None = None
        mic_win_seq: int | None = None   # set when mic_fut IS the wake window
        mic_gen_seen = _MIC["gen"]
        # Wake-word mode: same open mic, but a transcript-level gate.
        # A bare wake word opens a "window": the NEXT capture is the
        # command, no wake word needed. The window is LOOP STATE, not
        # an inline await — press_fut and typed_fut must stay in the
        # asyncio.wait set at all times or the talk key and typed
        # input die for its duration (and worse: a press would fire
        # stale afterwards and kill the very turn it delivered).
        # The matcher is LAZY about its phrase set: a theme pick swaps
        # the character's name at any moment (_apply_theme_voice bumps
        # _WAKE), and the very next utterance must already match the
        # new name — a rebuild parked at the loop top would miss it.
        _wm = {"ver": None, "fn": None}

        def wake_match(text):
            if _wm["ver"] != _WAKE["ver"]:
                _wm["ver"] = _WAKE["ver"]
                _wm["fn"] = wakeword.build_matcher(
                    CFG["wake_phrases"], CFG["wake_aliases"],
                    _WAKE["name"] or CFG.get("name"),
                    strict=CFG.get("wake_strict") or ())
                log(f"[wake] answering to '{_WAKE['name'] or NAME}'")
            return _wm["fn"](text)
        wake_cue = str(REPO / "assets" / "wake.wav")
        done_cue = str(REPO / "assets" / "done.wav")
        close_cue = str(REPO / "assets" / "close.wav")
        # on: a window is open. seq: invalidates in-flight window
        # captures when bumped. armed_at: the capture ignores audio
        # until then, so the VAD never eats the wake chime itself.
        wake_win = {"on": False, "seq": 0, "armed_at": 0.0, "until": 0.0}

        def _wake_cancel(state="idle"):
            """Close the window (talk key or typed input claimed the
            turn). The in-flight capture aborts within one frame."""
            if wake_win["on"]:
                wake_win["on"] = False
                wake_win["seq"] += 1
                signals.set_state(state)
        # The open mic yields while the BUTTON records (or the double
        # capture would turn one held utterance into two turns), and,
        # without barge-in, while the mouth speaks.
        # 350ms hangover after speech: _speaking clears a beat before
        # the device buffer drains, and without the hangover the VAD
        # can open on the tail of the agent's own sentence (worst in
        # enrollment, where a leaked ".. at any point" became a name).
        # signals.cue_playing() is NOT under `barge_in`: barge-in means
        # talking over the AGENT, and nobody interrupts a 240ms chirp --
        # it is our own sound, and letting it through is how "Beep."
        # arrived as a spoken turn.
        mic_gate = (lambda: _MIC["btn"]
                    or signals.cue_playing()
                    # ...and the thinking sound, which is 36 seconds long
                    # and was outside every gate. See signals.static_playing.
                    or signals.static_playing()
                    or (not barge_in
                        and (mouth.speaking
                             or time.monotonic() - mouth.last_done
                             < 0.35)))
        # The ON-TIME listening indicator. Every other signal in this
        # pipeline waits on whisper, which waits on you to stop talking
        # -- a full second late, and silent entirely when the wake gate
        # then drops the line. This one fires the frame the VAD opens.
        # ponytail: it means "I hear speech", NOT "I heard my name" --
        # the name is only known once there is a transcript. Real
        # on-the-syllable wake indication needs an audio-side detector
        # (openWakeWord/porcupine); add one if the blink isn't enough.
        heard_prev = {"s": None}

        def _heard(on):
            if on:
                # Two conditions where this blink is a LIE, and both were
                # reaching the ring:
                #  - COLD WAKE MODE. The gate is about to drop this: a
                #    fan, a cough, the TV. ears.transcribe() already
                #    suppresses its stage line for exactly this reason
                #    ("looked exactly like a false wake"); the state line
                #    is the louder half of the same tell and never got
                #    the guard, so a silent room pulsed "listening" every
                #    time the VAD twitched — 139 of them in one log, with
                #    no wake word ever spoken.
                #  - MID-TURN. The ring belongs to the answer in flight.
                #    Room noise flipped thinking -> listening -> thinking
                #    and made a long build look like it was oscillating.
                # "wake" means the audio detector heard the NAME: that blink
                # is true, cold mode or not. A plain VAD open in cold mode
                # shows NOTHING: the mic is always open, so "hearing" is not
                # information, and from across the room it reads as
                # listening -- the very lie this guard exists to stop.
                if on != "wake" and (signals.unsummoned() or mouth.turn_live
                                     or mouth.speaking):
                    return
                heard_prev["s"] = signals.state()
                signals.set_state("listening")
            elif heard_prev["s"] is not None:
                prev, heard_prev["s"] = heard_prev["s"], None
                # Undo only OUR OWN blink. If a turn started while the
                # VAD was open, the ring has already moved on to
                # thinking/speaking and restoring the saved value would
                # drag it back to idle for the rest of the turn.
                if signals.state() == "listening":
                    signals.set_state(prev)

        mic_fails = 0
        _conv_visual()   # the greeting opens the first window
        while True:
            if _MIC["gen"] != mic_gen_seen:
                mic_gen_seen = _MIC["gen"]
                # consume futures that completed under the old mode so
                # a stale press or capture can't fire after a switch
                # These are DISCARDS: a parked exception must be
                # swallowed here, not re-raised into the loop top
                # where nothing catches it.
                if press_fut is not None and press_fut.done():
                    try:
                        press_fut.result()
                    except Exception as e:
                        log(f"[ptt] stale press discarded: {e!r}")
                    press_fut = None
                if mic_fut is not None and mic_fut.done():
                    try:
                        mic_fut.result()
                    except Exception as e:
                        log(f"[ears] stale capture discarded: {e!r}")
                    mic_fut = None
                    mic_win_seq = None
                # a live mode switch also closes any open wake window
                wake_win["on"] = False
                wake_win["seq"] += 1
            if typed_fut is None:
                typed_fut = loop.run_in_executor(None, typed_q.get)
            if press_fut is None:
                press_fut = loop.run_in_executor(None, ptt.wait_press)
            waiters = {press_fut, typed_fut}
            if _MIC["mode"] in ("open", "wake"):
                if mic_fut is None:
                    g = _MIC["gen"]
                    if _MIC["mode"] == "wake" and wake_win["on"]:
                        # window capture: deadline-bound, deaf until
                        # the chime finishes, dies with the window
                        s, armed, until = (wake_win["seq"],
                                           wake_win["armed_at"],
                                           wake_win["until"])
                        mic_win_seq = s
                        mic_fut = loop.run_in_executor(
                            None, lambda g=g, s=s, armed=armed,
                            until=until: (g, *_mic_turn(
                                lambda: ears.listen_once(
                                    gate=lambda: (mic_gate()
                                                  or time.monotonic()
                                                  < armed),
                                    timeout_s=max(0.2,
                                                  until
                                                  - time.monotonic()),
                                    abort=lambda: (_MIC["gen"] != g
                                                   or wake_win["seq"]
                                                   != s),
                                    want_audio=True,
                                    on_speech=_heard))))
                    else:
                        mic_win_seq = None
                        # In wake mode the gate hasn't run yet — defer
                        # the speaker embed to the loop side so dropped
                        # room speech never pays for it.
                        # (enrollment needs the raw audio regardless)
                        ident = (_MIC["mode"] == "open"
                                 and not _ENROLL["on"])
                        # Cold wake mode: the name must be HEARD (wakeword_audio)
                        # before anything is captured, let alone transcribed.
                        # Open mode and an open wake window listen as before.
                        from backtalk import wakeword_audio
                        wk = (wakeword_audio.feed
                              if _MIC["mode"] == "wake" and wakeword_audio.enabled(
                                  _WAKE["name"] or CFG.get("name"))
                              else None)
                        mic_fut = loop.run_in_executor(
                            None, lambda g=g, ident=ident, wk=wk: (
                                g, *_mic_turn(
                                    lambda: ears.listen_once(
                                        gate=mic_gate,
                                        abort=lambda: _MIC["gen"] != g,
                                        want_audio=True,
                                        on_speech=_heard,
                                        wake=wk),
                                    identify=ident)))
                waiters.add(mic_fut)
            done, _ = await asyncio.wait(
                waiters, return_when=asyncio.FIRST_COMPLETED)
            if typed_fut in done:
                text = typed_fut.result(); typed_fut = None
                if text:
                    _wake_cancel()   # typing claims any open window
                    # ...AND ANY KEY PRESS THAT ARRIVED WITH IT. asyncio.wait
                    # returns a SET, so the talk key and the typed line can
                    # land in the same batch -- and with the talk key bound to
                    # a key you submit with (enter is the shipped example of
                    # exactly this), they routinely do. The press was left in
                    # `waiters` and fired on the very next iteration, killing
                    # the reply the line had just started: an answer that never
                    # came, and one "[ptt] (tap or empty)" to explain it.
                    # Typing claims the turn, so the press that sent it is
                    # spent. Same discard the mode-switch path already does.
                    if press_fut is not None and press_fut in done:
                        try:
                            press_fut.result()
                        except Exception as e:
                            log(f"[ptt] press discarded with typed line: {e!r}")
                        press_fut = None
                if text and _RENAME["on"]:
                    if await _rename_step(text, None):
                        continue
                if text and _ENROLL["on"]:
                    # a typed name or "cancel" works; samples must be
                    # spoken (no audio in a typed line)
                    if await _enroll_step(text, None):
                        continue
                if text and not await handle(text):
                    return
                continue
            if mic_fut is not None and mic_fut in done:
                try:
                    g, text, speaker, pcm = mic_fut.result()
                except Exception as e:
                    mic_fut = None
                    if mic_win_seq is not None:
                        # the failed capture was a wake window: close
                        # it and settle the face before the fallback —
                        # but only if the window is still ours (a press
                        # may have claimed it and own the face now)
                        if wake_win["seq"] == mic_win_seq:
                            wake_win["on"] = False
                            signals.set_state("idle")
                        mic_win_seq = None
                    mic_fails += 1
                    if not explain_audio_failure(e):
                        log(f"[ears] open mic failed ({mic_fails}): {e!r}")
                    if mic_fails >= 3:
                        _MIC["mode"] = "ptt"
                        _MIC["gen"] += 1
                        signals.set_mic("ptt")
                        mic_fails = 0
                        mouth.say("The open microphone keeps failing, "
                                  "so I'm switching to push to talk. "
                                  "Hold the key to reach me, and "
                                  "check this window for the error.")
                    continue
                mic_fut = None
                was_win, mic_win_seq = mic_win_seq, None
                if g != _MIC["gen"]:
                    continue             # captured before a switch
                # MEDIA TURNS THE OPEN MIC BACK INTO WAKE MODE. Open mic
                # is "always listening", and to a VAD a song IS speech: the
                # lyrics transcribe, land as turns, and bury the one
                # sentence actually aimed at her. Nothing in this stack can
                # tell them apart acoustically -- signals.media_playing()
                # carries the why -- so the name comes back as the
                # separator for as long as the music is on, and goes away
                # again the moment it stops. The wake WINDOW is untouched:
                # "<name>" -> chime -> "next track" still works over music,
                # because that window is one summons, not a standing
                # invitation.
                if _MIC["mode"] == "wake" or signals.media_playing():
                    if was_win is not None:
                        # This capture WAS the wake window.
                        live = (wake_win["on"]
                                and wake_win["seq"] == was_win)
                        wake_win["on"] = False
                        if not live:
                            continue   # key/typing/switch claimed it
                        if not text:
                            # Closed on silence: a low chime says so —
                            # never a silent dead end.
                            signals.play_cue(close_cue)
                            signals.set_state("idle")
                            continue
                        signals.play_cue(done_cue)
                        # falls through to handle(text), no wake word
                        # needed — the window WAS the wake word.
                    elif text:
                        # The gate steps aside while a spoken
                        # permission ask or a LIVE auto-approve
                        # confirm is pending — the agent asked a
                        # question; the answer needs no wake word. An
                        # exact quit phrase passes ungated too
                        # ("goodbye jarvis" must always hang up), but
                        # only exact: room speech merely CONTAINING
                        # "hang up" stays gated.
                        perm_wait = (_PERM["fut"] is not None
                                     and not _PERM["fut"].done())
                        confirm_live = bool(
                            _CONFIRM["verb"]
                            and time.monotonic() - _CONFIRM["at"] <= 120)
                        quit_hit = _norm_speech(text) in _QUIT_NORMS
                        # The conversation window: for a few seconds
                        # after the agent speaks, follow-ups need no
                        # wake word — a dialogue stays a dialogue. The
                        # +2.5 covers the utterance's own capture and
                        # transcription time (speech that STARTED
                        # inside the window must count).
                        # THE FOLLOW-UP WINDOW: while she is speaking, and
                        # for a beat afterwards, you are in a conversation
                        # and should not have to say the name again.
                        #
                        # It deliberately does NOT cover a turn that is
                        # still THINKING. It used to -- the test carried
                        # `speak_task and not speak_task.done()` -- and
                        # that is the whole duration of a turn, including
                        # the long silent stretch where the agent is
                        # running tools and saying nothing. In that window
                        # the gate below is skipped entirely, so wake_match
                        # never runs and ANY transcript that clears _finish
                        # lands as a turn and cancels the reply in flight.
                        # Maximum exposure to room noise, zero protection,
                        # for exactly as long as the hard questions take.
                        # Field-caught: a whisper ghost destroyed two turns
                        # this way without matching the wake word at all.
                        #
                        # While she THINKS, the name is required again. The
                        # talk key and the escape on the face still stop a
                        # turn without it, so nothing that could already
                        # interrupt has been taken away.
                        # ...and it closes entirely while music plays.
                        # This is the hole the lyrics walk through: the
                        # window exists to skip the name, which is the only
                        # thing separating a command from a chorus. Alexa
                        # suspends follow-up mode during media for this
                        # exact reason. Cost is real and intended -- over
                        # music, every turn is summoned.
                        in_conv = (not _MIC["hush"]
                                   and CFG["wake_followup_s"] > 0
                                   and not signals.media_playing()
                                   and (mouth.speaking
                                        or time.monotonic()
                                        - mouth.last_done
                                        < (CFG["wake_followup_s"] + 2.5
                                           + CFG["endpoint_silence_ms"]
                                           / 1000.0)))
                        if not (perm_wait or confirm_live or quit_hit
                                or _ENROLL["on"] or _RENAME["on"]
                                or in_conv):
                            woke, rest, maybe = wake_match(text)
                            if not woke and maybe and CFG.get("wake_judge"):
                                # whisper's mishear of the name, carrying a
                                # sentence. No phrase table can tell "SHODAN,
                                # show that to me" from "show that to me" —
                                # put the name back and see which reading is
                                # a thing a person says. Only this branch
                                # waits on a model; the bare name never does.
                                woke = await intent_ai.is_summons(
                                    _WAKE["name"] or NAME, text)
                            if not woke:
                                log(f"[wake] (not for me: {text!r})")
                                continue
                            if rest:
                                # "Jarvis, do X" — one shot:
                                # acknowledge, process the remainder.
                                # Logged like the bare-name path: this
                                # branch used to pass silently, so a
                                # one-shot looked exactly like a wake
                                # word that never registered.
                                log("[wake] woke (one shot)")
                                signals.play_cue(done_cue)
                                text = rest
                            else:
                                # Bare wake word. Saying the name IS
                                # the interrupt, same as the talk key:
                                # kill any reply in flight, chime, and
                                # open the window — the loop re-arms
                                # the mic as the window capture, and
                                # armed_at keeps it deaf while the
                                # chime plays so the VAD can't eat it.
                                if speak_task and not speak_task.done():
                                    log("[turn] interrupted — wake word")
                                    _deny_pending()
                                    speak_task.cancel()
                                    mouth.shut_up()
                                log("[wake] woke (window open)")
                                _unhush()   # the name is coming back
                                signals.static_stop()
                                signals.play_cue(wake_cue)
                                signals.set_state("listening")
                                # Deaf only for as long as the chime
                                # actually sounds (wake.wav is 180ms).
                                # ponytail: the mic clears its pre-roll
                                # while gated, so every millisecond here
                                # is a millisecond of your first word
                                # thrown away -- do not round it up.
                                now = time.monotonic()
                                wake_win.update(
                                    on=True, seq=wake_win["seq"] + 1,
                                    armed_at=now + WAKE_DEAF_S,
                                    until=(now + WAKE_DEAF_S
                                           + CFG["wake_window_s"]))
                                continue
                if text and _ENROLL["on"]:
                    if await _enroll_step(text, pcm):
                        continue
                if text and speaker is None and pcm is not None \
                        and voiceid.enabled():
                    # Deferred embed for wake-mode turns that passed
                    # the gate (one-shots and ungated pass-throughs).
                    speaker = (await loop.run_in_executor(
                        None, lambda p=pcm: voiceid.identify(p)))[0]
                if text and _RENAME["on"]:
                    if await _rename_step(text, speaker):
                        continue
                if text and not await handle(text, speaker=speaker,
                                             from_mic=True):
                    return
                continue
            if press_fut in done:
                press_fut.result(); press_fut = None
                press_t = time.monotonic()
                # the button claims any open wake window (its capture
                # aborts within a frame; _MIC["btn"] then gates it)
                _wake_cancel("listening")
                perm_wait = (_PERM["fut"] is not None
                             and not _PERM["fut"].done())
                if speak_task and not speak_task.done() and not perm_wait:
                    log("[turn] interrupted mid-reply — key pressed")
                    speak_task.cancel()          # the button = interrupt
                # During a permission ask the TURN stays alive; the
                # press only silences playback and records the answer.
                mouth.shut_up()
                signals.static_stop()            # button kills the static too
                _unhush()                        # the button = coming back
                signals.set_state("listening")
                mouth.ducker.speech_start()      # duck NOW, while you talk
                print("[ptt] recording (release to send)...", flush=True)
                _MIC["btn"] = True               # open mic yields to the button
                try:
                    # record_held's own ceiling is 60 s, plus whisper on
                    # a minute of audio. Past that the mic thread is
                    # wedged inside the audio system (a CoreAudio
                    # deadlock did exactly this) and the face must not
                    # sit on "listening" for the rest of the day.
                    text, pcm = await asyncio.wait_for(
                        loop.run_in_executor(
                            None, lambda: record_held(ptt.is_held,
                                                      want_audio=True)),
                        timeout=120)
                except asyncio.TimeoutError:
                    log("[ears] the microphone is stuck inside the audio "
                        "system -- restart the voice line")
                    mouth.say("My microphone is stuck inside the audio "
                              "system. I need a restart.")
                    text, pcm = None, None
                except Exception as e:
                    # A device-level failure gets plain words instead of a
                    # raw exception. The pre-flight at startup cannot catch
                    # a microphone unplugged mid-session, and that is the
                    # case where the old message was worst: jargon, on
                    # every press, with the key hook still working so it
                    # looked like it was listening.
                    if explain_audio_failure(e):
                        mouth.say("I can't hear you. There's no working "
                                  "microphone I can use.")
                    else:
                        log(f"[ears] record/transcribe failed: {e!r}")
                        mouth.say("My ears hit an error. Check this "
                                  "window for the details.")
                    text, pcm = None, None
                finally:
                    _MIC["btn"] = False
                mouth.ducker.speech_end(0.2)     # snap back fast on release
                # Into the LOG, beside the [state] lines: "recording" only
                # went to stdout, so a hold whose ring dropped early left
                # nothing to read afterwards.
                log(f"[ptt] released after {time.monotonic() - press_t:.2f}s "
                    f"({'speech' if text else 'nothing'})")
                if text and _ENROLL["on"]:
                    if await _enroll_step(text, pcm):
                        signals.set_state("idle")
                        continue
                # Identify AFTER the volume snaps back: the embed can
                # take a beat and must never sit between key release
                # and the music coming home.
                speaker = None
                if text and voiceid.enabled():
                    speaker = (await loop.run_in_executor(
                        None, lambda p=pcm: voiceid.identify(p)))[0]
                if text and _RENAME["on"]:
                    if await _rename_step(text, speaker):
                        signals.set_state("idle")
                        continue
                if not text:
                    log("[ptt] (tap or empty — ignored)")
                    signals.set_state("idle")
                    continue
                if not await handle(text, spoke_from=press_t,
                                    speaker=speaker, from_mic=True):
                    return
    except KeyboardInterrupt:
        pass
    finally:
        # the curtain goes back up: a face outliving the voice line must
        # not keep showing a ready agent that is not there any more
        signals.set_ready(False)
        signals.set_stage("")
        _MIC["gen"] += 1     # abort any live open-mic capture promptly
        # The badge watcher dies FIRST: a parked _conv_watch resuming
        # after clear_mic would resurrect "wake hot" on a dead line.
        if conv_task and not conv_task.done():
            conv_task.cancel()
            try:
                await conv_task
            except (asyncio.CancelledError, Exception):
                pass
        if speak_task and not speak_task.done():
            speak_task.cancel()
        mouth.shutdown()  # restores the music on Ctrl-C / crash paths too
        signals.static_stop()
        signals.set_state("idle")
        signals.clear_mic()   # a dead line must not claim to listen
        await brain.stop()
        log("[backtalk] hung up")


# Loopback port used purely as a mutex. Nothing is ever served on it.
_INSTANCE_PORT = 8791
_instance_lock = None


def _claim_single_instance() -> bool:
    """Refuse to be the second voice line on this machine, out loud.

    Two instances both hold the keyboard hook and both open the
    microphone, and the result looks EXACTLY like a broken talk key:
    presses register, the audio goes to whichever process won the
    device, and the loser reports an ignored tap. Nothing warned about
    it, so a user who double-clicks the Talk icon twice concludes the
    product is broken. The tell, when it was finally caught, was the
    same sentence transcribed twice at an identical timestamp.

    A bound socket is the mutex rather than a pid file, because the
    operating system releases it when this process dies HOWEVER it dies.
    A pid file outlives a crash or a force-kill and then lies about a
    process that is long gone, which is the failure it would exist to
    prevent.
    """
    global _instance_lock
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # No SO_REUSEADDR here on purpose: reuse is exactly what would let a
    # second instance bind alongside the first and defeat the whole point.
    try:
        s.bind(("127.0.0.1", _INSTANCE_PORT))
        s.listen(1)
    except OSError:
        s.close()
        return False
    _instance_lock = s
    return True


def main():
    if not _claim_single_instance():
        print("[backtalk] ANOTHER VOICE LINE IS ALREADY RUNNING on this "
              "machine, so this one is stopping.", flush=True)
        print("[backtalk] Two of them fight over the microphone and the "
              "talk key, which looks exactly like the talk key being "
              "broken. Use the window that is already open, or close it "
              "and start again.", flush=True)
        sys.exit(1)
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("\n[backtalk] interrupted — hanging up", flush=True)


if __name__ == "__main__":
    main()
