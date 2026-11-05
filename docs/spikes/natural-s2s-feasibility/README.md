# Natural speech-to-speech: bounded feasibility checkpoint

Latest delivery update, 2026-11-05: the two P0 repairs (U+06DD compatibility and ACKed interrupted context) are now installed with a passing bounded installed-root native parity check. See `docs/LOCAL-S2S-P0-INSTALL-STATE.json`. Earlier investigation/source-only statements below are dated checkpoint history, not the latest P0 deployment state. The broader local acoustic-intent, automatic spoken-interruption and human-device goals remain open; cloud audio remains rejected.

Status: **investigation, not implemented or released**. Recorded 2026-11-01T14:29:09Z against source commit `59335e629efa41cd5f41915a15f190b22c3cab55`. This document does not approve a production change or a claim that the current application is a native audio-to-audio model.

## User outcome and scope

The user wants a working, natural speech-to-speech product after GitHub publication, not separately working STT, LLM and TTS demos. The reasoning backend may be local or API-based. Keep the current API/Codex brain unless the user explicitly selects a different backend; this requirement does not authorize downloading or silently falling back to a local LLM.

Distinguish these architectures honestly:

1. **Native audio conversation front end with a delegated reasoning backend.** An audio-capable model handles streaming listening/speaking and may delegate reasoning/tools to the chosen LLM. The audio model remains an additional model, not an arbitrary text LLM magically gaining audio capability. Its exact acoustic understanding, latency, delegation fidelity, Indonesian quality and interruption behavior must be demonstrated. It may change the accepted Supertonic B voice, billing and where audio is processed.
2. **Modular end-to-end voice experience.** Retain transcription/reasoning/synthesis internally, but jointly manage retained acoustic evidence, incremental response, turns, cancellation, playback and history. This can be evaluated as a complete speech product, but must not be called a jointly trained native audio-to-audio model. Text-only punctuation restoration is not proof of acoustic intonation understanding.

## Current evidence and unresolved gaps

- Installed Zipformer model token inventory `/root/voice-lab/autonomous/zipformer/model/tokens.txt` contains no `?`, `!`, or `.` token or token ending in one of those characters. Verified with a local read-only vocabulary scan. The worker emits the recognizer's text; absence of punctuation is not proof of a microphone fault.
- `voice_app/prosody.py` currently leaves pitch/F0 and emotion unevaluated. Do not claim those diagnostic fields represent measured acoustic intent.
- Current half-duplex capture pause and playback ACK tests are useful but do not prove natural full-duplex interruption with a physical microphone and speaker.
- The previous Al-Fatihah ornament repair only maps U+FD3F/U+FD3E. The newly supplied U+06DD verse-marker case is **unresolved**: only the introductory sentence was heard and the latest failure was classified `invalid_text` / exit 1. Preserve this as an output-completeness regression, not as an already-fixed case. No private full reply or audio is included here.
- User acceptance of one exact Arga B sample is not blanket acceptance of future text, emotional acting, Arabic pronunciation or device-level speech interaction.
- Read-only hardware discovery: 4 logical CPUs; MemTotal 8131764 KiB, MemAvailable 2321648 KiB at the checkpoint; no `nvidia-smi` binary and no DRM GPU device exposed. This is not proof that every possible GPU interface is absent, but a GPU-dependent local native model is not an established runnable option on this host.
- A configured credential's presence does not establish native audio API access, a valid billing project or authorization to send microphone recordings to a new processor. No credential values were recorded; no new audio API request was made.

The independent read-only actual-code audit has completed (`deleg_37223c55`, baseline commit as above). Parent spot-checks confirmed the text-only prompt/history construction at `voice_app/server.py:469–489`, the local capture pause and non-local-only automatic interrupt at `web/app.js:462–499`, and the assembled Codex completion at `voice_app/providers.py:21–48`. The audit ran no inference, downloads or test suites and changed no files/services. Parent changes for this spike are documentation only; no production source, service or model changes have been made.

### Audit conclusions and evidence limits

- The current implementation is a modular, half-duplex voice application, not an already-native audio-to-audio system. Browser processing precedes the PCM called original input; do not call it untouched microphone sensor audio.
- Full Codex completion and full-response collection precede TTS, and natural planning precedes first render (`voice_app/server.py:207–219,549–564`; `sela_live_tts/natural.py:67–104`). The audit also found native WAV collection until worker completion (`voice_app/speech.py:105–143`). These are code-level latency bottlenecks, not measured end-to-end timings.
- Local capture is suppressed while awaiting/speaking, so automatic spoken interruption is not implemented on that route. The stop button is not equivalent evidence. Remote work is shielded; local playback stopping does not prove Codex inference has stopped.
- Acknowledged `heard_text` is recorded, but the next reasoning prompt excludes interrupted assistant turns entirely (`voice_app/session_core.py:167–172`; `voice_app/server.py:487`). This loses already-heard conversational context and must be tested in the eventual design.
- Lease ownership, bounded prefetch/ACK and terminate/kill/reap defenses are existing useful safeguards. Their fixture passes do not measure cancellation latency or prove human full-duplex operation.
- Clean-host reconstruction still has fixed-root/platform dependencies, while provider coverage includes mocks and opt-in inference. No clean-host, physical microphone, human normal-tempo or acoustic-intent acceptance was newly exercised by this audit.

The auditor proposed exploratory targets: 30 varied reply fixtures with complete source coverage; 50 consented human utterances with WER <=15% and false turn split <=5%; 20 word-identical intent pairs with balanced accuracy >=85%; 30 real-turn latency samples with speech-end to audible response p50 <=2s/p95 <=4s; and 20 spoken interruptions with onset-to-stop p95 <=300ms. These are **unmeasured proposals, not achieved results or approved release criteria**. Confirm feasible thresholds and data collection before treating them as a binding test plan. The full system and human/device gates below remain mandatory in scope.

## Primary-source research, not a tested implementation

- OpenAI's GPT-Live getting-started documentation describes streaming, full-duplex voice with independently selected backend reasoning. Client delegation can connect any model, agent harness or service. This is a relevant candidate architecture, not evidence it is accessible with this account or that it meets Indonesian/voice fidelity requirements. The documented API route needs an OpenAI project API key and bills voice duration separately from backend usage.
- The same primary documentation recommends WebRTC media tracks plus a JSON event channel and a trusted server that retains the permanent API key. Any prototype must retain existing application authentication and scope client credentials appropriately.
- The official Hermes plugin catalog currently lists community plugin `hermes-talk` v0.22.1, pinned at `790f6d2`, requiring Hermes >=0.21.7. It describes a GPT-Live Codex-subscription lane and an explicitly selected API-billed lane with no automatic paid fallback. **Its disclosure also says the subscription GPT-Live lane uses an undocumented Codex realtime endpoint outside the vendor's published API terms and may stop working.** Treat it as a separately risk-gated experiment, not an established stable or officially documented OpenAI API authentication route for this product. Installed host/account compatibility and plugin security have not been verified here. No Hermes upgrade, plugin installation, configuration change, token handling or switch to Talk has been authorized by this checkpoint.

Primary references actually retrieved (recheck before implementation):

- `https://developers.openai.com/api/docs/guides/live`
- `https://developers.openai.com/api/docs/models/gpt-live-1`
- `https://hermes-agent.nousresearch.com/docs/plugins/hermes-talk`

## Local feasibility checkpoint (not new production capabilities)

The local-only spike completed and the parent reran its scratch diagnostic `/root/.hermes/cache/scratch/local_dsp_feasibility.py` using the existing audio-runtime interpreter. Four in-memory synthetic cases (silence, 200 Hz tone, white noise, clipped square), each 20 seconds, exercised a frame-local FFT autocorrelation primitive with NumPy 2.5.4. Parent results were 15.765–148.709 ms DSP per case and peak process RSS 61288 KiB. This measures that diagnostic process, **not a production worst-case resource limit**. Both tone and clipped square yielded periodic frequency 200 Hz; silence/noise yielded none. It does not prove robust speech F0, speech activity, acoustic intent or near-end/echo separation. No dependencies were added and no microphone audio was sent anywhere.

Recommended bounded slices remain:

1. Retain validated acoustic observations with their final-generation/utterance identity, without storing raw PCM or asserting emotion/question intent. Acoustic-intent classification needs consented word-identical human contrast recordings; they are not currently available. Do not guess `?` from a rise or `!` from loudness. Do not silently upload raw acoustic contours/biometric features to the text brain as a substitute for local voice analysis.
2. Measure a local-clock latency ledger before changing endpoint or streaming policy. A parent source check of the installed Hermes `_CodexCompletionsAdapter.create` (`agent/auxiliary_client.py:1555–1621`) confirms it consumes a Responses event stream and returns one assembled completion even when chat-style `stream=True` is supplied. Merely changing that flag is not a delta-streaming fix; a supported streaming transport and incremental structured-delivery contract are separate feasibility gates. Do not modify Hermes authentication or copy credentials to bypass this boundary.
3. Keep any browser near-end detector isolated from ASR admission: detect → stop playback → cancel/reap-ready → admit buffered onset. The current immediate `interrupted` event is not a native-reap acknowledgment. Preserve canonical lease ownership and ASR quiescence; do not remove capture gating and assume browser `echoCancellation:true` proves usable double-talk. Automatic speakerphone barge-in remains default-off until real echo/near-end fixtures pass.

The first implemented source slices are U+06DD speech-copy compatibility and acknowledged context after interruption. Their current evidence is `docs/LOCAL-S2S-REGRESSION-VERIFICATION.json` and `docs/LOCAL-S2S-CONTEXT-VERIFICATION.json`; full audible delivery, local intent, automatic barge-in, deployment and user acceptance remain separate open gates.

## Completion gates for any selected architecture

These are requirements, not results. A GitHub push alone satisfies none of them.

1. **Reproducible installation:** a clean checkout follows documented setup and reaches the working authenticated application with explicitly configured credentials/assets. Never commit keys, cookies, production databases, model weights or private recordings. Clearly list network, hardware, asset and provider prerequisites.
2. **Acoustic understanding:** normal-tempo Indonesian utterances and minimal word-identical pairs with different intended punctuation/affect are tested from actual recordings. Record ground truth, uncertainty and errors. A punctuation heuristic or synthetic-only test is not acceptance.
3. **Complete response delivery:** dynamic responses, including the exact U+06DD regression, complete without silent truncation. Display/history must represent what was actually spoken/acknowledged rather than falsely marking an entire response as heard after a prefix.
4. **Real latency:** report cold/warm timings for end-of-user-speech, first audible response, sustained output gaps and cancellation; state hardware, provider/network and sample count. Set numerical pass thresholds after measuring a feasible candidate, not by inventing results.
5. **Turn-taking and interruption:** demonstrate actual voice interruption, bounded cancellation/reaping and immediate recovery for the next turn, including playback echo and speech-end ambiguity. A stop button or muted microphone is not proof of full duplex.
6. **Conversation integration:** microphone → acoustic interpretation → selected reasoning backend when required → audible answer → playback/ACK/history → next microphone turn is exercised across multiple turns, with disconnect/error/cancel recovery and the existing security/admission guarantees intact.
7. **Naturalness:** controlled same-text listening comparisons plus varied unseen conversations, followed by user acceptance on the real target device. Do not claim new emotional acting, pronunciation/tajwid correctness or intonation recognition from fixture passes.
8. **Publication and deployment:** review exact tested artifacts; install with bounded failure recovery; read back the running target; verify remote Git SHA and clean-install reproducibility; explicitly disclose any remaining physical-device or authenticated-public checks. Only then announce a completed rollout.

## Decision: audio processing remains local

The user selected **"Tidak, pemrosesan suara harus tetap lokal"** in the explicit architecture/consent question. Therefore the cloud audio-native path above is **rejected for this task**, not pending authorization and not a fallback. Do not send microphone recordings to any external voice provider, install Talk as a workaround or incur new native-audio API usage. Keep the selected API/Codex reasoning backend, chosen local speech basis and existing production deployment while evaluating local changes. This decision does not authorize a new local LLM.

The active target is an integrated local-audio speech product with retained acoustic evidence, responsive turns, interruption, faithful spoken history and complete dynamic output. With a text-only remote reasoning backend, describe its modular architecture honestly; do not relabel it a jointly trained native audio-to-audio model. Native local candidates, if later considered, still require hardware/language/provenance and explicit component-selection gates before any installation.

Next bounded work: reproduce and repair the U+06DD output blocker with exact-source/tokenizer evidence and TDD; separately evaluate local acoustic-information and latency/turn-taking seams without replacing speech engines or relaxing safety guards. All human normal-tempo, acoustic intent, listening and physical-device acceptance gates remain open.
