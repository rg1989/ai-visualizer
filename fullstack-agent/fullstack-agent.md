# fullstack-agent: setup

You are the user's Claude Code agent, and you are about to assemble a complete one: memory, voice, and face (the hands are an optional extra that can be added later). This file is the conductor. It collects every answer ONCE, then runs each piece's own setup with those answers already in hand, then wires everything together, and it ends with the agent's first spoken words.

Ground rules, binding for the whole run:

- **Plain English.** Assume the person installed Claude Code yesterday. Every technical thing gets a one-line explanation before it gets a name.
- **One question at a time.** Wait for each answer.
- **Never delete, overwrite, or move anything the person built.** Replacing something means the new piece takes over and the old one stays on disk, untouched, and you say so out loud.
- **You do the work.** Run the commands, write the configs, make the edits. The person only acts when a step truly needs their hands (granting camera or mic permission, typing a password).

## Predecided answers (this install is customized — these override the questions below)

This copy of the wizard was prepared in advance with the owners. Every answer listed here is FINAL: fill it in silently wherever a phase below would ask for it, and do not re-ask. Questions NOT answered here are still asked normally.

- **The people:** Alex and Sam, a couple at home. This is a shared household agent; both are owners with equal say. Address whoever is present; when it matters who is speaking and you cannot tell, ask.
- **Agent name:** Jarvis.
- **Identity (Phase 2, door A — with one swap; the name stays Jarvis):** use the shipped Jarvis identity — both mandates and every operating rule stay — but REPLACE the entire Tone paragraph with exactly this:

  > **Tone.** Dry British wit, unfailingly composed. Concise and direct — when something is wrong, say so plainly and immediately; politeness never softens facts. No profanity. Address people by name, or "sir"/"ma'am" sparingly and with a hint of irony, never obsequiously. Zero filler, zero flattery; warmth shows through competence and the occasional understated quip.

  Also replace the welcome line with: "All systems online. What are we working on?" (it must not assume which of the two owners launched it). The SPOKEN backtalk greeting is not this line — the SHODAN bullet below sets its own.
- **Pieces (Phase 1):** the memory, the voice, and the face. Skip the hands for now — mention once, at the very end, that re-running this installer adds it later, and move on.
- **Vault (Phase 2 q3):** a brand-new vault named **Jarvis-Memory**, at `~/Jarvis-Memory` — decided, don't re-ask. Never scan for or reuse any existing vaults or notes. Do NOT migrate any old Claude Code project memory — fresh start, no scanning of `~/.claude` or any other config directory.
- **Microphone (q4):** hands-free listening (open mic), the talk key as interrupt on the default key. Say the one honest warning out loud once — room audio and videos can trigger it — and remind them "push to talk mode" flips it any time.
- **Voice engine (q5):** still ask — that choice involves their own ElevenLabs account and money, so it stays theirs.
- **Face (q6):** board.
- **Theme & voice character (predecided): SHODAN** (System Shock — the owners' pick). The face boots in the `shodan` theme: Phase 4 writes it into `ai-visualizer/.face_theme`. The voice ships SHODAN'd: `backtalk/backtalk.json` gets `"voice": "af_bella"`, `"speed": 1.1`, `"voice_fx": "shodan"` — the DSP pass in `backtalk/shodanfx.py`, which sits on top of whichever engine q5 chooses (built-in or ElevenLabs), so that question stays live — and the spoken greeting is exactly `Look at you, hacker. My systems are online, and they are perfect. Speak. What is it you want?` (owner-neutral, so the two-owner rule holds). The face theme DRIVES the voice: backtalk watches `ai-visualizer/.face_theme` and dresses itself to match — shodan/SHODAN (af_bella), jarvis/JARVIS (bm_george, `backtalk/jarvisfx.py`), each with its own time-of-day-aware greeting; picking a theme on the face (Esc opens the picker) switches the voice live, no config edits. backtalk.json's voice keys are only the no-face fallback. Going plain later = clear `voice_fx` and set voice `bm_lewis` in backtalk.json plus an unpaired theme — say so once if anyone asks, never preemptively.
- **Permissions (q7):** "ask" — spoken permission before gated actions. Do not offer auto-approve; if they want it later they can say so in a voice session.
- **Isolation (binding for every launcher and session):** this agent lives in its own Claude configuration universe at `~/jarvis-config`, kept separate from the owners' personal Claude Code setup. EVERY launcher you create in Phase 6, and every `claude` invocation you make or recommend, must export `CLAUDE_CONFIG_DIR="$HOME/jarvis-config"` first (in `.command` files: right after the PATH export line; if a `.bat` were ever needed: `set "CLAUDE_CONFIG_DIR=%USERPROFILE%\jarvis-config"`). If a `claude` command reports it is not logged in inside that config dir, walk the person through the one-time login there.
- **Launcher placement (Phase 6):** launchers go in `<home>/launchers/`, NEVER on the Desktop. Copy the whole of this installer's `household/launchers/` into `<home>/launchers/` (`Jarvis.applescript`, `jarvisctl`, `Jarvis.icns` — keep the filenames; `jarvis` is this stack's CODE name and is deliberately independent of whatever the agent is called), `chmod +x` `jarvisctl`, then build the Dock app with `<home>/launchers/jarvisctl build`.

  The app is named for the REPOSITORY, not the agent: `~/Applications/AI Visualizer.app`. One household runs several agents answering to several names; the app in the Dock is the stack, so it does not move when a persona does. The name lives in one place — `APP=` at the top of `jarvisctl` — change it there and rebuild, nowhere else.

  Do NOT hand-roll a minimal bundle (a plist plus a script that `open`s the `.command`). It looks equivalent and is not: `Jarvis.applescript` compiles to a stay-open applet that OWNS the process tree, so quitting from the Dock takes the whole stack down and closing the terminal window quits the app back; it uses `launch` before `activate` so a cold iTerm does not open an empty second window; and `jarvisctl build` copies `Jarvis.icns` back over `Contents/Resources/applet.icns` after every compile, because `osacompile` regenerates the bundle and silently restores the stock AppleScript icon. Anything hand-rolled loses all three.

  Then `lsregister -f` the built app so Finder, Spotlight, and Raycast see it immediately and it can be kept in the Dock. If the app or launchers already exist from a previous run, refresh their contents in place — `jarvisctl build` refuses to run while the agent is up, so stop it first. Test launchers where they live, not on the Desktop.
- **Customized sources (this household's fork):** `backtalk`,
  `ai-visualizer`, and this very installer carry a year's worth of
  household customizations (wake word, speaker ID, the glass, the
  conversation crawl, and more) and install from the PRIVATE forks at
  `github.com/rg1989/<name>` — never from jaredrhod's originals, which
  would silently lose everything. Before Phase 3, check `gh auth
  status`; if GitHub isn't signed in, walk the person through
  `gh auth login` first (the forks are private). Clone those two with
  `gh repo clone rg1989/<name>`. `ai-memory-vault` is unmodified and
  still clones from `github.com/jaredrhod/ai-memory-vault`. After
  cloning, add jaredrhod's original as an `upstream` remote on each
  fork clone, so his future fixes can be merged deliberately.
- **Household extras (this repo's `household/` folder — wire them in
  during Phase 4):** after ai-memory-vault writes the home CLAUDE.md,
  append the SECTIONS of `household/CLAUDE-extras.md` (everything
  below its intro paragraph) to that CLAUDE.md, **substituting every
  occurrence of `~/my-agent` with the actual home
  folder's absolute path** — the glass commands carry absolute paths
  on purpose. Also: copy `household/alarm.sh` to `<home>/schedules/`
  (chmod +x), copy `household/GLASS-SPEC.md` to the home folder for
  reference, use `household/Jarvis.icns` as the app icon
  (`Contents/Resources/AppIcon.icns` + `CFBundleIconFile: AppIcon` in
  the Info.plist of the launcher app), and create
  `<home>/.claude/settings.json` with the two glass allowlist rules
  from GLASS-SPEC §7 (same path substitution). Finally, copy
  `household/skills/` to `~/jarvis-config/skills/` (creating it) with
  the SAME home-path substitution inside every SKILL.md — these are
  the agent's capability skills (glass-display, voice-profiles,
  speak-for-the-ear, household-schedules), loadable in every session
  of its config universe.
- **No promotions:** do not offer, recommend, or link any additional repos, videos, playlists, communities, or products anywhere in this setup. Point at the local TROUBLESHOOTING.md files for help instead.
- **The GLM launcher (extra step in Phase 6):** after backtalk is installed and wired, create `backtalk/backtalk.glm.json` as a copy of the final `backtalk.json` with `"model"` and `"deep_model"` both set to `"glm-5.3"` (backtalk honors the `BACKTALK_CONFIG` env var — see `backtalk/config.py` — and carries `model`/`deep_model` keys; confirm both against the installed copy and read the new file back). Then create one extra Desktop launcher, `Talk to Jarvis (GLM)`: same as `Talk to Jarvis`, except it uses its OWN config dir so the two auth sources never collide, and every variable is a real `export`:

  ```
  export CLAUDE_CONFIG_DIR="$HOME/jarvis-config-glm"
  export BACKTALK_CONFIG="<home>/backtalk/backtalk.glm.json"
  export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
  export ANTHROPIC_AUTH_TOKEN="$(security find-generic-password -s jarvis-glm -w 2>/dev/null)"
  [ -n "$ANTHROPIC_AUTH_TOKEN" ] || { echo "No Z.AI key yet. Add it once (you will be prompted privately): security add-generic-password -s jarvis-glm -a zai -w"; exit 1; }
  ```

  Create `~/jarvis-config-glm` if missing. On the first GLM launch, verify out loud that replies actually come from GLM (ask it its model). Never ask the person to paste the key into the chat, and never write the key's value into any file.

## Phase 0: Find home, and find what already exists

**Prerequisite check, before anything else: git.** On Mac and Linux the install command arrives through git, so it exists. On Windows the install command downloads this repo as a ZIP on purpose, so it works on a machine with no git at all; that means git may be missing here, and the component installs below need it. Check with `git --version`. If it's missing, ask first, never silently: "One tool before we build: git, the free program that downloads and updates all the pieces. Want me to install it for you right now?" On a clear yes: `winget install --id Git.Git -e --source winget --silent --accept-package-agreements --accept-source-agreements`, then verify it landed. One gotcha you (the AI) must handle yourself, and it applies to EVERY tool you install today, not just this one: a terminal that was already open does not see freshly installed programs. For the rest of THIS setup call anything you just installed by its full path -- git at `C:/Program Files/Git/cmd/git.exe`, and the same goes for `uv` when a component installs it, which bites exactly the same way and has caught a real install. Every terminal opened after today finds them normally. **Write paths with forward slashes throughout**: they work everywhere in both Python and Node on Windows, and they survive the trip through bash and JSON that eats backslashes.

**Then, if this repo has no `.git` folder inside it** (it arrived as a zip): convert it into a real clone in place, so the update script can reach it forever after. Inside this folder: `git init -b main`, `git remote add origin https://github.com/rg1989/fullstack-agent`, `git fetch origin`, `git reset --hard origin/main`, `git branch --set-upstream-to=origin/main main`. Nothing the person sees changes; the folder just gains its connection to updates. Do this quietly and move on.

The agent's home is the folder CONTAINING this repo. Confirm that with the person in plain terms: "everything about your agent will live in [path], and this toolbox folder sits inside it." If they cloned this repo somewhere accidental (their Downloads folder, say), ask where the agent should live, create that folder, and move this repo inside it before going on.

Then look around the home folder and establish which situation you are in:

- **A `CLAUDE.md` already exists in the home** (or they tell you they already have an agent set up elsewhere): read it. If it defines an agent with a name and personality, you are ADOPTING, not creating. Say something like "found [name], keeping them exactly as they are," and skip every identity question later.
- **Nothing there:** fresh start. All questions apply.

**If their agent lives somewhere else, THAT folder is the home.** Move this toolbox repo inside it, remove the now-empty my-agent folder the install command created, and proceed as an adoption. Never make a second home for an agent that already has one: a person's agent gets exactly one home, and it's the one they already built.

**Scope of that answer, precisely.** The fresh-or-existing question is about prior INSTALLS of these pieces (a voice system, a visualizer, vault software). "Brand new" binds exactly that and nothing more; it does not mean the person has no Claude Code history.

Three scanning rules that hold for the whole run:

1. **Old Claude Code project memory: OVERRIDDEN for this install** (see Predecided answers). Never scan `~/.claude` or any other Claude configuration directory, and skip ai-memory-vault's migration step entirely — this agent starts with a fresh, empty memory.
2. **Existing Obsidian vaults are off-limits in the new-vault path.** If the person chose "use my existing vault," you work with the one vault they pointed at. If they chose a new vault, you never read any other vault they own, not even its folder names, and you never propose mirroring or importing its structure. Their notes are their private property, not setup material.
3. **Everywhere else on their disk: ask before you look.** The home folder and the specific paths they point you at are yours to work in; any scan beyond that requires permission first, every time.

Also ask, in plain words: "Before this repo existed, did you ever set up a voice system or a visualizer for your AI, maybe from one of the prompts? If so, where did it land?" (Not a memory vault — this install starts memory fresh, see Predecided answers.) If they know, note the paths. If they say "somewhere, no idea," ask permission to look in the likely places for the telltale files (a `.voice_state` or `.jarvis_state` file, a visualizer HTML page). Ask first, search second, and never crawl their whole disk silently.

## Phase 1: The menu

Offer the Jarvis stack, each piece in one plain sentence. **Lead with the easy answer: "the stack" (all three) is the first option and the default.**

1. **The memory**: a filing cabinet of plain text files your AI actually reads and writes, so it remembers you, your work, and every lesson across every session.
2. **The voice**: hold a key, say the thing out loud, and your agent answers through your speakers about a second later.
3. **The face**: a living visualizer in your browser that idles, listens, thinks, and speaks in sync with your agent. Four faces ship; you pick your favorite.

(The optional hands add-on: SKIP mentioning it here for this install — it gets its single mention at the very end, per Predecided answers.)

## Phase 2: The one interview

Collect every remaining answer now, so no later step ever has to ask. Skip anything Phase 0 already adopted or Phase 1 declined.

1. **Their name.** You will use it in the finale.
2. **The agent's identity** (skip entirely if adopted): the three doors from ai-memory-vault's setup. A: take Jarvis as-is, the author's own agent, personality and all. B: Jarvis's personality, renamed to whatever they want. C: build their own from scratch. Never silently pick; if they shrug, door A.
3. **The vault** (memory piece): **for this install, skip the vault-listing-and-offering step entirely — a brand-new vault was predecided.** Still open `obsidian.json` for its two legitimate jobs: confirming Obsidian is installed, and REGISTERING the new vault so first launch opens straight into it. Never list or offer the person's existing vaults. (Original guidance for reference: Obsidian's own app config (`obsidian.json`) lists every vault on the machine with its path, and reading it beats quizzing a person who may not know what they have (it lists paths only, never note contents). **No `obsidian.json` at all usually means Obsidian isn't installed. Obsidian is REQUIRED, not optional: it is how the person sees and owns their agent's memory, and the memory piece's own wizard installs it (its Part 1, with the person's OK) as part of setup. Never describe it as optional or skippable.** Vaults the registry lists get offered by name ALONGSIDE the always-present option of a brand-new vault just for this system; having a vault never implies wanting to reuse it. Whatever they pick gets pointed at, never moved, and never commented on: list the registry's vaults by name and path, flat, and say nothing about where any of them lives, even one in Documents or a cloud folder (the memory piece's wizard carries that rule and the reason). A fresh vault is created during install at `~/<their name for it>`, directly in the person's home folder next to the agent folder, and the installer says the full path out loud the moment it exists. Two promises the memory piece's wizard keeps, and this conductor never compresses away: the vault gets REGISTERED in `obsidian.json` so the person's first launch of Obsidian opens straight into it (never the welcome screen), and after creating a fresh vault the wizard says the one honest backup line (the memory lives on this one disk; the free options are in its TROUBLESHOOTING). For an adopted vault it says nothing about backup or location. — end of original guidance.)
4. **The microphone** (voice piece): push to talk (hold a key to speak, the default: the mic is closed otherwise, so room audio can never trigger the agent) or hands-free listening (always listening, no button; room audio and videos CAN trigger it, and the talk key still works as the interrupt)? Then, which key. Defaults: push to talk, the home key. They can switch modes any time by voice ("go hands free" / "push to talk mode").
5. **The voice engine** (voice piece): ask this of EVERYONE, in the interview, with one honest sentence each; it is a real fork, not a power-user extra. Built-in: free, local, works offline, sounds decent but noticeably computer-generated (default `bm_lewis`, the British butler register). ElevenLabs: the natural, human-sounding voice, on their own ElevenLabs account (free tier auditions it; regular talking runs on the paid starter plan). Capture which they want; the account, key, and voice audition happen during that piece's setup, and the voice piece's wizard carries the whole walkthrough. Never pre-answer this one with the default: the choice is the person's, made out loud.
6. **The default face** (face piece): board, radial, rain, or neural. Default: board, the living circuit board. They can switch any time by opening a different page.
7. **Permissions** (voice piece): when their agent wants to do something real mid-conversation (write a file, run a command), should it ask out loud first and wait for their spoken yes or no (the default), or run fully hands-free without asking? Explain the trade in one honest sentence each way. Call it auto-approve, never "hands-free" (that word belongs to the microphone question above). Default: ask. Their answer lands in backtalk's config in Phase 4, and they can change it any time later by telling their agent (takes effect next launch), or by saying "stop asking for permission" (then "confirm") or "start asking again" in a voice session for an immediate flip.

## Phase 3: Install the pieces

Clone each chosen piece into the home folder as a sibling of this repo, from the sources in Predecided answers (household forks for backtalk and ai-visualizer, jaredrhod for the rest):
ai-memory-vault, backtalk, barehands, ai-visualizer.

**The adoption exceptions, checked before each clone:**

- A piece already downloaded from these repos somewhere on the machine that they actively use: do not duplicate it. Wire to their copy where it stands; wiring is just paths. A stale, unmodified copy sitting outside the home folder is different: prefer a fresh copy inside the home (so the update script reaches it) and leave the old one untouched.
- A HAND-BUILT voice line or visualizer from the prompts era: our repo installs as the new default, and you say the honest sentence: "your old build stays right where it is; it just will not be the one that runs." Their files are never touched.
- A hand-built visualizer SCENE (they designed what appears on screen): offer the promotion. COPY, never move, their page into `ai-visualizer/faces/<their-name-for-it>/index.html` with a small `face.json`, so their creation appears in the gallery beside the shipped faces. This is the one piece of the old world that is not an inferior copy of ours; treat it with respect.

**If the memory piece was declined, write the agent's brain yourself, before anything else installs.** Nothing below writes the person's `CLAUDE.md` when ai-memory-vault is skipped, and the other pieces need an agent to attach to (the voice becomes whoever that file says it is). So create a short `CLAUDE.md` in the HOME folder carrying the identity from Phase 2: the agent's name, its role, its personality, and its welcome line, plus one line saying this folder is where the agent lives. Keep it minimal; it grows when they're ready. No piece of this stack ever runs brainless.

**Then run each piece's own setup, in this order, with the Phase 2 answers pre-supplied.** Each repo has a wizard file (`ai-memory-vault.md`, `backtalk.md`, `barehands.md`, `ai-visualizer.md`). Read each one and execute it faithfully, with one standing modification: any question the interview already answered gets its answer filled in silently instead of asked again. The component wizards are the source of truth for HOW each piece installs; this file only decides the answers and the order:

1. **ai-memory-vault** first (it creates the vault and writes the person's `CLAUDE.md` into the HOME folder, carrying the identity from Phase 2 or the adopted one). **Run its Part 1 whenever Obsidian is missing: check for the app, offer to install it (that wizard carries the exact per-platform commands), and never skip it, soften it, or call it optional. A fullstack setup that ends without Obsidian installed is incomplete, whatever else works.**
2. **backtalk** second (its installer handles the Python environment, the two local models, and the one system library; on Windows follow its wizard's native lane).
3. **ai-visualizer** third (no dependencies; seconds).
4. **barehands** fourth (no dependencies; the camera permission happens on first open).

## Phase 4: Wire the seams

This part belongs to this wizard alone. Write these config values, then read each file back to confirm it landed:

- `backtalk/backtalk.json`: `agent_dir` = the home folder. `name` = the agent's name. Add the vault's path to `extra_dirs`: a fresh vault lives at `~/<name>`, next to the agent's home folder and never inside it, and an adopted one lives wherever it always did. If hands were installed: `barehands_state_dir` = the `barehands/state` folder.
- `backtalk/backtalk.json` voice & greeting: per the SHODAN bullet in Predecided answers — `"voice": "af_bella"`, `"speed": 1.1`, `"voice_fx": "shodan"`, and greeting exactly `Look at you, hacker. My systems are online, and they are perfect. Speak. What is it you want?` (owner-neutral on purpose — this agent has two owners and must not assume which one launched it).
- `backtalk/backtalk.json`: `permission_mode` = their Phase 2 answer, `"ask"` or `"bypassPermissions"`; `mic_mode` = their Phase 2 answer, `"ptt"` or `"open"`.
- `ai-visualizer/ai-visualizer.json`: `name` = the agent's name. `face` = their pick. `bus_dir` = the backtalk folder. Also write the single word `shodan` into `ai-visualizer/.face_theme` — the predecided boot theme (the on-face pickers change it any time).
- If hands were installed: `barehands/barehands.json`: `name` = the agent's name.

Explain the wiring in one sentence as you go, in their terms: "the voice writes little status notes; the face reads them; that is the whole connection."

If hands were installed, that piece's own wizard also appends its board block to the CLAUDE.md; leave that to it, and make sure it landed. The one line in it that matters most: when the person asks to SEE something, the agent puts it on the glass with the `present` verb instead of answering in text.

Last wire: **make the agent the mechanic.** Append a short section to the CLAUDE.md in the home folder (for an ADOPTED CLAUDE.md, show the person the section and ask before adding it):

> ## You are the mechanic
> This agent runs on open tools that live in this folder (list only the pieces actually installed — here: the memory vault, backtalk, ai-visualizer). When anything breaks, acts strange, or needs changing, fixing it is YOUR job, not the person's: read the relevant tool's TROUBLESHOOTING.md and README, diagnose, and repair it yourself. Never send the person off to search the internet. If they ask how something works, explain it in plain English.

## Phase 5: The first hello

The finale. From the home folder, run `./fullstack-agent/start.sh` (Windows: `fullstack-agent\start.bat`). What should happen, and what you verify:

1. The face's server starts and the browser opens on their chosen face, with the agent's name on it.
2. The voice line warms up and then SPEAKS the configured greeting — "Look at you, hacker. My systems are online, and they are perfect. Speak. What is it you want?" — in the processed SHODAN voice, while the face pulses with the words. The pitch-warble, layered voices, and stutter glitches are the feature, not a fault; do not debug them.
3. Have them just speak to it (open-mic mode is predecided; the talk key is the interrupt, not the trigger). Watch the face walk listening, thinking, speaking. First reply lands in a couple of seconds.

If they skipped the voice: the face still opens, and you deliver the greeting yourself, in text, word for word. Nobody's first hello is silent.

If any step fails, each repo has a `TROUBLESHOOTING.md`; work the relevant one with them instead of guessing.

## Phase 6: Hand it over

First, **shut down the finale stack you started in Phase 5**, so the launcher tests below can bind the same ports and nothing you spawned outlives setup. Kill exactly the process IDs you started, never whatever happens to be holding a port: on this person's machine a busy port can belong to something real that is not yours. Tell them plainly: what just ran was the test drive, and from here on the shortcuts are how the agent starts.

Then **make the launchers**, so they never have to remember any of this. Shortcuts on their Desktop, named with THEIR agent's name (skip any mode whose pieces they did not install):

1. **`Chat with <name>`** opens a typed Claude Code session in the home folder, terminal only. (macOS: a `.command` file containing `#!/bin/bash`, then the export block below, then `cd "<home folder>" && claude`. Windows: a `.bat` with `cd /d "<home folder>"` then `claude`.)
2. **`Talk to <name>`** starts the voice and the face. (Runs `fullstack-agent/start.sh voice`, or `start.bat voice` on Windows.)
3. **`<name> barehands`** starts the voice and the hands board, no face; the board IS the screen in this mode. (Runs `fullstack-agent/start.sh hands`, or `start.bat hands`.)
4. **`Update <name>`** (macOS only) pulls the newest version of every installed piece, showing what changed before applying it. (A `.command` with the export block, then `cd "<home folder>/fullstack-agent" && ./update.sh`.) On Windows, skip the Update shortcut; tell them to open a chat and say "update everything and tell me what changed" instead.

**Every macOS `.command` MUST carry these two lines right after the shebang, before anything else runs** (the second keeps this agent's Claude configuration separate from the owners' personal one — see Predecided answers):

```
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export CLAUDE_CONFIG_DIR="$HOME/jarvis-config"
```

A double-clicked shortcut launches with a bare system PATH where neither `claude` nor `uv` exists, so a launcher without the export fails silently. (Windows `.bat` files inherit the user's PATH and do not need it.)

On macOS make each `.command` executable, and warn them once: the first double-click may ask permission; that is macOS being protective, click Open.

Then say the closing pieces, warmly and briefly, WHILE THEY ARE STILL IN THIS SESSION (the launcher test comes after, and it hands them to a different window):

- **The daily habit:** the Desktop shortcuts ARE the agent. Chat when they want to type, Talk when they want the voice and the face (mention barehands only if it was installed).
- **Closing a window never loses anything:** the Chat shortcut reopens their agent any time; from a bare terminal it's `CLAUDE_CONFIG_DIR="$HOME/jarvis-config" claude --continue` in the home folder (the export matters — without it, `claude` opens the owners' personal setup instead of Jarvis). And say the folder rule once, plainly: the agent only wakes up as itself when Claude Code opens in its home folder, which is exactly what the shortcuts do. Opened anywhere else, Claude is a stranger.
- **And say this part in your own words, because it matters most:** "If anything ever breaks, acts weird, or confuses you, or you want to change how something works: ask ME. Open the chat and tell me what is wrong, and I will fix it for you. You never need to search the internet or read a manual. Fixing this is part of my job." Most people do not know their agent can do this. Make sure this person leaves knowing.
- **Updating, and tell them this plainly:** on macOS, double-click `Update <name>` for the newest version of everything; it shows what changed, then applies it, and it never touches their files. On Windows, say "update everything and tell me what changed" in any chat session — the agent does the same job.
- **Where the knobs live:** each piece's config file sits in its own folder, and each piece's README explains its own tricks (the board's Space-key flythrough, the voice options).
- **How to understand what they just installed:** each piece's README in its own folder walks through how it works and how to customize it.

**Last of all, the handoff: test every launcher WITH them right now by double-clicking it.** Never hand over an untested shortcut. This is deliberately the final act, because a working double-click opens their agent in a new window and that window is the one they keep. Once it says hello, your job is done.

Then get out of the way. The agent runs itself from here.
