# Sela Live — tahap pertama TTS untuk Sela

**Status: TTS Live quality12 sudah dipasang pada Sela; pengembangan aplikasi Live penuh tetap bertahap.**

Komponen TTS memakai source SDK Supertonic yang diclone dan dipin; ini bukan model baru atau hasil training ulang. STT Zipformer stateful, LLM remote API/Codex, UI, autentikasi, anchor conditioning B dan pemilihan conditioning otomatis yang sudah ada dipertahankan. Kontrol native diubah dari8 menjadi12 langkah setelah pengguna memilih hasil paragraf identik yang lebih bagus; preferensi satu sampel bukan jaminan setiap intonasi/emosi sudah benar.

- Source native: `sela_live_tts/` dan `vendor/supertonic/`, commit upstream `df0f9686dac7fbbde391b759e2ee5286a3737622`, dengan lisensi/provenance dan hash sumber. Worker terpasang berada di root aplikasi produksi, tidak bergantung pada clone/scratch.
- Adapter aktif ditandai `tts_identity.adapter=sela_live`, `total_steps=12`, backend dasar tetap `supertonic3`. Tidak ada penggantian LLM dengan model lokal atau fallback TTS diam-diam.
- Pemasangan memakai allowlist, snapshot/rollback, sumber terikat review/hash/mode dan lock host persisten. Guard CPU/RSS/network/deadline tidak dihapus. Baca [kontrak dan pemasangan TTS Live](docs/SELA-LIVE-TTS-INSTALL.md).
- Verifikasi:296 tes aplikasi tanpa kegagalan (4 opt-in skip),66 setup/layout,121 tes Node; suite runtime122 juga lulus normal/optimized dan dari ekspor source bersih dengan dependensi yang sudah terpasang. Full suite mencakup fixture native ASR/Piper yang sudah ada; tidak diklaim inference-free atau fresh-host.
- Empat sintesis12-langkah pada jalur Codex/API dan browser terisolasi lulus, dua turn per jalur dengan playback selesai, ACK dan riwayat sesuai. Sintesis baru dari source terpasang serta health/auth/Host/loopback terpasang diperiksa lagi. Akun produksi tidak diubah; login produksi dan mikrofon/speaker fisik tetap pengujian pengguna.
- Status transaksi dan publikasi: `docs/LIVE-INSTALL-STATE.json`; hasil pengujian: `docs/LIVE-RELEASE-VERIFICATION.json`. `syamsulmuiin/sela-voice` tetap snapshot baseline, sedangkan `syamsulmuiin/sela-live` memuat source proyek Live penuh bertahap ini.

Jangan menjalankan fresh-install baseline di bawah pada host produksi yang sudah berisi Sela. Jalur quality12 saat ini adalah upgrade terverifikasi pada host dengan dependensi speech terpasang; instalasi Sela Live lengkap pada host kosong belum diklaim teruji. Source/receipt eksperimen lama tetap provenance historis, bukan status pemasangan terbaru.

## Dokumentasi baseline snapshot (historis)

Bagian berikut dipertahankan dari snapshot `sela-voice` sebagai konteks rekonstruksi. Nama, status dan langkah instalasinya bukan pernyataan bahwa Sela Live sudah diluncurkan.


Repository ini menyimpan **seluruh sistem yang sudah dibangun**: mikrofon → STT Indonesia → LLM remote API/Codex → Supertonic TTS → playback dan ACK, beserta antarmuka, autentikasi, lifecycle dan pengujiannya.

**Bukan repository khusus TTS.** Pengembangan berikutnya bernama **Sela Live**, juga end-to-end dari STT sampai TTS, dan akan memakai repository terpisah. Baca [pembagian scope](docs/REPOSITORY-SCOPE.md).

## Konfigurasi acuan

- STT lokal-server: stateful Indonesian Zipformer int8, preview lokal aktif.
- LLM: **pilih** Codex melalui instalasi Hermes sendiri, atau API remote kompatibel Chat Completions. Tidak memasang llama.cpp/LLM lokal.
- TTS: Supertonic-3 M1/id, 44100Hz mono PCM16, conditioning B sebagai anchor netral.
- Pemilihan conditioning otomatis aktif di sekitar B; numeric speed/pause experiments OFF. Ini belum emosi independen yang dilatih atau pembuktian identitas suara selalu sama.
- Balasan panjang dikirim dalam bounded chunks dengan exact source ACK; tidak ada batas30detik untuk seluruh jawaban. Guard native tetap per piece.
- [Audio continuity terpasang](docs/AUDIO-CONTINUITY.md): pangkas hanya boundary floor PCM `abs(sample) <= 1`, minimal250ms, simpan120ms, cap penghapusan750ms per tepi. Punctuation sudah dipakai sebelumnya; jeda internal dan ACK/transport floor tetap ada. Bukan voice baru atau klaim mendengar speaker fisik.
- Backend dan audio berada di server; browser Android/desktop mengakses server yang sama. Aplikasi native Android/Windows belum disertakan.
- Bahasa aplikasi sekarang Indonesia. Model TTS multibahasa tidak otomatis membuat STT/aplikasi sudah multilingual.

## Mulai dari git clone

**Target setup otomatis saat ini: host BARU Ubuntu24.04 Linux x86_64, Python3.12, root dengan kemampuan network-namespace.** Worker acuan memiliki path tetap `/root/voice-lab`; arbitrary install directory belum didukung. Jangan menjalankan fresh-install ini pada server produksi yang sudah berisi Sela.

```bash
# Pada host BARU, setelah akses repository private sudah diotorisasi
sudo -i
apt-get update
apt-get install -y git python3.12 python3.12-venv nodejs ca-certificates

git clone https://github.com/syamsulmuiin/sela-voice.git /root/voice-lab
cd /root/voice-lab

# Default offline/read-only; lihat syarat tanpa mengunduh/memulai layanan
python3.12 scripts/bootstrap.py --check

# Eksplisit: download artifact terpin+hash, buat tiga venv terisolasi
python3.12 scripts/bootstrap.py --install
```

Repository private memerlukan autentikasi GitHub milik operator, misalnya GitHub CLI resmi. Jangan menaruh token pada URL clone. Model besar/venv tidak di-Git; bootstrap mengunduh dari manifest, memverifikasi SHA256, dan tidak menggunakan `latest` atau menurunkan guard.

### Pilihan A — LLM API remote

```bash
python3.12 scripts/configure.py \
  --backend api \
  --model MODEL_ID_DARI_PROVIDER \
  --base-url https://ENDPOINT_PROVIDER/v1 \
  --write
```

Ganti dua placeholder dengan endpoint/model yang benar. Key diminta lewat **prompt tersembunyi**, bukan argumen command-line. Alternatif server automation: environment `VOICE_API_KEY`. Konfigurasi disimpan mode0600; tidak pernah dimasukkan ke Git.

### Pilihan B — Codex melalui Hermes

Pasang Hermes dari dokumentasi resminya pada host tersebut, autentikasi akun sendiri, dan tentukan interpreter yang benar-benar bisa mengimpor SDK Hermes dan dependency web.

```bash
hermes auth add openai-codex --type oauth

python3.12 scripts/configure.py \
  --backend hermes \
  --model MODEL_CODEX_YANG_TERSEDIA_DI_AKUNMU \
  --hermes-python /PATH/ABSOLUT/INTERPRETER/HERMES/bin/python \
  --write
```

`/PATH/...` adalah placeholder, bukan path yang sudah disediakan repository. Interpreter web khusus API berbeda dari interpreter Hermes. Jangan menyalin OAuth token ke repository, `.env` aplikasi atau browser. Baca [panduan LLM](docs/LLM-PROVIDERS.md).

### Periksa lalu jalankan

```bash
python3.12 scripts/doctor.py
python3.12 scripts/run.py --check
python3.12 scripts/run.py
```

Buka `http://127.0.0.1:8091` pada host lokal, atau gunakan SSH port-forward ke laptop. Daftarkan **akun aplikasi baru** melalui halaman setup dengan token privat di `data/bootstrap-token` pada host baru. Token berlaku30menit dan tidak boleh dibagikan. Akses mikrofon remote perlu HTTPS; penyiapan domain/TLS/tunnel tidak dilakukan otomatis.

**Jangan `source .env`.** File yang dibuat tooling berformat `KEY=JSON-string`, dibaca sebagai data oleh launcher; bukan shell script dan bukan `systemd EnvironmentFile`.

## Panduan lengkap

- [Instalasi, service, domain, konfigurasi ulang dan troubleshooting](docs/INSTALL.md)
- [Pilihan LLM dan autentikasi terpisah](docs/LLM-PROVIDERS.md)
- [Arsitektur dan langkah mencapai konfigurasi sekarang](docs/ARCHITECTURE.md)
- [Verifikasi, hasil acuan dan batas klaim](docs/VERIFICATION.md)
- [Keamanan, privasi, lisensi dan batas backup](docs/SECURITY-AND-BACKUP.md)
- [Pemisahan sistem sekarang vs Sela Live](docs/REPOSITORY-SCOPE.md)
- [Handoff setup awal dan batas verifikasi](config/SETUP-HANDOFF.json) — catatan historis sebelum perbaikan review/relokasi tes; gunakan perintah terbaru di README ini

Dokumen rollout historis di root menjelaskan keputusan komponen. Beberapa tautan path receipt merujuk host pengembangan; log/rekaman/akun produksi sengaja tidak ikut snapshot. README dan panduan setup baru adalah entrypoint reproduksi.

## Pengujian

```bash
# Self-contained setup tests: tidak memerlukan API key/model, tidak menjalankan bootstrap install
PYTHONDONTWRITEBYTECODE=1 python3.12 -B -m unittest discover -s tests/setup -t . -v

# Unit/regresi shipping client; ini Node, bukan mikrofon/speaker fisik
node --test tests/web/*.test.cjs
```

Full aplikasi: `PYTHONPATH=tests/python python -B -m unittest discover -s tests/python -t . -v` (interpreter dengan SDK Hermes; path fixture diperlukan oleh tes continuity byte-exact). Memerlukan environment pengembangan, SDK Hermes dan dependency native/legacy; sebagian tes menjalankan inference native. Tes korupsi aset yang diaudit memakai salinan sementara, bukan aset produksi. Jalankan suite pada checkout pengembangan terisolasi, bukan server aktif. Snapshot historis sebelumnya mencatat225 run/221 pass/4 skip dan22 setup/layout. Release TTS Live kini telah menjalankan296 tes aplikasi (4 opt-in skip, tanpa kegagalan),66 setup/layout dan121 Node; lihat `docs/LIVE-RELEASE-VERIFICATION.json`. Pengujian memakai dependensi host yang sudah tersedia, bukan klaim fresh-host.

## Batas reproduksi dan keamanan

- Tool setup dan wheel environments telah diuji; **deployment penuh pada host Ubuntu kosong belum diverifikasi**. Pemeriksaan metadata/HEAD, install venv sementara dan hash existingdeps tidak sama dengan uji fresh-host end-to-end.
- Bootstrap hanya untuk fresh-install; menolak `.env`, data, service, port8091 atau runtime yang sudah ada. Tidak ada upgrade/resume/cleanup otomatis, dan tidak memasang/memulai systemd.
- Kredensial, akun/chat/rekaman, model besar, venv dan seluruh archive eksperimen tidak disimpan di Git. Clone tidak memulihkan akun/riwayat lama.
- Supertonic model/profile turunan tetap mempunyai terms model asal; salinan terms di `web/supertonic3-LICENSE.txt`. Repository ini tidak memberi lisensi ulang menyeluruh untuk komponen pihak ketiga.
- Referensi source baseline yang disalin: `docs/PRODUCTION-SOURCE-MANIFEST.json` (historis). Relokasi/path/import test tercatat terpisah di `tests/move-inventory.json`; manifest baseline tidak ditulis ulang. Perbaikan runtime berikutnya dicatat sebagai delta/release, bukan diklaim byte-identik dengan snapshot awal.
