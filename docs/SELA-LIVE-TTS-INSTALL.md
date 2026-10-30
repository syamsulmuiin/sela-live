# TTS Sela Live: quality12 untuk Sela

Status transaksi terpasang dan sinkronisasi GitHub dicatat di `LIVE-INSTALL-STATE.json`. Jangan menyamakan kandidat yang lolos uji dengan layanan yang sudah beralih.

## Ruang lingkup

- Ini tahap TTS dari proyek percakapan Live end-to-end, bukan model baru atau hasil training ulang.
- Supertonic SDK sumber dipin pada `df0f9686dac7fbbde391b759e2ee5286a3737622` dan disertakan di `vendor/supertonic/`, bersama lisensi/provenance. Model dan venv tidak disimpan di Git.
- Adapter `voice_app/sela_live_bridge.py` memakai worker terisolasi `sela_live_tts/worker.py`. Backend dasar tetap `supertonic3`; identitas API menambahkan `adapter=sela_live` dan `total_steps=12`.
- Anchor conditioning B, seed20261026, speed1.05, sumber kata/tanda baca, pengucapan, chunk ACK dan pemilihan conditioning otomatis yang sudah ada dipertahankan. Perubahan kualitas native adalah8→12 langkah. Preferensi pendengar berlaku untuk sampel paragraf identik yang dipilih, bukan jaminan setiap emosi/jeda/ucapan selalu benar.
- STT Zipformer stateful, LLM remote API/Codex, autentikasi, UI dan batas CPU/RSS/network/deadline tetap. Tidak ada LLM lokal atau fallback TTS diam-diam.

## Kontrak konfigurasi

`VOICE_SELA_LIVE_TTS` hanya menerima string `0`/`1`. Default0 mempertahankan adapter lama. Jika1, semua konfigurasi berikut wajib eksplisit dan benar; sumber hilang/berubah ditolak:

- `VOICE_SELA_LIVE_PYTHON`: interpreter speech yang sudah dipasang.
- `VOICE_SELA_LIVE_WORKER`: worker di root aplikasi produksi, bukan staging/scratch.
- `VOICE_SELA_LIVE_WORKER_SHA256`: digest worker **setelah** manifest diikat ulang ke root pemasangan.
- `VOICE_SELA_LIVE_LOCK`: inode lock persisten yang sama bagi produksi dan evaluasi, pada host ini `/root/voice-lab/.runtime/live-tts.lock`.

Jangan mengganti `VOICE_TTS_BACKEND` dengan `sela_live`, memasukkan label emosi ke teks, atau menyalin worker source mentah tanpa rebinding. Service menggunakan drop-in khusus; `.env` tooling merupakan data JSON-string, bukan berkas shell/systemd EnvironmentFile.

## Packaging dan pemasangan aman

1. Verifikasi source/native/SDK/profile yang dipin, lalu bangun payload baru dengan `tools/build_live_runtime.py`. Tool default hanya membuat rencana; `--write` membuat staging baru, tidak memasang produksi. Source dan target harus terpisah.
2. Manifest hasil mengubah tiga root; worker hanya berubah pada literal digest manifest. Tiga native guard lama disalin byte-identik sebagai preserved inputs, tidak ditimpa installer. Dependensi produksi tetap app-owned.
3. Jalankan readiness dan integrasi native dari lokasi relokasi, review runtime dan installer pada hash tepat, lalu bekukan baseline source/unit/selector sebelum transaksi.
4. `python tools/install_live_tts.py --plan /path/to/reviewed-plan.json` bersifat read-only. Hanya `--apply` pada plan terikat review/hash/mode menjalankan observe→archive→stop→write→restart→readback. CLI sengaja dibatasi ke root/service host ini.
5. Snapshot/receipt transaksi berada di `/root/sela-live-install-snapshots/`, tidak dipublikasikan. Rollback hanya dimulai setelah stop berhasil dan tidak memulai ulang sebelum seluruh baseline pulih/terverifikasi. Kegagalan pemulihan berarti berhenti dan rekonsiliasi, bukan memaksa restart.

Ini jalur upgrade untuk host yang sudah memiliki dependensi terverifikasi. Fresh-install baseline dijelaskan terpisah di README; full fresh-host Sela Live quality12 belum diklaim teruji. Jangan menjalankan bootstrap fresh-install pada produksi yang sudah berjalan.

## Bukti pengujian

- `reviews/LIVE-INTEGRATION-RUNTIME-REVIEW.json`: source/SDK/runtime dan lifecycle.
- `LIVE-NATIVE-BROWSER-VERIFICATION.json`: panggilan Codex nyata, empat sintesis12-langkah, dua giliran pada tiap jalur network/browser, playback selesai dan ACK cocok, layout390px tanpa overflow. Akun fixture sementara dihapus, bukan akun produksi.
- `reviews/LIVE-INSTALLER-APPROVAL.json`: review installer yang pernah ditolak, dipertahankan sebagai provenance. Verdict perbaikan dan manifest final harus dipakai untuk transaksi baru, bukan verdict lama.
- `PRODUCTION-SOURCE-MANIFEST.json`, `CONTINUITY-DELTA-MANIFEST.json` dan `tests/move-inventory.json` tetap bukti historis yang immutable. Delta baru tidak ditutupi dengan mengganti baseline lama.

Pengujian browser otomatis bukan bukti mikrofon/speaker fisik pengguna, dan fixture login terisolasi bukan klaim login akun produksi sudah diuji. Gunakan halaman produksi dengan akun sendiri untuk uji dengar/mikrofon setelah pemberitahuan pemasangan selesai.
