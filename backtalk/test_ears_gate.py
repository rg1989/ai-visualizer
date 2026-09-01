"""The one check that fails if the noise gates break.

Every case here is a real line from logs/backtalk.log — things whisper
actually invented from room noise, and things it must never throw away.
"""
import math

from backtalk import ears


def test_gates():
    ears.set_prompt("SHODAN")
    # a GLOSSARY line, never a sentence and never the bare name: nobody
    # says this out loud, which is what makes dropping it on sight safe
    assert ears._PROMPT == "SHODAN, voice log, transcript."
    good, junk = (None, -0.3), (0.9, -1.4)     # (no_speech, avg_logprob)

    # --- must be dropped -------------------------------------------------
    assert ears._junk("!", *good)                      # no words at all
    assert ears._junk("...", *good)
    assert ears._junk("Beep.", 0.82, -0.4)             # scored non-speech
    assert ears._junk("Hello?", 0.1, -1.31)            # whisper's own doubt
    # NaN = mlx computed no logprob, not "whisper doubted it". With no
    # score to judge on, length is the evidence: whisper's silence ghosts
    # are stock fragments, real requests are sentences.
    assert ears._junk("You", 0.1, math.nan)
    assert ears._junk("Thank you.", 0.1, math.nan)
    assert ears._junk("Bye.", 0.3, math.nan)

    # --- must survive ----------------------------------------------------
    assert not ears._junk("SHODAN", *good)             # the wake word itself
    assert not ears._junk("Yes.", *good)               # a real short reply
    assert not ears._junk("where is Citadel Station", *good)
    assert not ears._junk("Beep.", *good)              # confident -> believed
    assert not ears._junk("Hello?", None, None)        # no scores != noise
    # the regression this split fixes: "SHODAN, show me a map of Tel Aviv"
    # arrived as this, scored no_speech 0.01 / logprob NaN, and was binned
    assert not ears._junk("! Show me a map of Tel Aviv.", 0.01, math.nan)

    # --- whisper's repetition loop ---------------------------------------
    # Scores like clean speech (the words ARE confidently predicted --
    # that is the failure), so only its shape gives it away. The real
    # question at the front survives; the seizure behind it does not.
    assert ears._unloop("What's the name of the people? The people? "
                        + "I don't know. " * 27) == (
        "What's the name of the people? The people?", "27x 'i don t know'")
    assert ears._unloop(("I'm sorry, " * 56).strip())[0] == ""
    # a person repeating themselves twice is a person
    assert ears._unloop("It's a little bit too much. "
                        "It's a little bit too much.")[1] == ""
    for ok in ("no no no", "very very very good", "Yes.",
               "Shodan, show me a map of Tel Aviv.",
               "I am going to the shop and then I will come back."):
        assert ears._unloop(ok) == (ok, ""), ok

    # --- the hint coming home --------------------------------------------
    # UNCONDITIONAL, never score-gated: an echo is the model quoting itself
    # so it scores like clean speech. Trusting the score here turned every
    # quiet moment into a spoken wake word and woke her in a loop.
    assert ears._finish("SHODAN, voice log, transcript.", []) == ""
    assert ears._finish("shodan voice log transcript", []) == ""
    assert ears._finish("SHODAN", []) == "SHODAN"      # the summons lives

    # min() on no_speech, mean on logprob: one bad segment must not sink it
    ns, lp = ears._scores([{"no_speech_prob": .9, "avg_logprob": -.2},
                           {"no_speech_prob": .1, "avg_logprob": -.4}])
    assert (ns, round(lp, 3)) == (0.1, -0.3)


def test_pronunciation():
    """All-caps names are initialisms to espeak — the persona would spell
    its own name out loud on every single line."""
    from backtalk.mouth import _phonemize_names as fx
    assert fx("I am SHODAN.") == "I am [Shodan](/ʃoʊˈdæn/)."
    assert fx("shodan and Shodan") == \
        "[Shodan](/ʃoʊˈdæn/) and [Shodan](/ʃoʊˈdæn/)"
    # real initialisms are spelled out on purpose — never touch them
    assert fx("The API and CPU are fine.") == "The API and CPU are fine."


def test_unsummoned():
    """Cold wake mode is the ONE state where a transcript is speculative:
    the face stays dark, so room chatter never looks like a false wake."""
    import tempfile
    from backtalk import signals
    # the bus is a live directory: write the probes somewhere harmless
    signals._MIC_FILE = tempfile.mktemp()
    signals._STATE_FILE = tempfile.mktemp()
    signals._BH_STATE = ""
    signals.set_mic("wake")
    signals.set_state("idle")
    assert signals.unsummoned()
    signals.set_state("listening")            # summoned: window or follow-up
    assert not signals.unsummoned()
    for mode in ("open", "ptt"):              # every utterance is a turn
        signals.set_mic(mode)
        signals.set_state("idle")
        assert not signals.unsummoned()


def test_hint_echo_is_cut_even_with_litter_stapled_to_it():
    """Two traps, one guard.

    (1) The hint exists because small.en cannot spell the persona's name, so
    the echo test must not demand that name come back spelled right. Field-
    caught: "Shoredan, voice log, transcript." on room noise slipped an exact
    compare, and the wake matcher -- which DOES forgive the name -- then woke
    on it and killed two turns mid-answer.

    (2) Whisper does not stop at the hint. It drifts on into a stock silence
    ghost, so matching the whole string still let
    "<name>, voice log, transcript. Thank you." through. So the guard TRIMS
    rather than answering yes/no: what matters is that the NAME is gone from
    the remainder, because the name is what wakes her."""
    from backtalk import ears
    ears.set_prompt("SHODAN")
    # cut to nothing
    for ghost in ("SHODAN, voice log, transcript.",
                  "Shoredan, voice log, transcript.",   # the reported ghost
                  "Show dan, voice log, transcript.",   # two-word rendering
                  "shodin voice log transcript",
                  "SHODAN, voice log, transcript. "     # doubled: one rep
                  "SHODAN, voice log, transcript."):    # under LOOP_MIN_REPS
        kept, note = ears._strip_hint_echo(ghost)
        assert note and kept == "", (ghost, kept)
    # cut down to the litter -- which no longer carries the wake word.
    # "Thank you." / "Bye." / "You" are the three ghosts this file's own
    # test_gates already pins as measured whisper output on silence.
    for ghost, rest in (("SHODAN, voice log, transcript. Thank you.", "Thank you."),
                        ("SHODAN, voice log, transcript. Bye.", "Bye."),
                        ("Shodin, voice log, transcript. You", "You")):
        kept, note = ears._strip_hint_echo(ghost)
        assert note and kept == rest, (ghost, kept)
        assert "shodan" not in ears._norm(kept)

    ears.set_prompt("JARVIS")                 # echoes 5 clips in 5, measured
    assert ears._strip_hint_echo("Jarvus, voice log, transcript.")[0] == ""

    # ...and real speech that brushes the same words must be left ALONE.
    # The run has to START the clip, which is what makes these safe.
    ears.set_prompt("SHODAN")
    for real in ("what is the voice log transcript",
                 "show me the voice log transcript",
                 "SHODAN, what is the weather today",
                 "SHODAN", "voice log", "transcript"):
        assert ears._strip_hint_echo(real) == (real, ""), real


def test_hint_is_withheld_on_non_speech():
    """The hint is what makes small.en spell the persona's name (measured
    3 clips in 4 with it, 0 in 4 without) -- and on a NON-speech clip it is
    the very thing whisper hands back as a phantom sentence. Disjoint inputs,
    so gate it: prime the decoder only when the clip is speechlike.

    BOTH halves are needed. webrtcvad alone calls loud broadband hiss a
    confident 1.00, same as a voice; spectral flatness alone has no opinion
    about a click. Together they were exact on every clip measured."""
    import numpy as np
    from backtalk import ears
    rng = np.random.default_rng(5)
    n = 16000 * 2
    quiet = [("digital silence", np.zeros(n, dtype=np.int16)),
             ("60Hz mains hum",
              (np.sin(np.arange(n) * 2 * np.pi * 60 / 16000)
               * 0.02 * 32768).astype(np.int16))]
    # the case webrtcvad gets WRONG on its own -- it scores these 1.00
    for amp in (0.008, 0.02, 0.05):
        quiet.append((f"white noise {amp}",
                      (rng.standard_normal(n) * amp * 32768).astype(np.int16)))
    for label, pcm in quiet:
        assert not ears._speechlike(pcm), label
    # loud hiss really does fool the VAD half: that is why _flatness exists
    loud = (rng.standard_normal(n) * 0.02 * 32768).astype(np.int16)
    assert ears._voiced_ratio(loud) > ears._HINT_MIN_VOICED
    assert ears._flatness(loud) > ears._FLAT_MAX
    # a clip too short to hold one frame is not speech either
    assert ears._voiced_ratio(np.zeros(10, dtype=np.int16)) == 0.0


if __name__ == "__main__":
    test_gates()
    test_pronunciation()
    test_unsummoned()
    test_hint_echo_is_cut_even_with_litter_stapled_to_it()
    test_hint_is_withheld_on_non_speech()
    print("ok")
