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
"""Which brain answers — Anthropic's Claude, or Z.AI's GLM.

The choice used to be made by WHICH LAUNCHER you double-clicked: two
.command files that differ only in a handful of exported environment
variables. That is a fine way to configure a machine and a terrible way
to offer a choice, because the choice is invisible from inside the
running program — the one place the person actually is.

So the environment moves in here. The brain is a ClaudeSDKClient that
spawns the `claude` CLI, and that CLI inherits os.environ, which means
a provider switch is LIVE: mutate the environment, point CFG at the new
model ids, then `await brain.stop()` / `await brain.start()` and the
next spawn picks the new brain up. No relaunch.

What does NOT survive that: the conversation. A new CLI process is a
new session, so the agent forgets what you were just talking about.
That is honest behavior for "change brains" — but it must be SAID OUT
LOUD by whoever calls apply(), never hidden.

The key never lives here. See key_status()/key_tail(): this module can
tell you a key EXISTS and show its last four characters, and that is the
most it will ever tell anyone, including a log file.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from backtalk.config import CFG
from backtalk.vlog import log

# The two brains, and everything that differs between them.
#
#   label    what the picker draws on the button.
#   variants the tiers this brain offers, id -> {label, models}. Each
#            "models" fills the three CFG slots: the fast tier, the
#            deep-work tier ("switch to the deep model"), and the tiny
#            intent interpreter. Full ids ON PURPOSE, same reasoning as
#            config.py — a bare alias gets resolved by the CLI and can
#            land somewhere older than you think.
#   variant  the tier a brain nobody has picked a tier for answers on.
#            Every provider has one, so CFG["brain_model"] is never
#            empty and the picker never has to guess what is live.
#   config_dir  CLAUDE_CONFIG_DIR. Separate per provider ON PURPOSE:
#            one holds a logged-in Anthropic session, the other must
#            never be handed one, and a shared dir would cross them.
#   env      extra environment the CLI needs to reach this provider.
#            Empty for Anthropic ON PURPOSE — "no override" IS the
#            configuration, and see _MANAGED_VARS for why that matters.
#   auth_var / key_slot  where the secret goes, and the credential-store
#            item it comes out of. "" means this provider has no key.
PROVIDERS = {
    "claude": {
        "label": "CLAUDE",
        # Three presets, because the honest axis here is WAIT: the ears
        # and the mouth cost ~0.1s together, so the model IS the pause
        # between you finishing a sentence and hearing one back.
        #   fast      Haiku answers, Sonnet still behind "switch to the
        #             deep model" — for a room you talk to in short
        #             turns and want answered like a person.
        #   balanced  the long-standing default, unchanged.
        #   think     Opus on both tiers: every reply is slower and
        #             burns usage doing it, and that is the trade.
        # Intent stays on Haiku in all three ON PURPOSE — it is a
        # one-line classifier on the critical path, and a bigger model
        # there buys nothing but delay.
        "variants": {
            "fast": {
                "label": "FASTEST",
                "models": {"model": "claude-haiku-4-5-20251001",
                           "deep_model": "claude-sonnet-5",
                           "intent_model": "claude-haiku-4-5-20251001"},
            },
            "balanced": {
                "label": "BALANCED",
                "models": {"model": "claude-sonnet-5",
                           "deep_model": "claude-opus-5",
                           "intent_model": "claude-haiku-4-5-20251001"},
            },
            "think": {
                "label": "THINKING",
                "models": {"model": "claude-opus-5",
                           "deep_model": "claude-opus-5",
                           "intent_model": "claude-haiku-4-5-20251001"},
            },
        },
        "variant": "balanced",
        "config_dir": "~/jarvis-config",
        "env": {},
        "auth_var": "",
        "key_slot": "",
    },
    "zai": {
        "label": "Z.AI",
        # Which GLM answers YOU. 5.3 is the strong one; 5.3-flash is
        # far cheaper on the plan's credit multipliers (2.3/8 against
        # 6.9/24) and noticeably quicker to first word. One id fills all
        # three slots: the tiers differ by model, not by role.
        "variants": {
            "glm-5.3": {
                "label": "GLM-5.3",
                "models": {"model": "glm-5.3", "deep_model": "glm-5.3",
                           "intent_model": "glm-5.3"},
            },
            "glm-5.3-flash": {
                "label": "5.3 FLASH",
                "models": {"model": "glm-5.3-flash",
                           "deep_model": "glm-5.3-flash",
                           "intent_model": "glm-5.3-flash"},
            },
        },
        "variant": "glm-5.3",
        "config_dir": "~/jarvis-config-glm",
        # z.ai coding-plan ids (docs.z.ai/devpack/tool/claude). The SDK
        # still asks for haiku/sonnet/opus internally; these three map
        # those names onto GLM so nothing 404s — or, worse, quietly
        # falls back to the Anthropic endpoint and bills you there.
        "env": {
            "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5.3-flash",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.3",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.3",
        },
        "auth_var": "ANTHROPIC_AUTH_TOKEN",
        "key_slot": "jarvis-glm",
    },
}

# Public API is exactly PROVIDERS, current(), key_status(), key_tail()
# and apply(). Everything else here is an implementation detail and
# is free to change under a caller.
_DEFAULT = "claude"

# THE TRAP THIS SET EXISTS TO CLOSE.
#
# Every variable ANY provider sets, including the secret ones. apply()
# clears this whole set before it sets anything, so switching BACK to a
# provider that overrides nothing still fully un-does the one before it.
#
# Get that wrong and the failure is silent and expensive: leave a single
# ANTHROPIC_BASE_URL behind when switching to Claude and the CLI happily
# keeps talking to z.ai, with Anthropic model ids it will reject or
# silently remap — while the picker, the face and the logs all say
# CLAUDE. The bug looks like "the model got dumber", never like
# "an environment variable outlived its provider".
#
# Derived from PROVIDERS rather than typed out, so a third provider
# added above cannot forget to be cleaned up.
_MANAGED_VARS = frozenset(
    [v for spec in PROVIDERS.values() for v in spec["env"]]
    + [spec["auth_var"] for spec in PROVIDERS.values() if spec["auth_var"]]
)


def _spec(p: str) -> dict:
    """The provider record, or a loud failure. Never a silent default:
    a typo'd provider must not quietly route you to the other brain."""
    spec = PROVIDERS.get(p)
    if spec is None:
        raise RuntimeError(
            f"unknown brain provider {p!r} — expected one of "
            f"{', '.join(sorted(PROVIDERS))}")
    return spec


def _config_dir(p: str) -> Path:
    return Path(os.path.expanduser(_spec(p)["config_dir"]))


def _lookup_key(slot: str) -> str:
    """The provider's API key, from the most secure store available —
    NEVER from a file in this repo. Same shape as mouth.py's ElevenLabs
    lookup, deliberately:
      1. macOS Keychain, item `jarvis-glm`. Seed it once with:
         security add-generic-password -a "$USER" -s jarvis-glm -T /usr/bin/security -w
         (it prompts for the secret privately; -T lets this code read it
         back without a GUI prompt every launch)
      2. Linux secret-tool (libsecret):
         secret-tool store --label jarvis service jarvis-glm
      3. the ZAI_API_KEY environment variable — last resort, and the
         only option on Windows for now. Know the tradeoff: an export
         line in a shell profile is a plaintext key on disk, which is
         exactly what the keychain path avoids.

    NOT cached, unlike mouth.py's. The settings picker can store a key
    mid-session, and a cache would keep reporting "missing" at a face
    the person just typed a working key into.

    The auth_var (ANTHROPIC_AUTH_TOKEN) is deliberately NOT consulted as
    a fallback: apply() sets that variable itself, so trusting it would
    mean reporting our own export back as evidence of a stored key —
    and still saying "ready" long after the Keychain item was deleted.
    """
    if not slot:
        return ""
    key = ""
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["security", "find-generic-password",
                                "-s", slot, "-w"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                key = r.stdout.strip()
        elif sys.platform.startswith("linux"):
            from shutil import which
            if which("secret-tool"):
                r = subprocess.run(["secret-tool", "lookup", "service", slot],
                                   capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    key = r.stdout.strip()
    except Exception:
        pass    # an unreachable keychain reads as "no key", never a crash
    return key or os.environ.get("ZAI_API_KEY", "")


def _signed_in(p: str) -> bool:
    """Does this provider's config dir hold a completed Claude Code login?

    Claude has no key to check — it rides a logged-in session — so this
    is the honest substitute, worked out by INSPECTING the real dirs:
    ~/jarvis-config carries an "oauthAccount" object in .claude.json and
    ~/jarvis-config-glm (never logged in, token-authed) does not. That
    marker is written when the login completes, so its presence is a
    true statement about THIS dir.

    Its limits, stated rather than papered over: it says a login was
    COMPLETED here, not that the token is still valid — the token itself
    lives in the Keychain item "Claude Code-credentials", which this
    module refuses to read because a process outside that item's ACL
    gets a modal password prompt for its trouble, and a settings screen
    must never do that. Nothing here runs `claude login` or any other
    command that could prompt, and nothing here makes a network call.

    Read failures resolve to True. A file we could not parse is not
    evidence of being logged out, and "signed-out" is the kind of claim
    that sends someone off to re-authenticate an account that was fine.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True     # key auth instead of a login — also usable
    d = _config_dir(p)
    # Linux and older installs keep the OAuth token beside the config
    # instead of in a system keychain.
    try:
        creds = d / ".credentials.json"
        if creds.is_file() and creds.stat().st_size > 2:
            return True
    except OSError:
        return True
    try:
        data = json.loads((d / ".claude.json").read_text())
    except FileNotFoundError:
        return False    # no config at all: nobody has ever logged in here
    except (OSError, ValueError):
        return True     # unreadable != logged out. See the docstring.
    return bool(data.get("oauthAccount"))


def current() -> str:
    """The provider CFG says is live right now."""
    p = str(CFG.get("brain_provider") or _DEFAULT).strip().lower()
    return p if p in PROVIDERS else _DEFAULT


def key_status(p: str) -> str:
    """Is this brain reachable? "ready", "missing" or "signed-out".

    Two providers, two kinds of credential, so two kinds of "no":
      zai     "ready" once the Keychain item resolves, else "missing" —
              a key is expected and there isn't one.
      claude  "ready" when its config dir looks logged in, else
              "signed-out" — no key is expected, so "missing" would send
              the person hunting for a key that does not exist. The face
              can say "run the Claude launcher once and log in".
    Neither answer touches the network; neither proves the credential
    still WORKS. This is "is there something to try with", nothing more.
    """
    spec = _spec(p)
    if spec["key_slot"]:
        return "ready" if _lookup_key(spec["key_slot"]) else "missing"
    return "ready" if _signed_in(p) else "signed-out"


def key_tail(p: str) -> str:
    """The last 4 characters of the stored key, or "". NEVER more.

    This is the entire vocabulary the UI gets for a secret: enough to
    tell "the key I pasted" from "some other key", useless to a
    shoulder or a screenshot. A key too short to have four characters
    left over is returned as "" rather than mostly-revealed, and a
    provider with no key at all ("claude") always answers "".
    """
    key = _lookup_key(_spec(p)["key_slot"])
    return key[-4:] if len(key) >= 8 else ""


def variants(p: str) -> dict:
    """The model tiers this provider offers, id -> label. Empty when it
    has only one, which is also how a caller knows not to draw a tier
    row at all."""
    return {k: v["label"] for k, v in _spec(p)["variants"].items()}


def apply(p: str, model: str | None = None) -> None:
    """Point the process at provider p: os.environ, then CFG.

    Idempotent — applying the live provider again is a no-op that still
    rewrites every variable, which is the point: it repairs an
    environment something else has meddled with.

    Raises RuntimeError for an unknown provider, or when a provider that
    needs a key has none (better a refusal here than a brain that boots
    and then 401s at the first thing you say to it).

    The caller still owns the two halves this function deliberately does
    not do: restarting the brain (await brain.stop(); await brain.start()
    — and a WarmBrain caches its own .model, so give the new instance
    CFG["model"] or set brain.model too), and persisting the choice
    (main.py's _write_config_key("brain_provider", p)).
    """
    spec = _spec(p)
    key = _lookup_key(spec["key_slot"]) if spec["key_slot"] else ""
    if spec["key_slot"] and not key:
        raise RuntimeError(
            f"no API key stored for {spec['label']} — add it in the "
            f"BRAIN tab, or: security add-generic-password -a \"$USER\" "
            f"-s {spec['key_slot']} -T /usr/bin/security -w")

    # Clear FIRST, and clear everything any provider sets — see the
    # _MANAGED_VARS comment for the silent bug this closes.
    for var in _MANAGED_VARS:
        os.environ.pop(var, None)

    os.environ["CLAUDE_CONFIG_DIR"] = str(_config_dir(p))
    os.environ.update(spec["env"])
    if key:
        os.environ[spec["auth_var"]] = key

    # GLM DELIBERATES ON EVERYTHING, and no supported knob reaches it from
    # here: Claude Code speaks Anthropic's protocol, which has no
    # reasoning_effort, and z.ai's shim ignores thinking.budget_tokens.
    # backtalk/zaifast.py adds the one field that IS honored, in a loopback
    # thread. Measured 2.1x faster to first word, and it fixes replies that
    # came back EMPTY because the model spent the whole budget thinking.
    # Set zai_disable_thinking false in backtalk.json to go direct.
    if p == "zai" and CFG.get("zai_disable_thinking", True):
        try:
            from backtalk import zaifast
            os.environ["ANTHROPIC_BASE_URL"] = zaifast.start()
        except Exception as e:
            # Never let the accelerator stop the brain from booting: the
            # env already holds the direct z.ai URL, so this just runs slow.
            log(f"[provider] thinking-off proxy unavailable ({e!r}) — "
                f"talking to z.ai directly, expect slower replies")

    # Which tier answers. An unknown or absent one falls back to the
    # profile's default rather than refusing -- a stale name in
    # backtalk.json must not be able to stop the brain from booting.
    tiers = spec["variants"]
    want = model if model in tiers else spec["variant"]
    models = tiers[want]["models"]
    for slot in ("model", "deep_model", "intent_model"):
        CFG[slot] = models[slot]
    if "ANTHROPIC_DEFAULT_SONNET_MODEL" in spec["env"]:
        # This provider answers to Anthropic's model NAMES (z.ai does):
        # the SDK asks for sonnet/opus internally, so point those at the
        # chosen tier or they resolve to whatever spec["env"] froze in.
        # HAIKU is deliberately NOT moved: that is the background errand
        # model, and flash is the right answer for it either way.
        os.environ["ANTHROPIC_DEFAULT_SONNET_MODEL"] = models["model"]
        os.environ["ANTHROPIC_DEFAULT_OPUS_MODEL"] = models["deep_model"]
    CFG["brain_provider"] = p
    CFG["brain_model"] = want

    # Never the key, and not even its tail: a log file is forever and
    # gets pasted into bug reports.
    log(f"[provider] brain is {spec['label']}, model {CFG['model']}, "
        f"config {spec['config_dir']}, key {key_status(p)}")


if __name__ == "__main__":
    # python -m backtalk.provider
    # No key needed, no network, no keychain: _lookup_key is stubbed so
    # the checks are the same on a machine that has never seen a z.ai
    # key as on the one that has.
    _real_lookup = _lookup_key
    _stub_key = ""

    def _stubbed(slot: str) -> str:
        return _stub_key if slot else ""
    _lookup_key = _stubbed          # noqa: F811 — module-global rebind

    before_env = dict(os.environ)
    before_cfg = {k: CFG.get(k) for k in
                  ("model", "deep_model", "intent_model", "brain_provider")}

    # An unknown provider raises rather than defaulting into a brain.
    for bad in ("gpt", "", "CLAUDE!"):
        for fn in (apply, key_status, key_tail):
            try:
                fn(bad)
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"{fn.__name__}({bad!r}) did not raise")

    # A provider that needs a key and hasn't got one refuses.
    _stub_key = ""
    assert key_status("zai") == "missing"
    assert key_tail("zai") == ""
    try:
        apply("zai")
    except RuntimeError:
        pass
    else:
        raise AssertionError("apply('zai') with no key did not raise")

    _stub_key = "fake-not-a-real-key-ABCD1234"
    assert key_status("zai") == "ready"

    # key_tail NEVER hands out more than four characters, whatever it
    # is holding — including a short key, where four would be most of it.
    for _stub_key in ("fake-not-a-real-key-ABCD1234", "abcdefgh", "abc", ""):
        assert len(key_tail("zai")) <= 4, _stub_key
    assert key_tail("zai") == ""                      # last case: no key
    _stub_key = "fake-not-a-real-key-ABCD1234"
    assert key_tail("zai") == "1234"
    assert key_tail("claude") == ""                   # no key to have a tail

    apply("zai")
    assert current() == "zai"
    # The base URL is the LOOPBACK injector by default (zaifast strips GLM's
    # forced deliberation on the way past); turning the flag off must send
    # us straight back to z.ai, because that is the documented escape hatch.
    assert os.environ["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:"), \
        os.environ["ANTHROPIC_BASE_URL"]
    _was = CFG.get("zai_disable_thinking", True)
    try:
        CFG["zai_disable_thinking"] = False
        apply("zai")
        assert os.environ["ANTHROPIC_BASE_URL"] == \
            "https://api.z.ai/api/anthropic", os.environ["ANTHROPIC_BASE_URL"]
    finally:
        CFG["zai_disable_thinking"] = _was
        apply("zai")
    assert os.environ["ANTHROPIC_AUTH_TOKEN"] == _stub_key
    assert os.environ["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "glm-5.3-flash"
    assert os.environ["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "glm-5.3"
    assert os.environ["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "glm-5.3"
    assert os.environ["CLAUDE_CONFIG_DIR"].endswith("jarvis-config-glm")
    assert CFG["model"] == CFG["deep_model"] == CFG["intent_model"] == "glm-5.3"

    apply("claude")
    assert current() == "claude"
    # THE ONE THAT MATTERS: not one z.ai variable outlives the switch.
    for var in _MANAGED_VARS:
        assert var not in os.environ, f"{var} survived the switch to claude"
    assert os.environ["CLAUDE_CONFIG_DIR"].endswith("jarvis-config")
    assert not os.environ["CLAUDE_CONFIG_DIR"].endswith("glm")
    assert CFG["model"] == "claude-sonnet-5"
    assert CFG["deep_model"] == "claude-opus-5"
    assert CFG["intent_model"] == "claude-haiku-4-5-20251001"
    assert key_status("claude") in ("ready", "signed-out")

    # The tiers. A preset moves all three slots at once; an unknown
    # or missing one lands on the profile default instead of refusing,
    # because a stale name in backtalk.json must never cost a boot.
    assert variants("claude") == {"fast": "FASTEST", "balanced": "BALANCED",
                                  "think": "THINKING"}
    apply("claude", "fast")
    assert CFG["model"] == "claude-haiku-4-5-20251001"
    assert CFG["deep_model"] == "claude-sonnet-5"
    assert CFG["brain_model"] == "fast"
    apply("claude", "think")
    assert CFG["model"] == CFG["deep_model"] == "claude-opus-5"
    for junk in ("no-such-tier", "", "glm-5.3"):
        apply("claude", junk)
        assert CFG["brain_model"] == "balanced", junk
        assert CFG["model"] == "claude-sonnet-5", junk
    # A Claude preset must not leak model-NAME overrides: Anthropic ids
    # are already what the CLI asks for, and an override left behind is
    # exactly the silent "the model got dumber" bug _MANAGED_VARS closes.
    for var in _MANAGED_VARS:
        assert var not in os.environ, var
    apply("zai", "glm-5.3-flash")
    assert CFG["model"] == CFG["deep_model"] == "glm-5.3-flash"
    assert os.environ["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "glm-5.3-flash"
    assert os.environ["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "glm-5.3-flash"
    apply("claude")

    # Idempotent: applying the live provider again changes nothing.
    snapshot = dict(os.environ)
    apply("claude")
    assert dict(os.environ) == snapshot
    assert current() == "claude"

    # And back again, twice, to prove the clear isn't one-directional.
    apply("zai")
    apply("zai")
    assert (os.environ["ANTHROPIC_BASE_URL"].startswith("https://api.z.ai")
            or os.environ["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:"))
    apply("claude")
    assert "ANTHROPIC_BASE_URL" not in os.environ

    _lookup_key = _real_lookup
    os.environ.clear()
    os.environ.update(before_env)
    CFG.update(before_cfg)
    print("provider.py self-check: all assertions passed")
