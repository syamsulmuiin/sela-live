import asyncio
import unittest
from voice_app.provider_streaming import stream_plain_speech


class FakeProvider:
    async def stream(self, messages):
        for delta in ("Halo apa", " kabar? ", "Baik sekali."):
            yield delta


class StructuredProvider(FakeProvider):
    delivery_protocol_version = 1


class ProviderStreamingTests(unittest.TestCase):
    def test_plain_provider(self):
        async def run():
            return [s async for s in stream_plain_speech(FakeProvider(), [], min_segment=8)]
        parts = asyncio.run(run())
        self.assertEqual("".join(parts), "Halo apa kabar? Baik sekali.")

    def test_structured_provider_rejected(self):
        async def run():
            return [s async for s in stream_plain_speech(StructuredProvider(), [])]
        with self.assertRaises(ValueError):
            asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
