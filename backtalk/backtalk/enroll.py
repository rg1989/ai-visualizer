"""Enroll a voice: `python -m backtalk.enroll Alex`

Run it while the voice line is STOPPED (both want the microphone).
Records four sentences, embeds each, checks they agree with each
other, and saves the averaged voiceprint to voices.json. Re-running
with the same name replaces the old print — that is the fix whenever
someone's voice keeps coming up unrecognized.
"""

import socket
import sys

import numpy as np

from backtalk import voiceid
from backtalk.ears import Ears, check_microphone, warm

PROMPTS = [
    "Count from one to seven, slowly.",
    "Say what you had for breakfast, in a full sentence.",
    "Say: hey Jarvis, what does the rest of my day look like?",
    "Say anything you like, a couple of sentences long.",
]

# Conversation endpointing (480 ms of silence = done) is exactly wrong
# for "count slowly": the pause between numbers ends the take at "one".
# Enrollment listens with a patient window instead — keep talking
# through pauses up to ~2 s; it closes ~2 s after you actually stop.
ENROLL_SILENCE_MS = 2000
# And a real voiceprint needs more audio than the runtime minimum:
# short takes enroll noise, not a voice.
ENROLL_MIN_S = 2.5


def main():
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("usage: python -m backtalk.enroll <Name>")
        sys.exit(2)
    name = sys.argv[1].strip()
    # The voice line and this tool both want the microphone — and
    # prompt 3 literally speaks the wake phrase, which a live line
    # would act on. Backtalk's single-instance mutex is a bound
    # loopback socket; if anyone answers, the line is up.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.3)
    running = probe.connect_ex(("127.0.0.1", 8791)) == 0
    probe.close()
    if running:
        print("The voice line is running. Stop it first (Ctrl-C in "
              "its window, or say the quit phrase), then run "
              "enrollment again.")
        sys.exit(1)
    # Mic check BEFORE the model loads: no point downloading 80 MB to
    # discover there's nothing to record with. (check_microphone
    # memoizes, so it must run before warm()'s own first call.)
    if not check_microphone():
        sys.exit(1)
    print(f"Enrolling {name}. Loading the ears and the voiceprint "
          "model (first run downloads it)...", flush=True)
    warm()
    voiceid._classifier()
    ears = Ears(silence_ms=ENROLL_SILENCE_MS)
    vecs = []
    for i, prompt in enumerate(PROMPTS, 1):
        for attempt in (1, 2):
            print(f"\n[{i}/{len(PROMPTS)}] {prompt}")
            print("    ... listening (pauses are fine; it closes about "
                  "two seconds after you stop)", flush=True)
            got = ears.listen_once(timeout_s=45, want_audio=True)
            text, pcm = got if isinstance(got, tuple) else (None, None)
            if text and pcm is not None \
                    and len(pcm) >= ENROLL_MIN_S * voiceid.RATE:
                print(f"    heard: {text!r}")
                vecs.append(voiceid.embed(pcm))
                break
            if attempt == 1:
                print("    Too short or too quiet — same one, once "
                      "more (keep going a little longer).")
            else:
                print("    Still nothing usable — moving on.")
    if len(vecs) < 3:
        print("\nNot enough clean recordings (need 3+). Run it again "
              "somewhere quieter.")
        sys.exit(1)
    # Internal consistency: every clip should sound like the others.
    ok_all, sims = voiceid.agreement(vecs)
    print(f"\nInternal agreement: {[round(s, 2) for s in sims]} "
          "(healthy is 0.6+)")
    if not ok_all:
        print("One clip disagrees with the rest (noise, or someone "
              "else spoke). Run the enrollment again.")
        sys.exit(1)
    voiceid.save_profile(name, vecs)
    print(f"Saved. {name} is enrolled in {voiceid.PROFILES_PATH.name}. "
          "The voice line picks it up on its next launch.")


if __name__ == "__main__":
    main()
