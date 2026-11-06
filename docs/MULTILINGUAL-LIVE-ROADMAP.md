# Sela Live multilingual and natural interaction development

Status: foundation branch only. Not deployed or enabled. Existing Indonesian production behavior is unchanged.

## Current verified source constraints
- Stateful Zipformer STT is Indonesian-specific.
- Sela Live natural TTS requires `lang='id'` and exact reviewed hashes.
- The API backend streams SSE; the Hermes/Codex backend currently assembles a complete response (`stream=False`).
- Browser audio is half-duplex. Session generation/epoch, cancellation and playback ACK already exist.
- The installed runtime has a reviewed, transactional upgrade process. Do not bypass it.

## First implementation
- `sela_live/audio/language_contract.py`: strict, provider-neutral final-transcript and speech-segment metadata. It does not infer language, claim multilingual recognition, or change synthesis.
- `tests/python/test_language_contract.py`: inference-free validation tests.

## Development progress (2026-11-06)
- Added provider-neutral language and final-turn contracts, plus inference-free tests.
- Added bounded incremental text segmentation and tests; **not connected to the live provider or TTS path**.
- No production runtime, deployed service, authentication, or voice model changed.
- Tests are committed but have not yet been executed in a supported runtime.

- Added `voice_app/provider_streaming.py` with an opt-in plain-text streaming adapter. Structured delivery JSON remains full-buffer validated; no server wiring yet.
- Added `sela_live/audio/voice_capabilities.py` for explicit multilingual STT / expressive TTS capability negotiation. Installed models remain Indonesian-only.
- Added isolated unit tests for both modules. Tests not executed in target runtime.

- Decision update: extend existing **local Zipformer** and **local Supertonic-3 natural_v1**, not replace them with faster-whisper or network TTS.
- Removed the experimental replacement adapters `whisper_multilingual.py` and `edge_multilingual.py` from this branch. They were never wired to production.
- Existing language/style capability contracts remain as future validation infrastructure, not proof of model support.
- Multilingual recognition requires compatible local Zipformer acoustic/tokenizer model assets and a verified decoding pipeline; changing a language flag alone cannot make an Indonesian model multilingual.
- Expressive synthesis requires checking the pinned Supertonic-3 model's supported languages, styles, and actual conditioning capabilities. Preserve reviewed natural_v1 native controls and worker hashes until a separately tested upgrade is approved.
- Streaming provider-to-playback remains pending and must retain delivery-envelope validation and source-span ACK.

## Existing local engine inspection (2026-11-06)
- Inspected `sela_live/audio/stt.py`: `ZipformerState` uses persistent sherpa decoder state and feeds only new samples for growing PCM prefixes.
- Inspected `voice_app/zipformer_worker.py`: the older worker explicitly re-decodes bounded WAV prefixes. Do not conflate that transport with the newer stateful core.
- Inspected `sela_live/audio/tts.py`: `natural_v1` explicitly pins Indonesian language, tokenizer admission, selected style and 12-step synthesis.
- Inspected `voice_app/supertonic3_worker.py`: Supertonic has selected conditioning profiles and bounded numeric delivery controls, but no proven native emotional conditioning.
- Added `sela_live/audio/local_voice_audit.py` and source-level test for conservative offline verification. This is not a runtime model-weight audit.
- **Not yet tested:** installed model metadata, tokenizer inventory, multilingual acoustic accuracy, language pronunciation, subjective expressive quality, or real-device latency.

## Installed asset and real audio audit preparation (2026-11-06)
- Added `sela_live/audio/installed_asset_audit.py`: read-only SHA-256/size inventory for explicitly named local model files, and objective PCM WAV signal metrics (duration, RMS, peak, clipped samples). Python 3.13 compatible.
- Added `tests/python/test_installed_asset_audit.py` with synthetic fixtures; tests have not been executed on the installed runtime.
- These tools do **not** run STT/TTS inference or prove model language coverage, word error rate, voice naturalness, or expressiveness.
- Installed model binaries and real recorded/generated WAVs are not available via the GitHub repository alone. Collect operator-approved model hashes/metadata and anonymized audio test files from the deployment machine for actual verification.
- No credentials, model assets, production services, or reviewed manifests were modified.

## Next isolated milestones
1. STT: implement an opt-in multilingual recognizer adapter with language confidence, bounded PCM and exact final-transcript semantics. Compare Indonesian baseline before enabling; do not relabel Indonesian Zipformer as multilingual.
2. TTS: validate per-language capability and pronunciation on a separate reviewed worker/model; retain existing Supertonic B/12/id natural_v1 unchanged until approved. Preserve source text, source-span ACK, and hash/manifest checks.
3. LLM streaming: implement incremental response handling for the Codex path without breaking the existing structured text/delivery envelope or emitting partial invalid envelopes. Preserve API SSE behavior.
4. Natural turn-taking: add echo cancellation and barge-in in an opt-in client/server path, preserving epoch/generation stale-result rejection, ASR quiescence and heard_text ACK. Do not equate cancellation with acoustic barge-in.
5. Measure language accuracy, code-switching, time-to-first-audio, interruption latency, prosody quality, CPU/RSS and regression behavior on real devices.

## Release gates
- Unit and protocol tests pass in supported environments.
- Actual multilingual STT/TTS model inference is measured; fixtures are not acoustic proof.
- Real microphone/speaker tests for interruption, echo and playback.
- Independent review of runtime manifests, worker digests, resource limits and rollback.
- Explicit operator approval before production installer apply.

Do not change the production service, environment, credentials, model assets, current API/Codex access or `main` as part of this foundation.
