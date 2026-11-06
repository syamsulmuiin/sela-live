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
