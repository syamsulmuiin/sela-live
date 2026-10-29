import asyncio, unittest
from unittest.mock import patch, AsyncMock
from voice_app.speech import Speech
from voice_app.audio import pcm16_wav

class PreviewAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_preparation_cannot_load_whisper_while_piper_slot_is_held(self):
        from voice_app.speech import LocalAudioBusy
        with patch('voice_app.speech._runtime_ready',return_value=True):
            speech=Speech(environ={'VOICE_LOCAL_PREVIEW':'1'})
        worker=type('Worker',(),{'ready':AsyncMock()})()
        speech._stream_asr=worker
        await speech._local_slot.acquire()
        try:
            with self.assertRaises(LocalAudioBusy): await speech.prepare_preview()
            worker.ready.assert_not_awaited()
        finally: speech._local_slot.release()

    async def test_preview_explicit_default_off_and_local_only(self):
        with patch('voice_app.speech._runtime_ready',return_value=True):
            self.assertFalse(getattr(Speech(environ={}), 'local_preview', False))
            for env in ({'VOICE_LOCAL_PREVIEW':'bad'}, {'VOICE_LOCAL_PREVIEW':'1','VOICE_STT_BACKEND':'api'}):
                with self.assertRaises(ValueError): Speech(environ=env)
            speech=Speech(environ={'VOICE_LOCAL_PREVIEW':'1'})
            self.assertTrue(speech.local_preview)

    async def test_real_adapter_routes_preview_and_final_to_same_worker_not_remote(self):
        with patch('voice_app.speech._runtime_ready',return_value=True):
            speech=Speech(environ={'VOICE_LOCAL_PREVIEW':'1'})
        worker=type('Worker',(),{'ready':AsyncMock(),'preview':AsyncMock(return_value='draft'),
            'transcribe':AsyncMock(return_value='final'),'close':AsyncMock()})()
        speech._stream_asr=worker
        wav=pcm16_wav(b'\x01\x01'*16000,16000)
        await speech.prepare_preview()
        self.assertEqual(await speech.preview(wav),'draft')
        self.assertEqual(await speech.transcribe(wav),'final')
        worker.preview.assert_awaited_once_with(wav)
        worker.transcribe.assert_awaited_once_with(wav)
        await speech.close_preview()
        worker.close.assert_awaited_once()
