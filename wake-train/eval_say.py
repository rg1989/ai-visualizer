"""Recall / false-fire sweep for a wake model, with macOS `say` voices as stand-in
speakers (until Roman's own clips exist; see the plan's Task 5 for the real thing).
Runs through the SAME Detector backtalk uses:
  cd ~/my-agent/backtalk && .venv/bin/python <this> path/to/shodan.onnx
  cd ~/my-agent/backtalk && .venv/bin/python <this> hey_jarvis_v0.1.onnx   # harness control
ponytail: TTS voices are not Roman; this ranks thresholds and catches a dead model, no more."""
import glob, os, subprocess, sys, wave
import numpy as np
from backtalk import wakeword_audio

VOICES = ["Samantha", "Daniel", "Karen", "Moira", "Rishi", "Tara", "Aman", "Fred",
          "Kathy", "Ralph", "Albert", "Junior"]
POS = ["show dan", "showdan", "Shodan", "hey show dan", "show dan, what time is it",
       "okay show dan, lights on"]
NEG = ["show them", "show then", "show that", "show down", "shut down", "show me",
       "so done", "jordan", "sean", "hey jarvis", "what time is it",
       "turn on the lights please", "the weather today is nice"]
CACHE = os.path.expanduser("~/.cache/wake-eval-say")


def clip(voice, text):
    path = os.path.join(CACHE, f"{voice}_{abs(hash(text))}.wav")
    if not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        subprocess.run(["say", "-v", voice, "-o", path + ".aiff", text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        path + ".aiff", path], check=True, capture_output=True)
        os.remove(path + ".aiff")
    with wave.open(path) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    pad = np.zeros(8000, dtype=np.int16)          # 0.5 s of silence each side
    return np.concatenate([pad, x, pad, pad])


def fires(det, x):
    det.m.reset(); det.buf = det.buf[:0]
    return sum(det.feed(x[i:i + 16000]) for i in range(0, len(x), 16000))


model = sys.argv[1]
pos = {(v, t): clip(v, t) for v in VOICES for t in POS}
neg = {(v, t): clip(v, t) for v in VOICES for t in NEG}
print(f"model {os.path.basename(model)}: {len(pos)} positive clips, {len(neg)} negative clips, {len(VOICES)} voices")
print(f"{'thr':>5} {'recall':>7} {'neg fired':>10}   worst phrases")
for thr in (0.3, 0.4, 0.5, 0.6, 0.7):
    det = wakeword_audio.Detector(model, thr, 0.0)
    hit = {k: fires(det, x) > 0 for k, x in pos.items()}
    nf = {k: fires(det, x) > 0 for k, x in neg.items()}
    by_phrase = {t: sum(hit[(v, t)] for v in VOICES) for t in POS}
    worst = ", ".join(f"{t!r}={n}/{len(VOICES)}" for t, n in sorted(by_phrase.items(), key=lambda kv: kv[1])[:2])
    neg_fired = sorted({t for (v, t), f in nf.items() if f})
    print(f"{thr:>5.2f} {sum(hit.values()) / len(hit):>7.0%} {sum(nf.values()):>10d}   {worst}  neg: {neg_fired[:4]}")
