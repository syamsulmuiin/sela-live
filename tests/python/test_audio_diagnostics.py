"""Real ASGI/real Speech adapter; fixture ASR output is not model accuracy evidence."""
import base64
import json
import struct
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient
from tests.python.test_server import Provider, Speech as FixtureSpeech
from voice_app.server import create_app
from voice_app.speech import Speech


class AudioDiagnosticsTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def connect(self, speech):
        return TestClient(create_app(provider=Provider(), speech=speech,
                         local_test_auth=True, data_dir=self.tmp.name))

    def start(self, client):
        ws = client.websocket_connect('/ws', headers={'origin':'http://localhost', 'host':'localhost'})
        return ws

    def collect(self, ws, end):
        events = []
        for _ in range(50):
            item = ws.receive_json()
            events.append(item)
            if item['type'] == end:
                return events
        self.fail('missing end event')

    def test_original_audio_diagnostics_precede_asr_preserve_string_adapter_and_private_logs(self):
        pcm = struct.pack('<hh', -32768, 32767) * 800
        with patch('voice_app.speech._runtime_ready', return_value=True), patch(
                'voice_app.speech._bounded_process', new_callable=AsyncMock,
                return_value=b'{"text":"PRIVATE fixture transcript"}') as worker:
            speech = Speech(environ={})
            with self.connect(speech) as client, self.start(client) as ws:
                ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
                with self.assertLogs('voice_app.server', level='INFO') as logs:
                    ws.send_json({'type':'audio','pcm_b64':base64.b64encode(pcm).decode(), 'sample_rate':16000})
                    events = self.collect(ws, 'transcript')
                diagnostics = [e for e in events if e['type'] == 'audio_diagnostics']
                self.assertEqual(len(diagnostics), 1)
                d = diagnostics[0]['diagnostics']
                self.assertEqual(d['source'], 'original_pcm')
                self.assertEqual(d['warnings'], ['clipping', 'too_short'])
                self.assertIsNone(d['emotion'])
                self.assertEqual(d['emotion_status'], 'not_evaluated')
                self.assertEqual(events[-1]['text'], 'PRIVATE fixture transcript')
                stt_calls = [call for call in worker.call_args_list if call.args[0][-2] == 'stt']
                self.assertEqual(len(stt_calls), 1)
                self.assertEqual(stt_calls[0].args[1][:4], b'RIFF')
                self.assertNotIn('PRIVATE', '\n'.join(logs.output))
                self.assertNotIn(base64.b64encode(pcm).decode(), '\n'.join(logs.output))
                self.assertIn('audio_diagnostics', '\n'.join(logs.output))
                self.assertNotIn('text', json.dumps(d))

    def test_failed_asr_still_reports_capture_quality_without_exception_content(self):
        class FailingSpeech(FixtureSpeech):
            stt_available = True
            async def transcribe(self, wav):
                raise ValueError('PRIVATE decoder content')
        with self.connect(FailingSpeech()) as client, self.start(client) as ws:
            ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
            with self.assertLogs('voice_app.server', level='INFO') as logs:
                ws.send_json({'type':'audio', 'pcm_b64':base64.b64encode(b'\0\0' * 16000).decode(), 'sample_rate':16000})
                events = self.collect(ws, 'error')
            diagnostics = [e for e in events if e['type'] == 'audio_diagnostics']
            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0]['diagnostics']['warnings'], ['low_level', 'mostly_silent'])
            self.assertNotIn('PRIVATE', json.dumps(events) + '\n'.join(logs.output))

    def test_browser_text_has_no_acoustic_or_emotion_evidence(self):
        with self.connect(FixtureSpeech()) as client, self.start(client) as ws:
            ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
            ws.send_json({'type':'voice_text','text':'Fixture browser text'})
            events = self.collect(ws, 'response_done')
            self.assertFalse(any(e['type'] == 'audio_diagnostics' for e in events))
            self.assertEqual(next(e for e in events if e['type'] == 'transcript')['source'], 'browser')


if __name__ == '__main__':
    unittest.main()
