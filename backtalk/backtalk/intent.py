"""The escape hatch from deterministic listening.

Every spoken flow keeps a rule-based fast path — an exact "yes" or
"cancel" costs nothing and works offline. But people don't speak in
phrase tables, and a voice line with a language model for a brain has
no business replying "say yes or no" to "yes, you have permission,
but stop asking in future" (a real field loop). Anything off-script
lands here: the flow's actual question and the person's actual words
go to a small, fast model, which answers with the person's INTENT.

Fail-safe by construction: any error, timeout, or nonsense reply
returns intent "other", and the caller falls back to exactly its old
deterministic behavior. This can therefore only ever make the system
more understanding, never less safe — and it interprets meaning, it
never authorizes anything by itself.
"""

import asyncio
import json

from backtalk.config import CFG
from backtalk.vlog import log

INTENTS = ("yes", "no", "cancel", "name", "question", "other")


async def classify(situation: str, utterance: str) -> dict:
    """-> {"intent": one of INTENTS, "name": str | None}. Never raises."""
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
        prompt = (
            "You are interpreting ONE spoken reply inside a voice "
            "assistant's flow. Judge what the person MEANT, however "
            "they phrased it.\n"
            f"Situation: {situation}\n"
            f'The person said: "{utterance}"\n'
            'Reply with ONLY this JSON, nothing else: '
            '{"intent": "yes"|"no"|"cancel"|"name"|"question"|"other", '
            '"name": "<the name they gave, else null>"}. '
            'Use "yes"/"no" only when that is clearly their decision, '
            'whatever words carry it; "name" when their reply is '
            'essentially supplying a (person\'s) name; "question" when '
            'they are asking about the process; "cancel" when they '
            'want out; "other" when none of it is clear.'
        )
        out = ""

        async def run():
            nonlocal out
            async for m in query(prompt=prompt, options=ClaudeAgentOptions(
                    model=(CFG.get("intent_model")
                           or "claude-haiku-4-5-20251001"),
                    max_turns=1)):
                for b in getattr(m, "content", None) or []:
                    t = getattr(b, "text", None)
                    if t:
                        out += t

        await asyncio.wait_for(run(), 25)
        d = json.loads(out[out.find("{"):out.rfind("}") + 1])
        intent = d.get("intent")
        if intent in INTENTS:
            name = d.get("name")
            name = str(name).strip().split()[0].capitalize() \
                if name and str(name).strip() else None
            log(f"[intent] {utterance!r} -> {intent}"
                + (f" ({name})" if name else ""))
            return {"intent": intent, "name": name}
    except Exception as e:
        log(f"[intent] classify failed: {e!r}")
    return {"intent": "other", "name": None}


async def is_summons(name: str, utterance: str, timeout: float = 4.0) -> bool:
    """Did the person call the assistant, or just talk?

    Only asked when the wake table cannot tell: whisper renders "SHODAN"
    as "show that", so an utterance opening that way is either a summons
    or an ordinary sentence, and no phrase list can separate the two.
    The test is substitution — put the name back and see which reading a
    person would actually say. "SHODAN, to me" is nobody's sentence;
    "show that to me" is. Refuses on any error, timeout or unclear
    answer, which is exactly the behaviour without this function, so it
    can only ever recover a turn that was already being dropped.
    """
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
        prompt = (
            f'A voice assistant is named "{name}". Its speech recognizer '
            f'often mis-transcribes that name as other words that sound '
            f'like it, so a transcript may contain those words where the '
            f'person actually said "{name}".\n'
            f'The transcript is: "{utterance}"\n'
            "Read it BOTH ways and decide which one a person would "
            "really say out loud:\n"
            f'  A) a summons: the opening words are the name "{name}", '
            "and anything after it is a command to the assistant.\n"
            "  B) ordinary speech: the words mean what they say, and the "
            "assistant was not addressed at all.\n"
            "If the sentence reads naturally as ordinary English, it is "
            "B. Choose A only when the opening words make sense as the "
            "name and the rest makes sense as something asked of an "
            "assistant.\n"
            'Reply with ONLY this JSON: {"summons": true|false}.'
        )
        out = ""

        async def run():
            nonlocal out
            async for m in query(prompt=prompt, options=ClaudeAgentOptions(
                    model=(CFG.get("intent_model")
                           or "claude-haiku-4-5-20251001"),
                    max_turns=1)):
                for b in getattr(m, "content", None) or []:
                    t = getattr(b, "text", None)
                    if t:
                        out += t

        await asyncio.wait_for(run(), timeout)
        d = json.loads(out[out.find("{"):out.rfind("}") + 1])
        verdict = bool(d.get("summons"))
        log(f"[wake] judged {utterance!r} -> "
            + ("summons" if verdict else "ordinary speech"))
        return verdict
    except Exception as e:
        log(f"[wake] judge failed ({e!r}) — not waking")
        return False


async def compose_greeting(persona: str, exemplar: str,
                           daypart: str = "", timeout: float = 240.0):
    """A fresh in-character opening line, or None.

    The exemplar carries the character — tone, era, attitude — so the
    model has something concrete to vary instead of a name and a guess.
    Returns None on any failure; the caller then keeps the fixed line,
    which is exactly the old behaviour.

    The timeout is deliberately huge: this runs in the background for the
    NEXT launch, so nobody is waiting on it, and the SDK spends most of a
    minute just booting its subprocess. 45s lost the race about half the
    time and cost a launch its fresh line for no reason.
    """
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
        prompt = (
            f"Write ONE fresh greeting that the character {persona} says "
            "when her voice assistant finishes starting up.\n"
            f"This is the canonical version, for tone only — match its "
            f"voice, attitude and era, do not reuse its wording:\n"
            f'  "{exemplar}"\n'
            "Rules: one to three short spoken sentences. It is read "
            "aloud by a TTS engine, so plain prose only — no markdown, "
            "no emoji, no stage directions, no quotation marks around "
            "it. Do not mention starting up, loading or booting more "
            "than the original does. Stay in character absolutely.\n"
            + (f"It is currently {daypart}.\n" if daypart else "")
            + "Reply with the greeting itself and nothing else."
        )
        out = ""

        async def run():
            nonlocal out
            async for m in query(prompt=prompt, options=ClaudeAgentOptions(
                    model=(CFG.get("greeting_model")
                           or CFG.get("intent_model")
                           or "claude-haiku-4-5-20251001"),
                    max_turns=1)):
                for b in getattr(m, "content", None) or []:
                    t = getattr(b, "text", None)
                    if t:
                        out += t

        await asyncio.wait_for(run(), timeout)
        line = " ".join(out.split()).strip().strip('"').strip()
        # a runaway answer is a broken answer: speak the known-good line
        if not (8 <= len(line) <= 400):
            log(f"[greeting] rejected ({len(line)} chars)")
            return None
        log(f"[greeting] composed: {line!r}")
        return line
    except Exception as e:
        log(f"[greeting] compose failed: {e!r}")
        return None
