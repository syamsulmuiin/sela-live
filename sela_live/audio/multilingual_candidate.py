"""Isolated Supertonic-3 candidate policy, not a production worker entrypoint.

Use only inside the existing audited sandbox/guard; never bypass native guard.
"""
from sela_live.audio.multilingual_tts_request import parse_multilingual_request
from sela_live.audio.tts import validate_text


def controlled_multilingual_runtime(guard, chunker):
    """Wrap pinned native Runtime without altering the existing natural_v1 worker."""
    class CandidateRuntime(guard.Runtime):
        def __init__(self, root, manifest, selected_profile='conditioning_b'):
            if selected_profile != 'conditioning_b':
                raise ValueError('Only conditioning_b profile is permitted')
            super().__init__(root, manifest, selected_profile)

        def synthesize(self, request):
            import numpy as np
            parsed = parse_multilingual_request(request)
            engine = self.engine
            original = engine.synthesize
            previous = engine.__dict__.get('synthesize')
            had_override = 'synthesize' in engine.__dict__

            def checked(text, **kwargs):
                if (type(text) is not str or text != parsed.text
                        or set(kwargs) != {'voice_style', 'lang', 'total_steps', 'verbose', 'speed'}
                        or kwargs['voice_style'] is not self.style
                        or kwargs['lang'] != 'id' or type(kwargs['lang']) is not str
                        or kwargs['total_steps'] != 8 or type(kwargs['total_steps']) is not int
                        or kwargs['verbose'] is not False
                        or type(kwargs['speed']) is not float or kwargs['speed'] != 1.05):
                    raise ValueError('delivery_controls')
                chunks = chunker(text, 800)
                if (type(chunks) is not list or len(chunks) != 1
                        or type(chunks[0]) is not str
                        or ' '.join(chunks[0].split()) != ' '.join(text.split())):
                    raise ValueError('text_limit')
                validate_text(chunks[0])
                ids, mask = engine.model.text_processor([chunks[0]], parsed.language)
                if (not isinstance(ids, np.ndarray) or ids.dtype != np.int64
                        or ids.ndim != 2 or ids.shape[0] != 1
                        or not 1 <= ids.shape[1] <= 512 or not (ids >= 0).all()
                        or not isinstance(mask, np.ndarray) or mask.dtype != np.float32
                        or mask.shape != (1, 1, ids.shape[1]) or not (mask == 1).all()):
                    raise ValueError('text_limit')
                duration, *_ = engine.model.dp_ort.run(None, {
                    'text_ids': ids, 'style_dp': self.style.dp, 'text_mask': mask})
                if (not isinstance(duration, np.ndarray) or duration.shape != (1,)
                        or duration.dtype.kind != 'f'):
                    raise ValueError('duration_limit')
                duration = duration / 1.05
                if not np.isfinite(duration).all() or not 0 < float(duration[0]) <= 28:
                    raise ValueError('duration_limit')
                return original(text, **dict(kwargs, lang=parsed.language,
                                             total_steps=12, max_chunk_length=800))

            engine.synthesize = checked
            try:
                # Original guard retains PCM16, clipping and 30s output checks.
                return super().synthesize({'text': parsed.text})
            finally:
                if had_override:
                    engine.synthesize = previous
                else:
                    del engine.synthesize
    return CandidateRuntime
