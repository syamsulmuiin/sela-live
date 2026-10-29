"""Automatic activation transport tests; scripted boundaries, no inference."""
import asyncio
import json
import tempfile
import threading
import unittest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from voice_app.audio import pcm16_wav
from voice_app.server import create_app

LITERAL = '  `MYFUNCTION("x")` https://example.test/a?b=1\n"quoted" 🙂  '
HEADERS = {'host': 'localhost', 'origin': 'http://localhost'}


class Provider:
    model = 'fixture'
    delivery_protocol_version = 1

    def __init__(self):
        self.messages = []
        self.release = threading.Event()
        self.started = threading.Event()
        self.delay = False
        self.invalid = False

    async def stream(self, messages):
        self.messages.append(messages)
        index = len(self.messages) - 1
        if self.delay and index == 0:
            self.started.set()
            while not self.release.is_set():
                await asyncio.sleep(.005)
        if self.invalid:
            yield '{"text":"PRIVATE","delivery":{"emotion":"unknown","intensity":"high"}}'
        else:
            yield json.dumps({'text': LITERAL, 'delivery': {
                'emotion': ('joy', 'sadness', 'neutral')[index % 3], 'intensity': 'high'}})


class Speech:
    stt_available = False
    stt_reason = 'fixture'
    tts_label = 'fixture B'
    tts_identity = {'backend': 'supertonic3', 'voice_style_profile': 'conditioning_b'}
    automatic_conditioning_enabled = True
    automatic_conditioning_emotions = ('neutral', 'joy', 'sadness', 'future_category')
    delivery_intent_supported = True
    delivery_controls_supported = False

    def __init__(self):
        self.received = []
        self.choices = []

    def conditioning_for(self, delivery):
        self.choices.append(delivery)
        activation = {'joy': 'active', 'sadness': 'calm'}.get(delivery.emotion, 'base')
        return {'profile_id': 'fixture_' + activation, 'activation': activation,
                'intended_emotion': delivery.emotion, 'intensity': delivery.intensity, 'supported': True}

    async def synthesize(self, text, *, delivery=None):
        self.received.append((text, delivery))
        return pcm16_wav(b'\0\0' * 100, 16000)


class ChunkedSpeech(Speech):
    async def synthesize_chunks(self, text, *, delivery=None):
        self.received.append((text, delivery))
        midpoint = len(text) // 2
        yield text[:midpoint], pcm16_wav(b'\0\0' * 100, 16000), False
        yield text[midpoint:], pcm16_wav(b'\0\0' * 100, 16000), True


class AutomaticConditioningServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def app(self, provider, speech, **kwargs):
        return create_app(provider=provider, speech=speech, data_dir=self.directory.name,
                          public_origin='', local_test_auth=kwargs.pop('local_test_auth', True), **kwargs)


    def until(self, ws, kind):
        events = []
        for _ in range(40):
            event = ws.receive_json()
            events.append(event)
            self.assertNotEqual(event['type'], 'error', event)
            if event['type'] == kind:
                return events
        self.fail('missing ' + kind)

    def test_capabilities_are_experimental_activation_not_emotion_or_identity_claims(self):
        speech = Speech()
        for enabled in (True, False, '1'):
            speech.automatic_conditioning_enabled = enabled
            with TestClient(self.app(Provider(), speech)) as client:
                config = client.get('/api/config', headers=HEADERS).json()
                cap = config['delivery_capabilities']
                self.assertIs(cap['automatic_conditioning_enabled'], enabled is True)
                self.assertEqual(cap['automatic_conditioning_scope'], 'activation_only_experimental')
                self.assertEqual(cap['automatic_conditioning_emotion_ids'], list(speech.automatic_conditioning_emotions))
                self.assertFalse(cap['native_emotion_conditioning'])
                self.assertIsNone(cap['speaker_identity_unchanged'])
                self.assertFalse(cap['experimental_native_prosody_enabled'])
                self.assertEqual('otomatis' in config['tts_label'], enabled is True)
                if enabled is True:
                    self.assertIn('eksperimental', config['tts_label'])
                    self.assertEqual(cap['conditioning_scope'], 'activation_only_experimental')

    def test_cancelled_or_superseded_reply_never_selects_or_emits_stale_status(self):
        for command in ('interrupt', 'stop', 'text'):
            with self.subTest(command=command):
                provider, speech = Provider(), Speech()
                provider.delay = True
                try:
                    with TestClient(self.app(provider, speech)) as client:
                        with client.websocket_connect('/ws', headers=HEADERS) as ws:
                            ws.receive_json(); ws.send_json({'type': 'start'}); ws.receive_json()
                            ws.send_json({'type': 'text', 'text': 'old'})
                            old = self.until(ws, 'response_start')[-1]['token']
                            self.assertTrue(provider.started.wait(2))
                            ws.send_json({'type': command, **({'text': 'new'} if command == 'text' else {})})
                            if command == 'text':
                                events = self.until(ws, 'response_done')
                                choices = [e for e in events if e['type'] == 'delivery_status']
                                self.assertEqual(len(choices), 1)
                                self.assertNotEqual(choices[0]['token'], old)
                            else:
                                events = self.until(ws, 'state')
                                self.assertFalse(any(e['type'] == 'delivery_status' for e in events))
                                self.assertEqual(speech.choices, [])
                            provider.release.set()
                            # A command/state barrier after the stale producer completes.
                            client.portal.call(asyncio.sleep, .025)
                            ws.send_json({'type': 'provider', 'provider': 'default'})
                            events = self.until(ws, 'state')
                            self.assertFalse(any(e['type'] == 'delivery_status' for e in events))
                            self.assertEqual(len(speech.choices), int(command == 'text'))
                finally:
                    provider.release.set()

    def test_auth_revocation_drops_pending_reply_and_unauthorized_config_and_socket(self):
        class Auth:
            allowed = True
            def valid_session(self, _cookie):
                return self.allowed
        auth = Auth()
        provider, speech = Provider(), Speech()
        provider.delay = True
        try:
            with TestClient(self.app(provider, speech, auth=auth, local_test_auth=False)) as client:
                with client.websocket_connect('/ws', headers=HEADERS) as ws:
                    ws.receive_json(); ws.send_json({'type': 'start'}); ws.receive_json()
                    ws.send_json({'type': 'text', 'text': 'pending'})
                    self.until(ws, 'response_start')
                    self.assertTrue(provider.started.wait(2))
                    auth.allowed = False
                    provider.release.set()
                    with self.assertRaises(WebSocketDisconnect) as error:
                        while True:
                            event = ws.receive_json()
                            self.assertNotIn(event['type'], ('delivery_status', 'text_delta', 'audio'))
                    self.assertEqual(error.exception.code, 1008)
                self.assertEqual(client.get('/api/config', headers=HEADERS).status_code, 401)
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect('/ws', headers=HEADERS):
                        pass
            self.assertEqual(speech.choices, [])
            self.assertEqual(speech.received, [])
        finally:
            provider.release.set()

    def test_disabled_automatic_has_no_extra_event_and_invalid_metadata_has_no_choice(self):
        for enabled, invalid in ((False, False), ('1', False), (True, True)):
            with self.subTest(enabled=enabled, invalid=invalid):
                provider, speech = Provider(), Speech()
                provider.invalid = invalid
                speech.automatic_conditioning_enabled = enabled
                with TestClient(self.app(provider, speech)) as client:
                    with client.websocket_connect('/ws', headers=HEADERS) as ws:
                        ws.receive_json(); ws.send_json({'type': 'start'}); ws.receive_json()
                        ws.send_json({'type': 'text', 'text': 'question'})
                        events = []
                        for _ in range(30):
                            event = ws.receive_json(); events.append(event)
                            if event['type'] in ('response_done', 'error'):
                                break
                        self.assertFalse(any(e['type'] == 'delivery_status' for e in events))
                        self.assertEqual(speech.choices, [])
                        if invalid:
                            self.assertEqual(event['code'], 'llm_failed')
                            self.assertEqual(speech.received, [])
                            self.assertFalse(any(e['type'] in ('text_delta', 'audio') for e in events))
                        else:
                            self.assertEqual(speech.received[0][1].emotion, 'joy')

    def test_status_does_not_authorize_stale_ack_or_survive_chunk_interruption(self):
        for speech_type in (Speech, ChunkedSpeech):
            with self.subTest(speech=speech_type.__name__):
                speech = speech_type()
                with TestClient(self.app(Provider(), speech)) as client:
                    with client.websocket_connect('/ws', headers=HEADERS) as ws:
                        ws.receive_json(); ws.send_json({'type': 'start'}); ws.receive_json()
                        ws.send_json({'type': 'text', 'text': 'question'})
                        events = self.until(ws, 'audio')
                        audio = events[-1]
                        self.assertEqual(len([e for e in events if e['type'] == 'delivery_status']), 1)
                        if speech_type is Speech:
                            self.until(ws, 'response_done')
                            self.until(ws, 'state')
                        stale = {**audio['token'], 'epoch': audio['token']['epoch'] - 1}
                        ws.send_json({'type': 'ack', 'token': stale, 'chunk_id': audio['chunk_id']})
                        state = self.until(ws, 'state')[-1]['state']
                        self.assertEqual(state['history'][-1]['heard_text'], '')
                        if speech_type is Speech:
                            ws.send_json({'type': 'ack', 'token': audio['token'], 'chunk_id': audio['chunk_id']})
                            state = self.until(ws, 'state')[-1]['state']
                            self.assertEqual(state['history'][-1]['heard_text'], LITERAL)
                        else:
                            ws.send_json({'type': 'interrupt'})
                            self.until(ws, 'interrupted')
                            self.until(ws, 'state')
                            client.portal.call(asyncio.sleep, .025)
                            ws.send_json({'type': 'provider', 'provider': 'default'})
                            after = self.until(ws, 'state')
                            self.assertFalse(any(e['type'] in ('delivery_status', 'audio') for e in after))
                            self.assertEqual(after[-1]['state']['history'][-1]['heard_text'], '')

    def test_three_turns_delivery_is_separate_exact_and_acknowledged(self):
        for speech_type in (Speech, ChunkedSpeech):
            with self.subTest(speech=speech_type.__name__):
                provider, speech = Provider(), speech_type()
                with TestClient(self.app(provider, speech)) as client:
                    with client.websocket_connect('/ws', headers=HEADERS) as ws:
                        ws.receive_json(); ws.send_json({'type': 'start'}); ws.receive_json()
                        statuses = []
                        for turn in range(3):
                            ws.send_json({'type': 'text', 'text': 'question ' + str(turn)})
                            events = []
                            for _ in range(40):
                                event = ws.receive_json(); events.append(event)
                                self.assertNotEqual(event['type'], 'error', event)
                                if event['type'] == 'audio':
                                    ws.send_json({'type': 'ack', 'token': event['token'], 'chunk_id': event['chunk_id']})
                                if event['type'] == 'response_done':
                                    break
                            self.assertEqual(event['type'], 'response_done')
                            choice = [e for e in events if e['type'] == 'delivery_status']
                            self.assertEqual(len(choice), 1)
                            statuses.append(choice[0])
                            self.assertEqual(choice[0]['token'], event['token'])
                            self.assertEqual([e['text'] for e in events if e['type'] == 'text_delta'], [LITERAL])
                            for _ in range(20):
                                state = ws.receive_json()
                                if state['type'] == 'state' and state['state']['history'][-1]['heard_text'] == LITERAL:
                                    break
                            self.assertEqual(state['state']['history'][-1]['text'], LITERAL)
                            self.assertEqual(state['state']['history'][-1]['heard_text'], LITERAL)
                            self.assertNotIn('profile_id', json.dumps(state))
                        self.assertEqual([s['choice']['activation'] for s in statuses], ['active', 'calm', 'base'])
                self.assertEqual([text for text, _ in speech.received], [LITERAL] * 3)
                self.assertEqual([d.emotion for _, d in speech.received], ['joy', 'sadness', 'neutral'])
                self.assertEqual(speech.choices, [d for _, d in speech.received])
                for messages in provider.messages[1:]:
                    self.assertTrue(any(m['role'] == 'assistant' and m['content'] == LITERAL for m in messages))
                    self.assertNotIn('profile_id', json.dumps(messages))


if __name__ == '__main__':
    unittest.main()
