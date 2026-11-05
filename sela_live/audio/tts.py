"""Sela Live natural synthesis policy; unchanged B/12/id acoustics.

Owns request/control validation, tokenizer and selected-style DP admission.
NumPy is imported only inside synthesis, after native guard isolation.
"""

def validate_text(text):
    if (type(text) is not str or not text.strip() or len(text) > 800
            or len(text.encode('utf8')) > 3200):
        raise ValueError('text_limit')
    if '<' in text or '>' in text:
        raise ValueError('unsupported_tag')
    if any(ord(c) < 32 and c not in '\n\t' for c in text):
        raise ValueError('text_control')
    return text


def controlled_runtime(guard, chunker):
    class ControlledRuntime(guard.Runtime):
        def synthesize(self, request):
            if type(request) is not dict or set(request) != {'text'}:
                raise ValueError('request_fields')
            validate_text(request['text'])
            engine = self.engine
            original = engine.synthesize
            had_override = 'synthesize' in engine.__dict__
            previous = engine.__dict__.get('synthesize')

            def checked(text, **kwargs):
                import numpy as np
                if (type(text) is not str or text != request['text']
                        or set(kwargs) != {'voice_style', 'lang', 'total_steps', 'verbose', 'speed'}
                        or kwargs['voice_style'] is not self.style
                        or type(kwargs['lang']) is not str or kwargs['lang'] != 'id'
                        or type(kwargs['total_steps']) is not int or kwargs['total_steps'] != 8
                        or kwargs['verbose'] is not False
                        or type(kwargs['speed']) is not float or kwargs['speed'] != 1.05):
                    raise ValueError('delivery_controls')
                chunks = chunker(text, 800)
                if (type(chunks) is not list or len(chunks) != 1
                        or type(chunks[0]) is not str
                        or ' '.join(chunks[0].split()) != ' '.join(text.split())):
                    raise ValueError('text_limit')
                validate_text(chunks[0])
                # Match the exact context the pinned high-level chunker will pass to core.
                ids, mask = engine.model.text_processor([chunks[0]], 'id')
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
                # Use genuine selected-style DP, and the SDK's dtype-preserving division.
                duration = duration / 1.05
                if not np.isfinite(duration).all() or not 0 < float(duration[0]) <= 28:
                    raise ValueError('duration_limit')
                return original(text, **dict(kwargs, total_steps=12, max_chunk_length=800))

            engine.synthesize = checked
            try:
                return super().synthesize(request)
            finally:
                if had_override:
                    engine.synthesize = previous
                else:
                    del engine.synthesize
    return ControlledRuntime

