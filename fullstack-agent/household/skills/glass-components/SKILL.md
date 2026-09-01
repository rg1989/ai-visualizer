---
name: glass-components
description: Prebuilt cards for the glass (the dashboard) — a table, a key/value readout, ranked bars, big stat tiles, and an up/down status board. Use whenever the thing to show is rows and columns, metrics or a big number, service health, or labelled values to copy, instead of hand-writing an html card. Also covers adding a new reusable component.
---

# Components

Ready-made cards. You supply data; the component supplies the layout, the
sizing, and the live face theme. Five of them, one command, no styling
decisions at the call site — plus `custom`, the door to a card none of them
has, which the **glass-design** skill owns.

    ~/my-agent/components/component.sh <<'JSON'
    {"component":"status","title":"Services","data":{"items":[
      {"name":"backtalk","state":"ok","detail":"voice line up"},
      {"name":"vault backup","state":"warn","detail":"last ran 3 days ago"}]}}
    JSON

One JSON object in, one finished card on the glass. `--dry-run` prints the
payload instead of showing it. Everything the glass itself takes — `title`,
`cell`, `span`, `ttl`, `pin`, `new`, `id` — rides at the top level next to
`component` and behaves exactly as it does in the **glass-display** skill.

## The catalog

| component | data | for |
|---|---|---|
| `table` | `columns` `[str]`, `rows` `[[str]]` | rows and columns — bills, inventory, a comparison |
| `keyvalue` | `pairs` `[[label, value]]` | labelled values someone will read off or copy |
| `bars` | `items` `[{label,value,text?}]`, `max?`, `unit?`, `width?`, `sort?` | ranked magnitude — spend, storage, hours |
| `stat` | `tiles` `[{label,value,unit?,status?}]` | big numbers, 1–6 of them |
| `status` | `items` `[{name,state,detail?}]` | is everything up |
| `metrics` | `items` `[{label,value(0-100),text?,state?}]` | live pressure gauges — CPU, memory, disk; colour from thresholds (warn 65, crit 85) or an explicit `state`, value always printed |
| `custom` | `html`, `css`, `values?` | a shape none of the above has — read **glass-design** first |

`status` on a stat tile and `state` on a status row are `ok` / `warn` /
`crit` / `off`. Each prints its word as well as its colour, so the card
still reads correctly to someone who cannot separate red from green — never
strip the word to save space.

Worked examples:

    {"component":"table","title":"Bills","data":{
      "columns":["Item","Due","Amount"],
      "rows":[["Rent","1st","$2,400"],["Internet","5th","$70"]]}}

    {"component":"keyvalue","title":"Guest Wi-Fi","data":{"pairs":[
      ["Network","Home-Guest"],["Password","see Keychain: wifi-guest"]]}}

    {"component":"bars","title":"Spend","data":{"items":[
      {"label":"Groceries","value":840,"text":"$840"},
      {"label":"Fuel","value":190,"text":"$190"}]}}

    {"component":"stat","title":"House","data":{"tiles":[
      {"label":"Disk free","value":"412","unit":"GB","status":"ok"},
      {"label":"Indoor","value":"21.4","unit":"°C"}]}}

Columns that hold numbers right-align themselves. `bars` sorts biggest-first
unless you pass `"sort":"none"`, and labels every bar with its own value, so
nothing needs hovering — the glass has no pointer.

## The rules that make them behave

- **Theme is automatic.** Omit `theme` and the card is dressed in whatever
  face is live. Pass `"theme":"jarvis"|"shodan"` only to force one
  — for a screenshot, or a card built for a face that is not up yet.
- **Each component owns one card**, id'd by its own name, so `table` and
  `bars` (both note cards underneath) stop overwriting each other. Showing
  `bars` again replaces the bars card; `{"a":"dismiss","id":"bars"}` clears
  it. The id shows in the card's chrome, so it can be read off the screen.
  `"new":true` for a genuine second one.
- **They default to 3 columns wide**, like every native type, because the
  faces reserve their middle and a wider card often has nowhere to land.
  Too narrow? One call: `{"a":"move","id":"stat","span":[6,3]}` — the stat
  tiles reflow into columns and every component resizes its own type to fit.
  Height is guessed from the data and errs tall.
- **`keyvalue` lives 600 s** instead of the usual 180, because its whole job
  is being copied down. Pass `ttl` or `pin` to override.
- Refusals are the glass's own (`no_room`, `over_reserve`) — read them and
  act, exactly as in **glass-display**.

## Reach for a native type instead

A component is for what a plain card cannot do. Do NOT wrap these:

| want | use |
|---|---|
| a checklist, things to tick off | native `list` |
| the day or the week | native `calendar` |
| where somewhere is, a route | native `map` |
| a countdown | native `timer` |
| a paragraph, a note, prose | native `note` |

## Adding one

`components/render.py` holds every component as one small function returning
`(payload, span)`, plus a line in `REGISTRY`. Text-shaped things render to a
native `note` inside a fenced block — they inherit the house style for free
and always match the theme. Only reach for an `html` card when the thing
genuinely needs colour or type size a note cannot give: that card is an
iframe with an opaque origin, so it inherits NOTHING — `render.py` has to
inject the theme tokens, and the house fonts cannot load inside it at all.
Size html components in container-query units (`cqh`) off a `1fr` grid row,
never in `vmin` — the card's short side is not the dimension you want.

Then run `./component.sh --self-check` before showing anything.
