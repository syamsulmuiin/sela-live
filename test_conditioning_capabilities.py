"""Truthful config for a listener-selected modified conditioning profile."""
import tempfile
import unittest
from fastapi.testclient import TestClient
from voice_app.server import create_app
import test_server as fixtures


class ConditioningCapabilityTests(unittest.TestCase):
    def test_modified_B_is_not_a_claim_of_unchanged_identity_or_all_emotions(self):
        speech = fixtures.Speech()
        speech.tts_identity = {'backend': 'supertonic3', 'voice': 'M1', 'voice_style_profile': 'conditioning_b'}
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(provider=fixtures.Provider(), speech=speech,
                    data_dir=directory, local_test_auth=True, public_origin=''), base_url='http://localhost') as client:
                response = client.get('/api/config')
                self.assertEqual(response.status_code, 200)
                cap = response.json()['delivery_capabilities']
        self.assertIsNone(cap['speaker_identity_unchanged'])
        self.assertEqual(cap['conditioning_scope'], 'listener_selected_fixed_style')
        self.assertFalse(cap['native_emotion_conditioning'])

    def test_original_fixed_conditioning_keeps_legacy_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(provider=fixtures.Provider(), speech=fixtures.Speech(),
                    data_dir=directory, local_test_auth=True, public_origin=''), base_url='http://localhost') as client:
                cap = client.get('/api/config').json()['delivery_capabilities']
        self.assertIs(cap['speaker_identity_unchanged'], True)
        self.assertEqual(cap['conditioning_scope'], 'original_fixed_style')
