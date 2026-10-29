"""Loopback voice transport. AI runs only through a remote provider."""
import asyncio
import base64
import binascii
import json
import logging

logger = logging.getLogger(__name__)
# Uvicorn does not enable this named INFO logger by default. Emit only the
# allowlisted command/aggregate records below; never audio or transcript text.
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False
SAFE_COMMANDS = frozenset(('start', 'text', 'voice_text', 'audio', 'audio_preview', 'ack', 'interrupt',
                           'stop', 'mute', 'press', 'release', 'provider'))
ERROR_MESSAGES = {
    'session_not_ready': 'Sesi belum aktif. Mulai sesi terlebih dahulu.',
    'invalid_message': 'Pesan tidak valid. Muat ulang halaman lalu coba lagi.',
    'microphone_muted': 'Mikrofon dibisukan. Aktifkan mikrofon lalu coba lagi.',
    'api_stt_unavailable': 'STT belum tersedia. Periksa konfigurasi server atau kirim pesan teks.',
    'browser_stt_disabled': 'Pengenalan suara browser tidak diaktifkan oleh server. Gunakan audio lokal atau pesan teks.',
    'provider_busy': 'Provider sedang sibuk. Tunggu respons selesai lalu coba lagi.',
    'no_speech': 'Ucapan belum terbaca dengan jelas. Coba dekatkan mikrofon dan ulangi satu kalimat.',
    'local_audio_busy': 'Pemroses audio lokal masih menyelesaikan permintaan sebelumnya. Tunggu sebentar lalu coba lagi.',
    'audio_timeout': 'Pemrosesan audio melewati batas waktu. Coba ucapan yang lebih singkat.',
    'audio_worker_failed': 'Pemroses audio gagal. Silakan coba lagi; kode kegagalan dicatat di server.',
    'llm_auth': 'Autentikasi API Codex ditolak. Koneksi akun server perlu diperiksa.',
    'llm_rate_limited': 'API Codex sedang membatasi permintaan. Tunggu sebentar lalu coba lagi.',
    'llm_timeout': 'API Codex belum menjawab dalam batas waktu. Silakan coba lagi.',
    'llm_failed': 'Permintaan ke API Codex gagal. Kode kegagalan dicatat di server.',
    'tts_busy': 'Jawaban teks sudah tersedia, tetapi pemroses suara lokal masih sibuk.',
    'tts_timeout': 'Jawaban teks sudah tersedia, tetapi pembuatan suara melewati batas waktu.',
    'tts_failed': 'Jawaban teks sudah tersedia, tetapi pembuatan suara lokal gagal.',
}


class CommandError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

from .audio import pcm16_wav
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .providers import create_provider
from .delivery import load_registry, parse_reply, plain_reply, MAX_ENVELOPE_CHARS
from .speech import LocalAudioBusy, NoSpeechDetected
from .session_core import Session, ResponseToken

MAX_WAV_BYTES = 4_000_000
PREVIEW_MIN_INTERVAL = 1.5
STATEFUL_PREVIEW_MIN_INTERVAL = 0.25
CHUNK_ACK_TIMEOUT = 60


def valid_host(host):
    try:
        parsed = urlsplit('http://' + host)
        return (parsed.hostname in ('localhost', '127.0.0.1', '::1')
                and parsed.netloc == host and not parsed.username and not parsed.password
                and not parsed.path and not parsed.query and not parsed.fragment
                and (parsed.port is None or 1 <= parsed.port <= 65535))
    except ValueError:
        return False


def valid_origin(ws):
    host = ws.headers.get('host', '')
    scheme = 'https' if ws.scope['scheme'] == 'wss' else 'http'
    return valid_host(host) and ws.headers.get('origin') == scheme + '://' + host


def validate(data, *, stateful=False):
    if not isinstance(data, dict) or not isinstance(data.get('type'), str):
        raise ValueError('object required')
    schemas = {'start': {'mode','provider','start_id','local_preview'}, 'text': {'text'}, 'voice_text': {'text'}, 'audio': {'pcm_b64','sample_rate','utterance_id'},
               'audio_preview': {'pcm_b64','sample_rate','utterance_id'},
               'ack': {'token','chunk_id'}, 'interrupt': set(), 'stop': set(),
               'mute': {'muted'}, 'press':set(), 'release':set(), 'provider': {'provider'}}
    kind = data['type']
    if kind not in schemas or set(data) - schemas[kind] - {'type'}:
        raise ValueError('invalid command')
    if kind == 'start' and 'start_id' in data:
        start_id = data['start_id']
        if (not isinstance(start_id, str) or not 1 <= len(start_id) <= 64
                or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for c in start_id)):
            raise ValueError('invalid start id')
    if kind == 'start' and 'local_preview' in data:
        if type(data['local_preview']) is not bool or 'start_id' not in data:
            raise ValueError('Invalid preview negotiation')
    if kind in ('text', 'voice_text') and (not isinstance(data.get('text'), str) or not data['text'].strip() or len(data['text']) > 4000):
        raise ValueError('invalid text')
    if kind in ('start', 'provider') and data.get('provider', 'default') != 'default':
        raise ValueError('unknown provider')
    if kind == 'mute' and type(data.get('muted')) is not bool:
        raise ValueError('invalid mute')
    if kind in ('audio', 'audio_preview') and (not isinstance(data.get('pcm_b64'), str) or not 0 < len(data['pcm_b64']) <= 853336 or type(data.get('sample_rate')) is not int or data['sample_rate'] != 16000):
        raise ValueError('invalid audio')
    if kind == 'audio_preview' or (kind == 'audio' and 'utterance_id' in data):
        if type(data.get('utterance_id')) is not int or not 1 <= data['utterance_id'] <= 2147483647:
            raise ValueError('invalid utterance id')
    preview_min_bytes = 16000 if stateful else 48000
    if kind == 'audio_preview' and len(data['pcm_b64']) < ((preview_min_bytes + 2) // 3) * 4:
        raise ValueError('preview is below negotiated minimum')
    if kind == 'ack':
        if not isinstance(data.get('token'), dict) or type(data.get('chunk_id')) is not int:
            raise ValueError('invalid ack')
        ResponseToken(**data['token'])


def create_app(provider=None, speech=None, *, data_dir=None, public_origin=None, auth=None, local_test_auth=False):
    import os
    from .auth import AuthStore
    from starlette.responses import JSONResponse, RedirectResponse, HTMLResponse
    public_origin = public_origin if public_origin is not None else os.environ.get('VOICE_PUBLIC_ORIGIN', '')
    if public_origin:
        parsed = urlsplit(public_origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.netloc != parsed.hostname
                or parsed.path or parsed.query or parsed.fragment or parsed.username):
            raise ValueError('VOICE_PUBLIC_ORIGIN must be an exact HTTPS origin without path or port')
    public_host = urlsplit(public_origin).netloc if public_origin else None
    auth = auth if auth is not None else AuthStore(data_dir or os.environ.get('VOICE_DATA_DIR', str(Path(__file__).resolve().parent.parent / 'data')))
    def allowed_host(host):
        return valid_host(host) or bool(public_host and host == public_host)
    def allowed_origin(connection):
        host = connection.headers.get('host', '')
        if public_host and host == public_host:
            return connection.headers.get('origin') == public_origin
        return valid_origin(connection)
    def authenticated(connection):
        return (local_test_auth and valid_host(connection.headers.get('host', ''))) or auth.valid_session(connection.cookies.get('sela_session', ''))

    if provider is None:
        provider = create_provider()
    if speech is None:
        from .speech import Speech
        speech = Speech()
    app = FastAPI()
    delivery_registry = load_registry()  # Immutable app-owned snapshot, never user supplied.
    delivery_protocol = getattr(provider, 'delivery_protocol_version', None) == 1
    stateful = bool(getattr(speech, 'stateful_preview', False))
    remote_jobs = set()
    stt_jobs = set()
    preview_job = None
    preview_pending = None
    discard_jobs = {}

    def discard_native(key):
        if not stateful or key is None or key in discard_jobs:
            return
        # Stop new capture admission at four, reserving cleanup slots for
        # already admitted final jobs and lifecycle exits.
        if len(discard_jobs) >= 8:
            return  # Keys cannot be reused; bounded worker idle expiry remains.
        async def discard():
            try:
                async with asyncio.timeout(4):
                    await speech.discard_stream(key)
            except Exception:
                pass
            finally:
                discard_jobs.pop(key, None)
        discard_jobs[key] = asyncio.create_task(discard())

    async def preview_pump():
        nonlocal preview_pending
        while preview_pending is not None:
            wav, wire, emit, relevant, stream_key = preview_pending
            preview_pending = None
            if not relevant():
                continue
            try:
                if stateful:
                    text = await speech.preview(wav, stream_key=stream_key)
                else:
                    text = await speech.preview(wav)
                if text and relevant():
                    await emit({'type':'transcript_partial', 'text':text,
                                'provisional':True, 'source':getattr(speech, 'preview_source', 'local_whisper_prefix'), **wire})
            except Exception:
                pass  # Best effort. Final STT retains its normal safe errors.

    def queue_preview(item):
        nonlocal preview_pending, preview_job
        preview_pending = item  # one latest pending snapshot globally
        if preview_job is None or preview_job.done():
            preview_job = asyncio.create_task(preview_pump())

    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def lifespan(_app):
        yield
        if preview_job:
            await preview_job
        if discard_jobs:
            await asyncio.gather(*list(discard_jobs.values()), return_exceptions=True)
        if getattr(speech, 'local_preview', False):
            await speech.close_preview()
    app.router.lifespan_context = lifespan

    async def collect(messages, authorized):
        if not authorized():
            raise PermissionError('Session expired')
        result = ''
        async for delta in provider.stream(messages):
            limit = MAX_ENVELOPE_CHARS if delivery_protocol else 4000
            if not isinstance(delta, str) or len(result) + len(delta) > limit:
                raise ValueError('Provider output limit exceeded')
            result += delta
        if not result.strip():
            raise ValueError('Empty provider response')
        return (parse_reply(result, delivery_registry) if delivery_protocol
                else plain_reply(result, delivery_registry))

    def release_job(job):
        remote_jobs.discard(job)
        if not job.cancelled():
            job.exception()  # Retrieve errors even if the originating socket left.

    @app.middleware('http')
    async def host_guard(request, call_next):
        from starlette.responses import PlainTextResponse
        if not allowed_host(request.headers.get('host', '')):
            return PlainTextResponse('Host not allowed', status_code=400)
        public_paths = {'/health', '/login', '/setup', '/auth.js', '/auth.css', '/api/login', '/api/setup'}
        if request.url.path not in public_paths and not authenticated(request):
            response = JSONResponse({'detail':'Authentication required'}, status_code=401) if request.url.path.startswith('/api/') else RedirectResponse('/login' if auth.configured() else '/setup', status_code=303)
        else:
            response = await call_next(request)
        response.headers.update({'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff',
            'X-Frame-Options':'DENY', 'Referrer-Policy':'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.get('/health')
    async def health():
        return {'status': 'ok'}

    @app.post('/api/login')
    async def login(request: Request):
        if not allowed_origin(request):
            return JSONResponse({'detail':'Origin not allowed'}, status_code=403)
        attempt = auth.reserve_attempt(request.client.host if request.client else 'unknown')
        if attempt is None:
            return JSONResponse({'detail':'Terlalu banyak percobaan. Coba lagi dalam 5 menit.'}, status_code=429, headers={'Retry-After':'300'})
        try:
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 4096:
                    raise ValueError()
            if len(raw) > 4096:
                raise ValueError()
            body = json.loads(raw)
            username, password = body.get('username', ''), body.get('password', '')
            if not isinstance(username, str) or not isinstance(password, str) or len(username)>64 or len(password)>256:
                raise ValueError()
            token = await asyncio.to_thread(auth.login, username, password)
        except (ValueError, TypeError, AttributeError):
            token = None
        response = JSONResponse({'ok':bool(token)}, status_code=200 if token else 401)
        if token:
            auth.clear_attempt(attempt)
            response.set_cookie('sela_session', token, max_age=43200, httponly=True, secure=request.headers.get('host') == public_host, samesite='strict', path='/')
        return response

    @app.post('/api/logout')
    async def logout(request: Request):
        if not allowed_origin(request):
            return JSONResponse({'detail':'Origin not allowed'}, status_code=403)
        auth.logout(request.cookies.get('sela_session', ''))
        response = JSONResponse({'ok':True})
        response.delete_cookie('sela_session', path='/', secure=request.headers.get('host') == public_host, httponly=True, samesite='strict')
        return response

    @app.post('/api/setup')
    async def setup(request: Request):
        if not allowed_origin(request):
            return JSONResponse({'detail':'Origin not allowed'}, status_code=403)
        attempt = auth.reserve_attempt(request.client.host if request.client else 'unknown')
        if attempt is None:
            return JSONResponse({'detail':'Terlalu banyak percobaan. Coba lagi dalam 5 menit.'}, status_code=429, headers={'Retry-After':'300'})
        try:
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 4096:
                    raise ValueError()
            if len(raw) > 4096:
                raise ValueError()
            body = json.loads(raw)
            values = [body.get(k, '') for k in ('token', 'username', 'password')]
            if not all(isinstance(v, str) for v in values):
                raise ValueError()
            ok = await asyncio.to_thread(auth.setup, *values)
        except (ValueError, TypeError, AttributeError):
            ok = False
        if ok:
            auth.clear_attempt(attempt)
        return JSONResponse({'ok':ok}, status_code=201 if ok else 403)

    @app.get('/setup')
    @app.get('/login')
    async def auth_page():
        return HTMLResponse((Path(__file__).resolve().parent.parent / 'web' / 'auth.html').read_text())

    @app.get('/api/config')
    async def config():
        identity = getattr(speech, 'tts_identity', None)
        selected_conditioning = isinstance(identity, dict) and identity.get('voice_style_profile') == 'conditioning_b'
        automatic = getattr(speech, 'automatic_conditioning_enabled', False) is True
        return {'providers': [{'id': 'default', 'label': 'Remote AI · ' + provider.model}],
                'default_provider': 'default', 'max_audio_seconds': 20, 'session_ack_supported': True,
                'stt_available': speech.stt_available, 'stt_reason': speech.stt_reason,
                'local_preview_supported': bool(getattr(speech, 'local_preview', False) and speech.stt_available),
                'local_preview_semantics': getattr(speech, 'preview_semantics', 'repeated_bounded_whisper_prefix_decode'),
                'local_preview_interval_ms': 500 if getattr(speech, 'stateful_preview', False) else 1500,
                'local_stateful_preview_supported': bool(getattr(speech, 'stateful_preview', False)
                                                        and getattr(speech, 'local_preview', False) and speech.stt_available),
                'browser_stt_allowed': getattr(speech, 'browser_stt_allowed', True),
                'stt_mode': getattr(speech, 'stt_mode', 'api' if speech.stt_available else 'browser'),
                'tts_available': getattr(speech, 'tts_available', True),
                'tts_label': (speech.tts_label + ' · B otomatis, aktivasi eksperimental') if automatic else speech.tts_label,
                'tts_identity': getattr(speech, 'tts_identity', None), 'client_tts_allowed': False,
                'tts_delivery': 'chunked' if getattr(speech, 'tts_chunked', False) else 'buffered',
                'ai_delivery': 'buffered',
                'delivery_capabilities': {
                    **delivery_registry.capabilities(),
                    'automatic_conditioning_enabled': automatic,
                    'automatic_conditioning_scope': 'activation_only_experimental',
                    'automatic_conditioning_emotion_ids': list(getattr(speech, 'automatic_conditioning_emotions', ())),
                    'native_emotion_conditioning': False,
                    'speaker_identity_unchanged': None if selected_conditioning else True,
                    'conditioning_scope': ('activation_only_experimental' if automatic else
                        'listener_selected_fixed_style' if selected_conditioning else 'original_fixed_style'),
                    'metadata_protocol_active': delivery_protocol,
                    'native_prosody_available': bool(getattr(speech, 'delivery_controls_available', False)),
                    'experimental_native_prosody_enabled': bool(getattr(speech, 'delivery_controls_supported', False))}}

    @app.websocket('/ws')
    async def socket(ws: WebSocket):
        if not allowed_origin(ws) or not authenticated(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        session = Session(provider='default', max_audio_bytes=MAX_WAV_BYTES)
        tasks = set()
        capture_epoch = 0
        finalized_id = 0
        preview_id = 0
        preview_size = 0
        preview_time = -float('inf')
        connected = True
        stream_key = None
        final_stream_keys = set()
        final_epoch = 0
        ack_waiters = {}  # (response token, chunk id) -> event; one live waiter

        def invalidate_preview(discard=True, finals=True):
            nonlocal capture_epoch, finalized_id, preview_pending, stream_key, final_epoch
            capture_epoch += 1
            if finals:
                final_epoch += 1
            finalized_id = max(finalized_id, preview_id)
            if discard:
                discard_native(stream_key)
                for key in final_stream_keys:
                    discard_native(key)
            stream_key = None
            if preview_pending is not None and preview_pending[2] is send_event:
                preview_pending = None

        async def send_event(event):
            if not authenticated(ws):
                await ws.close(code=1008)
                raise WebSocketDisconnect(code=1008)
            await ws.send_json(event)

        async def command_error(code, command, *, start_id=None):
            logger.warning('command=%s errorcode=%s open=%s muted=%s inflight_jobs=%s',
                           command, code, session.open, session.muted,
                           len(remote_jobs) + len(stt_jobs))
            await send_event({'type':'error', 'code':code, 'command':command,
                              'message':ERROR_MESSAGES[code],
                              **({'start_id':start_id} if start_id is not None else {})})

        async def state():
            await send_event({'type':'state', 'state': {
                'open':session.open, 'mode':session.mode, 'muted':session.muted,
                'pressed':session.pressed, 'responding':session.responding,
                'speaking':session.speaking, 'active':asdict(session.active) if session.active else None,
                'history':[asdict(t) for t in session.history]}})

        def cancel_response():
            for task in tasks:
                if task is not asyncio.current_task() and not task.cancelling():
                    task.cancel()

        async def prepare_capture(start_id, epoch):
            try:
                await speech.prepare_preview()
                if connected and session.open and epoch == capture_epoch:
                    await send_event({'type':'session_ready', 'start_id':start_id})
            except asyncio.CancelledError:
                raise
            except Exception:
                if connected and epoch == capture_epoch:
                    await command_error('local_audio_busy', 'start', start_id=start_id)

        async def transcribe(wav, pcm, utterance_id=None, native_key=None, admitted_epoch=None):
            def current():
                return (connected and session.open and not session.muted
                        and admitted_epoch == final_epoch and authenticated(ws))
            async def final_error(code):
                if current():
                    await command_error(code, 'audio')
            try:
                if not current():
                    return
                await send_event({'type':'processing', 'stage':'transcribing'})
                # Measure received PCM before ASR/VAD discards acoustic information.
                # No samples, transcript, identifiers, or decoder errors enter logs.
                from .prosody import diagnose_pcm16
                diagnostics = await asyncio.to_thread(diagnose_pcm16, pcm, 16000)
                if not current():
                    return
                await send_event({'type':'audio_diagnostics', 'diagnostics':diagnostics})
                logger.info('audio_diagnostics duration_seconds=%s rms_dbfs=%s peak_dbfs=%s '
                            'clipping_fraction=%s silence_fraction=%s warnings=%s emotion_status=not_evaluated',
                            diagnostics['duration_seconds'], diagnostics['rms_dbfs'], diagnostics['peak_dbfs'],
                            diagnostics['clipping_fraction'], diagnostics['silence_fraction'],
                            ','.join(diagnostics['warnings']) or 'none')
                if stateful:
                    text = await speech.transcribe(wav, stream_key=native_key)
                else:
                    text = await speech.transcribe(wav)
                if not current():
                    return
                await send_event({'type':'transcript', 'text':text, **({'utterance_id':utterance_id} if utterance_id else {})})
                if current():
                    launch(text, 'voice')
            except asyncio.CancelledError:
                raise
            except CommandError as exc:
                await final_error(exc.code)
            except LocalAudioBusy:
                await final_error('local_audio_busy')
            except NoSpeechDetected:
                await final_error('no_speech')
            except TimeoutError:
                await final_error('audio_timeout')
            except Exception:
                await final_error('audio_worker_failed')

        def launch(text, source='text'):
            if not authenticated(ws):
                raise PermissionError('Session expired')
            if len(remote_jobs) >= 2:
                raise CommandError('provider_busy')
            invalidate_preview()
            token = session.submit(text, source)
            del session.history[:-40]
            messages = [{'role':'system','content':(
                'Jawab singkat dalam bahasa Indonesia, maksimal tiga kalimat. '
                'Gunakan pola tutur B: hangat, alami, dan enak didengar. '
                'Atur jeda lewat koma atau elipsis secukupnya; gunakan tanda seru hanya '
                'bila sesuai konteks dan tanda tanya untuk pertanyaan. '
                'Jangan berlebihan, jangan selalu mengawali dengan Baik, dan jangan '
                'menambahkan tawa, desahan, tag suara, atau pengakuan mendeteksi emosi. '
                'Pertahankan ketepatan isi; jangan mengubah kode, URL, nama fungsi, '
                'jalur file, kutipan, angka, atau identifier demi gaya tutur. '
                'Tanggapi isi percakapan secara dinamis, tanpa pembuka tetap. '
                'Gunakan tutur tenang dan tidak antusias dalam konteks dukacita; '
                'jangan menganggap pengguna berduka dari nada suara saja dan jangan '
                'gunakan pembuka dukacita untuk masalah teknis atau kehilangan barang. '
                'Jangan berakting menangis, menyisipkan tag emosi, atau mengaku mengalami '
                'kehilangan pribadi.'
            )}]
            if delivery_protocol:
                messages[0]['content'] += delivery_registry.instruction()
            messages += [{'role':t.role,'content':t.text} for t in session.history[:-1] if t.text and t.status != 'interrupted']
            cancel_response()
            job = asyncio.create_task(collect(messages, lambda: authenticated(ws)))
            remote_jobs.add(job)
            job.add_done_callback(release_job)
            task = asyncio.create_task(respond(token, job)); tasks.add(task)
            task.add_done_callback(tasks.discard)

        async def deliver_chunks(token, text, delivery):
            """One inflight Session WAV + one prefetched successor, never a queue.

            The receive loop remains free to validate ACKs and cancel this task.
            The prefetched worker remains owned until cancellation has reaped it.
            """
            controls = {'delivery': delivery} if getattr(speech, 'delivery_intent_supported',
                getattr(speech, 'delivery_controls_supported', False)) else {}
            stream = speech.synthesize_chunks(text, **controls)
            prefetch = None
            key = None
            def current():
                return connected and session.active == token and authenticated(ws)
            async def next_piece():
                if not current():
                    raise asyncio.CancelledError
                return await anext(stream)
            try:
                part, wav, last = await next_piece()
                while current():
                    if not session.response_audio(token, wav, part):
                        raise ValueError('Chunk exceeds session audio bound')
                    chunk = session.drain_audio(token)
                    key = (token, chunk.chunk_id)
                    played = asyncio.Event()
                    ack_waiters[key] = played  # Register before send, even for immediate ACK.
                    await send_event({'type':'audio', 'token':asdict(token),
                        'chunk_id':chunk.chunk_id, 'last_chunk':last,
                        'wav_b64':base64.b64encode(chunk.data).decode('ascii')})
                    if last:
                        return
                    prefetch = asyncio.create_task(next_piece())
                    await asyncio.wait_for(played.wait(), CHUNK_ACK_TIMEOUT)
                    ack_waiters.pop(key, None); key = None
                    if not current():
                        return
                    # Drop the acknowledged waveform before retaining its successor.
                    chunk = wav = None
                    part, wav, last = await prefetch
                    prefetch = None
            finally:
                if key is not None:
                    ack_waiters.pop(key, None)
                if prefetch is not None:
                    prefetch.cancel()
                    await asyncio.gather(prefetch, return_exceptions=True)
                await stream.aclose()

        async def respond(token, job):
            wire = asdict(token)
            stage = 'llm'
            try:
                await send_event({'type':'response_start', 'token':wire})
                await send_event({'type':'processing', 'stage':'thinking'})
                reply = await asyncio.shield(job)
                text = reply.text
                if not authenticated(ws):
                    await ws.close(code=1008)
                    return
                if not session.response_text(token, text):
                    return
                await send_event({'type':'text_delta','token':wire,'text':text})
                stage = 'tts'
                if (getattr(speech, 'automatic_conditioning_enabled', False) is True
                        and connected and session.active == token and authenticated(ws)):
                    choice = speech.conditioning_for(reply.delivery)
                    await send_event({'type':'delivery_status', 'token':wire, 'choice':choice})
                await send_event({'type':'processing','stage':'synthesizing'})
                if callable(getattr(speech, 'synthesize_chunks', None)):
                    await deliver_chunks(token, text, reply.delivery)
                else:
                    controls = {'delivery': reply.delivery} if getattr(speech, 'delivery_intent_supported',
                        getattr(speech, 'delivery_controls_supported', False)) else {}
                    wav = await speech.synthesize(text, **controls)
                    if not authenticated(ws):
                        await ws.close(code=1008)
                        return
                    if not session.response_audio(token, wav, text):
                        return
                    chunk = session.drain_audio(token)
                    await send_event({'type':'audio','token':wire, 'chunk_id':chunk.chunk_id,
                                        'wav_b64':base64.b64encode(chunk.data).decode('ascii')})
                if session.active != token or not authenticated(ws):
                    return
                session.finish(token)
                await send_event({'type':'response_done','token':wire})
                await state()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if session.active == token:
                    status = getattr(exc, 'status_code', None)
                    if type(status) is not int or not 100 <= status <= 599:
                        status = None
                    timed_out = isinstance(exc, TimeoutError) or type(exc).__name__ == 'APITimeoutError'
                    if stage == 'llm':
                        code = ('llm_auth' if status in (401, 403) else 'llm_rate_limited' if status == 429
                                else 'llm_timeout' if timed_out else 'llm_failed')
                        session.interrupt()
                    else:
                        code = 'tts_busy' if isinstance(exc, LocalAudioBusy) else 'tts_timeout' if timed_out else 'tts_failed'
                        # Preserve generated text as complete even when no audio
                        # could be produced; never claim that it was heard.
                        session.finish(token)
                        await send_event({'type':'response_done','token':wire,'audio_available':False})
                    safe_types = {'RuntimeError','ValueError','TimeoutError','LocalAudioBusy','APITimeoutError',
                                  'AuthenticationError','PermissionDeniedError','RateLimitError','APIStatusError','APIConnectionError'}
                    error_type = type(exc).__name__ if type(exc).__name__ in safe_types else 'other'
                    from .speech import NativeTTSFailure
                    if stage == 'tts' and isinstance(exc, NativeTTSFailure):
                        # Revalidate at the logging boundary; no native stderr,
                        # arbitrary exception messages, paths or request data.
                        safe = NativeTTSFailure(exc.reason, exc.returncode)
                        logger.warning('response_failure stage=%s code=%s error_type=NativeTTSFailure http_status=%s native_reason=%s native_exit=%s',
                                       stage, code, status, safe.reason, safe.returncode)
                    else:
                        logger.warning('response_failure stage=%s code=%s error_type=%s http_status=%s', stage, code, error_type, status)
                    await send_event({'type':'error','code':code,'stage':stage,'message':ERROR_MESSAGES[code]})
                    await state()

        await state()
        try:
            while True:
                data = None
                try:
                    raw = await ws.receive_text()
                    if not authenticated(ws):
                        await ws.close(code=1008)
                        return
                    if len(raw) > 860_000:
                        raise ValueError('Pesan terlalu besar')
                    data = json.loads(raw)
                    validate(data, stateful=stateful)
                    kind = data['type']
                    if kind in ('text', 'voice_text', 'audio', 'audio_preview', 'mute', 'press') and not session.open:
                        raise CommandError('session_not_ready')
                    if kind == 'start':
                        invalidate_preview()
                        mode = data.get('mode', 'hands_free')
                        if mode not in ('hands_free', 'push_to_talk'):
                            raise ValueError('Invalid mode')
                        if session.open:
                            session.end()
                            cancel_response()
                        session.start(mode)
                        if 'start_id' in data:
                            if data.get('local_preview'):
                                if not getattr(speech, 'local_preview', False):
                                    raise ValueError('Preview not supported')
                                task = asyncio.create_task(prepare_capture(data['start_id'], capture_epoch))
                                tasks.add(task); task.add_done_callback(tasks.discard)
                            else:
                                await send_event({'type':'session_ready', 'start_id':data['start_id']})
                    elif kind == 'text':
                        launch(data['text'])
                    elif kind == 'voice_text':
                        if session.muted:
                            raise CommandError('microphone_muted')
                        if not getattr(speech, 'browser_stt_allowed', True):
                            raise CommandError('browser_stt_disabled')
                        # Client-supplied final text, never evidence of server-side STT.
                        launch(data['text'], 'voice')
                        await send_event({'type':'transcript', 'text':data['text'], 'source':'browser'})
                    elif kind == 'audio_preview':
                        if stateful and len(discard_jobs) >= 4:
                            raise CommandError('local_audio_busy')
                        if not getattr(speech, 'local_preview', False) or not speech.stt_available:
                            raise CommandError('api_stt_unavailable')
                        if not session.mic_active or session.responding or session.speaking:
                            raise ValueError('Microphone not available')
                        identifier = data['utterance_id']
                        now = asyncio.get_running_loop().time()
                        if identifier <= finalized_id or identifier < preview_id:
                            await state()
                            continue
                        pcm = base64.b64decode(data['pcm_b64'], validate=True)
                        wav = pcm16_wav(pcm, data['sample_rate'])
                        if len(pcm) < (16000 if stateful else 48000):
                            raise ValueError('Preview too short')
                        if identifier > preview_id:
                            invalidate_preview(finals=False); preview_id = identifier; preview_size = 0
                            if stateful:
                                from uuid import uuid4
                                stream_key = uuid4().hex
                        interval = STATEFUL_PREVIEW_MIN_INTERVAL if stateful else PREVIEW_MIN_INTERVAL
                        growth = 8000 if stateful else 16000
                        if now - preview_time >= interval and len(pcm) >= preview_size + growth:
                            preview_time = now; preview_size = len(pcm)
                            epoch = capture_epoch
                            def relevant(epoch=epoch, identifier=identifier):
                                return (connected and capture_epoch == epoch and session.mic_active
                                        and identifier > finalized_id and not session.responding and not session.speaking
                                        and authenticated(ws))
                            queue_preview((wav, {'utterance_id':identifier, 'revision':len(pcm)}, send_event, relevant, stream_key))
                    elif kind == 'audio':
                        if stateful and len(discard_jobs) >= 4:
                            raise CommandError('local_audio_busy')
                        if not speech.stt_available:
                            raise CommandError('api_stt_unavailable')
                        if not session.mic_active:
                            raise ValueError('Mikrofon tidak aktif')
                        pcm = base64.b64decode(data['pcm_b64'], validate=True)
                        wav = pcm16_wav(pcm, data['sample_rate'])
                        identifier = data.get('utterance_id')
                        if identifier is not None and (identifier <= finalized_id or identifier < preview_id):
                            await state()
                            continue
                        if len(stt_jobs) >= 2:
                            raise CommandError('provider_busy')
                        native_key = stream_key if identifier is not None and identifier == preview_id else None
                        if stateful and native_key is None:
                            from uuid import uuid4
                            native_key = uuid4().hex
                        invalidate_preview(discard=False)
                        if identifier is not None:
                            finalized_id = identifier
                        session.interrupt()
                        cancel_response()
                        task = asyncio.create_task(transcribe(wav, pcm, identifier, native_key, final_epoch))
                        if stateful:
                            final_stream_keys.add(native_key)
                            def final_finished(done, key=native_key):
                                final_stream_keys.discard(key)
                                if done.cancelled():
                                    discard_native(key)
                            task.add_done_callback(final_finished)
                        tasks.add(task); stt_jobs.add(task)
                        task.add_done_callback(tasks.discard)
                        task.add_done_callback(stt_jobs.discard)
                    elif kind == 'ack':
                        ack_token = ResponseToken(**data['token'])
                        if session.ack_played(ack_token, data['chunk_id']):
                            waiter = ack_waiters.get((ack_token, data['chunk_id']))
                            if waiter is not None:
                                waiter.set()
                    elif kind == 'interrupt':
                        invalidate_preview()
                        if session.open:
                            session.interrupt()
                        cancel_response()
                        await send_event({'type':'interrupted'})
                    elif kind == 'stop':
                        invalidate_preview()
                        if session.open:
                            session.end()
                        cancel_response()
                    elif kind == 'mute':
                        invalidate_preview()
                        session.set_mute(data['muted'])
                    elif kind == 'press':
                        session.press()
                    elif kind == 'release':
                        if session.open:
                            session.release()
                    elif kind == 'provider':
                        if session.open:
                            session.select_provider(data['provider'])
                        else:
                            session.provider = data['provider']
                    await state()
                except CommandError as exc:
                    await command_error(exc.code, kind)
                except (ValueError, TypeError, KeyError, binascii.Error):
                    command = data.get('type') if isinstance(data, dict) else None
                    command = command if isinstance(command, str) and command in SAFE_COMMANDS else 'unknown'
                    await command_error('invalid_message', command)
        except WebSocketDisconnect:
            pass
        finally:
            connected = False
            invalidate_preview()
            for task in tasks:
                if not task.cancelling():
                    task.cancel()
            # ASGI disconnect may cancel the enclosing AnyIO scope repeatedly.
            # Shield the join, not the work: every child was cancelled above and
            # must retain its native slot until its subprocess is really reaped.
            from anyio import CancelScope
            with CancelScope(shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)

    app.mount('/', StaticFiles(directory=Path(__file__).resolve().parent.parent / 'web', html=True), name='web')
    return app
