"""Sela Live phase-one TTS; native Supertonic engine, not a new trained model."""
from dataclasses import dataclass


@dataclass(frozen=True)
class NativeSettings:
    """Only the documented 8/10/12 quality trials; pacing stays unchanged."""
    total_steps: int = 8

    def __post_init__(self):
        if type(self.total_steps) is not int or self.total_steps not in (8,10,12):
            raise ValueError('Unsupported bounded quality setting')


class SelaLiveTTS:
    """TTS-only adapter seam; no STT, LLM, HTTP exposure or production switch."""
    def __init__(self,settings=None,*,python='/root/voice-lab/autonomous/expressive-tts/venv/bin/python',assets='/root/voice-lab/autonomous/expressive-tts/assets',manifest='/root/voice-lab/autonomous/expressive-tts/assets-manifest.json',native_call=None,coordinator=None):
        import asyncio,os
        from pathlib import Path
        from voice_app.speech import _bounded_process
        settings=NativeSettings() if settings is None else settings
        if type(settings) is not NativeSettings:raise ValueError('Explicit native settings required')
        self.settings=NativeSettings(settings.total_steps)
        self.python,self.assets,self.manifest=map(Path,(python,assets,manifest))
        if (not all(p.is_absolute() for p in (self.python,self.assets,self.manifest))
                or not self.python.is_file() or not os.access(self.python,os.X_OK)
                or not self.assets.is_dir() or not self.manifest.is_file()):
            raise ValueError('Explicit installed speech runtime/assets required')
        self.worker=Path(__file__).absolute().with_name('worker.py')
        self._native=_bounded_process if native_call is None else native_call
        from .admission import AdmissionCoordinator
        self._coordinator=AdmissionCoordinator() if coordinator is None else coordinator
        self._slot=asyncio.Lock()
        self.identity={'adapter':'Sela Live TTS phase one','engine':'supertonic3','profile':'conditioning_b anchor','language':'id','sample_rate':44100,'total_steps':self.settings.total_steps,'stage':'candidate'}

    @staticmethod
    def _profile(profile_id):
        allowed=('conditioning_b',)+tuple(f'conditioning_b_{a}_{i}' for a in ('calm','active') for i in ('low','medium','high'))
        if type(profile_id) is not str or profile_id not in allowed:
            raise ValueError('Unknown server-owned conditioning selection')
        return profile_id

    async def _one(self,text,profile_id):
        import asyncio,json,io,wave
        from voice_app.supertonic3_worker import validate_request
        from voice_app.speech import normalize_pronunciation,_offline_env,_normalize_wav
        profile_id=self._profile(profile_id)
        validate_request({'text':text})
        spoken=normalize_pronunciation(text);validate_request({'text':spoken})
        payload=json.dumps({'text':spoken},ensure_ascii=False).encode('utf8')
        args=(str(self.python),'-I',str(self.worker),'tts',str(self.assets),str(self.manifest),profile_id,str(self.settings.total_steps))
        from .admission import LiveTTSBusy
        if self._slot.locked():raise LiveTTSBusy('Evaluation already active; zero queued waiters')
        with self._coordinator.claim():
            await self._slot.acquire()
            try:
                task=asyncio.create_task(self._native(args,payload,2_646_044,env=_offline_env(),native_diagnostics=True))
                try:
                    async with asyncio.timeout(90):
                        while True:
                            self._coordinator.check_production()
                            done,_=await asyncio.wait({task},timeout=self._coordinator.poll_interval)
                            if done:
                                self._coordinator.check_production()
                                raw=task.result()
                                break
                except BaseException:
                    if not task.done() and not task.cancelling():task.cancel()
                    while not task.done():
                        try:await asyncio.shield(task)
                        except asyncio.CancelledError:continue
                        except Exception:break
                    if task.done() and not task.cancelled():task.exception()
                    raise
                raw=_normalize_wav(raw)
                with wave.open(io.BytesIO(raw)) as reader:
                    if reader.getframerate()!=44100:raise ValueError('Native sample rate changed')
                return raw
            finally:
                self._slot.release()

    async def synthesize(self,text,*,profile_id='conditioning_b'):
        return await self._one(text,profile_id)

    async def synthesize_chunks(self,text,*,profile_id='conditioning_b'):
        from voice_app.tts_chunks import source_parts,synthesis_source
        from voice_app.speech import normalize_pronunciation
        from voice_app.audio_continuity import trim_boundary_silence
        profile_id=self._profile(profile_id)
        parts=source_parts(text,normalize_pronunciation)
        for index,part in enumerate(parts):
            audio=await self._one(synthesis_source(part),profile_id)
            if len(parts)>1:
                audio=trim_boundary_silence(audio,first=index==0,last=index==len(parts)-1,max_output_bytes=2_646_044)
            yield part,audio,index==len(parts)-1

