# Installed anchor: listener-selected conditioning B

Update 2026-10-29: experimental automatic activation around B is now enabled.
B remains the exact neutral anchor; ordinary replies are no longer limited to
one fixed profile. See `AUTOMATIC-CONDITIONING.md` for current scope and evidence.
The fixed-profile rollout and its historical acceptance are recorded below.

The latest user choice `B` selects `B-conditioning-plus.mp3`, bound in
`autonomous/approved-conditioning-b/SELECTION.json`. It is not the earlier
spoken-style-B wording/punctuation instruction.

## Current runtime

- Supertonic-3 M1-derived **fixed conditioning B (+0.4)** applies to arbitrary
  dynamic response text, using the supported native custom `voice_style` input.
- Selection: `VOICE_SUPERTONIC3_STYLE_PROFILE=conditioning_b` in the single reviewed
  drop-in `/etc/systemd/system/voice-lab.service.d/40-approved-conditioning-b.conf`.
- App-owned `voice_assets/conditioning-b/profile.json` and `manifest.json` are now
  PRODUCTION DEPENDENCIES. Do not delete them as experimental scratch. The runtime
  never loads profiles or audio from the experimental directories.
- The pinned helper verifies profile, base M1 and model-manifest hashes, native
  shapes/finite values and unchanged duration conditioning. Missing/corrupt data
  fails closed instead of silently changing the voice.
- Original mode remains explicitly available for verified rollback. No provider,
  LLM, model weights, STT engine, consent, auth, ACK or cancellation changes.

No speech recording is replayed. No selected example sentence is hardcoded into
responses. The existing conversational wording guidance remains independent.

## Acceptance scope

This is the listener's chosen fixed acoustic profile, not automatic emotion
switching or proof that every emotional category works. It is M1-derived, not a
claim of mathematically unchanged vocal identity. Metadata taxonomy remains
extensible. `native_emotion_conditioning=false`; experimental pacing remains OFF.

## Fresh installed checks

Canonical receipts: `autonomous/approved-conditioning-b/`.

- Independent review passed for exact application, one-variable unit and executing
  installer hashes; 41 mock/filesystem transaction tests passed. Recovery never
  restarts a mixed tree after failed stop/restore/readback/reload.
- Installed Python: 210 run, 206 passed, 4 opt-in skips; web: 118 passed.
- Both selected source utterances reproduce exact approved PCM through the actual
  installed adapter; a new utterance is synthesized dynamically with B.
- Three real network turns with remote Codex and native B, including two local-STT
  fixture turns, complete with source-exact text, retained history/ASR worker and
  ACKs under reproduced systemd restrictions.
- Installed shipping client: two turns, genuine native playback/final ACKs, full
  heard_text and 54.96163265306122 seconds of long-answer audio; B label visible,
  no caught browser errors or 390px horizontal overflow. Browser provider was
  scripted; actual remote Codex was verified separately.
- Public health200, unauthorized config401, login200, anonymous WS403. Live
  service environment was read back as conditioning_b, loopback8091, same STT/TTS.
- All reviewed app/unit bytes and modes matched after promotion. Rollback retained.

Production account untouched. Physical microphone/Android/Windows and arbitrary
emotional naturalness are not established by these fixtures. All test servers
and installation/verification jobs are stopped. Reload Sela for the current UI.
