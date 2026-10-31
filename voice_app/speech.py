"""Remote Edge neural TTS; explicit espeak and optional remote API STT.

The Edge worker runs only in the isolated speech venv, never the Hermes runtime.
"""
import asyncio
import io
import os
import shutil
import wave
import json
import subprocess
from pathlib import Path
from contextlib import AsyncExitStack

AUDIO_ROOT = Path(__file__).resolve().parent.parent / 'audio-runtime'
LOCAL_WORKER = Path(__file__).resolve().with_name('local_audio_worker.py')
LOCAL_RETRY_WAIT = 3


class LocalAudioBusy(RuntimeError):
    """One local worker is active and the bounded cleanup wait is exhausted."""


class NoSpeechDetected(ValueError):
    """The recognizer returned no usable speech, not a busy worker."""


def _runtime_ready(python, model, action):
    """A local, bounded import probe. Never obtains packages or models."""
    required = ([model / name for name in ('model.bin', 'config.json', 'tokenizer.json')]
                if action == 'stt' else [model / (role+'-iter-100000-avg-15-chunk-32-left-256.int8.onnx') for role in ('encoder','decoder','joiner')] + [model/'tokens.txt']
                if action == 'zipformer' else [model, Path(str(model) + '.json')])
    if not Path(python).is_file() or not all(p.is_file() and p.stat().st_size for p in required):
        return False
    try:
        worker, command = (Path(__file__).with_name('zipformer_worker.py'), 'check') if action == 'zipformer' else (LOCAL_WORKER, 'check-' + action)
        result = subprocess.run((python, '-I', str(worker), command, str(model)),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, env=_offline_env())
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _offline_env():
    # Do not pass API keys, OAuth or proxy settings to the audio interpreter.
    return {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'SYSTEMROOT')} | {
        'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1', 'HF_HUB_DISABLE_TELEMETRY':'1',
        'OMP_NUM_THREADS':'2', 'OPENBLAS_NUM_THREADS':'2', 'MKL_NUM_THREADS':'2',
        'TOKENIZERS_PARALLELISM':'false'}

MAX_WAV_BYTES = 4_000_000
MAX_MP3_BYTES = 2_000_000
TTS_TIMEOUT = 45
EDGE_PYTHON = '/root/voice-web-venv/bin/python'
FFMPEG = '/root/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin/ffmpeg'


class NativeTTSFailure(RuntimeError):
    """Only allowlisted metadata crosses the native failure boundary."""
    def __init__(self, reason, returncode=None):
        from .supertonic3_worker import SAFE_FAILURE_REASONS
        self.reason = reason if type(reason) is str and reason in SAFE_FAILURE_REASONS else 'native_error'
        self.returncode = returncode if type(returncode) is int and -255 <= returncode <= 255 else None
        super().__init__('Local TTS failed: ' + self.reason)


async def _bounded_process(args, payload, limit, *, env=None, native_diagnostics=False):
    """Drain while writing; terminate and reap on errors, timeout or cancellation.

    Native stderr is consumed concurrently, never retained or forwarded. Only a
    complete short allowlisted protocol line is accepted; overlong lines drop.
    Legacy engines keep stderr=DEVNULL and their original generic exception.
    """
    proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE if native_diagnostics else asyncio.subprocess.DEVNULL, env=env)
    reason = None
    async def drain_stderr():
        nonlocal reason
        from .supertonic3_worker import SAFE_FAILURE_REASONS, FAILURE_PREFIX
        markers = {FAILURE_PREFIX + value.encode('ascii'): value for value in SAFE_FAILURE_REASONS}
        tail, dropping = b'', False
        while piece := await proc.stderr.read(8192):
            lines = piece.split(b'\n')
            for index, part in enumerate(lines):
                if not dropping:
                    if len(tail) + len(part) <= 128:
                        tail += part
                    else:
                        dropping, tail = True, b''
                if index < len(lines) - 1:
                    if not dropping and tail in markers:
                        reason = markers[tail]
                    tail, dropping = b'', False
    async def write():
        try:
            proc.stdin.write(payload)
            await proc.stdin.drain()
        finally:
            proc.stdin.close()
    writer = asyncio.create_task(write())
    stderr_reader = asyncio.create_task(drain_stderr()) if native_diagnostics else None
    try:
        chunks, size = [], 0
        while True:
            piece = await proc.stdout.read(min(65536, limit + 1 - size))
            if not piece:
                break
            size += len(piece)
            if size > limit:
                raise ValueError('TTS output exceeds bounded output limit')
            chunks.append(piece)
        try:
            await writer
        except (BrokenPipeError, ConnectionResetError):
            if not native_diagnostics:
                raise
            # A child can reject admission before reading stdin. Preserve its
            # safe failure marker/exit rather than replacing it with a pipe error.
        await proc.wait()
        if stderr_reader:
            await stderr_reader
        if proc.returncode or not size:
            if native_diagnostics:
                raise NativeTTSFailure(reason or ('native_error' if proc.returncode else 'empty_output'), proc.returncode)
            raise RuntimeError('Speech subprocess failed; no automatic fallback')
        return b''.join(chunks)
    finally:
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        writer.cancel()
        if stderr_reader:
            stderr_reader.cancel()
        await asyncio.gather(writer, *([stderr_reader] if stderr_reader else []), return_exceptions=True)


def _normalize_wav(raw):
    with wave.open(io.BytesIO(raw), 'rb') as source:
        params = source.getparams()
        frames = source.readframes(MAX_WAV_BYTES)
    if not frames or params.nchannels != 1 or params.sampwidth != 2:
        raise ValueError('Invalid speech WAV')
    out = io.BytesIO()
    with wave.open(out, 'wb') as target:
        target.setparams(params._replace(nframes=0))
        target.writeframes(frames)
    result = out.getvalue()
    if len(result) > MAX_WAV_BYTES:
        raise ValueError('TTS WAV exceeds bounded output limit')
    return result


def normalize_pronunciation(text):
    """Deliberate Indonesian reading hints, never a rewrite of chat/history text."""
    import re
    from datetime import date
    digits = ('nol', 'satu', 'dua', 'tiga', 'empat', 'lima', 'enam', 'tujuh', 'delapan', 'sembilan', 'sepuluh', 'sebelas')
    def number(n):
        if n < 12:
            return digits[n]
        if n < 20:
            return number(n - 10) + ' belas'
        for scale, name in ((1000000, 'juta'), (1000, 'ribu'), (100, 'ratus'), (10, 'puluh')):
            if n >= scale:
                head, tail = divmod(n, scale)
                prefix = ('se' + name if head == 1 and scale in (100, 1000) else number(head) + ' ' + name)
                return prefix + (' ' + number(tail) if tail else '')
    def currency(match):
        original = match.group()
        value = re.sub(r'^Rp\s*', '', original)
        if not re.fullmatch(r'(?:0|[1-9]\d{0,8}|[1-9]\d{0,2}(?:\.\d{3}){1,2})(?:,\d{1,2})?', value):
            return original
        whole, _, fraction = value.partition(',')
        result = number(int(whole.replace('.', ''))) + ' rupiah'
        return result + (' koma ' + ' '.join(digits[int(c)] for c in fraction) if fraction else '')
    months = ('Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember')
    def calendar(match):
        try:
            d = date.fromisoformat(match.group())
            return number(d.day) + ' ' + months[d.month-1] + ' ' + number(d.year)
        except ValueError:
            return match.group()
    text = re.sub(r'(?<![\w/])Rp\s*\d[\d.,]*\d|(?<![\w/])Rp\s*\d', currency, text)
    text = re.sub(r'(?<![\w./:-])\d{4}-\d{2}-\d{2}(?![\w./:-])', calendar, text)
    # Keep identifiers, leading zeroes, paths, versions, addresses and malformed currency intact.
    text = re.sub(r'(?<![\w./,:-])(?:0|[1-9]\d{0,8})(?![\w/,:-]|\.\d)', lambda m: number(int(m.group())), text)
    lexicon = json.loads(Path(__file__).with_name('pronunciation-id.json').read_text(encoding='utf-8'))
    return re.sub(r'\b(?:' + '|'.join(re.escape(k) for k in lexicon) + r')\b', lambda m: lexicon[m.group()], text)


class Speech:
    tts_label = 'espeak-ng · bahasa Indonesia · rule-based non-AI'

    def __init__(self, environ=None, transport=None):
        env = os.environ if environ is None else environ
        self._backend = env.get('VOICE_TTS_BACKEND', 'piper')
        self._style_profile = env.get('VOICE_SUPERTONIC3_STYLE_PROFILE', 'original')
        if (self._style_profile not in ('original', 'conditioning_b')
                or (self._style_profile != 'original' and self._backend != 'supertonic3')):
            raise ValueError('VOICE_SUPERTONIC3_STYLE_PROFILE requires original or conditioning_b with Supertonic3')
        automatic_choice = env.get('VOICE_AUTOMATIC_CONDITIONING', '0')
        if type(automatic_choice) is not str or automatic_choice not in ('0', '1'):
            raise ValueError('VOICE_AUTOMATIC_CONDITIONING requires 0 or 1')
        self._automatic_conditioning_enabled = automatic_choice == '1'
        if self._automatic_conditioning_enabled and (self._backend != 'supertonic3' or self._style_profile != 'conditioning_b'):
            raise ValueError('VOICE_AUTOMATIC_CONDITIONING requires Supertonic3 conditioning_b')
        self._natural_policy = 'VOICE_SELA_LIVE_POLICY' in env
        if self._natural_policy and (
                type(env['VOICE_SELA_LIVE_POLICY']) is not str
                or env['VOICE_SELA_LIVE_POLICY'] != 'natural_v1'
                or env.get('VOICE_SELA_LIVE_TTS') != '1'
                or not self._automatic_conditioning_enabled
                or env.get('VOICE_EXPERIMENTAL_DELIVERY_PROSODY', '0') != '0'):
            raise ValueError('natural_v1 requires live automatic conditioning without numeric prosody; no fallback')
        self._live_tts = None
        live_choice = env.get('VOICE_SELA_LIVE_TTS', '0')
        if type(live_choice) is not str or live_choice not in ('0', '1'):
            raise ValueError('VOICE_SELA_LIVE_TTS requires 0 or 1')
        if live_choice == '1':
            if self._backend != 'supertonic3' or self._style_profile != 'conditioning_b':
                raise ValueError('Sela Live requires Supertonic3 conditioning_b')
            from .sela_live_bridge import SelaLiveBridge
            self._live_tts = SelaLiveBridge(env)
        self._conditioning_router = None
        if self._automatic_conditioning_enabled:
            from .automatic_conditioning import load_router
            self._conditioning_router = load_router()
        self.tts_chunked = self._backend == 'supertonic3'
        self.delivery_controls_available = self._backend == 'supertonic3'
        prosody_choice = env.get('VOICE_EXPERIMENTAL_DELIVERY_PROSODY', '0')
        if prosody_choice not in ('0', '1'):
            raise ValueError('VOICE_EXPERIMENTAL_DELIVERY_PROSODY requires 0 or 1')
        # Availability is not listening approval: experiments are default-off.
        self.delivery_controls_supported = self.delivery_controls_available and prosody_choice == '1'
        self._voice = env.get('VOICE_TTS_VOICE', 'id-ID-GadisNeural')
        if self._backend not in ('piper', 'edge', 'espeak', 'supertonic3'):
            raise ValueError('VOICE_TTS_BACKEND must be piper, edge, espeak or supertonic3')
        super_keys = ('VOICE_SUPERTONIC3_PYTHON', 'VOICE_SUPERTONIC3_MODEL_PATH', 'VOICE_SUPERTONIC3_MANIFEST')
        if self._backend == 'supertonic3':
            paths = tuple(env.get(key, '') for key in super_keys)
            if not all(value and Path(value).is_absolute() for value in paths):
                raise ValueError('Supertonic3 requires explicit absolute runtime, assets and manifest paths')
            self._super_python, self._super_assets, self._super_manifest = paths
            if not (Path(paths[0]).is_file() and os.access(paths[0], os.X_OK)
                    and Path(paths[1]).is_dir() and Path(paths[2]).is_file()
                    and Path(paths[2]).stat().st_size):
                raise ValueError('Supertonic3 runtime/assets/manifest unavailable; no fallback')
        elif any(key in env for key in super_keys):
            raise ValueError('Supertonic3 paths require explicit Supertonic3 selection')
        self.stt_mode = env.get('VOICE_STT_BACKEND', 'local')
        if self.stt_mode not in ('local', 'api', 'browser'):
            raise ValueError('VOICE_STT_BACKEND must be local, api or browser')
        browser_choice = env.get('VOICE_ALLOW_BROWSER_STT', '0')
        if browser_choice not in ('0', '1'):
            raise ValueError('VOICE_ALLOW_BROWSER_STT must be 0 or 1')
        self.browser_stt_allowed = self.stt_mode == 'browser' or browser_choice == '1'
        preview_choice = env.get('VOICE_LOCAL_PREVIEW', '0')
        if preview_choice not in ('0', '1') or (preview_choice == '1' and self.stt_mode != 'local'):
            raise ValueError('VOICE_LOCAL_PREVIEW requires 0/1 and a local backend')
        self.local_preview = preview_choice == '1'
        self._stream_asr = None
        self.asr_engine = env.get('VOICE_LOCAL_ASR_ENGINE', 'whisper')
        self.preview_source = 'local_zipformer_id_prefix' if self.asr_engine == 'zipformer_id_int8' else 'local_whisper_prefix'
        self.preview_semantics = ('repeated_bounded_zipformer_online_prefix_decode' if self.asr_engine == 'zipformer_id_int8'
                                  else 'repeated_bounded_whisper_prefix_decode')
        self._zip_python = env.get('VOICE_ZIPFORMER_PYTHON', '')
        self._zip_model = env.get('VOICE_ZIPFORMER_MODEL_PATH', '')
        if self.asr_engine not in ('whisper','zipformer_id_int8'):
            raise ValueError('Unknown explicit local ASR engine')
        stateful_choice = env.get('VOICE_ZIPFORMER_STATEFUL', '0')
        if stateful_choice not in ('0', '1'):
            raise ValueError('VOICE_ZIPFORMER_STATEFUL requires 0 or 1')
        self.stateful_preview = stateful_choice == '1'
        if self.stateful_preview:
            if self.asr_engine != 'zipformer_id_int8' or not self.local_preview or self.stt_mode != 'local':
                raise ValueError('Stateful capture requires explicitly selected local Zipformer previews')
            self.preview_source = 'local_zipformer_id_stream'
            self.preview_semantics = 'stateful_incremental_decode_over_bounded_prefix_snapshots'
        if self.asr_engine == 'zipformer_id_int8':
            if (not self.local_preview or self.stt_mode != 'local'
                    or not self._zip_python or not self._zip_model
                    or not all(Path(p).is_absolute() for p in (self._zip_python,self._zip_model))
                    or 'VOICE_DRAFT_MODEL_PATH' in env):
                raise ValueError('Zipformer requires explicit isolated absolute runtime/model, local previews and no Whisper draft override')
        elif 'VOICE_ZIPFORMER_PYTHON' in env or 'VOICE_ZIPFORMER_MODEL_PATH' in env:
            raise ValueError('Zipformer paths require explicit Zipformer selection')
        self._python = env.get('VOICE_AUDIO_PYTHON', str(AUDIO_ROOT / 'venv/bin/python'))
        self._stt_path = Path(env.get('VOICE_WHISPER_MODEL_PATH', str(AUDIO_ROOT / 'models/whisper-base')))
        self._draft_path = Path(env.get('VOICE_DRAFT_MODEL_PATH', str(self._stt_path)))
        if 'VOICE_DRAFT_MODEL_PATH' in env and (not self.local_preview or not self._draft_path.is_absolute()):
            raise ValueError('VOICE_DRAFT_MODEL_PATH requires enabled local previews and an absolute path')
        self._tts_path = Path(env.get('VOICE_PIPER_MODEL_PATH', str(AUDIO_ROOT / 'models/piper-id/id_ID-news_tts-medium.onnx')))
        if not all(Path(p).is_absolute() for p in (self._python, self._stt_path, self._tts_path)):
            raise ValueError('Local audio runtime and model paths must be absolute')
        self._local_slot = asyncio.Lock()
        self._local_waiting = False
        if self._voice not in ('id-ID-GadisNeural', 'id-ID-ArdiNeural'):
            raise ValueError('VOICE_TTS_VOICE must be id-ID-GadisNeural or id-ID-ArdiNeural')
        self.tts_label = ('Microsoft Edge online neural · ' + self._voice if self._backend == 'edge'
                          else 'espeak-ng · bahasa Indonesia · rule-based non-AI')
        self.tts_identity = {'backend':self._backend,
            'voice':self._voice if self._backend == 'edge' else 'id',
            'rate':'+0%' if self._backend == 'edge' else '165wpm',
            'pitch':'+0Hz' if self._backend == 'edge' else 'default',
            'format':'wav', 'sample_rate':24000 if self._backend == 'edge' else 22050,
            'channels':1, 'sample_width':2}
        self._base = env.get('VOICE_STT_API_BASE_URL', '')
        self._key = env.get('VOICE_STT_API_KEY', '')
        self._model = env.get('VOICE_STT_MODEL', '')
        if self._base:
            from urllib.parse import urlsplit
            url = urlsplit(self._base)
            if (url.scheme not in ('http', 'https') or not url.hostname or url.username
                    or url.password or url.query or url.fragment):
                raise ValueError('VOICE_STT_API_BASE_URL must be HTTP(S) without credentials, query or fragment')
        self.stt_available = (all(value.strip() for value in (self._base, self._key, self._model))
                              if self.stt_mode == 'api' else False)
        if self.stt_mode == 'local':
            if self.asr_engine == 'zipformer_id_int8':
                self.stt_available = _runtime_ready(self._zip_python, Path(self._zip_model), 'zipformer')
                if not self.stt_available:
                    raise ValueError('Explicit Zipformer runtime/model unavailable; no fallback')
            else:
                self.stt_available = _runtime_ready(self._python, self._stt_path, 'stt')
        if self.local_preview and 'VOICE_DRAFT_MODEL_PATH' in env and not _runtime_ready(self._python, self._draft_path, 'stt'):
            raise ValueError('Configured draft model unavailable; no fallback')
        self.stt_reason = '' if self.stt_available else ('STT lokal belum siap. Periksa runtime dan model; pesan teks tetap tersedia.'
            if self.stt_mode == 'local' else 'STT tidak aktif. Pilih mode browser secara eksplisit atau gunakan pesan teks.')
        self.tts_available = True
        if self._backend == 'piper':
            self.tts_label = 'Piper lokal · id_ID-news_tts-medium · suara tetap'
            self.tts_identity = {'backend':'piper', 'voice':'id_ID-news_tts-medium',
                'rate':'default', 'pitch':'default', 'format':'wav', 'sample_rate':22050,
                'channels':1, 'sample_width':2}
            self.tts_available = _runtime_ready(self._python, self._tts_path, 'tts')
        if self._backend == 'supertonic3':
            self.tts_label = 'Supertonic3 lokal · M1 · bahasa Indonesia · suara tetap'
            self.tts_identity = {'backend':'supertonic3', 'voice':'M1', 'language':'id',
                'rate':'default', 'pitch':'default', 'format':'wav', 'sample_rate':44100,
                'channels':1, 'sample_width':2}
            if self._style_profile == 'conditioning_b':
                self.tts_label += ' · gaya B (+0.4)'
                self.tts_identity.update(voice_style_profile='conditioning_b', style_label='Conditioning B (+0.4)')
            if self._live_tts is not None:
                self.tts_label += ' · Sela Live quality12'
                self.tts_identity.update(adapter='sela_live', total_steps=12)
                if self._natural_policy:
                    self.tts_identity['policy'] = 'natural_v1'
            try:
                check_args = (self._live_tts.args('check', self._super_assets, self._super_manifest, self._style_profile)
                    if self._live_tts is not None else
                    (self._super_python, '-I', str(Path(__file__).with_name('supertonic3_worker.py')),
                     'check', self._super_assets, self._super_manifest) + self._style_args())
                check = subprocess.run(check_args, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, env=_offline_env())
                self.tts_available = check.returncode == 0
                if self.tts_available and self.automatic_conditioning_enabled:
                    from .automatic_conditioning import PROFILE_IDS
                    for profile_id in PROFILE_IDS[1:]:
                        check_args = (self._live_tts.args('check', self._super_assets, self._super_manifest, profile_id)
                            if self._live_tts is not None else
                            (self._super_python, '-I', str(Path(__file__).with_name('supertonic3_worker.py')),
                             'check', self._super_assets, self._super_manifest, profile_id))
                        check = subprocess.run(check_args, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, env=_offline_env())
                        if check.returncode != 0:
                            self.tts_available = False
                            break
            except (OSError, subprocess.TimeoutExpired):
                self.tts_available = False
            if not self.tts_available:
                raise ValueError('Explicit Supertonic3 runtime/assets unavailable; no fallback')
        self._transport = transport

    @property
    def automatic_conditioning_enabled(self):
        return self._automatic_conditioning_enabled

    @property
    def delivery_intent_supported(self):
        return self.automatic_conditioning_enabled or self.delivery_controls_supported

    @property
    def automatic_conditioning_emotions(self):
        return self._conditioning_router.emotions if self._conditioning_router else ()

    @property
    def automatic_conditioning_version(self):
        return self._conditioning_router.version if self._conditioning_router else None

    def conditioning_for(self, delivery):
        if not self.automatic_conditioning_enabled:
            return None
        if delivery is None:
            return dict(profile_id='conditioning_b', activation='base',
                        intended_emotion='neutral', intensity='medium', supported=True)
        return self._conditioning_router.select(delivery)

    def _style_args(self, profile_id=None):
        # Keep legacy four-argument worker invocations byte-for-byte compatible.
        if profile_id is not None:
            from .automatic_conditioning import PROFILE_IDS
            if type(profile_id) is not str or profile_id not in PROFILE_IDS:
                raise ValueError('Invalid conditioning profile')
            return (profile_id,)
        return ('conditioning_b',) if self._style_profile == 'conditioning_b' else ()

    def _preview_worker(self):
        if self._stream_asr is None:
            from .stream_asr import PersistentASR
            # On this host the measured MemAvailable drop was ~500 MiB,
            # with ~837 MiB peak RSS (shared/file-backed pages differ). Require
            # 950 MiB available before starting, retaining service headroom.
            available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                             if line.startswith('MemAvailable:'))
            if available < 950 * 1024:
                raise LocalAudioBusy('Insufficient streaming ASR headroom')
            if self.asr_engine == 'zipformer_id_int8':
                if self.stateful_preview:
                    from .stateful_asr import StatefulASR
                    args = (self._zip_python, '-I', str(Path(__file__).with_name('zipformer_stateful_worker.py')), 'serve', self._zip_model)
                    self._stream_asr = StatefulASR(args, _offline_env())
                else:
                    args = (self._zip_python, '-I', str(Path(__file__).with_name('zipformer_worker.py')), 'serve', self._zip_model)
                    self._stream_asr = PersistentASR(args, _offline_env(), final_args=args)
            else:
                self._stream_asr = PersistentASR((self._python, '-I', str(LOCAL_WORKER),
                    'serve-draft' if self._draft_path != self._stt_path else 'serve-stt', str(self._draft_path)), _offline_env(),
                    final_args=(self._python, '-I', str(LOCAL_WORKER), 'serve-stt', str(self._stt_path)))
        return self._stream_asr

    async def prepare_preview(self):
        if not self.local_preview or not self.stt_available:
            return
        if self._local_slot.locked():
            raise LocalAudioBusy('Audio slot busy during preparation')
        async with self._local_slot:
            await self._preview_worker().ready()

    async def preview(self, wav, *, stream_key=None):
        if not self.local_preview or not self.stt_available or self._local_slot.locked():
            return None
        if self.stateful_preview:
            return await self._preview_worker().preview(wav, stream_key=stream_key)
        return await self._preview_worker().preview(wav)

    async def discard_stream(self, stream_key):
        # Never warm a model for lifecycle cleanup; the worker validates owner.
        if self.stateful_preview and self._stream_asr is not None:
            await self._stream_asr.forget(stream_key)

    async def close_preview(self):
        if self._stream_asr:
            await self._stream_asr.close()

    def _delivery_controls(self, delivery):
        if delivery is None:
            return None
        if not self.delivery_controls_supported:
            if self.automatic_conditioning_enabled:
                return None # metadata validated by conditioning_for; numeric opt-in stays separate
            raise ValueError('Native delivery controls unsupported or experimental prosody disabled')
        from types import MappingProxyType
        from .supertonic3_worker import validate_delivery
        # Capture once into our own immutable copy, never per chunk or globally.
        return MappingProxyType(validate_delivery(delivery.native_controls()))

    async def synthesize_chunks(self, text, *, delivery=None):
        """Yield bounded waveforms and exact ACK source spans with one snapshot."""
        choice = self.conditioning_for(delivery)
        if self._natural_policy:
            stream=self._natural_chunks(text,delivery,choice)
            try:
                async for item in stream:yield item
            finally:
                await self._close_natural_stream(stream)
            return
        profile_id = choice['profile_id'] if choice is not None else None
        controls = self._delivery_controls(delivery)
        if not self.tts_chunked:
            yield text, await self.synthesize(text), True
            return
        from .tts_chunks import source_parts, synthesis_source
        parts = source_parts(text, normalize_pronunciation)
        for index, part in enumerate(parts):
            source = synthesis_source(part)
            audio = (await self._synthesize(source, controls, profile_id=profile_id) if profile_id is not None
                     else await self.synthesize(source) if controls is None
                     else await self._synthesize(source, controls))
            # Only multipart Supertonic responses: source/ACK spans and the
            # already captured conditioning/controls remain untouched.
            if self._backend == 'supertonic3' and len(parts) > 1:
                from .audio_continuity import trim_boundary_silence
                audio = trim_boundary_silence(audio, first=index == 0,
                    last=index == len(parts) - 1, max_output_bytes=MAX_WAV_BYTES)
            yield part, audio, index == len(parts) - 1

    @staticmethod
    async def _close_natural_stream(stream):
        task=asyncio.create_task(stream.aclose())
        interrupted=False
        while not task.done():
            try:await asyncio.shield(task)
            except asyncio.CancelledError:interrupted=True
        task.result()
        if interrupted:raise asyncio.CancelledError

    async def _natural_chunks(self,text,delivery,choice):
        bridge=self._live_tts
        if bridge is None or not bridge.natural:raise ValueError('Natural runtime required')
        bridge.verify_runtime()  # Validate all candidate source before imports.
        from sela_live_tts.natural import NaturalTTS,RuntimePins
        from sela_live_tts.utterance import SpeechRequest,ResolvedDelivery
        from contextlib import contextmanager
        selected=ResolvedDelivery(choice['profile_id'],choice['intended_emotion'],choice['intensity'])
        if self._local_waiting or self._local_slot.locked():
            raise LocalAudioBusy('Natural response active; zero queued waiters')
        await self._local_slot.acquire()
        keep_slot=False
        try:
            async with AsyncExitStack() as retained:
                if self.local_preview:
                    if self.asr_engine=='zipformer_id_int8':
                        if self._stream_asr is not None:
                            await retained.enter_async_context(self._stream_asr.quiesce(LOCAL_RETRY_WAIT))
                    else:await self.close_preview()
                retained.enter_context(bridge.claim())
                class BorrowedCoordinator:
                    # The actual persistent host lease is retained by Speech.
                    # DP/render phases must not flock the same inode twice.
                    poll_interval=.05
                    active=True
                    owned=0
                    def check_production(self):
                        if not self.active:raise LocalAudioBusy('Natural lease released')
                    @contextmanager
                    def claim(self):
                        self.check_production()
                        bridge.verify_runtime()
                        self.owned+=1
                        try:yield
                        finally:self.owned-=1
                coordinator=BorrowedCoordinator()
                runtime=RuntimePins(bridge.python,self._super_assets,self._super_manifest,
                    str(bridge.worker),bridge.duration_worker,bridge.digest,
                    bridge.duration_digest,bridge.source_digest)
                engine=NaturalTTS(runtime,native_call=_bounded_process,coordinator=coordinator,
                                  resolve_delivery=lambda original:selected)
                stream=engine.synthesize_chunks(SpeechRequest(text,delivery))
                try:
                    async for item in stream:yield item
                finally:
                    try:await self._close_natural_stream(stream)
                    finally:
                        coordinator.active=False
                        if coordinator.owned:
                            # Failed DurationSession cleanup deliberately kept
                            # its phase lease. Retain the actual host lease and
                            # ASR quiescence too: unreaped work is not capacity.
                            self._natural_failed_lease=retained.pop_all()
                            keep_slot=True
        finally:
            if not keep_slot:self._local_slot.release()

    async def synthesize(self, text, *, delivery=None):
        choice = self.conditioning_for(delivery)
        controls = self._delivery_controls(delivery)
        if choice is not None:
            return await self._synthesize(text, controls, profile_id=choice['profile_id'])
        return await self._synthesize(text, controls)

    async def _synthesize(self, text, controls, *, profile_id=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('TTS text must contain 1–4000 characters')
        if self._backend == 'supertonic3':
            # Validate source and pronunciation copy without altering history or
            # automatically inserting expression tags.
            from .supertonic3_worker import validate_request
            validate_request({'text':text})
            spoken = normalize_pronunciation(text)
            validate_request({'text':spoken})
            request = {'text': spoken}
            if controls is not None:
                request['delivery'] = dict(controls)
            payload = json.dumps(request, ensure_ascii=False).encode('utf-8')
            raw = (await self._local_request('tts', payload, MAX_WAV_BYTES, profile_id=profile_id) if profile_id is not None
                   else await self._local_request('tts', payload, MAX_WAV_BYTES))
            result = _normalize_wav(raw)
            with wave.open(io.BytesIO(result)) as reader:
                if reader.getframerate() != 44100:
                    raise ValueError('Supertonic3 voice sample rate mismatch')
            return result
        if self._backend == 'piper':
            if not self.tts_available:
                raise RuntimeError('TTS lokal belum siap; tidak ada fallback')
            spoken = normalize_pronunciation(text)
            if len(spoken) > 16000:
                raise ValueError('Normalized TTS text exceeds limit')
            raw = await self._local_request('tts', json.dumps({'text':spoken}, ensure_ascii=False).encode('utf-8'), MAX_WAV_BYTES)
            result = _normalize_wav(raw)
            with wave.open(io.BytesIO(result)) as reader:
                if reader.getframerate() != self.tts_identity['sample_rate']:
                    raise ValueError('Piper voice sample rate mismatch')
            return result
        if self._backend == 'edge':
            from pathlib import Path
            async with asyncio.timeout(TTS_TIMEOUT):
                mp3 = await _bounded_process((EDGE_PYTHON, '-I', str(Path(__file__).resolve()),
                    '--edge-worker', self._voice), text.encode('utf-8'), MAX_MP3_BYTES)
                raw = await _bounded_process((FFMPEG, '-nostdin', '-v', 'error',
                    '-f', 'mp3', '-i', 'pipe:0', '-map', '0:a:0', '-vn',
                    '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', '-f', 'wav', 'pipe:1'),
                    mp3, MAX_WAV_BYTES)
                return _normalize_wav(raw)
        executable = shutil.which('espeak-ng')
        if not executable:
            raise RuntimeError('Install espeak-ng for rule-based speech output')
        proc = await asyncio.create_subprocess_exec(executable, '--stdout', '-v', 'id', '-s', '165', '--stdin',
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            async with asyncio.timeout(20):
                proc.stdin.write(text.encode('utf-8'))
                await proc.stdin.drain()
                proc.stdin.close()
                raw = await proc.stdout.read(MAX_WAV_BYTES + 1)
                # StreamReader.read may return short chunks before EOF.
                pieces = [raw]
                size = len(raw)
                while raw and size <= MAX_WAV_BYTES:
                    raw = await proc.stdout.read(min(65536, MAX_WAV_BYTES + 1 - size))
                    pieces.append(raw); size += len(raw)
                if size > MAX_WAV_BYTES:
                    raise ValueError('TTS WAV exceeds bounded output limit')
                await proc.wait()
                if proc.returncode:
                    raise RuntimeError('espeak-ng failed')
                # espeak stdout uses a streaming RIFF length. Write real lengths.
                with wave.open(io.BytesIO(b''.join(pieces)), 'rb') as source:
                    params = source.getparams()
                    frames = source.readframes(MAX_WAV_BYTES)
                out = io.BytesIO()
                with wave.open(out, 'wb') as target:
                    target.setparams(params)
                    target.writeframes(frames)
                return out.getvalue()
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 2)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

    async def _local_request(self, action, payload, limit, *, stream_key=None, profile_id=None):
        # Keep one running worker and at most one brief cleanup waiter. A retry
        # after interruption must not race process reaping or spawn in parallel.
        if self._local_waiting:
            raise LocalAudioBusy('Local audio worker busy')
        if self._local_slot.locked():
            self._local_waiting = True
            try:
                await asyncio.wait_for(self._local_slot.acquire(), LOCAL_RETRY_WAIT)
            except TimeoutError as exc:
                raise LocalAudioBusy('Local audio worker busy') from exc
            finally:
                self._local_waiting = False
        else:
            await self._local_slot.acquire()
        try:
            async with asyncio.timeout(90), AsyncExitStack() as retained:
                if self.local_preview:
                    if action == 'stt':
                        if self.stateful_preview:
                            text = await self._preview_worker().transcribe(payload, stream_key=stream_key)
                        else:
                            text = await self._preview_worker().transcribe(payload)
                        return json.dumps({'text':text}).encode('utf-8')
                    if self.asr_engine == 'zipformer_id_int8':
                        if self._stream_asr is not None:
                            await retained.enter_async_context(self._stream_asr.quiesce(LOCAL_RETRY_WAIT))
                    else:
                        await self.close_preview()  # Reclaim heavy Whisper before Piper allocation.
                args = ((self._super_python, '-I', str(Path(__file__).with_name('supertonic3_worker.py')),
                         'tts', self._super_assets, self._super_manifest) + self._style_args(profile_id)
                        if action == 'tts' and self._backend == 'supertonic3' else
                        (self._python, '-I', str(LOCAL_WORKER), action,
                         str(self._stt_path if action == 'stt' else self._tts_path)))
                if action == 'tts' and self._backend == 'supertonic3':
                    if self._live_tts is not None:
                        retained.enter_context(self._live_tts.claim())
                        args = self._live_tts.args('tts', self._super_assets, self._super_manifest,
                            profile_id or self._style_profile)
                        return await self._live_tts.run(_bounded_process, args, payload, limit, env=_offline_env())
                    return await _bounded_process(args, payload, limit, env=_offline_env(), native_diagnostics=True)
                return await _bounded_process(args, payload, limit, env=_offline_env())
        finally:
            self._local_slot.release()

    async def transcribe(self, wav, *, stream_key=None):
        if not self.stt_available:
            raise ValueError(self.stt_reason)
        if type(wav) is not bytes or not 44 < len(wav) <= 640044:
            raise ValueError('Invalid transcription WAV')
        try:
            with wave.open(io.BytesIO(wav)) as reader:
                if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (1, 2, 16000):
                    raise ValueError('Transcription requires mono PCM16 16000 Hz')
                count = reader.getnframes()
                if not 0 < count <= 320000 or len(reader.readframes(count + 1)) != count * 2:
                    raise ValueError('Invalid transcription duration or truncated WAV')
        except (wave.Error, EOFError) as exc:
            raise ValueError('Invalid transcription WAV') from exc
        if self.stt_mode == 'local':
            if self.stateful_preview and stream_key is None:
                from uuid import uuid4
                stream_key = uuid4().hex  # standalone final; cannot reuse another owner
            body = await self._local_request('stt', wav, 65536, stream_key=stream_key)
            text = json.loads(body).get('text')
            if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                raise NoSpeechDetected('No intelligible local speech detected')
            return text.strip()
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10), trust_env=False,
                                     transport=self._transport) as client:
            async with client.stream('POST', self._base.rstrip('/') + '/audio/transcriptions',
                    headers={'Authorization': 'Bearer ' + self._key},
                    files={'file': ('utterance.wav', wav, 'audio/wav')},
                    data={'model': self._model}) as response:
                response.raise_for_status()
                body = bytearray()
                async for piece in response.aiter_bytes():
                    body.extend(piece)
                    if len(body) > 65536:
                        raise ValueError('Transcription response too large')
        text = json.loads(body).get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('Invalid transcription text')
        return text.strip()


async def _edge_worker(voice):
    """Keyless online service adapter, invoked only by the fixed isolated interpreter."""
    import sys
    import edge_tts
    if voice not in ('id-ID-GadisNeural', 'id-ID-ArdiNeural'):
        raise ValueError('Invalid voice')
    payload = sys.stdin.buffer.read(16001)
    text = payload.decode('utf-8')
    if len(payload) > 16000 or not text.strip() or len(text) > 4000:
        raise ValueError('Invalid TTS text')
    size = 0
    async with asyncio.timeout(40):
        async for chunk in edge_tts.Communicate(text, voice, rate='+0%', pitch='+0Hz',
                                                 connect_timeout=10, receive_timeout=20).stream():
            if chunk['type'] == 'audio':
                data = chunk['data']
                size += len(data)
                if size > MAX_MP3_BYTES:
                    raise ValueError('TTS MP3 exceeds bounded output limit')
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
    if not size:
        raise RuntimeError('Empty Edge response')


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 3 or sys.argv[1] != '--edge-worker':
        sys.exit(2)
    try:
        asyncio.run(_edge_worker(sys.argv[2]))
    except Exception:
        # Never expose submitted private text, request headers or credentials.
        sys.exit(1)

