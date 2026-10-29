# Extensible intended-delivery boundary — installed

Update 2026-10-29: experimental automatic native activation around B is installed
and active. See `AUTOMATIC-CONDITIONING.md`; installed-browser final verification
passed and its fixture is stopped. The fixed-B/foundation scope and verification below are historical,
not the current claim that every response still uses one fixed profile.

## Shipped scope

- Remote Codex/API providers return a bounded structured reply containing `text`
  and separate `delivery` intent. Only `text` enters visible speech, TTS lexical
  input and conversation history. Malformed/unknown/duplicate control fields fail
  closed; raw envelopes are never a spoken fallback.
- App-owned immutable versioned registry starts with neutral plus the eight initial
  emotional categories. New registered IDs can be added without changing the
  parser, conversation token protocol or ACK lifecycle. Registry size/ID/numeric
  bounds remain safety guards, not a fixed eight-emotion design.
- Removed the mistaken cached empathy prefix, its runtime helper, forced-opening
  guidance, production assets and obsolete tests. Every response is dynamically
  synthesized, including replies starting with the old approved opening.
- Supertonic-3 M1/id remains the base. After the later listener selection, the
  installed native input is fixed conditioning B (+0.4), not the original unchanged
  M1 profile. See `APPROVED-CONDITIONING-B.md`. No new speech engine, local
  general-purpose LLM or third-party audio upload.

## Capability truth

The foundation release did not change the original M1 `voice_style`. The later
listener-selected conditioning-B rollout does change that native conditioning for
dynamic text; it remains a fixed chosen style, NOT automatic emotional rendering.
Current scope and installed evidence are in `APPROVED-CONDITIONING-B.md`.

Per-request native numeric speed/pause controls exist behind the strict opt-in
`VOICE_EXPERIMENTAL_DELIVERY_PROSODY=1`. They are OFF in production. They were
exercised in isolated fixtures only; pacing is not an accepted substitute for
emotion. The authenticated config reports metadata protocol active, prosody
availability separately from enablement, and `native_emotion_conditioning=false`.

Metadata intent is not a diagnosis of the user's emotional state. Human listening
on varied dynamic text and consistent speaker identity is still required before
claiming an emotional category works or enabling new acoustic presets.

## Verification

Canonical receipts: `autonomous/extensible-delivery/`.

- Independent exact-hash review passed; all installed reviewed targets, including
  intended deletions, were read back. Rollback transaction retained.
- Installed Python discovery: 196 run, 192 passed, 4 opt-in skips, no failures.
  Shipping web regression: 116 passed.
- Installed-source disposable authenticated fixture: two public-human-audio STT
  turns followed by a typed turn, real remote Codex, native M1 TTS, exact envelope
  text isolation, matching ACK/history and retained ASR worker. Ran with the
  service's NoNewPrivileges/PrivateTmp/ProtectSystem restrictions reproduced.
- Installed shipping browser: two turns on one fixture session, real native
  decoding/playback and final ACK, exact full heard text; long answer delivered
  54.96163265306122 seconds. No horizontal overflow at 390px or caught JS errors.
  This browser provider was scripted; remote Codex was verified separately.
- Public health 200, unauthenticated config 401, login 200, anonymous WS 403.
- An earlier installed-source probe hit upstream HTTP429; a later fresh recheck
  completed all three turns. The failed log is preserved, not counted as success.

No physical microphone, human-audible emotional quality, or completed native
Android/Windows client is claimed. The production account was not accessed or
modified for tests. No fixture server or synthesis experiment remains running.
