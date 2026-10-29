"""Offline synthetic PCM tests; no native model or remote provider calls."""
import io
import struct
import unittest
import wave

import importlib
import importlib.util


def trim(raw, *, first, last, **kwargs):
    assert importlib.util.find_spec('voice_app.audio_continuity') is not None, 'Missing boundary continuity helper'
    return importlib.import_module('voice_app.audio_continuity').trim_boundary_silence(raw, first=first, last=last, **kwargs)

RATE = 44100
PAD = 5292
MIN = 11025
CAP = 33075


def wav(samples, rate=RATE, channels=1, width=2):
    out = io.BytesIO()
    with wave.open(out, 'wb') as writer:
        writer.setparams((channels, width, rate, 0, 'NONE', 'not compressed'))
        writer.writeframes(struct.pack('<' + 'h' * len(samples), *samples))
    return out.getvalue()


def pcm(raw):
    with wave.open(io.BytesIO(raw), 'rb') as reader:
        return reader.readframes(reader.getnframes())


class ContinuityTests(unittest.TestCase):
    def test_only_internal_boundaries_trim_with_exact_retained_pcm(self):
        active = [2, -2, 3, -3] + [0] * 15000 + [5, -5]
        raw = wav([0, 1, -1] * 5000 + active + [1, 0, -1] * 6000)
        result = trim(raw, first=False, last=False)
        self.assertEqual(pcm(result), pcm(raw)[(15000-PAD)*2:-(18000-PAD)*2])
        self.assertEqual(len(pcm(result)), (2*PAD+len(active))*2)
        self.assertEqual(pcm(result)[PAD*2:-PAD*2], pcm(wav(active)))


    def test_thresholds_and_padding_are_sample_exact(self):
        for quiet in (0, 1, PAD, MIN-1, MIN, MIN+1):
            with self.subTest(quiet=quiet):
                raw = wav([1]*quiet + [2, -2] + [-1]*quiet)
                removed = quiet-PAD if quiet >= MIN else 0
                result = trim(raw, first=False, last=False)
                expected = pcm(raw)[removed*2:len(pcm(raw))-removed*2]
                self.assertEqual(pcm(result), expected)
                if not removed:
                    self.assertIs(result, raw)

    def test_quiet_consonant_amplitudes_stop_edge_scan(self):
        for low in (-32768, -3, -2, 2, 3, 32767):
            with self.subTest(low=low):
                raw = wav([low] + [0]*20000 + [low])
                self.assertIs(trim(raw, first=False, last=False), raw)
                raw = wav([0]*MIN + [low] + [0]*MIN)
                self.assertEqual(pcm(trim(raw, first=False, last=False)), pcm(wav([0]*PAD+[low]+[0]*PAD)))

    def test_first_last_and_one_piece_identity(self):
        raw = wav([0]*16000 + [7] + [0]*17000)
        for first, last in ((True, True), (True, False), (False, True), (False, False)):
            with self.subTest(first=first, last=last):
                result = trim(raw, first=first, last=last)
                left = 0 if first else 16000-PAD
                right = 0 if last else 17000-PAD
                self.assertEqual(pcm(result), pcm(raw)[left*2:len(pcm(raw))-right*2])
                if first and last:
                    self.assertIs(result, raw)

    def test_per_edge_removal_is_capped(self):
        for quiet in (CAP+PAD-1, CAP+PAD, CAP+PAD+1, RATE*2):
            with self.subTest(quiet=quiet):
                raw = wav([0]*quiet + [10] + [0]*quiet)
                removed = min(quiet-PAD, CAP)
                self.assertEqual(pcm(trim(raw, first=False, last=False)), pcm(raw)[removed*2:-removed*2])

    def test_silent_or_floor_only_is_kept_byte_exact(self):
        for values in ([0], [0]*MIN, [-1, 0, 1]*12000):
            raw = wav(values)
            for first, last in ((True, False), (False, True), (False, False)):
                self.assertIs(trim(raw, first=first, last=last), raw)

    def test_unsafe_wavs_reject_explicitly(self):
        raw = wav([7]*100)
        bad = [b'', b'not WAV', raw[:-1], raw+b'xx', bytearray(raw), wav([]),
               wav([7]*100, rate=16000), wav([7]*100, channels=2), wav([7]*100, width=1)]
        for offset, value, fmt in ((4, 0, '<I'), (16, 18, '<I'), (20, 3, '<H'),
                                    (28, 1, '<I'), (32, 1, '<H'), (40, 1, '<I')):
            changed = bytearray(raw); struct.pack_into(fmt, changed, offset, value); bad.append(bytes(changed))
        for value in bad:
            with self.subTest(prefix=repr(value)[:60]), self.assertRaises(ValueError):
                trim(value, first=False, last=False)

    def test_duration_and_output_limits(self):
        raw = wav([0]*(RATE*30-1) + [2])
        self.assertEqual(pcm(trim(raw, first=True, last=False)), pcm(raw))
        with self.assertRaises(ValueError):
            trim(wav([2]*(RATE*30+1)), first=False, last=False)
        raw = wav([0]*MIN+[2])
        with self.assertRaises(ValueError):
            trim(raw, first=False, last=False, max_output_bytes=len(raw)-1)
        self.assertLessEqual(len(trim(raw, first=False, last=False, max_output_bytes=len(raw))), len(raw))
        with self.assertRaises(ValueError):
            trim(b'x'*4_000_001, first=False, last=False)
        for limit in (0, 44, True, 4.0):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                trim(raw, first=False, last=False, max_output_bytes=limit)
        with self.assertRaises(ValueError):
            trim(raw, first=1, last=False)


class SpeechContinuityTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_piece_and_other_backends_bypass_helper(self):
        import subprocess
        from unittest.mock import patch
        import test_supertonic3_adapter as fixtures
        from voice_app.speech import Speech
        raw = wav([0]*16000 + [2] + [0]*17000)
        with fixtures.configured(VOICE_SUPERTONIC3_STYLE_PROFILE='conditioning_b') as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            speech = Speech(environ=env)
        with patch.object(speech, '_local_request', return_value=raw), patch('voice_app.audio_continuity.trim_boundary_silence', side_effect=AssertionError('single must bypass')):
            self.assertEqual([p async for p in speech.synthesize_chunks('  Satu kalimat.  ')], [('  Satu kalimat.  ', raw, True)])
        with patch('voice_app.speech._runtime_ready', return_value=False):
            piper = Speech(environ={'VOICE_TTS_BACKEND':'piper', 'VOICE_STT_BACKEND':'browser'})
        with patch.object(piper, 'synthesize', return_value=raw), patch('voice_app.audio_continuity.trim_boundary_silence', side_effect=AssertionError('other must bypass')):
            self.assertEqual([p async for p in piper.synthesize_chunks('  Kalimat.  '*40)], [('  Kalimat.  '*40, raw, True)])

    async def test_invalid_multipart_audio_is_not_delivered_or_retried(self):
        import subprocess
        from unittest.mock import patch
        import test_supertonic3_adapter as fixtures
        from voice_app.speech import Speech
        with fixtures.configured() as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            speech = Speech(environ=env)
        with patch.object(speech, '_synthesize', return_value=b'invalid') as synth:
            with self.assertRaises(ValueError):
                await anext(speech.synthesize_chunks('Kalimat cukup panjang yang tetap harus dibacakan. '*8))
            self.assertEqual(synth.await_count, 1)

    async def test_multipart_sources_flags_pcm_snapshot_and_ack_are_unchanged(self):
        import json
        import subprocess
        from unittest.mock import patch
        import test_supertonic3_adapter as fixtures
        from voice_app.speech import Speech, normalize_pronunciation
        from voice_app.tts_chunks import source_parts
        from voice_app.session_core import Session
        from voice_app.delivery import load_registry
        raw = wav([0]*16000 + [2, -2, 100, -100] + [1]*17000)
        text = ('  API 21 dengan kalimat yang cukup panjang dan tetap sama persis untuk sumber. ' * 4) + '\tSelesai.  '
        parts = source_parts(text, normalize_pronunciation)
        self.assertGreater(len(parts), 2)
        with fixtures.configured(VOICE_SUPERTONIC3_STYLE_PROFILE='conditioning_b', VOICE_AUTOMATIC_CONDITIONING='1') as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            speech = Speech(environ=env)
        self.assertFalse(speech.delivery_controls_supported)
        delivery = load_registry().resolve('sadness', 'low')
        calls = []
        async def request(action, payload, limit, **kwargs):
            calls.append((action, json.loads(payload), kwargs))
            return raw
        speech._local_request = request
        iterator = speech.synthesize_chunks(text, delivery=delivery)
        delivered = [await anext(iterator)]
        object.__setattr__(delivery, 'emotion', 'anger')
        object.__setattr__(delivery, 'intensity', 'high')
        delivered.extend([part async for part in iterator])
        self.assertEqual([item[0] for item in delivered], parts)
        self.assertEqual([item[2] for item in delivered], [False]*(len(parts)-1)+[True])
        for i, (_, audio, _) in enumerate(delivered):
            self.assertEqual(audio, trim(raw, first=i==0, last=i==len(parts)-1))
        self.assertTrue(all(call[2] == {'profile_id':'conditioning_b_calm_low'} for call in calls))
        self.assertTrue(all('delivery' not in call[1] for call in calls))
        self.assertEqual([call[1]['text'] for call in calls], [normalize_pronunciation(p.strip()) for p in parts])
        session = Session(); session.start(); token = session.submit('Fixture')
        session.response_text(token, text)
        for source, audio, _ in delivered:
            self.assertTrue(session.response_audio(token, audio, source))
            chunk = session.drain_audio(token)
            self.assertTrue(session.ack_played(token, chunk.chunk_id))
        self.assertEqual(session.history[-1].text, text)
        self.assertEqual(session.history[-1].heard_text, text)


if __name__ == '__main__':
    unittest.main()
