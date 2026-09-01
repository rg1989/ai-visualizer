# Troubleshooting

## The server won't start

- `python3: command not found` on Mac or Linux: install Python 3 from python.org or your package manager. On Windows use `run.bat`, which tries the `py` launcher first and plain `python` second.
- `Address already in use`: something else owns port 8790. Change `"port"` in `ai-visualizer.json` and rerun.

## The face just sits at idle

The face is only as alive as the bus it reads. Work down the chain:

1. `./run.sh --mock speaking` and reload. If the face performs now, the pages are fine and the problem is the bus wiring.
2. Check where your voice line writes its signals. backtalk's default is its own repo folder. Either set `bus_dir` here to that folder, or set `signals_dir` there to this folder. Both configs need a restart after editing.
3. While the voice line talks, the bus folder should contain `.voice_state` and `.voice_waveform` with fresh timestamps. `ls -la` them. If they are not updating, the problem is on the voice line's side.

## No thinking sound

- Browsers block audio until you interact with a page once. Click anywhere on the face, then trigger a thinking state.
- Move the mouse: the SND toggle appears bottom left. Make sure it says SND ON.
- If your voice line plays its own thinking sound, this one stays deliberately silent (that is the `.voice_loading_pid` deference working, not a bug).
- `"thinking_sound": false` in the config disables it everywhere.

## The mic meters run flat

The listening ribbon and MIC gauges want microphone permission, which the browser asks for on first load. Denied permission is fine; the meters just stay flat while everything else works. To grant it later, click the padlock in the address bar and allow the microphone.

## It's choppy

The radial and the neural core are the heaviest faces; the board and the rain are lighter. Chrome and Edge render canvas fastest. A smaller window costs less than fullscreen, and closing other heavy tabs helps more than you'd think. For a frame readout, add `?fps=1` to the board's URL; the neural core draws an always-on FPS number in its chrome. F toggles fullscreen in every face.

## In OBS

Add a browser source with the face URL (for example `http://127.0.0.1:8790/faces/board/index.html`) at your canvas size. The server must be running, and OBS renders its own browser, so grant nothing: the mic meters simply run flat there. If you want the thinking sound in the stream, enable "control audio via OBS" on the source.

## The rain face has no face in it

The face only surfaces while the agent is speaking, and it needs `assets/face.png` to exist: a portrait on a black background, PNG. Swap yours in and reload. If the face loads but looks thin, brighten the portrait; the loader reads pixel brightness as presence.

## glass.sh says the face isn't running

`bin/glass.sh` and `bin/glass-state.sh` talk to the same server that draws the face. "Connection refused" means nothing is listening: start it with `python3 server.py` (or `./run.sh`) and confirm with `bin/glass-state.sh`, which prints the grid header the moment the glass answers.

## The glass scripts hit the wrong port

Both scripts read `"port"` from the `ai-visualizer.json` next to them — the same file and the same precedence as the server (8790 when unset), with deliberately no flag to disagree. If they still miss, the running server was started before your config edit or from a different checkout: restart it and the two ends meet again. A proxy in the environment cannot cause this — the scripts pass `--noproxy '*'`, so `http_proxy` variables never detour the localhost POST.

## Something on the glass port answers 403

That is the server's own origin guard, not an intruder. Every request with an unexpected `Host` header is refused (the DNS-rebinding defense), and `/cmd` refuses any POST that is not `Content-Type: application/json` (the cross-site write defense: a browser must preflight that content type, and this server never answers preflights). The scripts always send the right host and header, so a 403 means some other tool knocked. When a script prints "something else is on the face's port", read its `reply began:` line — that is whatever actually answered.

## Every glass verb is refused with "disabled"

`"glass": false` in `ai-visualizer.json` turns the overlay off. Set it to `true` (or delete the line — on is the default) and restart the server. The overlay also only injects on face pages: the gallery, `?demo=1`, and `?shot=` never carry it, whatever the config says.

## Replies say viewers 0

Every `/cmd` reply carries `"viewers"`, and 0 means no face page has polled in the last ~3 seconds — the agent is narrating to an empty room. Open a face URL (for example `http://127.0.0.1:8790/faces/board/`) and check `bin/glass-state.sh`: its header names exactly which faces are watching.

## A map or embed card is a labeled blank

"no map offline" / "no embed offline" means the card was drawn while the machine had no network; when the connection returns, the card re-renders by itself on the next tick. A blank that says "embed failed to load", or stays empty with the site's host in the card chrome, is the remote site refusing to be framed — the page cannot detect or fix that, so pick an embeddable URL.

## The glass came up empty after a restart

Pinned items live in `glass-state.json` next to the server. If that file is damaged, the server says so once (`[glass] glass-state.json is unreadable ... starting with an empty glass`) and boots empty rather than wedging: the pinned cards are gone, but everything works. Delete the file if it bothers you — the next change rewrites it whole.

## The face stutters with cards up

You overrode `--glass-blur`. It ships at 0 because `backdrop-filter` over a face that repaints every frame makes the compositor re-blur continuously — several cards up can visibly drop the frame rate on always-on hardware. Lower the radius or set it back to 0; the cost scales with the pixel value.

## URL parameters, for poking at things

- `?demo=1` runs the scripted demo turn with no server bus.
- `?demo=1&state=speaking` pins one state (idle, listening, thinking, speaking).
- `?name=NOVA` overrides the display name in demo mode.
- `?fps=1` shows the frame meter on the board.
- `?shot=speaking&t=5000` renders a deterministic still and sets the page title to "ready" (the screenshot harness used to verify these faces).

## Updating

Run `./update.sh` in this folder (macOS), or double-click the `Update` icon if setup left one. On Windows, ask your agent: "pull the latest ai-visualizer and tell me what changed." The updater shows what changed before applying it and can never touch your `ai-visualizer.json`. If an older updater said "couldn't fast-forward" or mentioned local changes, run `./update.sh` once and it clears: it moves your config out of git's sight and everything flows after.
