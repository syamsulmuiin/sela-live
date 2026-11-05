"""Sela Live integrated local speech core; no eager native imports."""
__version__ = '0.1.0'
CAPABILITIES = {
    'product': 'Sela Live', 'version': __version__, 'audio_execution': 'local_server',
    'stt': 'stateful_zipformer_id_int8', 'tts': 'supertonic3_natural_v1_quality12',
    'trained_emotion': False, 'multilingual_stt': False, 'brain': 'remote_api_or_codex',
}
