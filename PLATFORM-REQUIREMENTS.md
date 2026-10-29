# Platform and voice acceptance requirements

## Scope
- Target browser, Android application, and Windows/desktop application using one authenticated backend. Native application packaging remains future work; browser testing is not native-device verification.
- LLM inference uses the available remote Codex/API connection. No local LLM installation or automatic local LLM fallback.
- Approved audio direction: package local STT and TTS engines/models to reduce dependence on changing remote speech services. The user explicitly requested proceeding with local STT/TTS as a foundation for their own audio system. This is an audio-only scope revision, not authorization to install local LLMs. Model selection, installation, benchmarks and deployment must each be reported from actual verification.

## Consistent output voice
- Generate speech on the server using the same provider, voice identifier, language, rate and pitch for every client.
- Clients play the server-generated audio; never silently replace it with browser speechSynthesis, Android TextToSpeech, Windows system voices or espeak.
- Expose the active speech provider and voice identity in public-to-authenticated configuration. A synthesis failure must be reported instead of switching voice invisibly.
- Voice identity consistency is the requirement, not a guarantee of identical acoustic output from different speakers/headphones or a claim to clone Claude's proprietary voice.

## Input and transport
- Keep capture/transcription adapters separate from conversation, Codex and synthesis. Use one documented message/token/playback-ack protocol across clients.
- Optional browser remote SpeechRecognition is experimental and browser-dependent; it is not the sole cross-platform input design or proof of Android WebView support.
- Keep API transcription as an explicit adapter when configured. A packaged local speech adapter may be selected explicitly under the revised audio scope; do not silently switch between local and remote processing.

## Packaged audio reliability
- Separate stable application interfaces from STT/TTS implementations. Pin engine/model versions and dependencies, verify model hashes and redistribution permissions, and test upgrades before replacing the installed package.
- Distinguish local-to-server audio inference from on-device inference. One server-side package can serve browser/Android/desktop with the same voice; separately packaging models for each device requires platform-specific performance and lifecycle testing.
- Local STT/TTS does not make Codex offline and does not automatically provide expressive speech or reliable emotion recognition.

## Acceptance gates
- Verify login, microphone permission/lifecycle, repeated spoken turns with retained context, playback, stop/mute and interruption independently on each target platform.
- Distinguish fixture transcripts and protocol tests from actual microphone transcription and physical audible playback.
- Do not claim a complete Claude clone, native app readiness, full-duplex barge-in, or cross-device acceptance until each is genuinely tested.
