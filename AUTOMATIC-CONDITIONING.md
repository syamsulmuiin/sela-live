# Installed: experimental automatic activation around B

Installed 2026-10-29. `VOICE_AUTOMATIC_CONDITIONING=1` is enabled by the sole new `50-automatic-conditioning.conf` drop-in; existing reviewed `40-approved-conditioning-b.conf` remains unchanged. Code defaults OFF. Numeric speed/pause experiments remain OFF.

The remote API/Codex supplies separate validated delivery intent. The application automatically selects one of seven bounded B-anchored native voice_style profiles for arbitrary dynamic reply text. Neutral/plain replies retain exact accepted B; one immutable selection is retained across a reply's chunks. No cached opening, spoken control tags, local LLM, new cloud audio processor or browser/OS voice fallback is introduced.

This is experimental activation around B, not seven independently learned emotions or detection of the user's vocal emotion. The intent registry is extensible; registry membership does not prove acoustic support. Human emotional suitability and invariant speaker identity remain unproven. `native_emotion_conditioning=false` remains truthful.

Production dependencies include `voice_assets/automatic-conditioning/`, `voice_assets/conditioning-b/` and the existing expressive-tts model/runtime. Do not delete them as experimental scratch.

## Exact release and installed evidence

Canonical task: `autonomous/automatic-conditioning/`.

- Application manifest: `e9b1a65e193d54776620932a8ea6cdc917f6ecfcd9d4911e54929effd450393c` (17 paths).
- Independent runtime review and installer/unit rereview PASS, combined in `review/verdict.json`; 55 mock/filesystem transaction tests passed independently and in the parent.
- All 14 preregistered native cases (two texts × seven profiles) passed unchanged acoustic/speaker/ASR diagnostic guards. Diagnostics do not replace listening acceptance.
- Fresh installed regression: 225 Python tests run, 221 passed, 4 skipped; 121 Node tests passed.
- Fresh installed-root network fixture: three real remote-Codex/native-TTS turns, including two licensed public-human-audio local-STT inputs, distinct actual worker profile routes, metadata outside lexical input, source-exact text/ACK/history and retained ASR worker. Service-equivalent sandbox reproduced. See `installed-e2e.json`.
- Fresh production process/installed hashes and modes verified; loopback8091, automatic enabled, conditioning_b anchor, unchanged Zipformer/Supertonic engines. Public health200/config401/login200/anonymousWS403. See `installed-public-verification.json`.
- Fresh installed shipping-browser acceptance PASS: three same-session active-high/calm-medium/neutral routes, seven real native audio chunks fully played and browser-ACKed, exact final heard text, 54.96163265306122-second long answer, no metadata in dialogue/history, no console errors or overflow at 390px. Scripted JSON provider, not remote LLM; remote-Codex verification is separate. Receipt: `installed-browser-verification.json`. Fixture stopped, port closed, disposable auth removed; parent verified retained artifact hashes.

Rollback transaction remains under `rollback/` and uses the exact reviewed installer. Production account is untouched. These fixtures do not verify a physical microphone/speaker or completed Android/Windows native clients.
