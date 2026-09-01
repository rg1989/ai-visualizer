#!/usr/bin/env python3
"""Reusable glass components — data in, a finished card on the glass.

One JSON object on stdin: {"component": "<name>", ...data..., ...glass keys...}
Every component renders in the live face theme; nothing here is styled by hand
at the call site.

Two families, and the split is deliberate:

  note-backed (table, keyvalue, bars) render to a native `note` card, so the
  glass's own CSS dresses them — perfect theme match, real house fonts, free.

  html-backed (stat, status, custom) render to an `html` card, which the face
  shows in an iframe sandboxed `allow-scripts` with an OPAQUE ORIGIN. That
  frame cannot read the page's CSS custom properties, and it cannot reach this
  server AT ALL — not the fonts, not the stylesheet, not /state. The block is
  Private Network Access (an opaque origin may not address the local network),
  not CORS, so adding CORS headers to server.py would not open it. Hence this
  file INJECTS the theme tokens into the fragment and falls back to a system
  font stack. That injection is the whole reason this layer exists: colour and
  size the note card cannot give.

  `custom` is that same shell with the markup left to the caller — the door to
  a card none of the other five covers. The design language it must obey to
  look like them lives in the glass-design skill, not here.
"""
import html
import json
import pathlib
import re
import subprocess
import sys

VIS = pathlib.Path("~/my-agent/ai-visualizer")
GLASS = VIS / "bin" / "glass.sh"

# Mirrors glass-theme.css :root and the --glass-* overrides in theme.js.
# When a theme lands or changes there, change it here — one dict, same keys.
BASE = {
    "--glass-bg": "rgba(4,12,8,.85)",
    "--glass-line": "#35e0ff",
    "--glass-line-dim": "rgba(53,224,255,.28)",
    "--glass-text": "#dce8e4",
    "--glass-text-dim": "#7a938c",
    "--glass-accent": "#3ddc84",
    "--glass-radius": "8px",
    "--glass-radius-sm": "3px",
    "--glass-title-font": '"SF Mono",Menlo,Consolas,monospace',
    "--glass-body-font": '-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
}
THEMES = {
    "jarvis": {"--glass-bg": "rgba(4,9,14,.85)", "--glass-accent": "#3daedc",
               "--glass-text": "#dceaf2", "--glass-text-dim": "#7a8b9c"},
    "shodan": {"--glass-bg": "rgba(7,7,13,.85)", "--glass-line": "#21e846",
               "--glass-line-dim": "rgba(33,232,70,.28)", "--glass-text": "#dcdcc8",
               "--glass-text-dim": "#6b6a85", "--glass-accent": "#21e846",
               "--glass-radius": "3px", "--glass-radius-sm": "2px"},
}
# Status colours are FIXED, never themed, and never travel alone — every use
# ships the word too, so a red/green-blind reader loses nothing.
STATUS = {"ok": ("#0ca30c", "OK"), "warn": ("#fab219", "WARN"),
          "crit": ("#d03b3b", "CRIT"), "off": ("#7a938c", "OFF")}

PASSTHROUGH = ("id", "new", "cell", "span", "ttl", "pin", "title")


# Every theme the face will actually switch to. neural / radial / matrix are
# registered there with an EMPTY css block, so they run on the base tokens —
# they must resolve to BASE, not get quietly dressed as jarvis.
PAGE_THEMES = {"jarvis", "shodan", "neural", "radial", "matrix"}


def active_theme():
    try:
        t = (VIS / ".face_theme").read_text().strip()
    except OSError:
        t = ""
    # theme.js does the same coercion for an unknown stored theme.
    return t if t in PAGE_THEMES else "jarvis"


def tokens(name):
    if name in (None, "", "auto"):
        name = active_theme()
    elif name not in PAGE_THEMES:
        raise SystemExit(f"unknown theme {name!r}; have: auto, "
                         + ", ".join(sorted(PAGE_THEMES)))
    return {**BASE, **THEMES.get(name, {})}


# ---------------------------------------------------------------- helpers
def fence(lines):
    """A fenced block for a note card. The face's markdown subset escapes
    everything inside a fence, so only a stray fence itself can break out."""
    body = "\n".join(str(l).replace("```", "'''") for l in lines)
    return "```\n" + body + "\n```"


def num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


NUMERIC = re.compile(r"^[\s$€£+-]*[\d,. ]+\s*[%kKmMgGbB]*$")


def span_for(lines):
    """Three columns wide, height from the content.

    Width is deliberately NOT derived from the text. Characters per column
    move with the viewport, so any guess is wrong on some screen — and the
    faces reserve their middle, leaving 3-wide gutters, so a 4-wide card
    frequently has nowhere to auto-place at all. Every native type defaults
    to 3 for the same reason. When 3 is too narrow the fix is one call —
    {"a":"move","id":"<component>","span":[6,3]} — and the html components
    reflow into the new width by themselves.

    ponytail: 3 text lines per grid row, measured on an 800px-tall face. That
    ratio moves with the viewport too, so this errs tall on purpose — a card
    with slack looks fine, a short one clips."""
    return [3, min(8, max(2, -(-(lines + 1) // 3)))]


def css_vars(tk):
    return "".join(f"{k}:{v};" for k, v in tk.items())


def frag(tk, body_css, inner):
    """One html fragment: injected tokens, transparent so the card's own
    background shows through, sized in vmin so a resize grows the type."""
    return ("<body><style>:root{" + css_vars(tk) + "--ok:#0ca30c;--warn:#fab219;"
            "--crit:#d03b3b;--off:#7a938c}*{box-sizing:border-box}html,body{height:100%}"
            "body{margin:0;padding:2.5vmin;background:transparent;"
            "color:var(--glass-text);font-family:var(--glass-body-font);"
            "-webkit-font-smoothing:antialiased;overflow:hidden}"
            + body_css + "</style>" + inner + "</body>")


def e(s):
    return html.escape(str(s))


# ------------------------------------------------------------- components
def c_table(d, tk):
    """Rows and columns, aligned. Numeric-looking columns go right."""
    cols = [str(c) for c in d.get("columns") or []]
    rows = [[("" if c is None else str(c)) for c in r] for r in d.get("rows") or []]
    if not cols and rows:
        cols = [""] * len(rows[0])
    n = len(cols)
    rows = [(r + [""] * n)[:n] for r in rows]
    right = [bool(rows) and all(NUMERIC.match(r[i] or "0") for r in rows)
             for i in range(n)]
    w = [max([len(cols[i])] + [len(r[i]) for r in rows]) for i in range(n)]
    def line(cells):
        return "  ".join((c.rjust(w[i]) if right[i] else c.ljust(w[i]))
                         for i, c in enumerate(cells)).rstrip()
    out = [line(cols), "  ".join("─" * x for x in w)] if any(cols) else []
    out += [line(r) for r in rows]
    return {"type": "note", "body": fence(out)}, span_for(len(out))


def c_keyvalue(d, tk):
    """Labels and values someone will read off the screen or copy down."""
    pairs = [(str(k), "" if v is None else str(v))
             for k, v in (d.get("pairs") or [])]
    w = max((len(k) for k, _ in pairs), default=0)
    out = [f"{k.ljust(w)}  {v}" for k, v in pairs]
    return {"type": "note", "body": fence(out)}, span_for(len(out))


def c_bars(d, tk):
    """Ranked magnitude, every bar labelled with its own value — so the card
    is readable from across the room with nothing to hover."""
    items = list(d.get("items") or [])
    if d.get("sort", "desc") == "desc":
        items.sort(key=lambda i: i.get("value") or 0, reverse=True)
    width = int(d.get("width") or 20)
    vals = [i.get("value") or 0 for i in items] or [0]
    top = d.get("max") or max(vals) or 1
    unit = d.get("unit") or ""
    lw = max((len(str(i.get("label", ""))) for i in items), default=0)
    out = []
    for i in items:
        v = i.get("value") or 0
        fill = 0 if top <= 0 else max(0, min(width, round(width * v / top)))
        text = i.get("text") or (num(v) + unit)
        out.append(f"{str(i.get('label','')).ljust(lw)}  "
                   f"{'█' * fill}{'░' * (width - fill)}  {text}")
    return {"type": "note", "body": fence(out)}, span_for(len(out))


def c_stat(d, tk):
    """Big numbers. The one thing a note card cannot do: size and colour."""
    tiles = list(d.get("tiles") or [])
    cells = []
    for t in tiles:
        col, word = STATUS.get(str(t.get("status") or ""), (None, None))
        vcol = f"color:{col}" if col else "color:var(--glass-accent)"
        badge = (f'<span class=b style="color:{col}">{word}</span>' if word else "")
        unit = f'<span class=u>{e(t["unit"])}</span>' if t.get("unit") else ""
        cells.append(f'<div class=t><div class=v style="{vcol}">'
                     f'{e(t.get("value", ""))}{unit}{badge}</div>'
                     f'<div class=l>{e(t.get("label", ""))}</div></div>')
    # Each tile is its own size container and the grid rows are 1fr, so the
    # numerals scale to the box the tile actually got — whatever the card's
    # size and however auto-fit chose to wrap them. Nothing here has to guess
    # the layout, which is what a vmin/tile-count formula got wrong: three
    # tiles stacked in a 3x2 card overflowed and clipped.
    css = ("body{display:grid;gap:1.4vmin;"
           "grid-template-columns:repeat(auto-fit,minmax(min(100%,110px),1fr));"
           "grid-auto-rows:1fr}"
           ".t{container-type:size;min-width:0;overflow:hidden;display:flex;"
           "flex-direction:column;justify-content:center}"
           ".v{font-family:var(--glass-title-font);font-size:clamp(14px,42cqh,76px);"
           "line-height:1.05;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
           ".u{font-size:clamp(8px,15cqh,20px);color:var(--glass-text-dim);"
           "margin-left:.35em}"
           ".b{font-size:clamp(7px,11cqh,14px);font-weight:700;letter-spacing:.08em;"
           "margin-left:.45em;vertical-align:middle}"
           ".l{margin-top:.5em;font-size:clamp(8px,13cqh,16px);"
           "color:var(--glass-text-dim);text-transform:uppercase;letter-spacing:.06em;"
           "white-space:nowrap;overflow:hidden;text-overflow:ellipsis}")
    n = max(1, len(tiles))
    return ({"type": "html", "html": frag(tk, css, "".join(cells))},
            [3, min(8, max(2, -(-n * 2 // 3)))])


def c_status(d, tk):
    """Is everything up. Glyph, name, the state word, and a dim detail."""
    rows = []
    for i in d.get("items") or []:
        col, word = STATUS.get(str(i.get("state") or "off"), STATUS["off"])
        glyph = {"OK": "●", "WARN": "▲", "CRIT": "✕", "OFF": "○"}[word]
        detail = f'<div class=d>{e(i["detail"])}</div>' if i.get("detail") else ""
        rows.append(f'<div class=r><span class=g style="color:{col}">{glyph}</span>'
                    f'<div class=m><div class=n>{e(i.get("name", ""))}</div>{detail}</div>'
                    f'<span class=s style="color:{col}">{word}</span></div>')
    # One size container per row, rows at 1fr: four services on a 3x2 card and
    # ten on a 3x8 both fill the space instead of clipping or shrinking to
    # nothing. No row count is baked into the CSS.
    css = ("body{display:grid;gap:.8vmin;grid-auto-rows:1fr}"
           ".r{container-type:size;display:grid;"
           "grid-template-columns:auto 1fr auto;align-items:center;gap:1.4vmin;"
           "min-width:0;overflow:hidden}"
           ".g{font-size:clamp(8px,30cqh,22px);line-height:1}"
           ".m{min-width:0}"
           ".n{font-size:clamp(10px,34cqh,26px);line-height:1.15;white-space:nowrap;"
           "overflow:hidden;text-overflow:ellipsis}"
           ".d{font-size:clamp(8px,24cqh,18px);line-height:1.15;"
           "color:var(--glass-text-dim);white-space:nowrap;overflow:hidden;"
           "text-overflow:ellipsis}"
           ".s{font-size:clamp(7px,22cqh,16px);font-weight:700;letter-spacing:.08em}")
    return ({"type": "html", "html": frag(tk, css, "".join(rows))},
            [3, min(8, max(2, -(-len(rows) * 2 // 3)))])


def c_metrics(d, tk):
    """Pressure gauges for live system numbers — CPU, memory, disk, load — as
    labelled tracks filled by percentage. Colour comes from thresholds unless
    the caller states a `state` outright, and the numeric value always rides
    on the row, so the card never speaks in colour alone.

    items: [{label, value (0-100), text?, state?}]. `text` overrides the
    printed value ("3.9 load" for a load average that is not a percent); the
    bar still fills by `value`. `state` is ok/warn/crit/off; without one,
    warn starts at 65 and crit at 85 — thresholds, not judgment."""
    rows = []
    for g in d.get("items") or []:
        v = max(0, min(100, float(g.get("value") or 0)))
        state = str(g.get("state") or ("crit" if v >= 85 else
                                       "warn" if v >= 65 else "ok"))
        col, word = STATUS.get(state, STATUS["ok"])
        text = e(g.get("text") if g.get("text") is not None
                 else f"{v:.0f}%")
        rows.append(f'<div class=r><div class=l>{e(g.get("label", ""))}</div>'
                    f'<div class=track><div class=fill style="width:{v}%;'
                    f'background:{col}"></div></div>'
                    f'<span class=v style="color:{col}">{text}</span></div>')
    css = ("body{display:grid;gap:1.2vmin;grid-auto-rows:1fr}"
           ".r{container-type:size;display:grid;"
           "grid-template-columns:minmax(4.5em,auto) 1fr auto;"
           "align-items:center;gap:1.4vmin;min-width:0}"
           ".l{font-size:clamp(8px,26cqh,18px);color:var(--glass-text-dim);"
           "text-transform:uppercase;letter-spacing:.06em;white-space:nowrap;"
           "overflow:hidden;text-overflow:ellipsis}"
           ".track{height:clamp(4px,18cqh,12px);min-width:0;"
           "background:var(--glass-line-dim);border-radius:99px;overflow:hidden}"
           ".fill{height:100%;border-radius:99px}"
           ".v{font-family:var(--glass-title-font);"
           "font-size:clamp(8px,26cqh,18px);font-weight:700}")
    n = max(1, len(rows))
    return ({"type": "html", "html": frag(tk, css, "".join(rows))},
            [3, min(8, max(2, -(-n * 2 // 3)))])


def c_custom(d, tk):
    """A card nothing here has a component for: you bring `html` and `css`, the
    shell brings the theme tokens, the reset, the transparent background and
    --ok/--warn/--crit/--off — so a one-off is dressed by the same machinery as
    stat and status. The design language it has to obey is the glass-design
    skill; this function is only the door to frag().

    `html` is markup you wrote, so it passes through raw. Data you were handed
    does NOT: write {{name}} and put the value in `values`, and it arrives
    escaped by e() the way every other component's data does. An unknown
    {{name}} is left standing on the card, because a visible brace is a better
    bug report than a silent blank. `css` only has its `</style` sealed, the
    way fence() seals a stray fence — it may not end the shell early."""
    vals = d.get("values") or {}
    # One pass, so a substituted value can never be re-scanned for a later key.
    body = re.sub(r"\{\{(\w+)\}\}",
                  lambda m: e(vals[m.group(1)]) if m.group(1) in vals
                  else m.group(0), str(d.get("html") or ""))
    # Case-insensitive: HTML compares rawtext end tags case-insensitively, so
    # a "</STYLE>" would close the shell early and dump the rest of the CSS
    # onto the card as text. In CSS "\\/" is still "/", so nothing renders
    # differently.
    css = re.sub(r"(?i)</(?=style)", r"<\\/", str(d.get("css") or ""))
    # [3,3] is the native html default (server.py TYPE_SPECS); there is nothing
    # to count here, so the skill makes span a deliberate step instead.
    return {"type": "html", "html": frag(tk, css, body)}, [3, 3]


REGISTRY = {"table": c_table, "keyvalue": c_keyvalue, "bars": c_bars,
            "stat": c_stat, "status": c_status, "metrics": c_metrics,
            "custom": c_custom}
# Key values are for copying down, so they linger past the 180s default.
TTL = {"keyvalue": 600}


def build(req):
    name = req.get("component")
    if name not in REGISTRY:
        raise SystemExit(f"unknown component {name!r}; have: "
                         + ", ".join(sorted(REGISTRY)))
    payload, guess = REGISTRY[name](req.get("data") or {}, tokens(req.get("theme")))
    payload["a"] = "show"
    payload["span"] = req.get("span") or guess
    # The glass is a singleton per TYPE, so table / keyvalue / bars — all note
    # cards — would clobber each other. Each component claims its own id
    # instead, which is just its name: showing `bars` twice replaces the bars
    # card and leaves the table alone, and "dismiss bars" needs no lookup.
    # ...except `custom`, which is a category and not a card: a moon-phase card
    # and a departures board would both answer to "custom" and replace each
    # other in silence. `new` is no escape either — that falls through to the
    # shared html-N counter, which is not a name anyone can dismiss by.
    if "id" not in req:
        if name == "custom":
            raise SystemExit('custom needs an "id" naming the subject, like '
                             '"moon-phase" — without one every bespoke card is '
                             'the same card and they overwrite each other')
        if not req.get("new"):
            payload["id"] = name
    if name in TTL and "ttl" not in req and not req.get("pin"):
        payload["ttl"] = TTL[name]
    for k in PASSTHROUGH:
        if k in req:
            payload[k] = req[k]
    return payload


def self_check():
    """The smallest thing that fails if the fiddly bits break. Run:
    ./component.sh --self-check"""
    t = tokens("jarvis")

    # Themes: jarvis recolours, neural is registered but overrides nothing
    # and must land on the BASE tokens rather than being dressed as jarvis.
    assert t["--glass-accent"] == "#3daedc", t["--glass-accent"]
    assert t["--glass-line"] == BASE["--glass-line"], "jarvis leaves the hairline alone"
    assert tokens("neural") == BASE
    assert tokens("shodan")["--glass-accent"] == "#21e846"

    # A numeric column goes right, a wordy one stays left.
    tbl, span = c_table({"columns": ["Item", "Due", "Amount"],
                         "rows": [["Rent", "1st", "$2,400"], ["Gym", "20th", "$58"]]}, t)
    body = tbl["body"].splitlines()          # [0] and [-1] are the fence
    assert body[1].startswith("Item"), body[1]
    assert body[3].endswith("$2,400") and body[4].endswith("   $58"), body[4]
    assert span[0] == 3, span

    # Ragged rows are padded, not crashed on.
    c_table({"columns": ["a", "b", "c"], "rows": [["1"], ["1", "2", "3", "4"]]}, t)

    # Bars are proportional, and the biggest fills the track.
    bars, _ = c_bars({"items": [{"label": "a", "value": 100},
                                {"label": "b", "value": 50},
                                {"label": "c", "value": 0}], "width": 10}, t)
    rows = bars["body"].splitlines()[1:4]
    assert rows[0].count("\u2588") == 10 and rows[0].count("\u2591") == 0, rows[0]
    assert rows[1].count("\u2588") == 5, rows[1]
    assert rows[2].count("\u2588") == 0 and rows[2].count("\u2591") == 10, rows[2]
    # ...sorted biggest-first by default, and left alone when asked.
    assert [r.split()[0] for r in rows] == ["a", "b", "c"]
    unsorted, _ = c_bars({"sort": "none", "items": [{"label": "a", "value": 1},
                                                    {"label": "b", "value": 9}]}, t)
    assert unsorted["body"].splitlines()[1].startswith("a"), "sort:none must not reorder"
    # An all-zero set must not divide by zero.
    c_bars({"items": [{"label": "a", "value": 0}]}, t)

    # A fence in the data cannot break out of the fenced block.
    ev, _ = c_keyvalue({"pairs": [["x", "```\ninjected"]]}, t)
    assert ev["body"].count("```") == 2, ev["body"]

    # HTML components escape their data and carry the theme with them.
    st, _ = c_stat({"tiles": [{"label": "<script>", "value": "1 & 2",
                               "status": "warn"}]}, t)
    assert "<script>" not in st["html"] and "&lt;script&gt;" in st["html"]
    assert "1 &amp; 2" in st["html"]
    assert "--glass-accent:#3daedc" in st["html"], "tokens must be injected"
    assert "#fab219" in st["html"] and ">WARN<" in st["html"], "status word, not colour alone"
    sv, _ = c_status({"items": [{"name": "n", "state": "crit"}]}, t)
    assert ">CRIT<" in sv["html"] and "#d03b3b" in sv["html"]

    # Gauges fill by percentage, carry their number in the row, and pick a
    # state word from thresholds when the caller does not name one.
    mt, _ = c_metrics({"items": [{"label": "cpu", "value": 44},
                                 {"label": "ram", "value": 94, "text": "15/16 GB"},
                                 {"label": "off", "value": 0, "state": "off"}]}, t)
    assert 'width:44.0%' in mt["html"] and 'width:94.0%' in mt["html"], mt["html"]
    assert ">44%" in mt["html"] and ">15/16 GB<" in mt["html"]
    assert "#fab219" in mt["html"], "65-85 must read WARN colour"
    assert "#d03b3b" in mt["html"], "94 must read CRIT colour"
    assert mt["html"].count("#0ca30c") >= 1, "44 stays under 65: OK"
    # A value beyond the rails clamps; the escape still applies to labels.
    mx, _ = c_metrics({"items": [{"label": "<b>x", "value": 140}]}, t)
    assert "width:100%" in mx["html"] and "&lt;b&gt;x" in mx["html"]
    # Tokens injected, like every html component.
    assert "--glass-accent:#3daedc" in mt["html"]

    # Each component owns one card id, so they stop overwriting each other —
    # unless the caller explicitly asked for a second one.
    assert build({"component": "bars", "data": {"items": []}})["id"] == "bars"
    assert "id" not in build({"component": "bars", "new": True, "data": {"items": []}})
    assert build({"component": "bars", "id": "mine", "data": {"items": []}})["id"] == "mine"

    # A bespoke card gets the same shell as stat and status — tokens injected,
    # background transparent, the status colours there for the taking — with
    # its own css inside it and the native html span, because there is nothing
    # here to count.
    cu, span = c_custom({"html": "<p class=x>{{v}}</p>",
                         "css": ".x{color:var(--off)}",
                         "values": {"v": "1 & 2 <script>"}}, t)
    assert "--glass-accent:#3daedc" in cu["html"], "tokens must be injected"
    assert "background:transparent" in cu["html"]
    assert ".x{color:var(--off)}" in cu["html"] and "--off:#7a938c" in cu["html"]
    assert span == [3, 3], span
    # Data goes through e() like every other component's; the markup does not.
    assert "<p class=x>" in cu["html"], "authored markup passes through raw"
    assert "&lt;script&gt;" in cu["html"] and "1 &amp; 2" in cu["html"]
    # A value cannot smuggle in a second placeholder for a later key...
    two = c_custom({"html": "{{a}}{{b}}", "values": {"a": "{{b}}", "b": "X"}}, t)[0]
    assert "&gt;" not in two["html"] and two["html"].count("X") == 1, two["html"]
    # ...and an unknown one stays visible rather than blanking the card.
    assert "{{gone}}" in c_custom({"html": "{{gone}}"}, t)[0]["html"]
    # css cannot end the shell's style element early.
    for bad in ('p::after{content:"</style>"}', 'p::after{content:"</STYLE>"}'):
        esc = c_custom({"css": bad}, t)[0]
        assert esc["html"].lower().count("</style>") == 1, bad

    # `custom` is a category, not a card, so it refuses to default its id to
    # its own name — including via `new`, which would fall to the html-N pool.
    assert build({"component": "custom", "id": "moon",
                  "data": {"html": "x"}})["id"] == "moon"
    for req in ({"component": "custom", "data": {"html": "x"}},
                {"component": "custom", "new": True, "data": {"html": "x"}}):
        try:
            build(req)
            raise AssertionError("custom must refuse to go up unnamed")
        except SystemExit:
            pass

    # Key values linger long enough to be copied down; an explicit ttl wins.
    assert build({"component": "keyvalue", "data": {"pairs": []}})["ttl"] == 600
    assert build({"component": "keyvalue", "ttl": 30, "data": {"pairs": []}})["ttl"] == 30
    assert "ttl" not in build({"component": "keyvalue", "pin": True, "data": {"pairs": []}})

    # Spans stay inside the 12x8 grid however much data arrives.
    big, _ = c_status({"items": [{"name": str(i), "state": "ok"} for i in range(40)]}, t)
    w, h = c_status({"items": [{"name": str(i), "state": "ok"} for i in range(40)]}, t)[1]
    assert 1 <= w <= 12 and 1 <= h <= 8, (w, h)

    print("self-check ok")


def main():
    argv = sys.argv[1:]
    if "--self-check" in argv:
        return self_check()
    dry = "--dry-run" in argv
    try:
        req = json.loads(sys.stdin.read())
    except json.JSONDecodeError as ex:
        raise SystemExit(f"bad JSON on stdin: {ex}")
    payload = build(req)
    if dry:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return
    r = subprocess.run([str(GLASS)], input=json.dumps(payload, ensure_ascii=False),
                       text=True, capture_output=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
