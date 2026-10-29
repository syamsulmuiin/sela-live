# Current production voice: Supertonic-3 M1-base, automatic activation around B

Experimental automatic selection around listener-approved B is installed and active
as of 2026-10-29 via `VOICE_AUTOMATIC_CONDITIONING=1`; B remains the neutral anchor.
Current scope and installed verification: `AUTOMATIC-CONDITIONING.md` and
`autonomous/automatic-conditioning/`. Installed-browser acceptance also passed; all verification fixtures are stopped.
This is bounded activation conditioning, not independently trained emotions or
proven invariant speaker identity. The fixed-B and original-engine rollout evidence
below is historical.

User selected Supertonic-3 as Sela's voice. It is installed through `/etc/systemd/system/voice-lab.service.d/supertonic3.conf`, not as a VoiceStudio installation.

## Preserved behavior

- Remote API/Codex LLM and stateful Indonesian Zipformer STT are unchanged.
- Existing authenticated HTTP/WebSocket boundaries, exact Host/Origin checks, final-generation guards, cancellation, playback ACK and half-duplex controls remain.
- This is a TTS backend/voice switch, not a fix for STT word errors or uppercase transcripts.

## Voice/runtime

- Fixed speaker M1, Indonesian `id`, 44100 Hz mono PCM16. Same server-generated voice for all clients.
- Pinned model: `Supertone/supertonic-3` revision `3cadd1ee6394adea1bd021217a0e650ede09a323`.
- Runtime and assets under `autonomous/expressive-tts/{venv,assets,assets-manifest.json}` are NOW PRODUCTION DEPENDENCIES, despite their historical staging directory name. Do not delete them as disposable experiments.
- Isolated one-shot ONNX CPU worker: no runtime downloads, native network denied, explicit memory/CPU/wall/output caps and no provider/voice fallback.
- Full model OpenRAIL-M terms are served to authenticated users at `/supertonic3-LICENSE.txt` and linked in voice settings. Main UI discloses synthetic output and the applicable use restrictions. This documentation is not legal clearance.
- Fixed steps 8, speed 1.05. No automatic laughter/sigh insertion, arbitrary happy/sad selector, or input-emotion detection. Human-heard expressivity and pronunciation remain a listening acceptance gate.

## Whole-answer speech and per-call protections

The former 30-second WHOLE-ANSWER rejection is removed by the later chunked-delivery update. Supertonic answers are partitioned into bounded pronunciation-aware pieces, delivered in order with one inflight waveform and at most one prefetched successor. Each intermediate successor waits for the matching playback ACK; final playback completion still controls microphone resumption. Exact original source spans remain attached to ACKs, without shortening the visible response.

Native calls retain their 800-character / 3200-UTF-8-byte and 30-second safety ceilings, CPU/RSS/deadline/output protections and no-fallback behavior. These are PER PIECE, not a 30-second total-answer cap. Existing application/provider text bounds remain unchanged; finite execution is not described as unlimited. This is segmented waveform delivery, not native audio-model streaming.

Latest delivery evidence: `autonomous/chunked-tts/` and reconciliation in `autonomous/long-reply-tts/`. The installed-root scripted long-answer check and real browser each completed 51.13034013605442 seconds of audio with all original source acknowledged. A separate native run produced 72.23727891156463 seconds across nine WAVs. Those long scripts are explicitly NOT Codex responses; actual remote Codex repeated turns are separately recorded in `installed-e2e.json`. Fresh installed-source regression covered 13 chunking tests and 106 Node tests, and current public authentication guards were rechecked. Older verification counts below describe the initial voice-engine rollout.

## Verification

- Independent review approved the exact seven delivered files; installed hashes rechecked.
- Installed Python: 148 run, 144 passed, 4 opt-in skips. Node: 104 passed.
- Actual 419-character synthetic reply: 29.487 seconds audio, 13.695 seconds wall, peak RSS 617152 KiB. Native cancellation/reaping and current StatefulASR quiescence/retention tested.
- Actual synthesis passed under service-equivalent User=root, NoNewPrivileges, PrivateTmp and ProtectSystem=full without relaxing service policy.
- Two installed-root authenticated network turns passed: real stateful STT -> remote Codex -> Supertonic -> valid 44100 WAV -> ACK, retained history and same ASR worker.
- Real browser decoded 44100-Hz audio and emitted playback ACK; mobile viewport 390 px had no horizontal overflow. This used a disposable loopback test-auth fixture, not production account credentials.
- Public health 200, unauthenticated config 401, unauthenticated WebSocket 403. Physical microphone/speaker and human voice preference are not proven by these tests.

Current evidence/status: `autonomous/supertonic-deploy/RUN.json`, `DEPLOYMENT-RECEIPT.json`, `INSTALLED-REGRESSION.json`, `installed-e2e.json` and `public-verification.json`. Original files are in `autonomous/supertonic-deploy/rollback/`; promotion has an automatic failure rollback. Reconcile hashes before manually restoring over any later changes.

Reload Sela to load the current config/UI. Voice settings should show Supertonic3 M1. Browser online recognition remains an independent, explicit opt-in; it is not needed for local-server STT.
