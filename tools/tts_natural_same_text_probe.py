"""Finite private quality12 comparison; no production switches or automatic retries."""
import asyncio
import hashlib
import io
import json
from pathlib import Path
import wave

ROOT=Path(__file__).absolute().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_review(path):
    data=json.loads(Path(path).read_text())
    if (data.get('private_native_probe_authorized') is not True
            or data.get('deployment_authorized') is not False
            or data.get('baseline_commit')!='fa0f9b0f1f952e192cc9844ca73b3d453d08704c'):
        raise ValueError('Independent private-probe approval required')
    rows=data.get('sha256_inventory')
    required={str(ROOT/'sela_live_tts'/name) for name in
              ('natural.py','natural_worker.py','duration.py','duration_worker.py','utterance.py',
               'backend.py','admission.py','source_pins.py','worker.py','upstream-manifest.json')}
    required.update(str(ROOT/'voice_app'/name) for name in
                    ('speech.py','supertonic3_worker.py','audio_continuity.py','tts_chunks.py',
                     'delivery.py','automatic_conditioning.py','approved_conditioning.py',
                     'pronunciation-id.json'))
    if type(rows) is not list or not required.issubset({r.get('path') for r in rows if type(r) is dict}):
        raise ValueError('Incomplete reviewed source inventory')
    for row in rows:
        p=Path(row['path'])
        if not p.is_absolute() or digest(p)!=row['sha256'] or p.stat().st_size!=row['bytes']:
            raise ValueError('Reviewed source drift')
    return data


async def collect_pair(text,baseline,natural,directory):
    from tools.tts_same_text_probe import finish_cleanup,require,wav_pcm
    result={}
    try:
        for name,stream in (('baseline',baseline),('natural',natural)):
            parts=[];last_seen=False;cursor=0
            joined=directory/(name+'12.wav')
            with wave.open(str(joined),'wb') as target:
                target.setnchannels(1);target.setsampwidth(2);target.setframerate(44100)
                async for source,audio,last in stream:
                    require(not last_seen and type(last) is bool,'last_sequence')
                    require(type(source) is str and source.strip(),'source_empty')
                    require(text[cursor:cursor+len(source)]==source,'source_coverage')
                    pcm=wav_pcm(audio)
                    chunk=directory/f'{name}-{len(parts)+1:02}.wav'
                    chunk.write_bytes(audio)
                    target.writeframes(pcm)
                    parts.append({'source':source,'start':cursor,'end':cursor+len(source),
                                  'wav':str(chunk),'sha256':digest(chunk),'frames':len(pcm)//2,
                                  'seconds':len(pcm)/88200,'last':last})
                    cursor+=len(source);last_seen=last
                    require(len(parts)<=24,'part_budget')
            require(cursor==len(text) and last_seen,'incomplete_source')
            result[name]={'parts':parts,'wav':str(joined),'sha256':digest(joined),
                          'seconds':sum(p['seconds'] for p in parts)}
        return result
    finally:
        async def close():
            for stream in (baseline,natural):await stream.aclose()
        task=asyncio.create_task(close())
        interrupted=await finish_cleanup(task)
        task.result()
        if interrupted:raise asyncio.CancelledError


async def main(*,review=ROOT/'docs/reviews/NATURAL-PRIVATE-PROBE-REPAIR-REVIEW.json'):
    # No models, adapters or output directory before independent approval.
    approval=validate_review(review)
    from dataclasses import asdict
    import os
    import tempfile
    from tools.tts_same_text_probe import atomic_receipt,production_hashes,require
    from sela_live_tts.admission import AdmissionCoordinator
    from sela_live_tts.backend import NativeSettings,SelaLiveTTS
    from sela_live_tts.natural import NaturalTTS,RuntimePins
    from sela_live_tts.utterance import SpeechRequest
    from voice_app.speech import _bounded_process
    source=Path('/root/sela-live-releases/live-release-4kkkhooe/prosody-arga/source.txt')
    source_sha='c55979e940a0489016bb158eb0443bbb4a435d18697559dd8501b3022deda94a'
    require(digest(source)==source_sha,'controlled_source_drift')
    text=source.read_text()
    pinned={r['path']:r['sha256'] for r in approval['sha256_inventory']}
    from voice_app.approved_conditioning import approved_profile
    profile=approved_profile(Path('/root/voice-lab/autonomous/expressive-tts/assets'),
             Path('/root/voice-lab/autonomous/expressive-tts/assets-manifest.json'))
    import importlib.util
    sdk_root=Path(json.loads((ROOT/'sela_live_tts/upstream-manifest.json').read_text())['sdk_root'])
    spec=importlib.util.spec_from_file_location('_natural_probe_pinned_sdk_utils',sdk_root/'utils.py')
    if spec is None or spec.loader is None:raise ValueError('Pinned SDK chunker unavailable')
    sdk=importlib.util.module_from_spec(spec);spec.loader.exec_module(sdk)
    coordinator=AdmissionCoordinator()
    coordinator.check_production()
    workers={str(ROOT/'sela_live_tts'/name) for name in
             ('worker.py','natural_worker.py','duration_worker.py')}
    def active_private_workers():
        found=[]
        for p in Path('/proc').iterdir():
            if not p.name.isdecimal():continue
            try:args=p.joinpath('cmdline').read_bytes().split(b'\0')
            except (FileNotFoundError,ProcessLookupError):continue
            if any(os.fsdecode(arg) in workers for arg in args):found.append(int(p.name))
        return found
    require(not active_private_workers(),'private_worker_already_active')
    output=ROOT/'autonomous/tts-natural-same-text'
    output.mkdir(parents=True,exist_ok=True)
    directory=Path(tempfile.mkdtemp(prefix='attempt-',dir=output))
    before=production_hashes()
    receipt={'status':'started','scope':'private comparison; production untouched',
             'source_sha256':source_sha,'source':text,'review':str(review),'review_sha256':digest(review),
             'driver_sha256':digest(Path(__file__)),'driver_test_sha256':digest(ROOT/'tests/python/test_sela_live_natural_probe.py'),
             'settings':{'quality_steps':12,'speed':1.05,'seed':20261026,'profile':'conditioning_b'},
             'profile_path':str(profile),'profile_sha256':digest(profile),
             'native_requests':[],
             'native_call_budget':24,'wall_seconds_budget':900,'native_calls':0,
             'cleanup_complete':False,'listener_accepted':False,'browser_transport_included':False}
    atomic_receipt(directory,receipt)
    async def native(args,payload,limit,**options):
        validate_review(review)
        require(args[2] in workers- {str(ROOT/'sela_live_tts/duration_worker.py')}
                and args[-2:] == ('conditioning_b','12'),'fixed_native_arguments')
        require(receipt['native_calls']<24,'native_call_budget')
        receipt['native_calls']+=1
        request={'index':receipt['native_calls'],'worker':args[2],'payload':json.loads(payload),
                 'sdk_contexts':sdk.chunk_text(json.loads(payload)['text'],800 if args[2].endswith('/natural_worker.py') else 300),
                 'started_monotonic':asyncio.get_running_loop().time(),'succeeded':False}
        receipt['native_requests'].append(request)
        atomic_receipt(directory,receipt)
        try:
            audio=await _bounded_process(args,payload,limit,**options)
            request['succeeded']=True
            return audio
        finally:
            request['wall_seconds']=asyncio.get_running_loop().time()-request['started_monotonic']
            atomic_receipt(directory,receipt)
    python='/root/voice-lab/autonomous/expressive-tts/venv/bin/python'
    assets='/root/voice-lab/autonomous/expressive-tts/assets'
    model_manifest='/root/voice-lab/autonomous/expressive-tts/assets-manifest.json'
    candidate_root=ROOT/'sela_live_tts'
    pins=RuntimePins(python,assets,model_manifest,str(candidate_root/'natural_worker.py'),
        str(candidate_root/'duration_worker.py'),pinned[str(candidate_root/'natural_worker.py')],
        pinned[str(candidate_root/'duration_worker.py')],pinned[str(candidate_root/'upstream-manifest.json')])
    failure=None
    try:
        baseline=SelaLiveTTS(NativeSettings(12),native_call=native,coordinator=coordinator)
        candidate=NaturalTTS(pins,native_call=native,coordinator=coordinator)
        async with asyncio.timeout(900):
            receipt['audio']=await collect_pair(text,baseline.synthesize_chunks(text,profile_id='conditioning_b'),
                        candidate.synthesize_chunks(SpeechRequest(text)),directory)
        if candidate.last_plan is None:raise ValueError('missing_plan')
        receipt['plan']=json.loads(json.dumps(asdict(candidate.last_plan),allow_nan=False))
        validate_review(review)
        receipt['status']='complete'
    except BaseException as error:
        receipt['status']='cancelled' if isinstance(error,asyncio.CancelledError) else 'failed'
        receipt['error_type']=type(error).__name__
        failure=error
    finally:
        receipt['remaining_private_workers']=active_private_workers()
        receipt['cleanup_complete']=not receipt['remaining_private_workers']
        receipt['production_app_unchanged']=production_hashes()==before
        if not receipt['cleanup_complete'] or not receipt['production_app_unchanged']:
            receipt['status']='failed_integrity_or_cleanup'
        atomic_receipt(directory,receipt)
    print(json.dumps({'directory':str(directory),'status':receipt['status'],
                      'native_calls':receipt['native_calls'],'cleanup_complete':receipt['cleanup_complete']}),flush=True)
    if failure is not None:raise failure
    require(receipt['status']=='complete','probe_not_complete')
    return directory


if __name__=='__main__':
    import sys
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(ROOT))
    if len(sys.argv)!=1:raise SystemExit('No request-controlled probe arguments accepted')
    asyncio.run(main())

