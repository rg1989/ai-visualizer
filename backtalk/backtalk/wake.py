"""Wake-word gating for mic_mode "wake".

The mic listens exactly like hands-free mode (same VAD, same local
whisper), but an utterance only reaches the agent when it starts with a
wake phrase ("hey jarvis", "jarvis", ...). Everything else is dropped
on the floor, unlogged beyond a debug line — the room can talk all day
without triggering a turn.

Two shapes work, matching how people actually speak:
  "Jarvis."                -> wake chime, then the NEXT utterance
                              (within wake_window_s) is the command
  "Jarvis, open the door." -> one shot: the remainder IS the command

Matching is against whisper's TRANSCRIPT, not raw audio, so no wake
model, no new dependency, and the same CPU cost as open mode. Whisper
decorates transcripts freely — leading dialogue dashes, ellipses,
punctuation glued to words — so matching runs on a normalized word
stream mapped back to the original tokens, and the remainder keeps the
original punctuation because it goes to the agent verbatim. Whisper
also misspells names; wake_aliases lists single-word spellings accepted
in the name's place.
"""

import re

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


def build_matcher(phrases, aliases, name=None, strict=()):
    """Compile the config into a closure: match(text) -> (woke, rest).

    woke is True when the utterance begins with a wake phrase; rest is
    what followed it ("" when the wake word stood alone), in the
    original wording. Aliases substitute for the NAME token — the last
    word of the configured name when given, else of the longest phrase.
    Multi-word aliases are ignored (single words only; whisper's
    mishears are single tokens).

    Returns (woke, rest, maybe). strict lists phrases that are ALSO
    ordinary English — whisper renders "SHODAN" as "show that", and
    "show that to me" is a sentence, not a summons. Alone they wake her;
    followed by more words they do not decide anything on their own, so
    the match comes back woke=False, maybe=True, rest=<the remainder>,
    and the caller can ask a model to read it both ways. Refusing on
    maybe is always safe — it is exactly the old behaviour. The real
    name is never strict, so one-shot commands never pay for this.
    """
    if isinstance(phrases, str):
        phrases = [phrases]
    if isinstance(aliases, str):
        aliases = [aliases]
    canon = sorted({_norm(p) for p in phrases if _norm(p)},
                   key=len, reverse=True)
    if not canon:
        # No phrases = no gate: everything passes through unchanged.
        return lambda text: (True, text.strip(), False)
    target = _norm(name or "").split() or canon[0].split()
    target = target[-1]
    alias = {_norm(a): target for a in aliases
             if _norm(a) and len(_norm(a).split()) == 1}
    lone = {_norm(p) for p in ([strict] if isinstance(strict, str) else strict)
            if _norm(p)}

    def match(text):
        toks = text.split()
        # Flatten each original token into its normalized words (a
        # token can yield zero — a bare dash — or several — "R2-D2"),
        # remembering which original token owns each word, so the
        # remainder can be cut at an original-token boundary.
        flat, owner = [], []
        for i, tok in enumerate(toks):
            for w in _norm(tok).split():
                flat.append(alias.get(w, w))
                owner.append(i)
        undecided = ""
        for phrase in canon:
            p = phrase.split()
            if len(flat) >= len(p) and flat[:len(p)] == p:
                if len(flat) == len(p):
                    return True, "", False
                cut = owner[len(p)]
                if owner[len(p) - 1] == cut:
                    # phrase ended mid-token ("jarvis's"): consume it
                    cut += 1
                rest = " ".join(toks[cut:]).strip()
                if phrase in lone:
                    # A mishear carrying a sentence -- unless whisper
                    # punctuated it. "Show them, show me a map" is not a
                    # thing anyone says; that comma is the person
                    # addressing her, and whisper marks a vocative with
                    # one reliably. No punctuation, no verdict: hold it
                    # and keep scanning, in case the real name also fits.
                    # Sentence-enders only. A hyphen or a dash is NOT a
                    # vocative mark -- whisper hands them back mid-thought
                    # ("show that - and the other one"), which read as
                    # "she was addressed" and woke her on a fragment. A
                    # semicolon joins clauses; it never follows a name.
                    if toks[owner[len(p) - 1]].rstrip("\"')]").endswith(
                            (",", ".", "!", "?", ":")):
                        return True, rest, False
                    undecided = undecided or rest
                    continue
                return True, rest, False
        return False, undecided, bool(undecided)

    return match


if __name__ == "__main__":
    def W(fn):   # the (woke, rest) view, for the checks that predate `maybe`
        return lambda t: fn(t)[:2]

    m = W(build_matcher(
        ["jarvis", "hey jarvis", "hi jarvis", "hello jarvis",
         "okay jarvis", "ok jarvis"],
        ["jervis", "jarvus", "jarves"], name="Jarvis"))
    assert m("Jarvis") == (True, "")
    assert m("  Hey, Jarvis!  ") == (True, "")
    assert m("- Jarvis?") == (True, "")                     # dialogue dash
    assert m("... Hey Jarvis") == (True, "")                # ellipsis
    assert m("Jarvis, what's the weather?") == (True, "what's the weather?")
    assert m("hello jarvis turn on the lights") == (True, "turn on the lights")
    assert m("OK Jervis, status report.") == (True, "status report.")
    assert m("I told Jarvis about it yesterday") == (False, "")
    assert m("Travis, come here") == (False, "")
    assert m("jar of pickles") == (False, "")
    assert m("") == (False, "")
    assert m("...") == (False, "")                          # punctuation only
    # name with punctuation, and a custom phrase list
    m2 = W(build_matcher(["hey r2-d2", "r2-d2"], [], name="R2-D2"))
    assert m2("Hey R2-D2, beep twice") == (True, "beep twice")
    assert m2("R2D2 report") == (False, "")   # normalized differently: no match
    # string-typed config values survive
    m3 = W(build_matcher("computer", "komputer", name="Computer"))
    assert m3("Computer, lights.") == (True, "lights.")
    assert m3("Komputer status") == (True, "status")
    # no phrases: gate off, original text passes through
    m4 = W(build_matcher([], []))
    assert m4("anything at all") == (True, "anything at all")
    # strict phrases: whisper's two-word mishears of "SHODAN", which are
    # also real English, so they only summon her when they stand alone
    alt = ["show that", "hey show that", "show dan", "hey show dan",
           "show them", "hey show them"]
    m5 = build_matcher(["shodan", "hey shodan"] + alt, ["showdan"],
                       name="SHODAN", strict=alt)
    assert m5("Show that.") == (True, "", False)         # the bare name
    assert m5("Hey, show that.") == (True, "", False)
    assert m5("Showdan") == (True, "", False)            # single-word alias
    assert m5("SHODAN, what time is it?") == (True, "what time is it?", False)
    assert m5("Shodan, show that to me") == (True, "show that to me", False)
    # punctuated mishear = a vocative, woken with no model in the loop
    assert m5("Show them, show me a map of Tel Aviv.") == (
        True, "show me a map of Tel Aviv.", False)
    assert m5("Show that. What time is it?") == (
        True, "What time is it?", False)
    # ambiguous: never woken by the table alone, handed up for a verdict
    assert m5("Show that to me") == (False, "to me", True)
    assert m5("show that report again") == (False, "report again", True)
    assert m5("Hey show that to me") == (False, "to me", True)
    # not a wake phrase at all: no verdict to ask for
    assert m5("that show was good") == (False, "", False)
    assert m5("showing that off") == (False, "", False)
    print("wake.py self-check: all assertions passed")
