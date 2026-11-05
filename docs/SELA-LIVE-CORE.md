# Sela Live core 0.1.0

## Ownership, not a new acoustic model

`audio/stt.py` is the former streaming Zipformer implementation moved without algorithm changes. The legacy import is a shim; standalone `-I` workers add only their installed project root to find the same package. The recognizer and weights remain sherpa/Zipformer Indonesian int8. The core retains bounded PCM, incremental feeding, prefix hash validation, stream finalization and reset behavior.

`audio/tts.py` is the extracted natural worker synthesis implementation, not a facade around a duplicate implementation. It owns text/request limits, exact baseline controls, tokenizer/mask checks, selected-style duration preflight and the single-context SDK call. NumPy stays lazy inside synthesis. The old worker functions delegate to a hash-verified load of this file, including under `-I`; no installed package lookup is trusted. Original guard runtime/main and upstream SDK are unchanged. `validate_text` and `controlled_runtime` remain compatible APIs.

## Exact native trust chain

1. Application `SelaLiveBridge` verifies the externally bound natural runtime manifest and its exact `NATURAL_RUNTIME_FILES` set before candidate imports. Installer has an equal explicit set.
2. `natural_worker.py` binds `source_pins.py` and `upstream-manifest.json` by literal SHA256. Existing SDK/live/guard validation runs unchanged.
3. Before guard import, the worker requires `core_root == HERE.parent` and the exact five-file core set: package initializers, owned STT/TTS, legacy STT shim. Each regular symlink-free bounded file is hashed through the pinned helper. A changed STT file therefore also prevents TTS startup: this binds one application source release.
4. Compatibility calls verify again before loading `audio/tts.py` by absolute location. Core package initializers are not executed by this native import. `guard.main` still owns isolation/limits before native SDK/NumPy sessions.

Runtime exceptions enforce pins; no security assertion depends on Python `assert` or optimization mode. Hash validation is local integrity/review binding, not a remote signature or protection from a privileged writer changing both code and approved digests.

## Relocation recipe (offline staging only)

Use `tools/build_live_runtime.py` with explicit source, installed-target and new disjoint output roots, and externally reviewed source worker hashes. Builder verifies the five-file group, copies it byte-exact, sets `core_root` to the installed app root, relocates existing guard/live/SDK roots, rehashes the generated upstream manifest, rewrites only workers' `MANIFEST_SHA` literals and generates an exhaustive runtime manifest including the new files. Source paths/manifests are never overwritten with production paths. The manifest transform report names the core relocation when present.

The native builder does not produce the whole application deployment plan: add reviewed current `voice_app/speech.py` and `voice_app/sela_live_bridge.py`, preserve the three native guards and license/provenance, bind all files/modes and final selector digests to the existing installer plan. Review must bind installer/testsource/baseline/runtime/payload/plan hashes, not historical approvals. Parent/operator owns production capture, approval, apply and push.

`Installer.check_inventory` includes `sela_live` even before the tree exists. Snapshot `exists:false` records support new files; rollback removes those files before recovery restart. Existing destinations must match frozen baseline hashes/modes. No new service or new host-audio lock is introduced.

## Verification boundaries

Core unit tests, normal/optimized tamper subprocesses, pure native dependency fixtures, STT fixture protocol/lifecycle tests and installer disposable-root tests are inference-free. They establish code path, controls, cancellation and package behavior, **not** acoustic equality on models. Native B WAV equivalence, real STT accuracy, browser live interaction, physical audio, production deployment/readback and fresh-host installation remain independent gates.

Existing `scripts/` tooling is baseline reconstruction, not the installed natural-policy upgrade mechanism. It is intentionally not expanded into another installer. README uses the reviewed builder/installer path; neither bootstrap nor configure is safe to run against production. This integration did not alter auth, brain, models, original guard, SDK, Arabic normalization or ACK logic.
