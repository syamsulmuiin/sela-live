"""Compatibility import for the Sela Live owned streaming STT state."""
# -I excludes the worker directory and project root. Trust only this installed
# sibling project, never the model directory or current working directory.
if not __package__:
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sela_live.audio.stt import ZipformerState, validate_key

__all__ = ['ZipformerState', 'validate_key']
