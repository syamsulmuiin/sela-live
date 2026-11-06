import tempfile
import unittest
import wave
from pathlib import Path
from sela_live.audio.installed_asset_audit import inventory, wav_metrics, save_report


class InstalledAssetAuditTests(unittest.TestCase):
    def test_inventory_hashes_without_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model.bin").write_bytes(b"fixture")
            result = inventory(root, names=["model.bin"])
            self.assertEqual(result["files"]["model.bin"]["bytes"], 7)
            self.assertFalse(result["languages_verified"])
            with self.assertRaises(ValueError):
                inventory(root, names=["../secret"])

    def test_wav_signal_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "sample.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\\x00\\x00" * 16000)
            metrics = wav_metrics(path)
            self.assertEqual(metrics["duration_seconds"], 1)
            self.assertEqual(metrics["clipped_samples"], 0)
            self.assertFalse(metrics["acoustic_quality_verified"])
            report = root / "report.json"
            save_report(report, metrics)
            with self.assertRaises(FileExistsError):
                save_report(report, metrics)


if __name__ == "__main__":
    unittest.main()
