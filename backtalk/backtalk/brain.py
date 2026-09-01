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
"""The warm brain — a persistent Claude session via the Agent SDK,
streaming.

One ClaudeSDKClient lives for the whole voice session: no per-turn
process spawn, no per-turn context reload. Partial-message streaming
means sentences are yielded the moment they're complete, so the mouth
starts speaking while the rest of the thought is still forming.

The session's cwd is YOUR agent's folder (agent_dir in backtalk.json) —
whatever CLAUDE.md lives there defines who is speaking. backtalk adds
only the spoken-delivery discipline (config.DISCIPLINE): the medium,
never the character.
"""
import asyncio
import os
import re
import warnings
from datetime import datetime

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

try:
    from claude_agent_sdk import CanUseToolShadowedWarning
except ImportError:                       # older SDKs: nothing to silence
    CanUseToolShadowedWarning = None

from backtalk import signals
from backtalk.config import CFG, DISCIPLINE
from backtalk.vlog import log

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


# ---------------------------------------------------------- tool lines
# A tool call is a WAIT, and an unexplained wait reads as a crash. The
# model may or may not narrate it; these build the line from the call
# itself, so the face always has something true to show. Present tense,
# plain words, short enough for the status pill.
def _host(u) -> str:
    """The bare site out of a URL — 'reuters.com', not an address."""
    if isinstance(u, (list, tuple)):
        u = u[0] if u else ""
    h = str(u or "").split("//", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    return h[4:] if h.startswith("www.") else h


def _basename(p) -> str:
    p = str(p or "").replace("\\", "/").rstrip("/")
    return p.rsplit("/", 1)[-1] or "a file"


def _short(s, n=34) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "\u2026"


def _narrate(tool: str, tool_input) -> str:
    """One present-tense line for the glass: what she is doing RIGHT NOW.

    Built from the tool call, never from the model — the whole point is
    that the blank wait stops being blank, so it has to hold even when
    the model says nothing at all. "" means nothing worth showing, which
    also clears whatever the last tool put up."""
    d = tool_input if isinstance(tool_input, dict) else {}
    short = str(tool or "").rsplit("__", 1)[-1]     # drop any mcp prefix
    low = short.lower()
    if low in ("todowrite", "structuredoutput", "exitplanmode"):
        return ""                     # instant; nobody is waiting on it
    if "search" in low:
        q = d.get("query") or d.get("q")
        return f"searching for {_short(q)}" if q else "searching the web"
    if "research" in low:
        q = d.get("query") or d.get("topic")
        return f"researching {_short(q)}" if q else "researching"
    if low == "webfetch" or "extract" in low or "crawl" in low \
            or low.endswith("map"):
        h = _host(d.get("url") or d.get("urls"))
        return f"reading {_short(h)}" if h else "reading a web page"
    if low in ("read", "notebookread"):
        return f"reading {_short(_basename(d.get('file_path')))}"
    if low in ("write", "edit", "multiedit", "notebookedit"):
        return "editing " + _short(_basename(d.get("file_path")
                                             or d.get("notebook_path")))
    if low in ("glob", "grep"):
        return "looking through your files"
    if low == "bash":
        desc = d.get("description")
        if desc:
            return _short(desc, 40)
        cmd = " ".join(str(d.get("command", "")).split())
        first = (cmd.split() or [""])[0].rsplit("/", 1)[-1]
        return f"running {_short(first, 24)}" if first else "running a command"
    if low in ("task", "agent"):
        return _short(d.get("description") or "working on it", 40)
    if low == "skill":
        return f"using the {_short(d.get('skill'), 24)} skill"
    return f"using {_short(low.replace('_', ' '), 30)}"


SESSION_FILE = os.path.join(CFG["signals_dir"], ".backtalk_session")


class WarmBrain:
    def __init__(self, model: str | None = None, can_use_tool=None,
                 resume_id: str | None = None, on_tool=None):
        # Full model id ON PURPOSE — never a bare alias. The SDK
        # resolves aliases through its own bundled CLI and can silently
        # land on an older model.
        self.model = model or CFG["model"]
        # The spoken permission gate (main.py builds it). Wired at
        # connect in EVERY mode, so a live mode flip needs no reconnect;
        # bypass simply never consults it.
        self._can_use_tool = can_use_tool
        # Called with a short line each time a tool starts, and with ""
        # when it finishes. main.py points it at signals.set_stage, so
        # the face says what the wait is for.
        self._on_tool = on_tool
        # Session usage, spoken on request ("usage report").
        self.session = {"turns": 0, "out_tokens": 0, "in_tokens": 0,
                        "cost": 0.0}
        self._client: ClaudeSDKClient | None = None
        # The session to reattach to at the FIRST start only (config key
        # resume_last_session). Consumed on use: a desync rebuild in
        # reset_turn() must always start FRESH: a rebuild means a turn
        # went sideways mid-stream, the wrong moment to gamble on
        # reattaching. (Community proposal, issue #1.)
        self._resume_id = resume_id
        # True while a query's response hasn't been consumed through its
        # ResultMessage — i.e. the shared message pipe may hold leftovers.
        self._dirty = False

    def _tool(self, line: str):
        """Publish (or clear) the tool line. Never breaks a turn."""
        if not self._on_tool:
            return
        try:
            self._on_tool(line)
        except Exception:
            pass

    async def start(self):
        mode = CFG["permission_mode"]
        if mode == "default":
            mode = "ask"     # legacy alias, see config.py
        # backtalk's "ask" = the SDK's "default" mode with gated calls
        # routed to the spoken can_use_tool gate.
        sdk_mode = "default" if mode == "ask" else mode
        if sdk_mode == "bypassPermissions" and self._can_use_tool \
                and CanUseToolShadowedWarning:
            # Deliberate auto-approve: the SDK warns that the callback is
            # shadowed. That IS the chosen behavior, so boot quietly.
            warnings.filterwarnings("ignore",
                                    category=CanUseToolShadowedWarning)
        resume, self._resume_id = self._resume_id, None   # consume once

        def _opts(rid):
            return ClaudeAgentOptions(
                cwd=CFG["agent_dir"],
                model=self.model,
                system_prompt={"type": "preset", "preset": "claude_code",
                               "append": DISCIPLINE},
                include_partial_messages=True,
                permission_mode=sdk_mode,
                can_use_tool=self._can_use_tool,
                add_dirs=CFG["extra_dirs"],
                skills=CFG["visible_skills"],
                resume=rid,
            )
        if resume:
            try:
                self._client = ClaudeSDKClient(options=_opts(resume))
                await self._client.connect()
                log(f"[brain] resumed session {resume[:8]}")
                return
            except Exception as e:
                # a stale or invalid saved session must never brick the
                # launch. Fall back to a fresh conversation and say so.
                log(f"[brain] resume failed ({str(e)[:80]}), "
                    f"starting fresh")
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
        self._client = ClaudeSDKClient(options=_opts(None))
        await self._client.connect()

    async def set_permission_mode(self, backtalk_mode: str):
        """Live flip, no reconnect, conversation intact ("ask" maps to
        the SDK's "default", whose gated calls hit the spoken gate)."""
        if self._client:
            sdk_mode = "default" if backtalk_mode == "ask" \
                else backtalk_mode
            await self._client.set_permission_mode(sdk_mode)

    async def context_usage(self):
        """The CLI's own context-window breakdown, or None."""
        try:
            return await self._client.get_context_usage()
        except Exception:
            return None

    def _remember_session(self, rm):
        """Persist the session id after a completed turn, so the next
        launch can reattach (config: resume_last_session). Must never
        break a turn; silence on any failure."""
        if not CFG.get("resume_last_session"):
            return
        sid = getattr(rm, "session_id", None)
        if not sid:
            return
        try:
            with open(SESSION_FILE, "w") as f:
                f.write(sid)
        except OSError:
            pass

    def _tally(self, rm, count_turn=True):
        """Session usage bookkeeping. Must never break a turn."""
        try:
            u = getattr(rm, "usage", None) or {}
            s = self.session
            if count_turn:
                s["turns"] += 1
            s["out_tokens"] += int(u.get("output_tokens") or 0)
            s["in_tokens"] += (int(u.get("input_tokens") or 0)
                               + int(u.get("cache_read_input_tokens")
                                     or 0))
            c = getattr(rm, "total_cost_usd", None)
            if c:
                s["cost"] += float(c)
        except Exception:
            pass

    async def _pull_rate_limits(self):
        """Ask the CLI outright how much of the plan is spent.

        A DIRECT QUERY, not the RateLimitEvent stream. The event fires
        rarely and usually arrives carrying resets_at with no utilization
        at all, so a listener built on it reports nothing most of the
        time -- which is exactly how this feature looked broken for its
        whole life. (Community fix, ai-visualizer issue #1.)

        THIS REACHES PAST THE SDK'S PUBLIC SURFACE ON PURPOSE, and a
        reader should know it rather than discover it. `get_usage` is a
        control request the bundled CLI answers but the SDK never wraps,
        so there is no supported call to make. The supported-looking
        alternative is a dead end and was tested as one: the terminal
        status line never fires in a headless session, so its numbers
        are unreachable from here.

        Which means this can stop working without anyone doing anything
        wrong, and the containment is the point. Every failure is
        swallowed and the readout simply goes quiet. It must never cost
        a turn, so it is also bounded -- an unanswered control request
        would otherwise hang the voice line mid-conversation."""
        if not CFG.get("show_usage"):
            return
        try:
            usage = await asyncio.wait_for(
                self._client._query._send_control_request(
                    {"subtype": "get_usage"}), 5)
            for window in ("five_hour", "seven_day"):
                w = (usage.get("rate_limits") or {}).get(window)
                if not w:
                    continue
                # Two spellings accepted deliberately: this shape is not
                # documented anywhere, so the cheap tolerance is worth
                # more than the tidiness. Both are percentages, and the
                # rest of the pipeline wants a 0..1 fraction.
                pct = w.get("utilization")
                if pct is None:
                    pct = w.get("used_percentage")
                pct = pct / 100 if pct is not None else None
                resets = w.get("resets_at")
                if isinstance(resets, str):
                    resets = int(datetime.fromisoformat(resets).timestamp())
                signals.set_rate_limit(window, pct, resets)
        except Exception:
            pass

    async def command(self, cmd: str) -> str:
        """Run a console slash command (/clear, /compact, /model,
        /effort) through the normal stream and return whatever text the
        CLI answered with (confirmations, errors). Slash-command replies
        arrive as COMPLETE AssistantMessages, not stream deltas, so
        ask_stream cannot see them. Bounded like reset_turn is: this
        stream is not trusted to always deliver, and an unbounded await
        here would deafen the whole voice loop. On timeout the pipe is
        left marked dirty so the next reset_turn drains or rebuilds."""
        self._dirty = True
        await self._client.query(cmd)
        texts = []

        async def _collect():
            async for msg in self._client.receive_response():
                t = type(msg).__name__
                if t == "AssistantMessage":
                    for b in getattr(msg, "content", []) or []:
                        txt = getattr(b, "text", None)
                        if txt:
                            texts.append(txt)
                elif t == "ResultMessage":
                    self._dirty = False
                    self._tally(msg, count_turn=False)
                    self._remember_session(msg)
                    break

        try:
            await asyncio.wait_for(_collect(), 90)
        except asyncio.TimeoutError:
            log(f"[brain] console command timed out: {cmd!r}")
            return "error: the command timed out"
        return " ".join(texts).strip()

    async def interrupt(self):
        if self._client:
            await self._client.interrupt()

    async def reset_turn(self, timeout: float = 8.0):
        """Re-align the message pipe after an interrupted/failed turn.

        THE OFF-BY-ONE BUG, and why this method exists: the SDK client
        has ONE shared message stream and receive_response() stops at
        the FIRST ResultMessage it sees — there is no pairing between a
        query and its response. A cancelled turn stops consuming
        mid-stream, leaving the dead turn's remaining messages
        (including its ResultMessage) buffered. The next query then
        pairs with those leftovers: the first ask lands on the stale
        ResultMessage and yields nothing, and every ask after that
        answers the PREVIOUS question — for the rest of the session.
        So: interrupt the dead turn, then drain the pipe through its
        stale ResultMessage before the next query goes out. No-op when
        the last turn was consumed clean."""
        if not self._client or not self._dirty:
            return
        try:
            await asyncio.wait_for(self._client.interrupt(), 5)
        except Exception:
            pass  # turn may already be over — the drain below is the point

        async def _drain() -> int:
            n = 0
            async for msg in self._client.receive_response():
                n += 1
                if type(msg).__name__ == "ResultMessage":
                    break
            return n

        try:
            drained = await asyncio.wait_for(_drain(), timeout)
            log(f"[brain] interrupted turn drained ({drained} stale messages)")
            self._dirty = False
        except Exception:
            # Can't re-align — rebuild the session rather than run
            # desynced. Loses this voice session's conversation memory;
            # better than answering every question one turn late for the
            # rest of the day.
            log("[brain] stream desynced beyond repair — rebuilding the "
                "session (conversation memory for this session resets)")
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
            await self.start()
            self._dirty = False

    async def stop(self):
        if self._client:
            await self._client.disconnect()
            self._client = None

    async def ask_stream(self, utterance: str):
        """Yield complete sentences as they stream out of the model."""
        self._dirty = True             # in flight until its ResultMessage
        await self._client.query(utterance)
        buf = ""
        async for msg in self._client.receive_response():
            t = type(msg).__name__
            if t == "StreamEvent":
                ev = getattr(msg, "event", {}) or {}
                if ev.get("type") == "content_block_delta":
                    delta = ev.get("delta", {}) or {}
                    if delta.get("type") == "text_delta":
                        buf += delta.get("text", "")
                        # emit any complete sentences
                        while True:
                            m = _SENTENCE_END.search(buf)
                            if not m:
                                break
                            sentence, buf = (buf[:m.end()].strip(),
                                             buf[m.end():])
                            if sentence:
                                yield sentence
                elif ev.get("type") == "content_block_stop":
                    # End of a speech block (e.g. right before a tool
                    # call): flush NOW. Without this, pre-tool filler
                    # ("On it — let me grab that.") sits silent in the
                    # buffer through the whole tool run, then plays
                    # glued to the answer: long dead air, then two
                    # thoughts at once.
                    tail = buf.strip()
                    buf = ""
                    if tail:
                        yield tail
            elif t == "AssistantMessage":
                # Tool calls arrive PARSED here, one message before the
                # tool actually runs — the partial stream carries them
                # only as unfinished JSON deltas. So this is the moment
                # to say what the next silence is for. Text blocks are
                # ignored: the deltas above already spoke them.
                for b in getattr(msg, "content", []) or []:
                    if type(b).__name__ in ("ToolUseBlock",
                                            "ServerToolUseBlock"):
                        self._tool(_narrate(getattr(b, "name", ""),
                                            getattr(b, "input", None)))
            elif t == "UserMessage":
                # The result came back: the wait that line described is
                # over, so stop describing it.
                for b in getattr(msg, "content", []) or []:
                    if type(b).__name__ in ("ToolResultBlock",
                                            "ServerToolResultBlock"):
                        self._tool("")
                        break
            elif t == "ResultMessage":
                self._tool("")         # nothing is running any more
                self._dirty = False    # turn fully consumed — pipe aligned
                self._tally(msg)
                self._remember_session(msg)
                await self._pull_rate_limits()
                break
        tail = buf.strip()
        if tail:
            yield tail


if __name__ == "__main__":
    import sys
    import time

    if "--check" in sys.argv:
        # ponytail: one runnable check for the only branchy thing here.
        assert _narrate("mcp__tavily__tavily_search",
                        {"query": "uk election"}) == "searching for uk election"
        assert _narrate("WebSearch", {}) == "searching the web"
        assert _narrate("mcp__tavily__tavily_extract",
                        {"urls": ["https://www.reuters.com/world/x"]}) \
            == "reading reuters.com"
        assert _narrate("WebFetch", {"url": "https://bbc.co.uk/news"}) \
            == "reading bbc.co.uk"
        assert _narrate("Bash", {"command": "/bin/ls -la",
                                 "description": "List the files"}) \
            == "List the files"
        assert _narrate("Bash", {"command": "/usr/bin/say hi"}) == "running say"
        assert _narrate("Read", {"file_path": "/a/b/notes.md"}) \
            == "reading notes.md"
        assert _narrate("TodoWrite", {"todos": []}) == ""
        assert _narrate("Frobnicate", None) == "using frobnicate"
        assert len(_narrate("WebSearch", {"query": "x" * 200})) <= 48
        assert _host("http://example.com:80/a?b=1") == "example.com:80"
        print("brain: narration self-check ok")
        raise SystemExit(0)

    async def demo():
        b = WarmBrain()
        await b.start()
        for prompt in ("Voice check: greet me in one sentence.",
                       "And what's two plus two, spoken like yourself?"):
            t0 = time.time()
            async for s in b.ask_stream(prompt):
                print(f"  ({time.time()-t0:4.1f}s) {s}", flush=True)
        await b.stop()

    asyncio.run(demo())
