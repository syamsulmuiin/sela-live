# Stateful Indonesian STT — installed

Current TTS override: the later user-selected Supertonic-3 M1 deployment replaces Piper. See `SUPERTONIC3.md` and `autonomous/supertonic-deploy/RUN.json`. Piper identity and test outcomes below describe this earlier STT rollout, not the current voice selection.

The application now enables `VOICE_ZIPFORMER_STATEFUL=1` through the project-specific systemd drop-in `/etc/systemd/system/voice-lab.service.d/stateful-stt.conf`.

## Behavior

- The selected local Indonesian Zipformer model and remote API/Codex LLM remain unchanged. Piper remains `id_ID-news_tts-medium` for consistent server-generated voice across clients.
- The browser sends bounded cumulative PCM snapshots at a negotiated 500 ms cadence. The worker verifies the prior PCM prefix, then feeds ONLY the unseen suffix into one native stream belonging to a server-generated utterance UUID. This is stateful native decoding over prefix-snapshot transport, not delta-only network framing.
- Finalization feeds the remaining source once, adds the existing 1.6-second tail, drains the recognizer and clears native state. Native endpoints do not prematurely split the application's turn.
- Only final transcripts invoke the LLM. A separate final generation prevents stale transcription after mute/unmute, stop/restart, interrupt, disconnect, superseding input or auth revocation. Legitimate PTT release after final admission still works.
- Half-duplex capture/playback, authentication, Host/Origin checks, response tokens, playback ACKs, resource limits and offline audio-worker isolation remain in place.

## Verified delivery

- Independent review passed for the exact 11-path delivery manifest; installed hashes match it.
- Installed Python suite: 133 total, 130 passed, 3 skipped. Node: 103 passed.
- Actual framed native tests: three full public FLEURS human recordings and one explicitly cropped short recording. Finals matched fresh legacy decoding and source samples were fed exactly once. This establishes no measured accuracy improvement; full-fixture WER remained 8/41.
- Installed-root authenticated real HTTP/WebSocket fixture: two spoken-input turns through actual STT -> remote Codex -> Piper, valid WAVs and ACKs, preserved history and the same native worker after TTS. First partial was about 1.56–1.58 seconds into the 3.2-second crop.
- Real browser with a licensed WAV routed through WebAudio MediaStream into shipping capture: one completed turn and playback ACK saved. A second completed browser turn is not claimed because browser-control connections were lost. See the explicit boundary report.
- Public HTTPS health 200, protected config 401, unauthenticated WebSocket 403. No production credentials or account resets were used for verification.

## Use

Reload Sela. Leave the optional online browser-recognition checkbox OFF to use this local-server path. Start the conversation and speak. Audio processing remains on the application server, not on-device; text goes to Codex. Physical microphone and listening quality still require user acceptance.

## Evidence and rollback

Task artifacts: `autonomous/stateful-stt/`.
Primary status: `RUN.json`; deployment allowlist and original/source hashes: `DEPLOYMENT-RECEIPT.json`; installed checks: `INSTALLED-REGRESSION.json`, `installed-e2e.json`, `public-verification.json`.
Original files are preserved in `autonomous/stateful-stt/rollback/` with `transaction.json`. The installer restores that snapshot if its promotion checks fail. Do not overwrite newer user changes with this snapshot without reconciling hashes first.

This change implements our own speech pipeline using streaming design references. It does not repair Google/SODA, recover a proprietary backend, prove broader Indonesian conversational accuracy, deliver native Android/Windows applications, or achieve full native-audio/live-model parity.
