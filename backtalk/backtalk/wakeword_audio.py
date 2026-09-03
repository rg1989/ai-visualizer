"""Hear the name in the AUDIO, so whisper only runs once it has been said.

Wake mode used to detect the wake word by transcribing every utterance the
VAD opened on and checking whether the text began with the name. The room
opens the VAD all day -- the TV, a cough, a fan -- so in a 43-hour log
whisper ran 536 times on nothing and 101 times on a real turn: 84% waste,
with degenerate decodes of up to 112 repeats on a 30 s hum. None of that
audio needed a 289 MB model; it needed a 1.2 MB one.

openWakeWord ships a stock "hey jarvis" model. Measured here on two TTS
voices: every "hey Jarvis ..." clip 0.997-0.999, a bare "Jarvis, ..." in
a male voice 0.622, four near-homophone sentences ("Travis ... service
... jealous", "the harvest ...", "Hey Marvin") all <= 0.005, eight
ordinary commands 0.000, 60 Hz hum 0.000. Cost: 3.0% of one core,
continuous; ~90 MB resident in wake mode only (nothing loads otherwise).

The transcript matcher (wakeword.py) still runs afterwards, on a clip that
now really does start with the name -- so a false fire costs exactly one
whisper run, as every VAD open did before, and a bare-name summons still
opens the follow-up window through the same code path as today.

The one NEW failure: a summons the detector does not hear never reaches
whisper. That is what wake_model_threshold is for. Lower it if she stops
answering to a bare "Jarvis"; the negatives above leave a lot of room.
"wake_model": false restores transcript-only detection.

ponytail: one stock model, one threshold. Training a custom model for
another persona name is openWakeWord's own notebook, not this file.
"""
import numpy as np

from backtalk.config import CFG
from backtalk.vlog import log

_CHUNK = 1280            # 80 ms at 16 kHz: openWakeWord's native hop


class Detector:
    def __init__(self, model_file: str, threshold: float, vad: float):
        from openwakeword.model import Model      # lazy: wake mode only
        kw = {"vad_threshold": float(vad)} if vad else {}
        self.m = Model(wakeword_models=[model_file],
                       inference_framework="onnx", **kw)
        pred = self.m.predict(np.zeros(_CHUNK, dtype=np.int16))
        self.key = next(iter(pred)) if len(pred) == 1 else next(
            k for k in pred if model_file.split(".")[0] in k)
        self.thr = float(threshold)
        self.buf = np.zeros(0, dtype=np.int16)
        self.m.reset()

    def feed(self, frame) -> bool:
        """One mic frame (int16 mono 16 kHz, any length) -> did it fire?"""
        self.buf = np.concatenate([self.buf, np.asarray(frame, dtype=np.int16)])
        fired = False
        while len(self.buf) >= _CHUNK:
            chunk, self.buf = self.buf[:_CHUNK], self.buf[_CHUNK:]
            if self.m.predict(chunk)[self.key] >= self.thr:
                fired = True
        if fired:
            self.m.reset()
            self.buf = self.buf[:0]
        return fired


_det = None      # Detector once loaded; False if it cannot load
_no_model = set()   # names already logged as having no audio model


def enabled(name: str) -> bool:
    """Is the audio gate usable for THIS wake name? Loads on first ask.

    The stock model hears "hey jarvis" and nothing else, and the theme
    system renames the persona at runtime (SHODAN answers to "shodan").
    A gate whose model does not know the current name would never fire,
    and then whisper never runs and she is simply deaf -- found the hard
    way. So: no matching model, no gate, transcript matching as before.
    """
    global _det
    if not CFG.get("wake_model", True):
        return False
    stem = str(CFG["wake_model_file"]).split(".")[0].lower()
    name = (name or "").strip().lower()
    if not name or name not in stem:
        if name not in _no_model:
            _no_model.add(name)
            log(f"[wake] no audio model for the name {name!r} (have "
                f"{CFG['wake_model_file']}); matching the name in "
                f"transcripts instead")
        return False
    if _det is None:
        try:
            _det = Detector(CFG["wake_model_file"],
                            CFG["wake_model_threshold"], CFG["wake_model_vad"])
            log(f"[wake] listening for the name in the audio "
                f"({CFG['wake_model_file']}, threshold "
                f"{CFG['wake_model_threshold']})")
        except Exception as e:
            log(f"[wake] audio detector unavailable ({type(e).__name__}: "
                f"{str(e)[:80]}); matching the name in transcripts instead")
            _det = False
    return _det is not False


def feed(frame) -> bool:
    return _det.feed(frame)
