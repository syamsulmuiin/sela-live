"""Fail-fast shared host admission: one workload, zero queued waiters.

Production and evaluation use one persistent app-owned inode, never unlinked or
replaced. Only evaluation performs read-only production detection/cancellation;
production uses the same lock after Speech owns/quiesces its audio resources.
"""
import fcntl
import os
from pathlib import Path
import stat
from contextlib import contextmanager

EVALUATION_LOCK = Path('/root/voice-lab/.runtime/live-tts.lock')


class LiveTTSBusy(RuntimeError):
    """Evaluation rejected or interrupted by admission/production priority."""


PRODUCTION_WORKERS = frozenset(('supertonic3_worker.py', 'zipformer_worker.py',
                                'zipformer_stateful_worker.py', 'local_audio_worker.py'))
PRODUCTION_APP = Path('/root/voice-lab/voice_app')


def production_busy(*, proc_root: str | Path = '/proc'):
    """Read cmdline/cwd only; errors deny evaluation, vanished PIDs are normal.

    Only exact worker path arguments count, not substrings in shell/-c text.
    Snapshot races and workers starting between snapshots remain possible.
    """
    try:
        configured_worker = Path(os.environ.get('VOICE_SELA_LIVE_WORKER', '/root/voice-lab/sela_live_tts/worker.py'))
        if not configured_worker.is_absolute() or '..' in configured_worker.parts:
            raise LiveTTSBusy('Invalid configured production worker')
        live_workers = {Path('/root/voice-lab/sela_live_tts/worker.py'), configured_worker}
        for process in Path(proc_root).iterdir():
            if not process.name.isdecimal():
                continue
            try:
                argv = process.joinpath('cmdline').read_bytes().split(b'\0')
                for argument in argv:
                    path = Path(os.fsdecode(argument))
                    if path.name not in PRODUCTION_WORKERS and path.name not in {p.name for p in live_workers}:
                        continue
                    if not path.is_absolute():
                        path = Path(os.readlink(process / 'cwd')) / path
                    if (path.resolve() in live_workers
                            or (path.name in PRODUCTION_WORKERS and path.resolve() == PRODUCTION_APP / path.name)):
                        return True
            except (FileNotFoundError, ProcessLookupError):
                # Ignore only a vanished PID, not an unreadable live snapshot.
                if process.exists():
                    raise LiveTTSBusy('Incomplete production worker snapshot')
                continue
        return False
    except Exception as error:
        raise LiveTTSBusy('Production worker probe failed closed') from error


class AdmissionCoordinator:
    """Separate instances/processes coordinate via nonblocking flock.

    Inject a temporary lock and synthetic production probe in unit tests.
    """
    def __init__(self, lock_path=None, *, production_probe=production_busy,
                 poll_interval=.05):
        self.lock_path = Path(os.environ.get('VOICE_SELA_LIVE_LOCK', str(EVALUATION_LOCK))
                              if lock_path is None else lock_path)
        self.production_probe = production_probe
        self.poll_interval = poll_interval

    def check_production(self):
        try:
            busy = self.production_probe()
        except Exception as error:
            raise LiveTTSBusy('Production worker probe failed closed') from error
        if busy:
            raise LiveTTSBusy('Production speech has priority')

    @contextmanager
    def claim(self):
        self.check_production()
        fd = None
        try:
            if (not self.lock_path.is_absolute() or '..' in self.lock_path.parts
                    or any(p.is_symlink() for p in (self.lock_path, *self.lock_path.parents))):
                raise LiveTTSBusy('Unsafe shared host lock path')
            # Runtime installer, not admission, provisions the parent directory.
            fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW |
                         os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise LiveTTSBusy('Unsafe evaluation lock')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current = self.lock_path.stat(follow_symlinks=False)
            if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                raise LiveTTSBusy('Evaluation lock inode changed')
            self.check_production()
        except BaseException as error:
            if fd is not None:
                os.close(fd)
            if isinstance(error, OSError):
                raise LiveTTSBusy('Evaluation lock unavailable') from error
            raise
        try:
            yield
        finally:
            os.close(fd)


class ProductionAdmissionCoordinator(AdmissionCoordinator):
    """Same persistent host lock, no evaluation-only production/ASR probe.

    Speech already owns its audio slot and quiesces ASR before claiming. An
    idle quiesced ASR PID is not a competing inference workload here.
    """
    def check_production(self):
        return

