"""Speaker identification: whose voice is this?

Personalization, NEVER authorization: a recording of Alex's voice IS
Alex to this module. Nothing security-relevant may key off the label.

Each utterance's PCM (int16 mono 16 kHz, same buffer the transcriber
gets) is embedded with SpeechBrain's ECAPA-TDNN voiceprint model
(speechbrain/spkrec-ecapa-voxceleb, ~80 MB, local after first
download) and cosine-compared against enrolled profiles in
voices.json beside the config. Everything stays on this machine.

Enrollment: `python -m backtalk.enroll <Name>` (run it while the voice
line is stopped; it records a few sentences and saves the averaged
print). Two profiles and a distinct-sounding household make this an
easy problem; the threshold below is deliberately conservative —
"unknown" is a safe answer, a wrong name is not.
"""

import json
import threading

import numpy as np

from backtalk.config import CFG, REPO
from backtalk.vlog import log

PROFILES_PATH = REPO / "voices.json"
MODEL_DIR = REPO / "models" / "ecapa"
# Cosine similarity floor: ECAPA same-speaker typically lands 0.4-0.7,
# different speakers under ~0.2. Overridable via config.
DEFAULT_THRESHOLD = 0.30
# Below this much captured audio the print is unreliable. The buffer
# includes pre-roll and the closing silence run (~0.7 s of non-speech
# on VAD captures), so this is deliberately above that floor: a bare
# "yes" goes unlabeled instead of misattributed.
MIN_SECONDS = 1.1
RATE = 16000

_model = None
_model_lock = threading.Lock()
_profiles = None


def _load_profiles():
    global _profiles
    if _profiles is None:
        try:
            raw = json.loads(PROFILES_PATH.read_text())
            if not isinstance(raw, dict):
                raise ValueError("top level must be an object")
            _profiles = {name: np.asarray(vec, dtype=np.float32)
                         for name, vec in raw.items()}
        except FileNotFoundError:
            _profiles = {}
        except (OSError, ValueError, TypeError) as e:
            log(f"[voiceid] voices.json unreadable ({e}) — ignoring")
            _profiles = {}
    return _profiles


def enabled() -> bool:
    """Cheap gate for callers: on only when configured AND someone is
    enrolled — no profiles means no model load, no cost at all.
    NEVER raises: this module's whole boundary degrades to 'unlabeled
    turn', and an exception here would instead masquerade as a mic
    failure in the caller's error handling."""
    try:
        return bool(CFG.get("voice_id")) and len(_load_profiles()) > 0
    except Exception as e:
        log(f"[voiceid] enabled() failed: {e!r}")
        return False


def _classifier():
    global _model
    with _model_lock:
        if _model is None:
            import torch  # noqa: F401  (backbone; already a kokoro dep)
            from speechbrain.inference.speaker import EncoderClassifier
            log("[voiceid] loading speaker model (first call downloads it)...")
            _model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=str(MODEL_DIR))
            log("[voiceid] speaker model ready")
    return _model


def warm():
    """Load the model ahead of the first turn (never raises; a failure
    surfaces later as an unlabeled turn, not a dead voice line)."""
    try:
        if enabled():
            _classifier()
        elif CFG.get("voice_id"):
            # Configured on, nobody enrolled: say so once in the log
            # instead of being silently inert forever.
            log("[voiceid] voice_id is on but nobody is enrolled — "
                "run `.venv/bin/python -m backtalk.enroll <Name>` "
                "(voice line stopped) to start tagging speakers")
    except Exception as e:
        log(f"[voiceid] warm failed: {e!r}")


def embed(pcm: np.ndarray) -> np.ndarray:
    """int16 mono 16 kHz -> L2-normalized voiceprint vector."""
    import torch
    audio = torch.from_numpy(pcm.astype(np.float32) / 32768.0).unsqueeze(0)
    with torch.no_grad():
        vec = _classifier().encode_batch(audio).squeeze().cpu().numpy()
    return _l2(vec)


def _l2(vec: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(vec))
    return vec / n if n > 0 else vec


def best_match(vec: np.ndarray, profiles: dict, threshold: float):
    """Pure math, model-free (unit-tested below): highest cosine wins
    if it clears the threshold AND beats the runner-up by a margin —
    two close scores mean the print is ambiguous, and 'unknown' beats
    a coin flip between the owners."""
    if not profiles:
        return None, 0.0
    scored = sorted(((float(np.dot(vec, p)), name)
                     for name, p in profiles.items()), reverse=True)
    score, name = scored[0]
    if score < threshold:
        return None, score
    margin = float(CFG.get("voice_id_margin") or 0.08)
    if len(scored) > 1 and score - scored[1][0] < margin:
        return None, score
    return name, score


def identify(pcm: np.ndarray):
    """-> (name | None, score). Never raises: a broken model or short
    clip answers (None, 0.0) and the turn proceeds unlabeled. Also
    never LOADS: only warm() and enrollment load the model, so a
    missing cache can cost a startup download but never stall a live
    turn behind one — turns before the warm finishes go unlabeled."""
    try:
        if _model is None:
            return None, 0.0
        if pcm is None or len(pcm) < MIN_SECONDS * RATE:
            return None, 0.0
        profiles = _load_profiles()
        if not profiles:
            return None, 0.0
        thr = float(CFG.get("voice_id_threshold") or DEFAULT_THRESHOLD)
        return best_match(embed(pcm), profiles, thr)
    except Exception as e:
        log(f"[voiceid] identify failed: {e!r}")
        return None, 0.0


def agreement(vecs, floor=0.45):
    """Do these clips all sound like the same person? -> (ok, sims).
    A clip that disagrees with the group mean is noise or a second
    speaker; enrolling it would poison the profile."""
    mean = _l2(np.mean(np.stack(vecs), axis=0))
    sims = [float(np.dot(v, mean)) for v in vecs]
    return min(sims) >= floor, sims


def rename_profile(old: str, new: str, allow_overwrite=False) -> bool:
    """Re-label an enrolled voiceprint — the print itself never
    changes, so this needs no re-enrollment. Effective immediately.
    Renaming ONTO another profile's name destroys that profile's
    print, so it is refused unless the caller explicitly confirmed
    (allow_overwrite) — catch the mistake, don't silently eat a
    voice."""
    global _profiles
    profiles = dict(_load_profiles())
    if old not in profiles or not new:
        return False
    if new in profiles and new != old and not allow_overwrite:
        return False
    profiles[new] = profiles.pop(old)
    try:
        tmp = PROFILES_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(
            {n: [round(float(x), 6) for x in v]
             for n, v in profiles.items()}))
        tmp.replace(PROFILES_PATH)
    except OSError:
        return False
    _profiles = profiles
    return True


def forget_all() -> None:
    """Delete every enrolled profile (the spoken 'forget all voices',
    after its confirm)."""
    global _profiles
    try:
        PROFILES_PATH.unlink()
    except OSError:
        pass
    _profiles = {}


def save_profile(name: str, vecs) -> None:
    """Average the enrollment vectors and persist. Overwrites any
    existing profile of the same name (re-enrollment is the fix for a
    drifted print)."""
    global _profiles
    mean = _l2(np.mean(np.stack([_l2(v) for v in vecs]), axis=0))
    profiles = dict(_load_profiles())
    profiles[name] = mean
    tmp = PROFILES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(
        {n: [round(float(x), 6) for x in v] for n, v in profiles.items()}))
    tmp.replace(PROFILES_PATH)
    _profiles = profiles


if __name__ == "__main__":
    a = _l2(np.array([1.0, 0.0, 0.0], dtype=np.float32))
    b = _l2(np.array([0.0, 1.0, 0.0], dtype=np.float32))
    near_a = _l2(np.array([0.95, 0.05, 0.0], dtype=np.float32))
    profs = {"Alex": a, "Sam": b}
    assert best_match(near_a, profs, 0.30) == ("Alex", best_match(near_a, profs, 0.30)[1])
    assert best_match(b, profs, 0.30)[0] == "Sam"
    # below threshold -> unknown
    assert best_match(_l2(np.array([0.1, 0.1, 1.0], dtype=np.float32)),
                      profs, 0.30)[0] is None
    # ambiguous (both owners score close) -> unknown
    mid = _l2(np.array([1.0, 1.0, 0.0], dtype=np.float32))
    assert best_match(mid, profs, 0.30)[0] is None
    assert best_match(mid, {}, 0.30) == (None, 0.0)
    print("voiceid.py self-check: all assertions passed")
