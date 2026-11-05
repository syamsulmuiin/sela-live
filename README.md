# Sela Live

Satu aplikasi percakapan suara: mikrofon → STT lokal-server → otak remote API/Codex → TTS lokal-server → playback/ACK. Pengembangan berikutnya dilakukan di repository ini, bukan proyek sampel terpisah.

## Core yang dimiliki proyek

- `sela_live/audio/stt.py` memiliki implementasi stateful Zipformer: growing-prefix, deduplikasi, validasi prefix, finalisasi dan reset stream. `voice_app.zipformer_state` adalah compatibility import; worker native `-I` dan aplikasi memakai kelas yang sama.
- `sela_live/audio/tts.py` memiliki kebijakan sintesis `natural_v1`: validasi teks/kontrol, satu konteks, batas token/mask, admission durasi DP berdasarkan style yang dipilih, dan pemanggilan SDK quality12. Worker native benar-benar memakai implementasi ini setelah verifikasi hash.
- `voice_app/` tetap memiliki web/auth, transport, lifecycle, admission/quiescence, pembatalan/reap, riwayat dan playback ACK. Entry point service tetap `voice_app.server:create_app`.
- `vendor/supertonic/` tetap source SDK dasar terpin dari `supertone-oss-archive/supertonic-py`, commit `df0f9686dac7fbbde391b759e2ee5286a3737622`, bukan diklaim karya/model baru. Lisensi dan provenance dipertahankan. Model STT tetap sherpa/Zipformer yang sudah dipasang, bukan model yang dilatih proyek ini.

Identitas core `0.1.0` berarti integrasi kepemilikan kode, **bukan peningkatan akustik yang sudah dibuktikan**. Anchor B, bahasa native `id`, speed1.05, quality12 dan `natural_v1` dipertahankan. Guard asli menerima baseline8 lalu kebijakan yang dipin memanggil SDK12; batas CPU/RSS/network/deadline dan `guard.main` tidak diubah. Tidak ada DSP/F1/singsong atau engine/model baru.

Belum ada klaim emotion terlatih, STT multilingual, tajwid, mikrofon/speaker fisik atau percakapan audio-to-audio native. Bahasa aplikasi/STT sekarang Indonesia. Otak tetap remote API/Codex, bukan LLM lokal. Audio diproses di server lokal, bukan API audio cloud.

## Satu source, dua root

```bash
# Checkout pengembangan (bukan root service)
git clone https://github.com/syamsulmuiin/sela-live.git /root/sela-live
cd /root/sela-live
```

`/root/sela-live` adalah source/review/test. `/root/voice-lab` adalah root aplikasi terpasang; nama direktori legacy itu tidak berarti produk lain. Jangan menyalin `.env`, auth, data/rekaman, model atau venv ke Git. Jangan mengarahkan service ke checkout pengembangan.

Upgrade terpasang menggunakan **satu jalur**: builder offline → review payload/digest → installer transaksional yang sudah ada. Tidak menjalankan fresh-bootstrap pada produksi.

```bash
# PLAN SAJA: tidak mengimpor native, tidak menyentuh target/service/lock.
# Parent output harus ada; output payload harus BARU.
python -B tools/build_live_runtime.py \
  --source-app-root /root/sela-live \
  --target-app-root /root/voice-lab \
  --output-root /root/sela-research/sela-live-reviewed-payload
```

Setelah review sumber, operator menambahkan `--expected-worker-sha256 HASH_REVIEW` dan `--expected-natural-worker-sha256 HASH_REVIEW`; `--write` hanya membuat staging payload baru, bukan memasang. Payload builder adalah dependency native; rencana aplikasi juga harus menyertakan `voice_app/speech.py` dan `voice_app/sela_live_bridge.py`, sementara ketiga guard asli dipreservasi. Manifest runtime harus sesuai allowlist aplikasi/installer. Detail: [core dan relokasi](docs/SELA-LIVE-CORE.md), [kontrak pemasangan](docs/SELA-LIVE-TTS-INSTALL.md).

```bash
# Memerlukan plan dengan baseline produksi beku dan approval exact-hash.
# Jangan membuat approval sendiri atau memakai receipt review historis.
python -B tools/install_live_tts.py --plan /PATH/TO/REVIEWED-PLAN.json
# Pemasangan hanya oleh operator setelah review independen:
# python -B tools/install_live_tts.py --plan /PATH/TO/REVIEWED-PLAN.json --apply
```

Core `0.1.0` **sudah dipasang pada 5 November 2026** melalui review independen dan upgrade transaksional dengan snapshot rollback. Readback payload/core, layanan aktif, dan guard publik sudah diverifikasi. Bukti native staging menunjukkan PCM B tetap identik dan jalur STT core berjalan pada fixture sintetis; itu bukan bukti akurasi ucapan manusia. Uji kode terpasang melalui akun fixture terisolasi juga lulus: audio sintetis → ASR lokal → API/Codex nyata → TTS, lalu giliran teks kedua dengan konteks, source-span, ACK dan riwayat tepat. Ini uji protokol ASGI, bukan bukti mikrofon atau playback fisik. Status: [instalasi core](docs/SELA-LIVE-CORE-INSTALL-STATE.json), [receipt dua giliran](docs/SELA-LIVE-CORE-ACCEPTANCE.json).

## Bootstrap/configure/run/doctor

`scripts/bootstrap.py`, `configure.py`, `run.py`, dan `doctor.py` adalah tooling rekonstruksi baseline. Bootstrap menolak runtime/data/service yang sudah ada; configure tidak meng-upgrade konfigurasi privat yang sudah ada. Doctor baseline bukan pengganti verifikasi manifest natural_v1 atau installer/readback. Launcher tetap mengarah ke aplikasi kompatibel yang kini mengimpor core, tetapi konfigurasi natural_v1 terpasang berada pada environment service/drop-in, bukan otomatis dipindahkan ke `.env` oleh tooling baseline.

Jadi **jangan menjalankan bootstrap/configure/run untuk meng-upgrade produksi**, dan jangan menyalakan instance kedua di port8091. Gunakan jalur builder/installer di atas; service, auth dan konfigurasi otak dipertahankan. Setup Sela Live penuh pada host kosong belum diverifikasi dan belum menjadi perintah instalasi yang direkomendasikan. [INSTALL historis](docs/INSTALL.md) dan [LLM providers](docs/LLM-PROVIDERS.md) hanya referensi prasyarat; jangan mengganti source dengan snapshot lama.

## Tes tanpa inference

Dari source root, interpreter dependency yang sudah tersedia merupakan input operator, bukan hasil bootstrap baru:

```bash
python -B -m unittest tests.setup.test_core_integration \
  tests.setup.test_live_runtime_package tests.setup.test_live_tts_install -v

# NATIVE_PYTHON: interpreter speech terpasang dengan NumPy, fixture saja.
PYTHONPATH=tests/python "$NATIVE_PYTHON" -B -m unittest \
  test_sela_live_core test_sela_live_natural_worker test_sela_live_duration -v
PYTHONPATH=tests/python "$NATIVE_PYTHON" -O -B -m unittest \
  test_sela_live_core test_sela_live_natural_worker test_sela_live_duration -v

# SDK_PYTHON: interpreter web/Hermes dengan dependency aplikasi.
PYTHONPATH=tests/python "$SDK_PYTHON" -B -m unittest \
  test_stateful_worker test_sela_live_natural_integration \
  test_heard_context test_quran_end_marker -v
node --test tests/web/*.test.cjs
```

Tes state dan DP menggunakan recognizer/tensor fixture yang diberi label, bukan hasil inference. Suite lengkap historis dapat memuat ASR/Piper nyata: **jangan memakai full discover aplikasi untuk tugas zero-NN**. Inventory historis tetap immutable; delta core dicatat terpisah di `tests/core-additions-inventory.json`.

## Bukti dan batas

- [natural_v1](docs/NATURAL-V1.md), [Arabic playback](docs/ARABIC-PLAYBACK-INSTALL-STATE.json), [P0 percakapan](docs/LOCAL-S2S-P0-INSTALL-STATE.json): checkpoint terpasang sebelumnya, bukan receipt core baru.
- [Regresi terdahulu](docs/LOCAL-S2S-REGRESSION-VERIFICATION.json), [arsitektur](docs/ARCHITECTURE.md), [keamanan/lisensi](docs/SECURITY-AND-BACKUP.md).
- Audio continuity dan pemetaan penanda ayat hanya pada salinan TTS dipertahankan; teks display/riwayat/ACK tidak diubah. Bagian interrupted yang sudah di-ACK tetap dipakai pada giliran berikutnya; ekor belum ACK tidak ditebak.
- Tidak ada model/venv, akun, OAuth atau rekaman privat di source. Fresh-host end-to-end, naturalness semua teks, emosi independen, multilingual STT, interupsi otomatis dan perangkat fisik masih gate terpisah.
