# backtalk: talk to your Claude Code agent out loud.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn GLM's deliberation off, because nothing else can.

z.ai's own docs say GLM-5.3 and GLM-5.3-Flash cannot disable thinking and
that `reasoning_effort` defaults to "max". Both are true of THEIR native
API — and neither knob is reachable from here, because Claude Code speaks
Anthropic's protocol, which has no `reasoning_effort` field at all. Measured
against Alex's key on 2026-08-31: `thinking.budget_tokens` (what /effort
low actually puts on the wire) and a top-level `reasoning_effort` are BOTH
ignored by z.ai's Anthropic-compatible endpoint.

But `{"thinking": {"type": "disabled"}}` — the one thing the docs promise
will error — is accepted there, and honored. Measured, 12 reps per arm:

    plain question    3.67s -> 1.76s   537 -> 0 thinking chars
    with tools        5.47s -> 3.90s   215 -> 0, tool_use still every time
    reasoning question 26.1s -> 2.5s   same quality answer
    and 3 of 12 thinking-ON replies came back EMPTY: the model spent its
    whole token budget deliberating and never reached the answer. That is
    a correctness bug, not a speed preference.

So this is a ~60 line loopback proxy that adds that one field on the way
past. It exists only because the field cannot be set any other way.

It runs as a thread inside backtalk — no second process to launch, nothing
to leave running, and it dies with the voice line. If it ever stops helping,
set zai_disable_thinking false in backtalk.json and provider.py goes back to
talking to api.z.ai directly.
"""
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://api.z.ai/api/anthropic"
# Hop-by-hop and body-framing headers: ours to decide, never copied through.
_SKIP = {"host", "content-length", "transfer-encoding", "connection",
         "accept-encoding", "keep-alive", "upgrade", "te", "trailer",
         "proxy-authorization", "proxy-authenticate"}


class _H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass                      # the voice log is not an access log

    def do_POST(self):
        path = self.path
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        # ONLY the completion endpoint, and only if it really is JSON we
        # understand. Anything else (count_tokens, a future route) is a
        # byte-for-byte passthrough — this proxy must never be the reason
        # a request the CLI knows how to make stops working.
        #
        # SPLIT THE QUERY STRING OFF FIRST. The CLI posts to
        # "/v1/messages?beta=true", never the bare path, so matching the
        # raw self.path silently rewrote NOTHING: every request sailed
        # past with the CLI's own {"thinking": {"type": "adaptive"}}
        # still on it and GLM deliberated on all of them (measured: 81s
        # of dead air and 13k characters of thinking before the first
        # tool call of a build request).
        route = path.split("?", 1)[0].rstrip("/")
        if route.endswith("/v1/messages") and body:
            try:
                obj = json.loads(body)
                if isinstance(obj, dict):
                    obj["thinking"] = {"type": "disabled"}
                    # budget_tokens is what /effort sets, and z.ai ignores
                    # it; leaving it beside "disabled" is a contradiction
                    # some future shim could resolve the wrong way.
                    obj.pop("reasoning_effort", None)
                    body = json.dumps(obj).encode()
            except (ValueError, TypeError):
                pass              # not JSON we understand — send it as-is
        h = {k: v for k, v in self.headers.items()
             if k.lower() not in _SKIP}
        h["Content-Length"] = str(len(body))
        req = urllib.request.Request(UPSTREAM + path, data=body, headers=h,
                                     method="POST")
        try:
            up = urllib.request.urlopen(req, timeout=300)
            status, hdrs = up.status, up.headers
        except urllib.error.HTTPError as e:
            up, status, hdrs = e, e.code, e.headers
        except Exception as e:
            # Upstream unreachable: answer in the shape the SDK expects so
            # it reports a real error instead of a parse failure.
            self._fail(502, f"{type(e).__name__}: {str(e)[:200]}")
            return
        try:
            self.send_response(status)
            for k, v in hdrs.items():
                if k.lower() not in _SKIP:
                    self.send_header(k, v)
            # Chunked by hand: the whole point is that SSE tokens reach the
            # voice line the instant they arrive. Never buffer the body.
            # read1() and NOT read(): read(1024) blocks until a full 1024
            # bytes have arrived, which held ~8 SSE frames hostage per
            # gulp and made a stream that is meant to be word-by-word
            # arrive in lumps. read1 hands over whatever landed.
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:
                chunk = up.read1(65536)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass                  # the CLI hung up (interrupt) — normal
        finally:
            try:
                up.close()
            except Exception:
                pass

    def _fail(self, code, msg):
        body = json.dumps({"type": "error",
                           "error": {"type": "api_error",
                                     "message": msg}}).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass


class _QuietServer(ThreadingHTTPServer):
    """The same server, minus the traceback when the CLI drops a socket.

    Interrupting a turn makes the CLI RESET its keep-alive connections
    rather than close them politely, and socketserver counts that as an
    unhandled error: a twenty-line traceback in the voice log for every
    single barge-in, which reads exactly like a crash and buries the real
    lines. Same fix ai-visualizer's QuietHTTPServer already uses. Only
    client-hangup errors go quiet; a genuine handler crash still prints."""

    def handle_error(self, request, client_address):
        import sys
        if not isinstance(sys.exc_info()[1],
                          (ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, client_address)


_server = None


def start() -> str:
    """Start (once) and return the base URL to point ANTHROPIC_BASE_URL at.
    Port 0 = the OS picks a free one, so two backtalks never collide."""
    global _server
    if _server is None:
        _server = _QuietServer(("127.0.0.1", 0), _H)
        threading.Thread(target=_server.serve_forever, daemon=True,
                         name="zaifast").start()
    return f"http://127.0.0.1:{_server.server_address[1]}"


if __name__ == "__main__":
    # ponytail: one runnable check — does the field actually get added, and
    # is everything else passed through untouched? Uses a stub upstream, so
    # it costs no tokens and needs no key.
    import sys
    seen = {}

    class _Stub(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen["path"] = self.path
            seen["body"] = self.rfile.read(n)
            seen["auth"] = self.headers.get("x-api-key")
            out = b'data: {"ok":true}\n\n'
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"%x\r\n" % len(out) + out + b"\r\n0\r\n\r\n")

    stub = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    globals()["UPSTREAM"] = f"http://127.0.0.1:{stub.server_address[1]}"
    base = start()

    def post(path, payload):
        r = urllib.request.Request(
            base + path, json.dumps(payload).encode(),
            {"Content-Type": "application/json", "x-api-key": "sekrit"},
            method="POST")
        return urllib.request.urlopen(r, timeout=10).read()

    # The CLI's REAL path carries a query string. This is the regression
    # that mattered: matching self.path raw missed it and the proxy
    # became a no-op.
    got = post("/v1/messages?beta=true",
               {"model": "glm-5.3-flash",
                "thinking": {"type": "adaptive"},
                "reasoning_effort": "low",
                "messages": [{"role": "user", "content": "hi"}]})
    sent = json.loads(seen["body"])
    assert sent["thinking"] == {"type": "disabled"}, sent["thinking"]
    assert seen["path"] == "/v1/messages?beta=true", seen["path"]

    got = post("/v1/messages", {"model": "glm-5.3-flash",
                                "thinking": {"type": "enabled",
                                             "budget_tokens": 1024},
                                "reasoning_effort": "low",
                                "messages": [{"role": "user", "content": "hi"}]})
    sent = json.loads(seen["body"])
    assert sent["thinking"] == {"type": "disabled"}, sent["thinking"]
    assert "reasoning_effort" not in sent
    assert sent["messages"] == [{"role": "user", "content": "hi"}]
    assert sent["model"] == "glm-5.3-flash"
    assert seen["auth"] == "sekrit", "auth header must survive the hop"
    assert got == b'data: {"ok":true}\n\n', got
    # a route we do not own is passed through byte for byte
    post("/v1/messages/count_tokens", {"model": "m", "messages": []})
    assert "thinking" not in json.loads(seen["body"]), "must not touch count_tokens"
    assert seen["path"] == "/v1/messages/count_tokens"
    print("zaifast: self-check ok")
    sys.exit(0)
