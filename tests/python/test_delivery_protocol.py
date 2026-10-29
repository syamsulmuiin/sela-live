"""Hidden delivery protocol: scripted fixtures, no live model/audio calls."""
import json
import unittest


class DeliveryParsingTests(unittest.TestCase):
    def test_neutral_preserves_installed_m1_speed_at_every_intensity(self):
        from voice_app.delivery import load_registry
        registry = load_registry()
        for intensity in ('low', 'medium', 'high'):
            self.assertEqual(registry.resolve('neutral', intensity).native_controls(),
                             {'speed': 1.05, 'silence_duration': 0.3})

    def test_literal_reply_is_separate_from_immutable_numeric_delivery(self):
        from voice_app import delivery
        literal = '  `MYFUNCTION("x")` https://example.test/a?b=1\n"quoted" 🙂  '
        reply = delivery.parse_reply(json.dumps({'text': literal, 'delivery': {
            'emotion': 'sadness', 'intensity': 'high'}}))
        self.assertEqual(reply.text, literal)
        self.assertEqual(reply.delivery.emotion, 'sadness')
        self.assertEqual(set(reply.delivery.native_controls()), {'speed', 'silence_duration'})
        self.assertEqual(reply.delivery.support_level, 'prosody_only_experimental')
        from dataclasses import FrozenInstanceError
        with self.assertRaises(FrozenInstanceError):
            reply.delivery.speed = 2


class DeliveryAdversarialTests(unittest.TestCase):
    def test_invalid_envelopes_reject_without_lexical_echo(self):
        from voice_app.delivery import parse_reply
        valid = {'text':'PRIVATE_SENTINEL','delivery':{'emotion':'joy','intensity':'low'}}
        bad = ['PRIVATE_SENTINEL', '```json\n'+json.dumps(valid)+'\n```',
               json.dumps(valid)+json.dumps(valid),
               '{"text":"PRIVATE_SENTINEL","text":"duplicate","delivery":{"emotion":"joy","intensity":"low"}}',
               '{"text":"PRIVATE_SENTINEL","delivery":{"emotion":"joy","emotion":"sadness","intensity":"low"}}',
               json.dumps({**valid,'extra':1}),
               json.dumps({**valid,'delivery':{'emotion':'unknown','intensity':'low'}}),
               json.dumps({**valid,'delivery':{'emotion':'joy','intensity':True}}),
               json.dumps({**valid,'delivery':{'emotion':['joy'],'intensity':'low'}}),
               json.dumps({**valid,'delivery':{'emotion':'joy','intensity':'low','speed':1}}),
               json.dumps({**valid,'text':'PRIVATE_SENTINEL\x00'}),
               json.dumps({**valid,'text':'x'*4001}), '['*3000+'0'+']'*3000, 'x'*32769]
        for index, raw in enumerate(bad):
            with self.subTest(case=index), self.assertRaises(ValueError) as raised:
                parse_reply(raw)
            self.assertNotIn('PRIVATE_SENTINEL', str(raised.exception))

    def test_registry_extension_is_data_only_bounded_and_immutable(self):
        from voice_app.delivery import load_registry, parse_reply
        from pathlib import Path
        import tempfile
        original = json.loads((Path(__file__).resolve().parents[2]/'voice_app'/'delivery_registry.json').read_text())
        self.assertEqual(set(original['entries']), {'neutral','joy','trust','fear','surprise','sadness','disgust','anger','anticipation'})
        original['version'] = 2
        original['entries']['tenderness'] = {'speed':0.97,'silence_duration':0.34,'support_level':'prosody_only_experimental'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'registry.json'
            path.write_text(json.dumps(original))
            registry = load_registry(path)
            reply = parse_reply(json.dumps({'text':'literal','delivery':{'emotion':'tenderness','intensity':'high'}}), registry)
            self.assertEqual(reply.delivery.registry_version, 2)
            self.assertEqual(reply.delivery.emotion, 'tenderness')
            self.assertIn('tenderness', registry.instruction())
            with self.assertRaises(TypeError): registry.entries['tenderness']['speed'] = 1
            for identifier in registry.ids:
                for intensity in ('low','medium','high'):
                    controls = registry.resolve(identifier,intensity).native_controls()
                    self.assertTrue(0.9 <= controls['speed'] <= 1.1)
                    self.assertTrue(0.2 <= controls['silence_duration'] <= 0.4)
            for change in ({'speed':True},{'speed':2},{'support_level':'proven'},{'extra':1}):
                mutated = json.loads(json.dumps(original))
                mutated['entries']['tenderness'].update(change)
                path.write_text(json.dumps(mutated))
                with self.assertRaises(ValueError): load_registry(path)
            original['entries']['BAD ID'] = original['entries'].pop('tenderness')
            path.write_text(json.dumps(original))
            with self.assertRaises(ValueError): load_registry(path)


class DeliveryProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_adapters_mark_protocol_and_reserve_metadata_budget(self):
        from voice_app.providers import HermesProvider, ChatProvider
        from unittest.mock import Mock, patch
        from types import SimpleNamespace
        for provider in (HermesProvider(),ChatProvider('https://example.test/v1','fixture')):
            self.assertEqual(getattr(provider,'delivery_protocol_version',None), 1)
        create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='fixture'))]))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch('agent.auxiliary_client.resolve_provider_client',return_value=(client,'fixture')):
            _ = [x async for x in HermesProvider().stream([])]
        self.assertGreaterEqual(create.call_args.kwargs['max_tokens'],512)
        self.assertLessEqual(create.call_args.kwargs['max_tokens'],1024)
        self.assertNotIn('response_format',create.call_args.kwargs)


class DeliveryServerTests(unittest.TestCase):
    def test_legacy_plain_reply_gets_neutral_and_chunked_snapshot_is_hidden(self):
        from fastapi.testclient import TestClient
        from voice_app.server import create_app
        from voice_app.audio import pcm16_wav
        from tests.python import test_server as fixtures
        import tempfile
        class Speech(fixtures.Speech):
            delivery_controls_supported = True
            seen = None
            async def synthesize_chunks(self,text,*,delivery):
                self.seen = delivery
                midpoint = len(text)//2
                yield text[:midpoint],pcm16_wav(b'\0\0'*100,16000),False
                yield text[midpoint:],pcm16_wav(b'\0\0'*100,16000),True
        speech = Speech()
        with tempfile.TemporaryDirectory() as tmp:
            with TestClient(create_app(provider=fixtures.Provider(),speech=speech,data_dir=tmp,local_test_auth=True,public_origin='')) as client:
                with client.websocket_connect('/ws',headers={'host':'localhost','origin':'http://localhost'}) as ws:
                    ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
                    ws.send_json({'type':'text','text':'fixture question'})
                    audios = []
                    for _ in range(30):
                        event = ws.receive_json()
                        self.assertNotEqual(event['type'],'error')
                        if event['type']=='audio':
                            audios.append(event)
                            ws.send_json({'type':'ack','token':event['token'],'chunk_id':event['chunk_id']})
                        if event['type']=='response_done': break
                    self.assertEqual(event['type'],'response_done')
                    # ACK state may have been emitted before response_done; wait
                    # for a state proving both exact source pieces were heard.
                    for _ in range(10):
                        event = ws.receive_json()
                        if event['type']=='state' and event['state']['history'][-1]['heard_text']=='Halo dari pengujian.': break
                    self.assertEqual(event['state']['history'][-1]['heard_text'],'Halo dari pengujian.')
        self.assertEqual(len(audios),2)
        self.assertEqual(speech.seen.emotion,'neutral')
        self.assertEqual(speech.seen.native_controls(),{'speed':1.05,'silence_duration':0.3})

    def test_capabilities_auth_gate_and_default_off_are_explicit(self):
        from fastapi.testclient import TestClient
        from voice_app.server import create_app
        from tests.python import test_server as fixtures
        import tempfile
        class Provider(fixtures.Provider): delivery_protocol_version = 1
        speech = fixtures.Speech()
        speech.delivery_controls_available = True
        speech.delivery_controls_supported = False
        with tempfile.TemporaryDirectory() as tmp:
            for authenticated in (False, True):
                with TestClient(create_app(provider=Provider(),speech=speech,data_dir=tmp,
                                          local_test_auth=authenticated,public_origin='')) as client:
                    response = client.get('/api/config',headers={'host':'localhost'})
                    if not authenticated:
                        self.assertEqual(response.status_code,401)
                        continue
                    cap = response.json()['delivery_capabilities']
                    self.assertTrue(cap['metadata_protocol_active'])
                    self.assertTrue(cap['native_prosody_available'])
                    self.assertFalse(cap['experimental_native_prosody_enabled'])
                    self.assertFalse(cap['native_emotion_conditioning'])
                    self.assertTrue(cap['speaker_identity_unchanged'])
                    self.assertIn('sadness', cap['emotion_ids'])

    def test_invalid_remote_metadata_never_reaches_text_history_audio_or_logs(self):
        from fastapi.testclient import TestClient
        from voice_app.server import create_app
        from tests.python import test_server as fixtures
        import tempfile
        class Provider(fixtures.Provider):
            delivery_protocol_version = 1
            raw = ''
            async def stream(self,messages):
                yield self.raw[:20]
                yield self.raw[20:]
        class Speech(fixtures.Speech):
            calls = 0
            async def synthesize(self,text):
                self.calls += 1
                return await super().synthesize(text)
        provider,speech = Provider(),Speech()
        with tempfile.TemporaryDirectory() as tmp:
            with TestClient(create_app(provider=provider,speech=speech,data_dir=tmp,local_test_auth=True,public_origin='')) as client:
                with client.websocket_connect('/ws',headers={'host':'localhost','origin':'http://localhost'}) as ws:
                    ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
                    for raw in ('PRIVATE_SENTINEL',
                                '{"text":"PRIVATE_SENTINEL","delivery":{"emotion":"joy","intensity":"low","intensity":"high"}}',
                                '{"text":"PRIVATE_SENTINEL","delivery":{"emotion":"unknown","intensity":"low"}}'):
                        provider.raw = raw
                        with self.assertLogs('voice_app.server',level='WARNING') as logs:
                            ws.send_json({'type':'text','text':'fixture question'})
                            events = []
                            for _ in range(30):
                                event = ws.receive_json(); events.append(event)
                                if event['type']=='error': break
                        self.assertEqual(events[-1]['code'],'llm_failed')
                        self.assertFalse(any(e['type'] in ('text_delta','audio') for e in events))
                        self.assertNotIn('PRIVATE_SENTINEL',str(events)+str(logs.output))
                        state = ws.receive_json()
                        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(state))
        self.assertEqual(speech.calls,0)

    def test_envelope_is_never_rendered_spoken_or_stored_and_turns_are_isolated(self):
        from fastapi.testclient import TestClient
        import tempfile
        from voice_app.server import create_app
        from tests.python import test_server as fixtures
        literal = '  MYFUNCTION("x") https://example.test/a?b=1\n"quoted"  '
        class Provider:
            model = 'scripted-protocol-fixture'
            delivery_protocol_version = 1
            received = []
            async def stream(self, messages):
                self.received.append(messages)
                yield json.dumps({'text': literal, 'delivery': {
                    'emotion': 'sadness' if len(self.received) == 1 else 'joy',
                    'intensity': 'high'}})
        class Speech(fixtures.Speech):
            delivery_controls_supported = True
            received = []
            async def synthesize(self, text, *, delivery=None):
                self.received.append((text, delivery))
                return await super().synthesize(text)
        provider, speech = Provider(), Speech()
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(provider=provider, speech=speech, data_dir=tmp,
                             local_test_auth=True, public_origin='')
            with TestClient(app, base_url='http://localhost') as client:
                with client.websocket_connect('/ws', headers={'origin':'http://localhost', 'host':'localhost'}) as ws:
                    ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
                    for turn in range(2):
                        ws.send_json({'type':'text', 'text':'fixture question'})
                        events = []
                        for _ in range(30):
                            event = ws.receive_json(); events.append(event)
                            self.assertNotEqual(event['type'], 'error')
                            if event['type'] == 'response_done':
                                break
                        self.assertEqual(events[-1]['type'], 'response_done')
                        self.assertEqual([e['text'] for e in events if e['type']=='text_delta'], [literal])
                        state = ws.receive_json()['state']
                        self.assertEqual(state['history'][-1]['text'], literal)
                        self.assertNotIn('delivery', json.dumps(events))
        self.assertEqual([x[0] for x in speech.received], [literal, literal])
        self.assertEqual([x[1].emotion for x in speech.received], ['sadness', 'joy'])
        self.assertLess(speech.received[0][1].speed, speech.received[1][1].speed)
        system = provider.received[0][0]['content']
        self.assertIn('exactly one JSON', system)
        self.assertNotIn('Sedih sekali mendengarnya', system)
        self.assertIn('pola tutur B', system)


if __name__ == '__main__':
    unittest.main()
