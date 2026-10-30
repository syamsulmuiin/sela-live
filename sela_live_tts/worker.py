"""Isolated cloned-SDK worker; reuse unchanged native safety/profile guards."""
import sys,os,hashlib,importlib.util
from pathlib import Path
sys.dont_write_bytecode=True
HERE=Path(__file__).absolute().parent
MANIFEST_SHA='f91cc185c59c19cb0519977811762d30b635cfd609a8336083c5d8dd77055f74'
PIN_HELPER_SHA='9436ec03105e5a7a787cdfc8cb87e4e77d10670a58656b1561fa3218a59dccc0'


def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:raise ValueError('Invalid native module specification')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def main():
    helper=HERE/'source_pins.py'
    if any(p.is_symlink() for p in (helper,*helper.parents)) or hashlib.sha256(helper.read_bytes()).hexdigest()!=PIN_HELPER_SHA:
        raise ValueError('Pinned verification helper drift')
    pins=load_module('_sela_live_source_pins',helper)
    data=pins.verify_sources(HERE/'upstream-manifest.json',MANIFEST_SHA)
    if len(sys.argv)!=6 or sys.argv[1] not in ('check','tts'):
        raise ValueError('Invalid worker arguments')
    action,assets,manifest,profile,raw_steps=sys.argv[1:]
    steps=int(raw_steps)
    if steps not in (8,10,12) or str(steps)!=raw_steps or not profile.startswith('conditioning_b'):
        raise ValueError('Invalid bounded worker selection')
    sys.path.insert(0,str(Path(data['sdk_root']).parent))
    controls=load_module('_sela_live_native_controls',HERE/'controls.py')
    guard=load_module('_sela_live_guard',Path(data['guard_root'])/'supertonic3_worker.py')
    base=guard.Runtime
    class ControlledRuntime(base):
        def synthesize(self,request):
            with controls.native_step_override(self.engine,steps):
                return super().synthesize(request)
    setattr(guard,'Runtime',ControlledRuntime)
    sys.argv=[str(Path(data['guard_root'])/'supertonic3_worker.py'),action,assets,manifest,profile]
    try:
        guard.main()
    except Exception as exc:
        guard.emit_failure(guard.failure_reason(exc));raise SystemExit(1)


if __name__=='__main__':
    try:main()
    except Exception:
        os.write(2,b'supertonic3_failure=asset_error\n');raise SystemExit(1)
