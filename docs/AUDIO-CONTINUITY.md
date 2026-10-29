# Audio continuity — delta terpasang

Delta ini menyalin tiga file persis dari versi yang sudah dipasang dan diverifikasi hash-nya: `voice_app/speech.py`, `voice_app/audio_continuity.py`, dan `tests/python/test_audio_continuity.py`. Tidak mengubah model, identitas voice M1/id, conditioning B/automatic selection, numeric pacing, worker, atau konfigurasi deployment.

## Perilaku konservatif

- Hanya respons **multipart Supertonic**: pangkas floor pada tepi sambungan, bukan VAD atau penghapusan semua jeda.
- Floor berarti sampel signed PCM16 dengan `abs(sample) <= 1`, berurutan dari tepi. Sampel pertama di atas ambang menghentikan scan.
- Tepi baru memenuhi syarat jika floor minimal **250 ms**; pertahankan minimal **120 ms**, dengan maksimum penghapusan **750 ms per tepi**. Pada 44100 Hz: 11025 / 5292 / 33075 frame.
- Awal piece pertama dan akhir piece terakhir tetap utuh. Single-piece, backend lain, internal pauses, dan audio seluruhnya floor tetap utuh. PCM yang dipertahankan adalah slice byte-exact, bukan resampling/fade/time-stretch.
- Input tidak aman/format selain canonical WAV mono PCM16 44100 Hz, durasi di atas 30 detik per piece, atau batas byte dilanggar ditolak; tidak diteruskan sebagai audio berhasil atau retry fallback.
- Punctuation **sudah digunakan** oleh pemecahan teks sebelumnya. Delta ini tidak memperkenalkan punctuation splitting baru dan tidak menghapus seluruh jeda linguistik.
- Exact source spans, snapshot conditioning, last flags, ACK/history, cancellation, dan prefetch tetap dipertahankan. **ACK/transport/browser decode floor tetap ada**; ini bukan gapless streaming.

## Bukti dan batasnya

Receipt produksi yang sudah ada menyatakan tiga file terpasang; sinkronisasi package ini tidak menjalankan deployment. Bukti browser terpisah pada 29 Oktober 2026 memakai replay capture native terdahulu: tiga chunk dengan ACK setelah natural end dan cancellation tanpa false ACK/successor. Tidak ada fresh native inference pada replay itu dan tidak ada klaim mendengar speaker fisik, mikrofon perangkat, kenyamanan subjektif, atau suara baru.

Sinkronisasi lokal menjalankan 22 setup/layout, 40 safe protocol, 11 continuity, dan 121 Node tests; semuanya pass. PCM continuity memakai waveform sintetis berlabel, bukan bukti native inference. Full native suite tidak dijalankan. Lihat [verifikasi](VERIFICATION.md).

## Inventori dan reproduksi tes

`tests/move-inventory.json` mempertahankan 39 entri dan byte historisnya. Satu tes baru terikat SHA256 dalam `tests/additions-inventory.json`; layout mematok hanya path/hash itu, bukan glob longgar. `docs/CONTINUITY-DELTA-MANIFEST.json` mengikat delta tanpa menulis ulang `PRODUCTION-SOURCE-MANIFEST.json`.

Dengan interpreter yang dapat mengimpor SDK Hermes dan dependency web, dari root checkout:

```bash
# PYTHONPATH ini hanya untuk import fixture legacy di tes baru yang disalin byte-exact.
# Tidak ada root shim atau salinan test tambahan.
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests/python "$SDK_PYTHON" -B -m unittest tests.python.test_audio_continuity -v
```

`SDK_PYTHON` harus diisi path interpreter aktual milik operator; bukan interpreter yang otomatis tersedia pada clone baru. Jangan menjalankan bootstrap fresh-install untuk meng-upgrade produksi. Sela Live tetap pengembangan end-to-end terpisah, bukan perluasan scope delta ini.
