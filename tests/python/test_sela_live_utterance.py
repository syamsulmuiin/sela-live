"""Pure planner tests: fake duration model, no native/model inference."""
from dataclasses import FrozenInstanceError
import unittest


class UtteranceTests(unittest.IsolatedAsyncioTestCase):
    def api(self):
        try:
            from sela_live_tts import utterance
        except ImportError:
            self.fail('Unified utterance planner is missing')
        return utterance

    async def test_every_planned_context_is_one_pinned_sdk_chunk(self):
        import importlib.util
        from pathlib import Path
        spec=importlib.util.spec_from_file_location('_planner_pinned_utils',
            Path(__file__).resolve().parents[2]/'vendor/supertonic/utils.py')
        if spec is None or spec.loader is None:
            self.fail('Pinned SDK chunker fixture unavailable')
        sdk=importlib.util.module_from_spec(spec);spec.loader.exec_module(sdk)
        for boundary in ('\n\n','\n \t\n','\n\u00a0\n'):
            text='Halo.'+boundary+'Dunia!'
            with self.subTest(boundary=repr(boundary)):
                plan=await self.planner().plan(self.api().SpeechRequest(text))
                self.coverage(plan)
                self.assertTrue(all(len(sdk.chunk_text(g.native_text,800))==1 for g in plan.groups))
                self.assertEqual([g.native_text for g in plan.groups],['Halo.','Dunia!'])

    async def test_one_request_exact_source_and_frozen_metadata(self):
        u = self.api()
        calls = []
        async def predict(text, profile_id):
            calls.append((text, profile_id))
            return u.Prediction(2.0, 18)
        text = '  Selamat sore.\n'
        request = u.SpeechRequest(text)
        plan = await u.NaturalSpeechPlanner(normalize=lambda s: s, predict=predict).plan(request)
        self.assertEqual(plan.source_text, text)
        self.assertEqual(plan.policy_id, 'natural_v1')
        self.assertFalse(plan.independent_emotion_rendering_proven)
        self.assertEqual(len(plan.groups), 1)
        group = plan.groups[0]
        self.assertEqual((group.start, group.end, group.source_text), (0, len(text), text))
        self.assertEqual(group.native_text, text.strip())
        self.assertEqual(calls, [(text.strip(), 'conditioning_b')])
        self.assertEqual(plan.prediction_calls, 1)
        for obj, attr, value in ((plan, 'policy_id', 'bad'), (request, 'text', 'bad'),
                                 (group, 'start', 5), (plan.delivery, 'profile_id', 'bad')):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, attr, value)

    def planner(self, predict=None, normalize=None, **kwargs):
        u = self.api()
        async def async_predict(text, profile):
            return predict(text, profile) if predict else u.Prediction(5., len(text))
        return u.NaturalSpeechPlanner(normalize=normalize or (lambda s: s),
            predict=async_predict, **kwargs)

    def coverage(self, plan):
        cursor = 0
        for group in plan.groups:
            self.assertEqual(group.start, cursor)
            self.assertEqual(group.source_text, plan.source_text[group.start:group.end])
            self.assertGreater(group.end, group.start)
            self.assertLessEqual(len(group.source_text), 800)
            self.assertLessEqual(len(group.source_text.encode('utf-8')), 3200)
            self.assertLessEqual(len(group.native_text), 800)
            self.assertLessEqual(len(group.native_text.encode('utf-8')), 3200)
            cursor = group.end
        self.assertEqual(cursor, len(plan.source_text))
        self.assertEqual(''.join(g.source_text for g in plan.groups), plan.source_text)

    async def test_arga_nine_legacy_parts_become_three_complete_sentences(self):
        from pathlib import Path
        import hashlib
        path = Path('/root/sela-live-releases/live-release-4kkkhooe/prosody-arga/source.txt')
        if not path.exists():
            self.skipTest('Private Arga fixture not installed')
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
            'c55979e940a0489016bb158eb0443bbb4a435d18697559dd8501b3022deda94a')
        text = raw.decode()
        from voice_app.tts_chunks import source_parts
        self.assertEqual(len(source_parts(text, lambda s: s)), 9)
        u = self.api()
        plan = await self.planner(predict=lambda t, p: u.Prediction(27.5, 420)).plan(u.SpeechRequest(text))
        self.coverage(plan)
        self.assertEqual(len(plan.groups), 3)
        self.assertTrue(all(g.source_text.rstrip().endswith('.') for g in plan.groups))
        self.assertTrue(all(g.reason == 'complete_sentence' for g in plan.groups))

    async def test_whole_request_context_including_paragraphs_is_tried_first(self):
        u = self.api()
        # Historical test identity retained; SDK paragraph boundaries now take
        # precedence over the incompatible whole-request expectation.
        for text in ('First thought.\n\nSecond thought.', '  Satu!\n\nDua?\n\nTiga.  '):
            calls = []
            def predict(t, p):
                calls.append(t)
                return u.Prediction(12., 80)
            plan = await self.planner(predict=predict).plan(u.SpeechRequest(text))
            self.coverage(plan)
            paragraphs=text.split('\n\n')
            expected=[p+'\n\n' for p in paragraphs[:-1]]+[paragraphs[-1]]
            self.assertEqual([g.source_text for g in plan.groups], expected)
            self.assertEqual(calls, [p.strip() for p in expected])

    async def test_complete_paragraph_context_preferred_when_it_fits(self):
        u = self.api()
        text = 'Satu kalimat. Kedua kalimat!\n\nParagraf berikutnya?'
        plan = await self.planner(predict=lambda t, p: u.Prediction(29. if t == text else 5., len(t))).plan(u.SpeechRequest(text))
        self.coverage(plan)
        self.assertEqual([g.native_text for g in plan.groups],
                         ['Satu kalimat. Kedua kalimat!', 'Paragraf berikutnya?'])

    async def test_safe_sentence_boundaries_do_not_repunctuate(self):
        u = self.api()
        cases = [
            ('Dr. Arga menemui Prof. Budi dll. dan dsb. hari ini. Berikutnya.',
             ['Dr. Arga menemui Prof. Budi dll. dan dsb. hari ini.', 'Berikutnya.']),
            ('Harga Rp 1.234,50 dan $3.14. Selesai.', ['Harga Rp 1.234,50 dan $3.14.', 'Selesai.']),
            ('Ia berkata, “Tunggu...” Lalu pulang! (Selesai.)',
             ['Ia berkata, “Tunggu...” Lalu pulang!', '(Selesai.)']),
            ('Lihat https://a.example/x.y dan foo.bar(x), v1.2.3. Selesai.',
             ['Lihat https://a.example/x.y dan foo.bar(x), v1.2.3.', 'Selesai.']),
            ('`foo. bar()` tetap utuh. Selesai.', ['`foo. bar()` tetap utuh.', 'Selesai.']),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                def predict(t, p):
                    return u.Prediction(29. if t == text else 4., 30)
                plan = await self.planner(predict=predict).plan(u.SpeechRequest(text))
                self.coverage(plan)
                self.assertEqual([g.native_text for g in plan.groups], expected)

    async def test_duration_and_model_tokens_trigger_source_preserving_fallback(self):
        u = self.api()
        text = 'Pagi yang panjang, kemudian sore yang tenang; lalu malam pun datang.'
        for duration, tokens, expected in [(29., 40, 'duration_limit'), (3., 513, 'model_token_limit')]:
            with self.subTest(expected=expected):
                def predict(t, p):
                    return u.Prediction(duration, tokens) if t == text else u.Prediction(4., 20)
                plan = await self.planner(predict=predict).plan(u.SpeechRequest(text))
                self.coverage(plan)
                self.assertGreater(len(plan.groups), 1)
                self.assertTrue(all(expected in g.fallback_reasons for g in plan.groups))
                self.assertTrue(all(g.resource_envelope_verified for g in plan.groups))

    async def test_quoted_attribution_stays_with_its_sentence(self):
        u = self.api()
        for first in ('“Go!” she said.', '"Tunggu!" kata Arga.', '“Siap?” tanya Nia.'):
            text = first + ' Berikutnya.'
            def predict(t, p):
                return u.Prediction(29. if t == text else 4., 40)
            plan = await self.planner(predict=predict).plan(u.SpeechRequest(text))
            self.coverage(plan)
            self.assertEqual([g.native_text for g in plan.groups], [first, 'Berikutnya.'])

    async def test_clause_boundaries_skip_code_and_quoted_commas(self):
        u = self.api()
        for token in ('`call(a, b; c)`', '“alpha, beta; gamma”', '"one, two; three"'):
            text = token + ' trailing words, next clause.'
            seen = []
            def predict(t, p):
                seen.append(t)
                return u.Prediction(29. if len(t) > len(token) else 4., 40)
            plan = await self.planner(predict=predict).plan(u.SpeechRequest(text))
            self.coverage(plan)
            self.assertEqual(plan.groups[0].native_text, token)
            self.assertTrue(any(token in g.source_text for g in plan.groups))

    async def test_adjacent_clauses_pack_largest_predicted_safe_prefix(self):
        u = self.api()
        for clauses, limit in ((['alpha', 'beta', 'gamma', 'delta', 'epsilon'], 3),
                               (['pagi tenang', 'siang ramai', 'sore sepi', 'malam hening'], 2)):
            text = ', '.join(clauses) + '.'
            seen = []
            def predict(t, p):
                seen.append(t)
                return u.Prediction(4. if t.rstrip(', ').count(',') < limit else 29., 40)
            plan = await self.planner(predict=predict).plan(u.SpeechRequest(text))
            self.coverage(plan)
            first = ', '.join(clauses[:limit]) + ', '
            self.assertEqual(plan.groups[0].source_text, first)
            self.assertEqual(len(plan.groups), 2)
            self.assertTrue(all(g.resource_envelope_verified for g in plan.groups))
            self.assertEqual(len(seen), len(set(seen)))
            self.assertTrue(all(g.native_text in seen for g in plan.groups))

    async def test_nonmonotonic_model_predictions_choose_longest_safe_clauses(self):
        u = self.api()
        text = 'a, bb, ccc, dddd, eeeee.'
        durations = {text: 29., 'a, bb, ccc, dddd,': 27., 'a, bb, ccc,': 35.,
                     'a, bb,': 2., 'a,': 1., 'eeeee.': 3.}
        calls = []
        def predict(t, p):
            calls.append(t)
            return u.Prediction(durations[t], 400)
        plan = await self.planner(predict=predict).plan(u.SpeechRequest(text))
        self.coverage(plan)
        self.assertEqual([g.native_text for g in plan.groups], ['a, bb, ccc, dddd,', 'eeeee.'])
        self.assertEqual(calls, [text, 'a, bb, ccc, dddd,', 'eeeee.'])

    async def test_partial_model_success_does_not_hide_later_error_or_cancellation(self):
        import asyncio
        u = self.api()
        text = 'First sentence. Second sentence.'
        for error in (RuntimeError('late model failure'), asyncio.CancelledError()):
            calls = []
            async def predict(t, p):
                calls.append(t)
                if len(calls) == 3:
                    raise error
                return u.Prediction(29. if t == text else 4., 40)
            with self.subTest(error=type(error)), self.assertRaises(type(error)):
                await u.NaturalSpeechPlanner(normalize=lambda t: t, predict=predict).plan(u.SpeechRequest(text))
            self.assertEqual(calls, [text, 'First sentence.', 'Second sentence.'])

    async def test_hard_raw_normalized_and_unicode_limits(self):
        u = self.api()
        cases = [('kata ' * 700, lambda s: s), ('z' * 2000, lambda s: s),
                 ('🙂' * 1100, lambda s: s), ('Rp 1.234,50 ' * 90, lambda s: s.replace('Rp', 'rupiah')),
                 ('x ' * 300, lambda s: s.replace('x', 'contohpanjang')),
                 (' ' * 1200 + 'Akhir.' + '\t' * 1000, lambda s: s)]
        for text, normalize in cases:
            with self.subTest(length=len(text)):
                plan = await self.planner(normalize=normalize).plan(u.SpeechRequest(text))
                self.coverage(plan)
                self.assertLessEqual(plan.prediction_calls, 64)
                self.assertTrue(all(g.native_text == normalize(g.source_text.strip()) for g in plan.groups))
                if text.startswith('Rp'):
                    self.assertTrue(all(not g.source_text.rstrip().endswith('Rp') for g in plan.groups))

    async def test_prediction_budget_covers_remaining_text_without_claiming_prediction(self):
        u = self.api()
        calls = []
        def expensive(t, p):
            calls.append(t)
            return u.Prediction(29., 600)
        text = ''.join(f'Kata panjang {i}, berikutnya lagi; selesai sekarang. ' for i in range(60))
        plan = await self.planner(predict=expensive).plan(u.SpeechRequest(text))
        self.coverage(plan)
        self.assertEqual(len(calls), 64)
        self.assertEqual(plan.prediction_calls, 64)
        remaining = [g for g in plan.groups if 'prediction_budget_exhausted' in g.fallback_reasons]
        self.assertTrue(remaining)
        self.assertTrue(all(g.prediction is None and not g.resource_envelope_verified for g in remaining))
        self.assertTrue(all(len(g.native_text) <= 180 for g in remaining))

    async def test_token_refusal_splits_without_invented_duration(self):
        u=self.api();calls=[]
        async def predict(text,profile):
            calls.append(text)
            return u.Prediction(None,608) if len(text)>512 else u.Prediction(4.,len(text)+9)
        text='kata '*120
        plan=await u.NaturalSpeechPlanner(normalize=lambda s:s,predict=predict).plan(u.SpeechRequest(text))
        self.coverage(plan)
        self.assertGreater(len(plan.groups),1)
        self.assertTrue(all(g.resource_envelope_verified for g in plan.groups))
        self.assertTrue(all('model_token_limit' in g.fallback_reasons for g in plan.groups))
        self.assertEqual(calls[0],text.strip())
        self.assertFalse(u.UtteranceGroup(0,1,'x','x','refused',u.Prediction(None,608)).resource_envelope_verified)
        with self.assertRaises(ValueError):
            await self.planner(predict=lambda t,p:u.Prediction(None,20)).plan(u.SpeechRequest('Halo.'))

    async def test_invalid_predictor_results_fail_closed(self):
        u = self.api()
        invalid = [None, {}, (2., 30), u.Prediction(float('nan'), 2),
                   u.Prediction(float('inf'), 2), u.Prediction(-1., 2), u.Prediction(0., 2),
                   u.Prediction(True, 2), u.Prediction(2., True), u.Prediction(2., 0),
                   u.Prediction(2., 1.5), u.Prediction('2', 3)]
        for result in invalid:
            with self.subTest(result=result), self.assertRaises(ValueError):
                await self.planner(predict=lambda t, p: result).plan(u.SpeechRequest('Teks asli.'))

    async def test_predictor_failure_and_cancellation_propagate_without_fallback(self):
        import asyncio
        u = self.api()
        for error in (RuntimeError('model failure'), asyncio.CancelledError()):
            async def predict(t, p):
                raise error
            with self.subTest(error=type(error)), self.assertRaises(type(error)):
                await u.NaturalSpeechPlanner(normalize=lambda s: s, predict=predict).plan(u.SpeechRequest('Teks asli.'))

    async def test_invalid_request_and_normalizer_fail_before_prediction(self):
        u = self.api()
        for text in ('', '  ', 'a' * 4001, 'bad\x00', '\ud800', 123):
            with self.subTest(text=repr(text)), self.assertRaises(ValueError):
                await self.planner().plan(u.SpeechRequest(text))
        for normalized in (None, 5, '', '\ud800', 'bad\x00'):
            with self.subTest(normalized=repr(normalized)), self.assertRaises(ValueError):
                await self.planner(normalize=lambda s: normalized).plan(u.SpeechRequest('Asli.'))

    async def test_delivery_is_resolved_once_and_never_enters_lexical_text(self):
        u = self.api()
        from voice_app.delivery import load_registry
        selected = load_registry().resolve('anger', 'high')
        resolves, calls = [], []
        def resolve_delivery(delivery):
            resolves.append(delivery)
            return u.ResolvedDelivery('conditioning_b_active_high', 'anger', 'high')
        def predict(text, profile):
            calls.append((text, profile))
            return u.Prediction(3., 100)
        text = 'Ini kata asli. ' * 100
        plan = await self.planner(predict=predict, resolve_delivery=resolve_delivery).plan(u.SpeechRequest(text, selected))
        self.coverage(plan)
        self.assertEqual(resolves, [selected])
        self.assertTrue(all(p == 'conditioning_b_active_high' for t, p in calls))
        self.assertTrue(all('anger' not in t and '<' not in t for t, p in calls))
        self.assertEqual(plan.delivery, u.ResolvedDelivery('conditioning_b_active_high', 'anger', 'high'))
        self.assertFalse(plan.independent_emotion_rendering_proven)

    async def test_default_registry_validates_forged_or_stale_delivery(self):
        from dataclasses import replace
        from voice_app.delivery import load_registry
        u = self.api()
        selected = load_registry().resolve('anger', 'high')
        good = await self.planner().plan(u.SpeechRequest('Kata asli.', selected))
        self.assertEqual(good.delivery.profile_id, 'conditioning_b_active_high')
        for delivery in ({'emotion': 'anger'}, replace(selected, speed=float('nan')),
                         replace(selected, registry_version=999), replace(selected, emotion='made_up')):
            with self.subTest(delivery=delivery), self.assertRaises(ValueError):
                await self.planner().plan(u.SpeechRequest('Kata asli.', delivery))

    async def test_disabled_resolver_has_no_cross_response_state_or_text_leakage(self):
        from voice_app.delivery import load_registry
        u = self.api()
        registry = load_registry()
        def disabled(delivery):
            if delivery is not None and delivery != registry.resolve(delivery.emotion, delivery.intensity):
                raise ValueError('Invalid delivery')
            return u.ResolvedDelivery()
        planner = self.planner(resolve_delivery=disabled)
        first = await planner.plan(u.SpeechRequest('Tetap asli.', registry.resolve('anger', 'high')))
        second = await planner.plan(u.SpeechRequest('Berikutnya.'))
        self.assertEqual(first.delivery, u.ResolvedDelivery())
        self.assertEqual(second.delivery, u.ResolvedDelivery())
        self.assertEqual(first.groups[0].native_text, 'Tetap asli.')
        self.assertEqual(second.prediction_calls, 1)

    async def test_resolver_output_cannot_supply_unapproved_profile_or_mutable_metadata(self):
        u = self.api()
        for result in ({'profile_id': 'conditioning_b'},
                       u.ResolvedDelivery('/tmp/voice.json'), u.ResolvedDelivery('original'),
                       u.ResolvedDelivery(intended_emotion=[]), u.ResolvedDelivery(intensity='extreme')):
            with self.subTest(result=result), self.assertRaises(ValueError):
                await self.planner(resolve_delivery=lambda d: result).plan(u.SpeechRequest('Teks.'))


if __name__ == '__main__':
    unittest.main()