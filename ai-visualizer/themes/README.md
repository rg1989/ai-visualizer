# Themes

A theme is a folder. Drop `themes/<id>/theme.js` in and the theme is in the
pickers on the next reload — nothing to register, no manifest to edit. Delete
the folder and it is gone. The server lists whatever is on disk (`GET
/themes.js`), so a theme you never told anyone about still works, and a theme
you keep out of the repo simply is not there for anyone else.

`<id>` must be letters and digits only, 32 characters max: the id is what the
server stores as the glass-wide choice and what goes into the `<script src>`
the browser runs.

## The whole contract

```js
/* mytheme */
(() => {
"use strict";
const HERE = new URL(".", document.currentScript.src).href;   // this folder

AVTheme.add("mytheme", {
  label: "MY THEME",        // what the pickers show
  face: "board",            // the faces/<dir> this theme lives on
  wip: true,                // optional: hide it from the pickers
  raw: `@font-face{...}`,   // optional: plain CSS (see "Assets")
  css:    { "--av-accent": "#ff8800" },   // DOM + glass colours
  canvas: { amber: "#ff8800" },           // values the canvas faces read
});
})();
```

Only `label` is really required. Everything else has a sane empty default, so
a two-line theme that just recolours the accent is a legitimate theme.

- **`css`** is injected as `:root[data-theme="<id>"]{ … }`. Pages read every
  token as `var(--av-…, <original value>)`, so anything you leave out keeps
  the shipped look — you override what you care about and nothing else. The
  glass tokens (`--glass-…`) are set here too; `:root[data-theme]` outranks
  `glass-theme.css`'s bare `:root` whatever the load order.
- **`canvas`** is plain JS the faces read at parse time as
  `AVTheme.overrides` — colours the canvas art bakes into sprite atlases and
  consts, which is why a theme change reloads the page rather than repainting.
  `name` in here renames the agent for this theme (SHODAN's board says
  SHODAN). What each face looks for is at the top of its `index.html`.
- **`face`** is the walled garden: a theme and its face are one curated pair,
  and picking the theme moves a face page to its partner. Leave it out and the
  theme rides whatever face you are on.

## Assets

Keep them in the theme folder and resolve them off `HERE` — that is what makes
the folder copyable:

```js
raw: `@font-face{font-family:"Satoshi";src:url("${HERE}Satoshi.ttf") format("truetype")}`,
canvas: { faceImg: HERE + "portrait.jpg" },
```

`raw` is for CSS that cannot live inside a `:root` block — an `@font-face`,
a `@keyframes`. It is injected verbatim, once, when the theme registers.

## Sharing one

Zip the folder. Whoever drops it into their `themes/` has your theme, fonts
and images included. `themes/shodan/` is the fullest worked example (palette,
canvas overrides, chat glyphs, a theme-local image); `themes/matrix/` is the
smallest thing that counts as a theme.

## Keeping one private

Add its folder to `.gitignore` — and its face, if the theme brings its own:

```
themes/mybrand/
faces/mybrand/
```

Nothing tracked names your themes, so the ignore line is the only place it has
to be said — and it is the only thing standing between a private theme and a
public repo, so add it before the folder, not after.

## The one rule for pages

`theme.js` has to stay a plain `<script src="…/theme.js"></script>` — no
`defer`, no `async`. It pulls the theme folders in with `document.write` so
they are all registered before the next `<script>` runs, which is what lets a
face read `AVTheme.overrides` on the line after it loads. Load it late and it
says so in the console instead of blanking the page.

Without the server (opening a face straight off disk) no theme registers and
every page falls back to its own baked-in colours, exactly as it does when
`theme.js` is missing altogether.
