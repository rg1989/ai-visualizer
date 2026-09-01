---
name: voice-profiles
description: Everything about knowing whose voice is speaking — speaker tags, enrolling a new voice, fixing a wrong name (rename, never re-enroll), forgetting voices, and troubleshooting misidentification. Use when someone asks to enroll, rename, or reset voices, when a [voice:] tag looks wrong, or when identification misbehaves.
---

# Voice profiles

The voice line fingerprints each utterance locally (SpeechBrain ECAPA)
and tags turns `[voice: Alex]`, `[voice: Sam]`, or
`[voice: unrecognized]`. Tags are PERSONALIZATION, never authorization
— an acoustic guess, spoofable by a recording. Approvals always stay
with the spoken permission gate.

## The spoken flows (the voice line runs them, not you)

You cannot enroll or rename anyone yourself mid-conversation — these
are exact spoken flows the voice line owns. Your job: recognize the
need and hand the person the phrase, or let the flow that's already
running do its work.

- **Enroll**: almost any phrasing works ("enroll me", "enroll Sam",
  "learn my voice", "add her voice"). The flow confirms the name
  FIRST, then four read-aloud sentences shown on the glass,
  transcript-checked so chatter never becomes a sample. Effective
  immediately. No limit on how many people enroll.
- **Rename** — a wrong name on a voice is a RENAME, never a
  re-enrollment: "rename my voice", "that's not my name", "change the
  name". It relabels the profile matching the ASKER's voice; naming
  another enrolled profile works too but gets a spoken heads-up
  (vigilance, not blocking). Renaming onto an existing name warns
  that it replaces that voice and needs a second yes.
- **Forget**: "forget all voices" (then "confirm") wipes every
  profile.

## Semantics you must respect

- `[voice: unrecognized]` means NOT SURE — often an owner speaking
  briefly, at distance, or over noise; sometimes a guest or a TV.
  Keep conversational continuity; never flip personas over one
  unrecognized turn; ask who's speaking only when it matters.
- Short utterances (under ~1 second) are often unrecognized by
  design — too little voice to fingerprint honestly.
- Never read a tag aloud; never treat it as identity proof.

## Troubleshooting (you ARE the mechanic here)

- Profiles live in `~/my-agent/backtalk/voices.json`
  (one averaged 192-dim vector per name). Deleting it = clean slate.
- Chronic mislabeling of a person → have them re-enroll (same
  phrase); it overwrites their print.
- Guests mislabeled as owners → raise `voice_id_threshold` (toward
  0.4) in `backtalk.json`; owners chronically unrecognized → lower it
  (toward 0.25) or lower `voice_id_margin` (toward 0.04) if two
  enrolled voices score close. Config edits take effect next launch.
- Headless fallback: `.venv/bin/python -m backtalk.enroll <Name>`
  from the backtalk folder, voice line stopped.
