"""Same-text phase-one native probe; one parity call plus seven quality calls."""
import asyncio
import hashlib
import io
import json
import sys
import wave
from pathlib import Path
ROOT=Path(__file__).absolute().parents[1]
SOURCE=Path('/root/voice-lab/autonomous/one-minute-c2a5a7l8/receipt.json')
OUT=ROOT/'autonomous/tts-phase1-same-text'

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def production_hashes():return {p.name:digest(p) for p in Path('/root/voice-lab/voice_app').iterdir() if p.is_file()}

def busy():
    from sela_live_tts.admission import production_busy
    return production_busy()

async def finish_cleanup(task):
    """Drain owned cleanup even if the owner is repeatedly cancelled."""
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.cancelled():
                interrupted = True
        except BaseException:
            break
    return interrupted


class CleanupFailure(RuntimeError):
    pass


async def monitored(coro, *, busy_probe=None, interval=.05):
    probe = busy if busy_probe is None else busy_probe
    task = None
    try:
        if probe():
            raise RuntimeError('production_busy')
        task = asyncio.ensure_future(coro)
        while not task.done():
            await asyncio.wait((task,), timeout=interval)
            if probe():
                raise RuntimeError('production_busy')
        return task.result()
    finally:
        if task is None:
            coro.close()
        else:
            cancelling_child = not task.done()
            if cancelling_child:
                task.cancel()
            interrupted = await finish_cleanup(task)
            if not task.cancelled():
                cleanup_error = task.exception()
                if cancelling_child and cleanup_error is not None:
                    raise CleanupFailure('cleanup_failure') from None
            if interrupted:
                raise asyncio.CancelledError

def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def validate_baseline(original, partition):
    import re
    require(original['choice']['profile_id'] == 'conditioning_b', 'baseline_profile')
    text, parts = original['text'], original['parts']
    require(type(text) is str and len(text) == 1008 and len(text.split()) == 148, 'baseline_text')
    require(type(parts) is list and len(parts) == 7 and 1 + len(parts) <= 8, 'native_budget')
    require(all(type(p['source']) is str and p['source'].strip() for p in parts), 'baseline_source')
    require(''.join(p['source'] for p in parts) == text, 'baseline_coverage')
    require(all(type(p['last']) is bool and p['last'] == (i == 6) for i, p in enumerate(parts)), 'baseline_last')
    require(all(type(p['sha256']) is str and re.fullmatch('[0-9a-f]{64}', p['sha256']) for p in parts), 'baseline_hash')
    require(partition(text) == [p['source'] for p in parts], 'source_layout')
    return parts


async def bounded_process(argv, *, timeout=20, output_cap=65536, busy_probe=None, env=None):
    """Resolved executable, bounded diagnostics/deadline, terminate/kill/reap."""
    import os, signal
    executable = Path(argv[0])
    require(executable.is_absolute() and executable.is_file() and os.access(executable, os.X_OK), 'converter_executable')
    require(timeout > 0 and type(output_cap) is int and output_cap > 0, 'converter_limits')
    async def operation():
        proc = None
        tasks = []
        spawn = None
        total = 0
        stdout = bytearray()
        async def drain(reader, capture=False):
            nonlocal total
            while True:
                data = await reader.read(4096)
                if not data:
                    return
                total += len(data)
                if total > output_cap:
                    raise RuntimeError('converter_output_limit')
                if capture:
                    stdout.extend(data)
        async def cleanup():
            nonlocal proc
            if spawn is not None and proc is None:
                await finish_cleanup(spawn)
                if not spawn.cancelled() and spawn.exception() is None:
                    proc = spawn.result()
            if proc is not None:
                if proc.returncode is None:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(proc.wait(), .25)
                    except TimeoutError:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        try:
                            await asyncio.wait_for(proc.wait(), 1)
                        except TimeoutError:
                            raise CleanupFailure('cleanup_failure') from None
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                async def discard(reader):
                    # Do not retain diagnostics beyond the acceptance cap.
                    while await reader.read(4096):
                        pass
                try:
                    async with asyncio.timeout(1):
                        await asyncio.gather(discard(proc.stdout), discard(proc.stderr))
                except TimeoutError:
                    proc._transport.close()
                    raise CleanupFailure('cleanup_failure') from None
        try:
            async with asyncio.timeout(timeout):
                spawn = asyncio.create_task(asyncio.create_subprocess_exec(*map(str, argv), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, start_new_session=True, limit=8192))
                proc = await asyncio.shield(spawn)
                tasks = [asyncio.create_task(drain(proc.stdout, True)), asyncio.create_task(drain(proc.stderr)), asyncio.create_task(proc.wait())]
                await asyncio.gather(*tasks)
                if proc.returncode != 0:
                    raise RuntimeError('converter_failed')
                return bytes(stdout)
        finally:
            closing = asyncio.create_task(cleanup())
            interrupted = await finish_cleanup(closing)
            closing.result()
            if interrupted:
                raise asyncio.CancelledError()
    return await monitored(operation(), busy_probe=busy_probe)


async def inspect_sdk_origin(python, sdk_root, busy_probe):
    import os
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'SYSTEMROOT')}
    code = ('import sys,json,importlib.util; '
            f'sys.path.insert(0, {str(sdk_root.parent)!r}); '
            's=importlib.util.find_spec("supertonic"); '
            'print(json.dumps({"origin":None if s is None else s.origin,"sdk_imported":"supertonic" in sys.modules,"executable":sys.executable,"version":sys.version}))')
    actual = json.loads(await bounded_process([str(python), '-I', '-B', '-c', code], timeout=10, output_cap=8192, busy_probe=busy_probe, env=env))
    expected = str(sdk_root/'__init__.py')
    require(actual['origin'] == expected and actual['sdk_imported'] is False, 'sdk_origin')
    return {'actual_sdk_origin': actual['origin'], 'expected_sdk_origin': expected, 'sdk_imported': False, 'native_interpreter': str(python), 'native_interpreter_sha256': digest(python), 'native_actual_executable': actual['executable'], 'native_interpreter_version': actual['version']}


def atomic_receipt(directory, receipt):
    import os
    raw = json.dumps(receipt, ensure_ascii=False, indent=2)
    temporary = directory / '.receipt.tmp'
    with temporary.open('w', encoding='utf8') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, directory / 'receipt.json')
    require(json.loads((directory / 'receipt.json').read_text()) == receipt, 'receipt_readback')


def wav_pcm(raw, *, max_bytes=2_646_044):
    require(type(raw) is bytes and 44 < len(raw) <= max_bytes, 'wav_size')
    with wave.open(io.BytesIO(raw)) as reader:
        require((reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getcomptype()) == (1, 2, 44100, 'NONE'), 'wav_format')
        frames = reader.getnframes()
        require(frames > 0, 'wav_empty')
        pcm = reader.readframes(frames)
        require(len(pcm) == frames * 2 and len(raw) == 44 + len(pcm), 'wav_truncated_or_noncanonical')
    return pcm


async def default_dependencies():
    """Read-only preflight; constructing adapters never constructs a model."""
    import os, shutil
    sys.dont_write_bytecode = True
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from sela_live_tts.backend import SelaLiveTTS, NativeSettings
    from sela_live_tts.admission import production_busy
    from sela_live_tts.source_pins import verify_sources
    from sela_live_tts.worker import MANIFEST_SHA, PIN_HELPER_SHA
    from voice_app.approved_conditioning import approved_profile
    from voice_app.audio_continuity import trim_boundary_silence
    from voice_app.tts_chunks import source_parts
    from voice_app.speech import normalize_pronunciation, _offline_env
    manifest = ROOT/'sela_live_tts/upstream-manifest.json'
    helper = manifest.with_name('source_pins.py')
    require(digest(helper) == PIN_HELPER_SHA, 'pin_helper_drift')
    pins = verify_sources(manifest, MANIFEST_SHA)
    adapters = {steps: SelaLiveTTS(NativeSettings(steps)) for steps in (8, 12)}
    reference = adapters[8]
    profile = approved_profile(reference.assets, reference.manifest)
    resolved = shutil.which('ffmpeg')
    require(resolved is not None, 'converter_missing')
    ffmpeg = Path(resolved).resolve()
    require(ffmpeg.is_file() and os.access(ffmpeg, os.X_OK), 'converter_missing')
    origin = await inspect_sdk_origin(reference.python, Path(pins['sdk_root']), production_busy)
    evidence = dict(origin, tool=str(Path(__file__).absolute()), tool_sha256=digest(Path(__file__)), pin_manifest=str(manifest), pin_manifest_sha256=digest(manifest), pin_helper_sha256=digest(helper), sdk_commit=pins['sdk_commit'], profile_id='conditioning_b', profile=str(profile), profile_sha256=digest(profile), ffmpeg=str(ffmpeg), ffmpeg_sha256=digest(ffmpeg), app_interpreter=sys.executable, app_interpreter_version=sys.version, app_interpreter_sha256=digest(Path(sys.executable)), hash_scope='candidate Python/pins/tool; production voice_app top-level regular files only; not service state, external configuration, or all assets')
    def preflight():
        require(digest(helper) == PIN_HELPER_SHA, 'pin_helper_drift')
        verify_sources(manifest, MANIFEST_SHA)
        approved_profile(reference.assets, reference.manifest)
        return evidence
    def snapshot():
        files = {str(p.relative_to(ROOT)): digest(p) for p in (ROOT/'sela_live_tts').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
        for path in (Path(__file__), ROOT/'voice_app/tts_chunks.py', ROOT/'voice_app/speech.py', ROOT/'voice_app/audio_continuity.py', ROOT/'voice_app/approved_conditioning.py', ROOT/'voice_app/automatic_conditioning.py', ROOT/'voice_app/supertonic3_worker.py', profile, reference.manifest):
            files[str(path)] = digest(path)
        return {'candidate_files': files, 'production_app_top_level_files': production_hashes(), 'interpreters_and_converter': {str(p): digest(p) for p in (reference.python, Path(sys.executable), ffmpeg)}}
    async def native(steps, text, profile_id):
        return await adapters[steps].synthesize(text, profile_id=profile_id)
    async def converter(wav, mp3):
        env = _offline_env()
        await bounded_process([str(ffmpeg), '-nostdin', '-n', '-v', 'error', '-threads', '2', '-i', str(wav), '-codec:a', 'libmp3lame', '-q:a', '3', '-fs', '4000000', str(mp3)], busy_probe=production_busy, env=env)
        require(mp3.is_file() and 0 < mp3.stat().st_size < 4_000_000, 'mp3_size')
        await bounded_process([str(ffmpeg), '-nostdin', '-v', 'error', '-xerror', '-threads', '2', '-i', str(mp3), '-f', 'null', '-'], busy_probe=production_busy, env=env)
    return {'native': native, 'converter': converter, 'busy_probe': production_busy, 'partition': lambda text: source_parts(text, normalize_pronunciation, target=180), 'trim': trim_boundary_silence, 'preflight': preflight, 'snapshot': snapshot}


async def main(*, baseline_path=SOURCE, expected_baseline_sha='903c30579fb1b55051a7e2a3d4b0b43abe0bcf332b0ff53e0c939418901fed52', artifact_root=OUT, native=None, converter=None, busy_probe=None, partition=None, trim=None, preflight=None, snapshot=None):
    """Run one bounded attempt. Injections are for model-free isolated fixtures."""
    import tempfile
    artifact_root = Path(artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='attempt-', dir=artifact_root))
    receipt = {'status': 'started', 'scope': 'candidate only; production not switched', 'native_budget': 8, 'native_call_count': 0, 'native_call_count_semantics': 'native synthesize requests submitted, including failed calls; no retries', 'parts': [], 'cleanup_complete': False, 'listening_accepted': False, 'browser_transport_gaps_included': False}
    atomic_receipt(directory, receipt)
    stream = None
    error = None
    before = None
    async def call(steps, source):
        require(receipt['native_call_count'] < 8, 'native_budget')
        receipt['native_call_count'] += 1
        atomic_receipt(directory, receipt)
        return await native(steps, source.strip(), 'conditioning_b')
    async def chunks(parts):
        for index, part in enumerate(parts):
            raw = await call(12, part['source'])
            audio = trim(raw, first=index == 0, last=index == 6, max_output_bytes=2_646_044)
            yield part['source'], audio, index == 6
    try:
        baseline_path = Path(baseline_path)
        raw_baseline = baseline_path.read_bytes()
        require(hashlib.sha256(raw_baseline).hexdigest() == expected_baseline_sha, 'baseline_receipt_hash')
        original = json.loads(raw_baseline)
        # Lazy imports and runtime inspection occur only inside an actual attempt.
        if any(x is None for x in (native, converter, busy_probe, partition, trim, preflight, snapshot)):
            deps = await default_dependencies()
            native = deps['native'] if native is None else native
            converter = deps['converter'] if converter is None else converter
            busy_probe = deps['busy_probe'] if busy_probe is None else busy_probe
            partition = deps['partition'] if partition is None else partition
            trim = deps['trim'] if trim is None else trim
            preflight = deps['preflight'] if preflight is None else preflight
            snapshot = deps['snapshot'] if snapshot is None else snapshot
        parts = validate_baseline(original, partition)
        for part in parts:
            baseline_wav = Path(part['wav']).read_bytes()
            wav_pcm(baseline_wav)
            require(hashlib.sha256(baseline_wav).hexdigest() == part['sha256'], 'baseline_artifact_hash')
        before = snapshot()
        if 'production_app_top_level_files' in before:
            require(all(before['production_app_top_level_files'].get(name) == expected for name, expected in original['source_hashes'].items()), 'baseline_production_source_drift')
        evidence = preflight()
        receipt.update(evidence=evidence, source_hashes_before=before, baseline_receipt=str(baseline_path), baseline_receipt_sha256=expected_baseline_sha, text=original['text'], text_sha256=hashlib.sha256(original['text'].encode()).hexdigest(), same_profile='conditioning_b', same_speed=1.05, same_seed=20261026, same_silence_duration=.3, target_chunk=180, steps_order=[8] + [12]*7)
        require(not busy_probe(), 'production_busy')
        parity = await monitored(call(8, parts[4]['source']), busy_probe=busy_probe)
        parity = trim(parity, first=False, last=False, max_output_bytes=2_646_044)
        wav_pcm(parity)
        require(hashlib.sha256(parity).hexdigest() == parts[4]['sha256'], 'baseline_parity')
        path = directory / 'baseline-parity-chunk05.wav'
        path.write_bytes(parity)
        require(digest(path) == parts[4]['sha256'], 'parity_readback')
        receipt['baseline_parity_byte_exact'] = True
        stream = chunks(parts)
        pcm = []
        for index in range(7):
            source, audio, last = await monitored(stream.__anext__(), busy_probe=busy_probe)
            require(source == parts[index]['source'] and type(last) is bool and last == (index == 6), 'part_identity')
            data = wav_pcm(audio)
            path = directory / f'quality12-chunk-{index+1:02}.wav'
            path.write_bytes(audio)
            require(path.read_bytes() == audio, 'part_readback')
            pcm.append(data)
            receipt['parts'].append({'source': source, 'last': last, 'wav': str(path), 'sha256': digest(path), 'duration_seconds': len(data)/88200, 'native_steps': 12})
            atomic_receipt(directory, receipt)
        require(receipt['native_call_count'] == 8, 'native_call_count')
        require(len(pcm) == 7 and ''.join(p['source'] for p in receipt['parts']) == original['text'], 'final_coverage')
        require([p['last'] for p in receipt['parts']] == [False]*6+[True], 'final_last')
        wav = directory / 'sela-live-tts-quality12-same-paragraph.wav'
        with wave.open(str(wav), 'wb') as writer:
            writer.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            writer.writeframes(b''.join(pcm))
        require(wav_pcm(wav.read_bytes(), max_bytes=7*2_646_044) == b''.join(pcm), 'final_wav_readback')
        mp3 = wav.with_suffix('.mp3')
        await monitored(converter(wav, mp3), busy_probe=busy_probe)
        require(digest(baseline_path) == expected_baseline_sha, 'baseline_final_hash')
        require(wav_pcm(wav.read_bytes(), max_bytes=7*2_646_044) == b''.join(pcm), 'final_wav_readback')
        for part in receipt['parts']:
            require(digest(Path(part['wav'])) == part['sha256'], 'final_part_hash')
        require(digest(directory/'baseline-parity-chunk05.wav') == parts[4]['sha256'], 'final_parity_hash')
        require(mp3.is_file() and 0 < mp3.stat().st_size < 4_000_000, 'mp3_size')
        require(preflight() == evidence, 'preflight_drift')
        receipt.update(wav=str(wav), wav_sha256=digest(wav), mp3=str(mp3), mp3_sha256=digest(mp3), duration_seconds=sum(map(len, pcm))/88200, same_text_same_source_parts=True)
    except BaseException as exc:
        error = exc
    finally:
        if stream is not None:
            cleanup = asyncio.create_task(stream.aclose())
            interrupted = await finish_cleanup(cleanup)
            try:
                cleanup.result()
            except BaseException:
                error = RuntimeError('cleanup_failure')
            else:
                receipt['cleanup_complete'] = True
                if interrupted:
                    error = asyncio.CancelledError()
        else:
            receipt['cleanup_complete'] = True
        if isinstance(error, CleanupFailure):
            receipt['cleanup_complete'] = False
        evidence_failure = False
        try:
            if before is not None:
                after = snapshot()
                receipt['source_hashes_after'] = after
                receipt['production_source_unchanged'] = before == after
                require(before == after, 'source_drift')
        except BaseException as exc:
            evidence_failure = True
            receipt['source_evidence_complete'] = False
            error = exc
        else:
            receipt['source_evidence_complete'] = before is not None
        receipt['partial_artifacts'] = []
        try:
            for path in sorted(directory.iterdir()):
                if path.is_file() and path.name not in ('receipt.json', '.receipt.tmp'):
                    receipt['partial_artifacts'].append({'name': path.name, 'sha256': digest(path), 'bytes': path.stat().st_size})
            receipt['artifact_inventory_complete'] = True
        except BaseException as exc:
            receipt['artifact_inventory_complete'] = False
            error = exc
        receipt['status'] = 'cancelled' if isinstance(error, asyncio.CancelledError) else 'failed' if error is not None else 'completed'
        receipt['result'] = 'listening_not_accepted' if error is None else 'not_accepted'
        if error is not None:
            # Never serialize raw exception messages, native diagnostics or secrets.
            receipt['reason'] = 'cleanup_failure' if not receipt['cleanup_complete'] else 'evidence_failure' if evidence_failure else 'artifact_inventory_failure' if not receipt['artifact_inventory_complete'] else 'cancelled' if isinstance(error, asyncio.CancelledError) else 'probe_failed'
        try:
            atomic_receipt(directory, receipt)
        except BaseException:
            receipt.update(status='failed', reason='receipt_readback_failure', result='not_accepted')
            atomic_receipt(directory, receipt)
            raise
    if error is not None:
        raise error
    return receipt

if __name__ == '__main__':
    asyncio.run(main())
