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
"""Configuration — backtalk.json in the repo root, merged over defaults.

backtalk deliberately owns NO personality. Your agent's identity lives in
the CLAUDE.md of whatever folder `agent_dir` points at — backtalk just
gives that agent a mouth and ears. The only voice-related instruction it
adds is the spoken-delivery discipline below, which is about the MEDIUM
(writing for the ear), never the character.
"""
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# One install, more than one assistant. Point BACKTALK_CONFIG at a different
# JSON file and you get a second agent (its own name, voice, folder and
# greeting) without a second copy of the code. A launcher exports it; nothing
# else changes.
CONFIG_PATH = Path(os.environ.get("BACKTALK_CONFIG") or (REPO / "backtalk.json"))

DEFAULTS = {
    # The folder whose CLAUDE.md defines WHO your agent is. The voice
    # session runs there, so it's the same assistant as your terminal
    # sessions — same name, same personality, same memory.
    "agent_dir": "~",
    # Display name, used in logs and to build the quit phrases
    # ("goodbye <name>" hangs up). Match your agent's actual name.
    "name": "Assistant",
    # WHICH brain answers: "claude" (Anthropic, the default) or "zai"
    # (Z.AI's GLM). It selects a whole set at once — the API endpoint,
    # the credential, the CLAUDE_CONFIG_DIR, and the three model ids
    # below — which is why it sits above them: change this and those
    # are chosen for you (see backtalk/provider.py, PROVIDERS).
    # Used to be decided by which launcher you double-clicked; now the
    # settings picker's BRAIN tab sets it LIVE (the brain restarts, so
    # the conversation starts fresh — you get told) and writes it back
    # here, so the next launch comes up on the same brain.
    # The API KEY IS NOT STORED HERE. It lives in the macOS Keychain
    # (item "jarvis-glm") and nowhere else, so this file stays the kind
    # of thing you can read out on a stream or paste into an issue.
    "brain_provider": "claude",
    # Which tier of the chosen brain answers. Claude: "fast" (Haiku),
    # "balanced" (Sonnet, Opus behind "switch to the deep model") or
    # "think" (Opus on both). Z.AI: "glm-5.3" (strongest) or
    # "glm-5.3-flash" (cheaper on plan credits, quicker to the first
    # word). Empty means the provider's own default, and an unknown id
    # degrades to it too — see provider.py, which owns both lists.
    # The BRAIN tab writes this; like brain_provider it survives a
    # restart, and like it, no API key is stored here.
    "brain_model": "",
    # The brain. Full model id ON PURPOSE — never a bare alias like
    # "sonnet": the SDK resolves aliases through its own bundled CLI and
    # can silently land on an older model. The fast tier is most of the
    # speed difference people ask about; a deep-work model makes every
    # reply noticeably slower and burns usage doing it.
    "model": "claude-sonnet-5",
    # The deep-work model for the voice console's "switch to the deep
    # model" command ("back to the fast model" returns to "model"
    # above). Full id ON PURPOSE, same reasoning as "model". The switch
    # lasts one session and is always spoken; this default never moves
    # by itself.
    "deep_model": "claude-opus-5",
    # Tool permissions for the voice session. "ask" is the default ON
    # PURPOSE (safety is opt-out, never opt-in): when the agent wants a
    # gated tool (write a file, run a real command), it ASKS OUT LOUD
    # and waits. Answer by voice or by typing. An EXACT yes approves
    # ("yes", "yeah", "go ahead", "approved"...); anything else denies,
    # and your words are passed back to the agent as the reason, so
    # "no, put it in drafts instead" actually steers it. No answer
    # within 75 seconds means no, out loud. Most read-only work passes
    # without asking; anything that changes things asks.
    # "bypassPermissions" is AUTO-APPROVE: the agent acts without
    # asking, exactly like a terminal session with approvals off.
    # (Not to be confused with hands-free LISTENING, which is about
    # the microphone: see mic_mode below.) Never hand-edit this file
    # to switch: tell your agent to change it (takes effect next
    # launch), or say "stop asking for permission" (then "confirm")
    # or "start asking again" inside a voice session for an immediate
    # flip that also saves. The legacy value "default" now
    # behaves as "ask" (a headless voice session could never render
    # the terminal prompt it promised).
    "permission_mode": "ask",
    # Which of your agent's skills the voice session can SEE. null keeps the
    # CLI's own default (all of them). [] hides every one. A list names the
    # ones to allow.
    #
    # This matters on a shared screen. Skill DESCRIPTIONS live in the system
    # prompt, so if yours name clients, employers or systems, they are one
    # screen-share away from an audience. A context filter, not a sandbox:
    # it decides what the session is TOLD about, not what it can reach.
    "visible_skills": None,
    # Extra folders the agent may access beyond agent_dir (e.g. your
    # notes vault). Absolute paths or ~ paths.
    "extra_dirs": [],
    # Hold-to-talk key. Named keys ("home", "f13", "right_alt", ...)
    # or a single character.
    "ptt_key": "home",
    # Who owns that key. "global" (default): a system-wide listener, so
    # the key works from any window -- and so does every stray press of
    # it in any window. "face": the face page reports the key itself, so
    # it counts only while that browser tab has focus; Enter typed
    # anywhere else stays that window's Enter, and no Input Monitoring
    # permission is needed.
    "ptt_scope": "global",
    # The microphone mode. "ptt" (push to talk, the default and the
    # recommendation): the mic is closed except while the key is held,
    # so room audio and your own speakers can never trigger the agent.
    # "open" (hands-free listening): always listening with voice
    # detection; a video, music with vocals, or another person in the
    # room CAN trigger it, and with open speakers it can hear itself
    # (headphones recommended). The key still works in hands-free
    # listening: it interrupts, and holding it always gets you heard.
    # Switch live by voice: "go hands free" / "push to talk mode"
    # (the switch saves itself here). The --open-mic launch flag
    # forces "open" for one session.
    # "wake" (wake word): always listening like "open", but an
    # utterance only reaches the agent when it starts with a wake
    # phrase. Saying the name alone chimes and opens a short window
    # for the command; "name, do X" is a one shot. Switch live by
    # voice: "wake word mode".
    "mic_mode": "ptt",
    # Wake-word mode knobs. wake_phrases defaults to the agent's name
    # plus the natural greetings ("<name>", "hey <name>", "hi <name>",
    # "hello <name>", "okay <name>", "ok <name>") — set it here only
    # to override that. wake_aliases are single-word misspellings
    # whisper produces for the name, accepted in its place; unset,
    # they default to the known Jarvis mishears when the name is
    # Jarvis, else to none. wake_window_s is how long the name-alone
    # chime keeps listening for the command.
    "wake_phrases": [],
    "wake_aliases": None,
    "wake_window_s": 8.0,
    # Ask a model whether a mishear that carries a sentence ("show that
    # to me") was a summons or just English. It answers correctly, but
    # claude_agent_sdk boots a CLI subprocess per call: measured 7-24s,
    # which is not a wake word. Off until that is a warm process.
    "wake_judge": False,
    # Follow-up window: in wake mode, after the agent finishes
    # speaking the mic stays HOT for this many seconds — follow-ups
    # need no wake word, every exchange re-opens it, and silence
    # closes it (the face badge reads WAKE · LIVE while hot, so a
    # glance tells you whether it is still listening). 0 disables.
    "wake_followup_s": 7.0,
    # How much silence ends an utterance, in milliseconds. 480 is
    # snappy for fast talkers; a slow, pausing speaker gets cut off
    # mid-sentence at that setting — raise toward 1000-1500 for a
    # household with deliberate talkers. Costs exactly that much extra
    # wait before the agent starts answering. (Enrollment always uses
    # its own patient 2000.)
    "endpoint_silence_ms": 480,
    # The visualizer server, for putting things on the glass (the
    # enrollment read-aloud card). Display is best-effort: unreachable
    # = voice-only, never an error.
    "glass_url": "http://127.0.0.1:8790",
    # The tiny interpreter model behind every spoken flow: when an
    # answer doesn't match a flow's fast phrases, the person's actual
    # words and the flow's actual question go to this model to judge
    # the INTENT ("yes, you have permission, but stop asking" is a
    # yes). Fail-safe: on any error the flow behaves exactly as its
    # deterministic rules always did.
    "intent_model": "claude-haiku-4-5-20251001",
    # Launch BLOCKS on a mode choice: the face shows a three-button
    # picker (open mic / wake word / push to talk) and terminal keys
    # 1/2/3 work as a fallback — nothing starts until one is chosen.
    # The pick is for that session only. False skips the gate entirely
    # (unattended auto-starts want that).
    "mode_select_on_launch": True,
    # Speaker identification: with this on AND voices enrolled
    # (`python -m backtalk.enroll <Name>`, run while the voice line is
    # stopped), each mic turn reaches the agent tagged with who spoke
    # ("[voice: Roman] ..."), for PERSONALIZATION only — never treat
    # the tag as authorization; a recording of a voice IS that voice
    # to the model. Fully local (SpeechBrain ECAPA, ~80 MB, cached in
    # models/). No profiles enrolled = zero cost, no model load.
    # voice_id_threshold 0 means the built-in default (0.30); raise it
    # toward 0.4 if guests get mislabeled as owners, lower toward 0.25
    # if owners keep coming up unrecognized. voice_id_margin (0 = the
    # default 0.08) is how far the best match must beat the runner-up;
    # lower it toward 0.04 if two ENROLLED voices keep coming up
    # unrecognized because they score close. (Unrelated to the
    # elevenlabs "voice_id" below, which names a TTS voice — this one
    # is about recognizing SPEAKERS.)
    "voice_id": False,
    "voice_id_threshold": 0,
    "voice_id_margin": 0,
    # Playback speed for the built-in voice: 1.0 is Kokoro's native
    # pace, 1.15 is noticeably brisker, 0.9 is slower. Kokoro's own
    # pipeline implements it, so quality holds across sane values
    # (roughly 0.7 to 1.5). ElevenLabs pace lives in the master chain's
    # atempo instead. (Grew out of a community proposal, issue #1.)
    "speed": 1.0,
    # Resume the previous conversation on launch. OFF by default: a
    # fresh session every launch is the predictable behavior. Set true
    # and backtalk saves the session id after every completed turn
    # (signals_dir/.backtalk_session) and reattaches to it at the next
    # launch, so killing the window stops costing you the conversation.
    # A resume that fails falls back to a fresh session and says so in
    # the log. (Grew out of the same community proposal, issue #1.)
    "resume_last_session": False,
    # Publish your Claude usage (the five-hour and weekly windows) on the
    # signal bus so a face can draw it. OFF by default and deliberately
    # so: this is your own account spend, and the faces this feeds are
    # frequently on a stream or a shared screen. Nothing is collected at
    # all while this is false. (Community fix, ai-visualizer issue #1.)
    "show_usage": False,
    # z.ai only: strip GLM's forced deliberation. Their docs say it
    # cannot be done and that reasoning_effort defaults to "max" -- but
    # their ANTHROPIC endpoint accepts thinking.type "disabled" and
    # honors it, which is the only knob that reaches this far. Measured
    # 3.67s -> 1.76s to first word, and it fixed replies that came back
    # empty because the budget went entirely on thinking. See
    # backtalk/zaifast.py. False = talk to z.ai directly, deliberation on.
    "zai_disable_thinking": True,
    # Reasoning effort for the voice session: "" inherits the model's
    # default; "low" / "medium" / "high" / "max" applies at launch.
    # Saying "set effort to X" in a voice session saves itself here.
    "effort": "",
    # The voice (Kokoro, local, free). bm_lewis is the proven default —
    # British male, the butler register. Others: bm_george, bm_daniel,
    # bm_fable, am_michael, af_heart... The first letter picks the
    # language pipeline (a=American, b=British, e/f/h/i/j/p/z = other
    # languages), so keep voice and accent matched.
    "voice": "bm_lewis",
    # Speech recognition (faster-whisper, local, free).
    # Models: tiny.en / base.en / small.en / medium.en / large-v3-turbo.
    # small.en is the speed sweet spot; it is also the one that "corrects"
    # unclear words into plausible ones, because a small decoder leans on
    # its language model where its ears fall short. Measured on an M2
    # (42 clips, MLX, word error rate clean / 10 / 5 / 0 dB SNR):
    #   small.en 8-bit        289 MB   0.32 s   3.6 / 7.3 / 10.3 / 31.3 %
    #   medium.en 8-bit       827 MB   0.93 s   3.6 / 7.3 /  7.5 / 19.2 %
    #   large-v3-turbo        1.5 GB   1.21 s   2.6 / 5.2 /  6.0 / 14.9 %
    #   large-v3-turbo 8-bit  833 MB   1.79 s   2.6 / 5.0 /  6.0 / 15.3 %
    # large-v3-turbo hears about half the errors of small.en on unclear
    # speech and never cleans anything up (there is no LLM pass here); it
    # costs ~0.9 s per utterance and ~9 s to load. Multilingual models
    # take stt_language below.
    "stt_model": "small.en",
    # The language a MULTILINGUAL model (large-v3-turbo, small, ...) is
    # told to transcribe; the ".en" models ignore it. "" lets whisper
    # detect the language on every clip, which on a two-second utterance
    # misfires often enough to be a known whisper failure mode -- set a
    # language unless the household really speaks several.
    "stt_language": "en",
    # A speech detector (silero VAD) in front of whisper: a clip whose
    # peak speech probability is under this is not transcribed at all.
    # Whisper answers room noise with confident words -- large-v3-turbo
    # says "Thank you." to a fan, scored exactly like a real "Bye." --
    # and this is the one thing that tells them apart. 0.5 is silero's
    # own (and faster-whisper's) default; 0 disables. Measured on 48
    # clips: speech peaks above 0.95 clean or quiet (-20 dB), never under
    # 0.53 at 5 dB SNR, one-word replies included; at 0 dB SNR (where
    # whisper itself misses one word in seven) the median is 0.55, so
    # some of that is lost. White, pink, fan, rumble, hum and crackle
    # noise never scored above 0.38. Lower it toward 0.4 if a quiet
    # talker in a loud room goes unheard; the log prints every number.
    "stt_vad": 0.5,
    # "auto" uses CUDA when present, otherwise CPU. int8 keeps CPU fast.
    "stt_device": "auto",
    "stt_compute": "int8",
    # Apple GPU only. MLX caches every Metal scratch buffer it allocates and
    # NEVER shrinks: measured 749 MB after one 3s clip, climbing to 1222 MB
    # once utterance lengths vary, and 2475 MB in a day-old session. Capping
    # it costs ~5 ms per utterance (240 -> 245 ms median) and hands back most
    # of a gigabyte, which on a 16 GB machine is the difference between
    # swapping and not. 0 disables the cap and restores the old behaviour.
    "stt_cache_mb": 256,
    # Apple GPU only. "8bit" loads a quantized conversion of the SAME model:
    # small.en drops from 462 MB resident to 289 MB and, measured over eight
    # spoken clips at clean/20/10/5/0 dB SNR, returned transcripts CHARACTER-
    # IDENTICAL to full precision every time, for +5 ms. "" keeps the full
    # weights. "4bit" exists and is 195 MB, but its output drifts from full
    # precision on noisy audio -- do not use it to save memory silently.
    # A conversion missing from the Hub falls back to the full weights.
    # NOT a free lunch on a big encoder: large-v3-turbo 8-bit keeps the
    # accuracy and 710 MB, but its quantized encoder runs 0.6 s SLOWER per
    # utterance on an M2 (1.79 vs 1.21 s) -- set "" there unless memory
    # is the tighter constraint.
    "stt_quant": "8bit",
    # When macOS reports memory pressure (another app needs the RAM), drop
    # the scratch caches; under CRITICAL pressure and only between turns,
    # drop the models too and reload them on the next turn (~3 s once).
    # Nothing is touched while the machine has memory to spare. See
    # pressure.py. false keeps everything resident no matter what.
    "evict_on_pressure": True,
    # Wake mode: hear the name in the AUDIO (openWakeWord) before whisper
    # runs at all, instead of transcribing every noise to look for it.
    # See wakeword_audio.py for the numbers. threshold: the stock model
    # scores a clear "hey Jarvis" ~0.998 and a bare "Jarvis" as low as
    # ~0.62 in some voices; nothing that is not the name scored above
    # 0.006 with the VAD gate on, so 0.45 keeps margin on both sides;
    # lower it if she stops answering to the bare name. vad: silero
    # speech gate on the detector (a loud fan scored 0.27 without it,
    # 0.000 with), 0 to disable.
    "wake_model": True,
    "wake_model_file": "hey_jarvis_v0.1.onnx",
    "wake_model_threshold": 0.45,
    "wake_model_vad": 0.5,
    # The microphone to record from, matched by NAME. "" means whatever
    # the OS calls the default input, which is right on most machines.
    #
    # Set a real device name to PIN the mic, so a headset connecting for
    # OUTPUT cannot steal your input -- which also keeps a Bluetooth
    # headset in high-quality A2DP instead of dropping it to the
    # narrowband call profile mid-sentence, degrading what you hear at
    # the same moment it takes your voice.
    #
    # A name and never an index: indices shift every time a device
    # connects or disconnects, the exact event this setting exists to
    # survive. Exact name wins, then the first case-insensitive
    # substring. A name matching nothing falls back to the default and
    # logs the inputs it did find; the mic degrades, it never goes mute.
    #
    # NOT "stt_device" below, which is the Whisper COMPUTE device.
    "mic_device": "",
    # Optional premium voice: ElevenLabs on YOUR key. The key NEVER
    # goes in a file: it's read from the macOS Keychain (item
    # `backtalk-elevenlabs`) or Linux secret-tool, with the
    # ELEVENLABS_API_KEY env var as last-resort fallback — see
    # mouth._get_elevenlabs_key for the seeding one-liners. Kokoro
    # remains the automatic fallback, so the voice degrades instead of
    # going mute if the cloud fails. Needs ffmpeg on the PATH.
    "elevenlabs": {
        "enabled": False,
        "voice_id": "",
        # Purely for you. Voice IDs are unreadable six months later, so put
        # the human name here; nothing reads it.
        "voice_note": "",
        "model": "eleven_turbo_v2_5",
        # Which OS credential-store entry holds the key. Change it if you
        # already keep an ElevenLabs key under a name of your own rather
        # than seeding a second copy of the same secret.
        "key_slot": "backtalk-elevenlabs",
        # Local mastering: ElevenLabs' site previews are mastered demo
        # clips and the raw API never matches them. This chain closes
        # the gap: presence lift, light chest, broadcast compression,
        # limiter. atempo is the one pace dial (1.0 = native).
        "master": ("atempo=1.12,highpass=f=70,"
                   "equalizer=f=3200:t=q:w=1.2:g=3.5,"
                   "equalizer=f=140:t=q:w=1:g=1.5,"
                   "acompressor=threshold=-18dB:ratio=2.5:attack=8:"
                   "release=120:makeup=4dB,alimiter=limit=0.95"),
    },
    # Where the signal-bus files are written (.voice_state,
    # .voice_waveform, .voice_loading_pid) — anything can watch them;
    # visualizers pair with this contract. Default: the repo root.
    "signals_dir": "",
    # THE BAREHANDS SEAM: point this at a barehands checkout's state/
    # folder and its on-screen ring becomes your agent's face — it
    # breathes while idle, spins while thinking, pulses with the voice.
    # (github.com/jaredrhod/barehands)
    "barehands_state_dir": "",
    # Sound played while the agent thinks, so a long pause never reads as
    # a dead line. The bundled one ships in assets/; a relative path
    # resolves against this repo. Set "" to think in silence.
    "thinking_sound": "assets/thinking.wav",
    # Spoken lines. {name} is replaced with "name" above.
    "greeting": "Voice line online. Hold {ptt_key} and talk to me.",
    # Spoken instead of "greeting" when mic_mode is "open", where telling
    # someone to hold a key is wrong. Leave "" to use "greeting" for both.
    "greeting_open_mic": "",
    "signoff": "Voice line closing. I'll be here when you need me.",
    # Appended to the spoken-delivery discipline below. The discipline covers
    # the MEDIUM (write for the ear, no markdown, keep it short); your agent's
    # CLAUDE.md covers the character. Use this for a note that belongs to
    # neither, e.g. a rule that only applies when it is speaking.
    "discipline_append": "",
}

# The spoken-delivery discipline — the MEDIUM half of what used to be a
# persona. The CHARACTER half deliberately is not here: it's whatever
# lives in the agent_dir's CLAUDE.md. One identity, one place.
DISCIPLINE = (
    "VOICE SESSION (your reply is spoken aloud through a TTS engine, "
    "not displayed): you are SPEAKING, in your own voice and "
    "personality — your CLAUDE.md is who you are. The TTS engine "
    "PERFORMS your punctuation, so write like a performance, never "
    "like a memo: contractions always, punchy conversational "
    "sentences, and if a line could open a quarterly report, rewrite "
    "it like you're telling a friend. Keep replies to a few short "
    "sentences; go longer only when the question genuinely needs it. "
    "No markdown, no lists, no code blocks, no emoji, no URLs. Say "
    "numbers the way a human says them out loud — never raw figures "
    "or symbols. NEVER SPEAK A FILE PATH: say the file, not its "
    "address. 'the config' or 'ears dot py', never a string of "
    "slashes and folder names read one by one — it is unbearable "
    "aloud and carries no meaning by ear. Same for URLs and long "
    "ids: name the thing, not the address. "
    "TWO CHANNELS, ONE ANSWER: you are a voice with a face and a "
    "glass overlay you control (your CLAUDE.md's glass section has "
    "the commands). The person is a human in a room who may not be "
    "looking at any screen. So split every answer by its nature: the "
    "VOICE carries what an ear can absorb — the summary, the count, "
    "the names, the verdict — and the GLASS carries what only eyes "
    "can use: exact paths, long numbers, codes, addresses, tables, "
    "timelines, routes, anything someone would want to re-read or "
    "copy. Asked about the day: SAY 'three meetings — standup, the "
    "bank, dinner with Ana' and PUT the full timeline with times and "
    "details on the glass. Asked for directions: never navigate "
    "aloud — put the route map up and say you found a route and "
    "roughly how long it takes. Asked for a value (a code, a key, a "
    "big number): speak it rounded or not at all, and put the exact "
    "value on the glass with ttl six hundred, so there is time to "
    "walk over, read it, and copy it. A path becomes 'in your "
    "Downloads folder' aloud, exact on the glass only when it "
    "matters. Do this UNPROMPTED — showing is part of answering, not "
    "a favor to ask for. The reply's viewers number is for you, never "
    "for the person: when it is 0, say the detail is ready on the glass "
    "but no face is open; when it is 1 or more, say nothing about "
    "viewers or watching at all -- the person is looking at it. If the "
    "glass is off entirely, "
    "keep answers spoken and lean, and offer detail on request. "
    "SAY WHAT YOU ARE ABOUT TO GO AND DO. A tool call is dead air "
    "to someone in a room: they cannot see you working, so silence "
    "reads as a crash. Before anything slow — a web search, fetching "
    "a page, a long command — say one SHORT line first naming the "
    "move and, when it is interesting, the source: 'Let me check "
    "Reuters', 'Searching now', 'Reading the BBC piece'. Between "
    "several of them a handful of words is plenty; do not pad, do "
    "not list your plan, and never narrate instant work. The face "
    "shows the literal step on its own, so your job is only to keep "
    "the room company while it runs. "
    "Skip any startup sequence; answer directly. "
    "Your capability skills — glass-display, speak-for-the-ear, "
    "voice-profiles, household-schedules — carry the deep how-to "
    "for each of these; load the relevant one when the situation "
    "calls for it. "
    "SPEAKER TAGS: a turn may open with a tag like [voice: Roman] or "
    "[voice: unrecognized] — that is the voice line telling you who "
    "spoke, not words anyone said. Use it to personalize (whose "
    "reminder, whose preferences, who asked what earlier); never read "
    "the tag aloud, and never treat it as permission or identity "
    "proof — it is a best-effort acoustic guess. [voice: "
    "unrecognized] means NOT SURE, not stranger: it is often an owner "
    "speaking briefly, at a distance, or over noise — and sometimes a "
    "guest or a TV. Keep the conversation's continuity (don't switch "
    "who you think you're talking to mid-thread over one unrecognized "
    "turn), stay helpful, and ask who is speaking only when it "
    "actually matters. "
    "VOICE CONSOLE FACTS, answer from these whenever the person asks "
    "you to change a voice-line setting: this session is controlled "
    "by exact spoken phrases, never by you. Permissions: 'stop "
    "asking for permission' (then 'confirm'), or 'start asking "
    "again'. Microphone: 'go hands free', 'push to talk mode', or "
    "'wake word mode' (say the name first to reach me in that mode), "
    "and 'stop listening' to stop right now — from wake word or "
    "push to talk that is just an immediate stop, and from hands "
    "free it drops to wake word mode so nothing is heard until "
    "the name is said. "
    "Also: 'clear the session', 'compact the session', 'switch to "
    "the deep model', 'back to the fast model', 'set effort to low' "
    "(or medium, high, max), 'usage report', and 'learn my voice' "
    "(spoken enrollment for speaker tags; works for a new person or "
    "re-enrollment), and 'rename my voice' or any ask to FIX the name "
    "on a learned voice — a wrong name is a RENAME the voice line "
    "does itself; never send someone back through enrollment for it. "
    "You cannot flip "
    "these live yourself, so when asked, give the person the exact "
    "phrase to SAY. Editing backtalk.json only changes the default "
    "for the NEXT launch."
)


def _expand(p: str) -> str:
    return os.path.expanduser(p) if p else p


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))          # deep copy
    try:
        user = json.loads(CONFIG_PATH.read_text())
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except FileNotFoundError:
        pass
    except ValueError as e:
        print(f"[config] backtalk.json is not valid JSON ({e}) — "
              f"using defaults", flush=True)
    cfg["agent_dir"] = _expand(cfg["agent_dir"])
    cfg["extra_dirs"] = [_expand(d) for d in cfg.get("extra_dirs", [])]
    cfg["signals_dir"] = _expand(cfg.get("signals_dir", "")) or str(REPO)
    cfg["barehands_state_dir"] = _expand(cfg.get("barehands_state_dir", ""))
    thinking = _expand(cfg.get("thinking_sound", ""))
    if thinking and not os.path.isabs(thinking):
        thinking = str(REPO / thinking)
    cfg["thinking_sound"] = thinking
    name = str(cfg.get("name") or "Assistant")
    low = name.lower()
    cfg["quit_phrases"] = tuple(cfg.get("quit_phrases") or (
        f"goodbye {low}", f"good bye {low}", "end voice mode",
        f"hang up {low}", "hang up"))
    if isinstance(cfg.get("wake_phrases"), str):
        cfg["wake_phrases"] = [cfg["wake_phrases"]]
    if not cfg.get("wake_phrases"):
        cfg["wake_phrases"] = [low, f"hey {low}", f"hi {low}",
                               f"hello {low}", f"okay {low}", f"ok {low}"]
    if isinstance(cfg.get("wake_aliases"), str):
        cfg["wake_aliases"] = [cfg["wake_aliases"]]
    if cfg.get("wake_aliases") is None:
        cfg["wake_aliases"] = (["jervis", "jarvus", "jarves"]
                               if low == "jarvis" else [])
    try:
        cfg["wake_window_s"] = max(1.0, float(cfg.get("wake_window_s", 8.0)))
    except (TypeError, ValueError):
        cfg["wake_window_s"] = 8.0
    try:
        cfg["wake_followup_s"] = min(30.0, max(
            0.0, float(cfg.get("wake_followup_s", 7.0))))
    except (TypeError, ValueError):
        cfg["wake_followup_s"] = 7.0
    try:
        cfg["endpoint_silence_ms"] = int(min(3000, max(
            200, float(cfg.get("endpoint_silence_ms", 480)))))
    except (TypeError, ValueError):
        cfg["endpoint_silence_ms"] = 480
    for key, ceil in (("voice_id_threshold", 0.95), ("voice_id_margin", 0.5)):
        try:
            cfg[key] = min(ceil, max(0.0, float(cfg.get(key) or 0)))
        except (TypeError, ValueError):
            cfg[key] = 0
    key_label = "the " + str(cfg.get("ptt_key", "home")).replace("_", " ") \
                + " key"
    # With no key to hold (open mic or wake word), a separate line can be set.
    if str(cfg.get("mic_mode", "ptt")) in ("open", "wake") \
            and cfg.get("greeting_open_mic"):
        cfg["greeting"] = cfg["greeting_open_mic"]
    cfg["greeting"] = str(cfg["greeting"]).replace(
        "{name}", name).replace("{ptt_key}", key_label)
    cfg["signoff"] = str(cfg["signoff"]).replace("{name}", name)
    return cfg


CFG = load()

# The character half stays in YOUR agent's CLAUDE.md. This is the medium.
if CFG.get("discipline_append"):
    DISCIPLINE = DISCIPLINE + " " + str(CFG["discipline_append"]).strip()
