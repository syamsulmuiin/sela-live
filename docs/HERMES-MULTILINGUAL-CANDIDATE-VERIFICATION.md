# Hermes isolated multilingual candidate verification

Status: NOT RUN. This is an operator-controlled test plan, not authorization to deploy.

Repository: syamsulmuiin/sela-live
Branch: feature/multilingual-live-foundation

## Hard boundaries
- Read the exact authenticated branch commit into scratch, never checkout/pull production.
- Do not modify /root/voice-lab, its service, models, credentials, manifests, or production locks.
- Preserve local Zipformer STT and Supertonic-3 TTS; no network voice providers, alternate engines, downloads, installs or fallback.
- Fail closed if source pins, worker digest, original guard equivalence, read-only model mounts, seccomp network denial, Landlock write restrictions, memory/CPU limits, or canonical production lock admission cannot be established.
- Production has priority. Reap all workers and descendants on timeout. Do not run simultaneous STT/TTS inference.

## Verification sequence
1. Obtain authenticated branch snapshot and record commit SHA. Inspect `sela_live/audio/multilingual_candidate.py`, `multilingual_tts_request.py`, `supertonic_language_admission.py` and all associated tests. Compare with the installed pinned native guard; do not assume compatibility.
2. Run unit tests in a scratch-only isolated environment. Include negative tests for language auto/unknown, missing/extra fields, wrong profile, tokenizer overflow, invalid DP, excessive output, clipping, and SDK keyword drift. Do not claim success if tests are only statically parsed.
3. Review whether the candidate's use of the original guard retains every safety boundary. The wrapper alone is NOT an executable sandbox. If any boundary is missing, report BLOCKED and do not infer.
4. If and only if reviewed, create an ephemeral scratch harness using the installed Supertonic-3 SDK and verified model assets, with the exact safety isolation from the completed SELA_LOCAL_VOICE_AUDIT. Pin source and model hashes. Use no production changes.
5. Run isolated serial synthesis for id/en/ja, one id-tagged mixed-language case, and an id baseline with the same text/profile/seed/steps. Record WAV SHA, clipping, duration, sample rate, token count, predicted duration, CPU, peak RSS, cold/warm synthesis, request-to-whole-PCM latency, and comparison to the previous id baseline. Do not label this as native audio-block streaming.
6. Obtain listening evaluation by a human for pronunciation, language switching, naturalness, identity, and calm/active appropriateness. Report NOT TESTED for anything without a listener; no automated proxy can claim perceptual approval.
7. Write a report with PASS/FAIL/BLOCKED for every gate, exact branch SHA, test commands, receipts and failures, all outputs scratch-only. Do not deploy or activate language routing.

## After stage 2 passes
- Zipformer: audit available trainable checkpoints and tokenizer provenance; design multilingual dataset and independent per-language/code-switch WER evaluation. Do not claim the installed Indonesian ONNX model can be finetuned without evidence.
- Real-time: design opt-in provider-to-TTS segmentation preserving structured envelope validation, playback ACK, source spans, epoch cancellation and barge-in safety. Benchmark first audible audio on a real client.
- No production promotion without explicit approval.
