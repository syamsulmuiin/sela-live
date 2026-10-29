"""Bounded stateful ASR adapter; keys must be server-generated UUID hex.

Shares PersistentASR's shielded single-producer admission, limits, idle reap,
CPU recycling, readiness and quiescence. A recycled/missing worker starts with
an empty native stream and therefore accepts the complete current WAV prefix.
"""
import asyncio
from .stream_asr import PersistentASR
from .zipformer_state import validate_key


class StatefulASR(PersistentASR):
    def __init__(self, args, env, **kwargs):
        if kwargs.get('final_args') is not None and kwargs['final_args'] != args:
            raise ValueError('Stateful preview and final require the same worker')
        # Never inherit credentials, proxies, Python injection or loader settings.
        safe = {k: v for k, v in env.items()
                if k in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'SYSTEMROOT')}
        safe.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                    HF_HUB_DISABLE_TELEMETRY='1', OMP_NUM_THREADS='2',
                    OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2',
                    TOKENIZERS_PARALLELISM='false')
        super().__init__(args, safe, **kwargs)
        self._forget_wait_seconds = 3

    @staticmethod
    def _frame(wav, key, op):
        validate_key(key)
        if op != 2:
            if type(wav) is not bytes or not 0 < len(wav) <= 640044:
                raise ValueError('Invalid ASR payload')
        return b'ST1' + bytes([op]) + bytes.fromhex(key) + wav

    def _validate_payload(self, payload):
        if type(payload) is not bytes or not 20 <= len(payload) <= 640064:
            raise ValueError('Invalid stateful ASR payload')
        if payload[:3] != b'ST1' or payload[3] not in (0, 1, 2):
            raise ValueError('Invalid stateful ASR frame')
        if payload[3] == 2 and len(payload) != 20:
            raise ValueError('Invalid ASR forget payload')
        if payload[3] != 2 and len(payload) == 20:
            raise ValueError('Empty ASR WAV')

    async def preview(self, wav, *, stream_key):
        return await super().preview(self._frame(wav, stream_key, 0))

    async def transcribe(self, wav, *, stream_key):
        return await super().transcribe(self._frame(wav, stream_key, 1))

    async def forget(self, stream_key):
        payload = self._frame(b'', stream_key, 2)
        from .speech import LocalAudioBusy
        if self._closing or self._final_waiting:
            raise LocalAudioBusy('ASR lifecycle admission busy')
        self._final_waiting = True
        try:
            async with asyncio.timeout(self._forget_wait_seconds):
                # Never cancel or steal the capacity of an existing producer.
                if self._job and not self._job.done():
                    await asyncio.shield(self._job)
                if self.pid is None:
                    return
                # No await between admission and producer registration.
                job = asyncio.create_task(self._exchange(payload, start=False))
                self._job = job
                job.add_done_callback(lambda task: None if task.cancelled() else task.exception())
                await asyncio.shield(job)
        except TimeoutError as exc:
            raise LocalAudioBusy('ASR still active before forget') from exc
        finally:
            self._final_waiting = False
