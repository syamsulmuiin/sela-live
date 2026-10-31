"""One immutable request drives model-aware natural utterance synthesis.

Candidate only: no production feature switch and no new trained emotion claim.
"""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePins:
    python: str
    assets: str
    asset_manifest: str
    worker: str
    duration_worker: str
    worker_sha256: str
    duration_worker_sha256: str
    source_manifest_sha256: str


class NaturalTTS:
    def __init__(self, runtime, *, duration_factory=None, native_call=None, coordinator=None,
                 resolve_delivery=None):
        from .backend import SelaLiveTTS, NativeSettings
        from .duration import DurationSession
        if type(runtime) is not RuntimePins:
            raise ValueError('Explicit immutable natural runtime pins required')
        self.runtime = runtime
        self._verify_sources()
        self.duration_factory = DurationSession if duration_factory is None else duration_factory
        self.resolve_delivery = resolve_delivery
        self.renderer = SelaLiveTTS(NativeSettings(12), python=runtime.python,
            assets=runtime.assets, manifest=runtime.asset_manifest,
            native_call=native_call, coordinator=coordinator)
        self.renderer.worker = Path(runtime.worker)
        self.coordinator = self.renderer._coordinator
        self.identity = {'adapter':'Sela Live natural candidate', 'policy':'natural_v1',
            'total_steps':12, 'independent_emotion_rendering_proven':False}
        self.last_plan = None
        self._active = False

    def _verify_sources(self):
        from voice_app.approved_conditioning import _read_pinned
        runtime = self.runtime
        try:
            _read_pinned(Path(runtime.worker), runtime.worker_sha256)
            _read_pinned(Path(runtime.duration_worker), runtime.duration_worker_sha256)
            _read_pinned(Path(runtime.worker).with_name('upstream-manifest.json'),
                         runtime.source_manifest_sha256)
        except (OSError, ValueError):
            raise ValueError('Reviewed natural runtime source unavailable') from None

    async def synthesize_chunks(self, request):
        from .utterance import NaturalSpeechPlanner, Prediction, SpeechRequest
        from voice_app.speech import normalize_pronunciation
        from voice_app.audio_continuity import trim_boundary_silence
        if type(request) is not SpeechRequest:
            raise ValueError('One SpeechRequest is required')
        from .admission import LiveTTSBusy
        if self._active:
            raise LiveTTSBusy('Natural request already active; zero queued waiters')
        self._active = True
        try:
            runtime = self.runtime
            self._verify_sources()
            from contextlib import AsyncExitStack
            async with AsyncExitStack() as stack:
                session = None
                async def predict(text, profile_id):
                    nonlocal session
                    # The planner resolves delivery once, before its first DP query.
                    # Open the fixed-profile worker only after that snapshot exists.
                    if session is None:
                        session = await stack.enter_async_context(self.duration_factory(
                            worker_sha256=runtime.duration_worker_sha256,
                            source_manifest_sha256=runtime.source_manifest_sha256,
                            python=runtime.python, assets=runtime.assets,
                            model_manifest=runtime.asset_manifest, worker=runtime.duration_worker,
                            profile_id=profile_id, coordinator=self.coordinator))
                    value = await session.predict(text, profile_id=profile_id)
                    return Prediction(value['seconds'], value['tokens'])
                plan = await NaturalSpeechPlanner(normalize=normalize_pronunciation,
                    predict=predict,resolve_delivery=self.resolve_delivery).plan(request)
            # DP process is closed/reaped before allocating the full TTS graph set.
            self.last_plan = plan
            # Empty lexical spans carry source/ACK whitespace only. They must
            # never allocate a native worker or create a silent playback chunk.
            render_groups = []
            pending = ''
            for group in plan.groups:
                if not group.native_text.strip():
                    pending += group.source_text
                    continue
                render_groups.append((pending + group.source_text, group))
                pending = ''
            if pending:
                if not render_groups:
                    raise ValueError('No spoken context')
                source, group = render_groups[-1]
                render_groups[-1] = (source + pending, group)
            for index, (source, group) in enumerate(render_groups):
                self._verify_sources()
                audio = await self.renderer.synthesize(group.source_text.strip(),
                                                       profile_id=plan.delivery.profile_id)
                last = index == len(render_groups)-1
                if len(render_groups)>1:
                    audio = trim_boundary_silence(audio, first=index==0, last=last,
                                                 max_output_bytes=2_646_044)
                yield source, audio, last
        finally:
            self._active = False
