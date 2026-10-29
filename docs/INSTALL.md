# Pemasangan dan operasi

## A. Host dan prasyarat

Rute yang disediakan adalah **fresh canonical install** pada Ubuntu24.04 Linux x86_64 dengan CPython3.12. Perintah di README dijalankan pada server BARU sebagai root. Karena worker acuan mematok path Zipformer, checkout runtime harus `/root/voice-lab`, bukan sembarang directory. Clone di path lain tetap bisa dibaca/diuji tooling-nya, tetapi launcher akan menolak menjalankannya.

Jangan menjalankan installer terhadap server Sela yang sudah aktif. Root dalam container belum tentu mempunyai CAP_SYS_ADMIN/unshare network namespace; bootstrap memeriksanya. Bila gagal, gunakan host/kernel/capability yang sesuai dan tinjau isolasinya, bukan menonaktifkan sandbox.

OS dependency minimum: git, ca-certificates, Python3.12, python3.12-venv; Node diperlukan untuk test client. Tidak memerlukan GPU untuk rute inference CPU yang disimpan, tetapi resource admission tetap berlaku dan membutuhkan RAM kosong yang cukup. Ini bukan spesifikasi GPU training.

## B. Jalankan bootstrap sebelum konfigurasi

Dari `/root/voice-lab` di host baru:

```bash
python3.12 scripts/bootstrap.py --help
python3.12 scripts/bootstrap.py --check
python3.12 scripts/bootstrap.py --check-sources
python3.12 scripts/bootstrap.py --install
```

- Default/`--check`: offline, read-only, tidak memasang/memulai apa pun.
- `--check-sources`: opt-in jaringan publik untuk metadata terpin dan HEAD artifact, tanpa inference atau provider call.
- `--install`: eksplisit fresh-install; sumber dan hash diverifikasi lalu dibuat `deploy/web-venv`, `autonomous/zipformer/venv` dan `autonomous/expressive-tts/venv`.
- `--verify-assets`: checksum/size existing speech dependencies. Bukan bukti kesiapan provider atau performa.

Model besar tidak ditanam di Git. Manifest runtime Supertonic tetap revision asli. Download memakai snapshot archive terpin dengan metadata/content identities yang cocok dan SHA256 asli yang tetap diperiksa; bukan memilih latest. Identitas/revision unduhan dan bukti ada di `config/SETUP-HANDOFF.json`.

Bootstrap menolak `.env`, data/auth, runtime model/wheels/venv, service yang dikenal dan port8091 yang sudah terpakai. Tidak ada pemasangan systemd otomatis, auto-upgrade atau penghapusan state. Pada download/install gagal, marker `.setup-installing` dan artifact parsial dipertahankan untuk inspeksi; **jangan menganggap menghapus marker saja aman untuk retry**. Belum ada recovery otomatis. Dokumentasikan/arsipkan percobaan dan gunakan fresh checkout pada host baru setelah memastikan tidak ada state privat/layanan yang perlu dipertahankan.

## C. Pilih backend LLM dan buat .env privat

Ikuti [LLM-PROVIDERS.md](LLM-PROVIDERS.md) atau contoh README. Bootstrap harus lebih dahulu karena konfigurasi `.env` akan membuat fresh-install guard menolak.

Konfigurasi dibuat dengan `--write` sebagai file0600 eksklusif. Tanpa `--write`, konfigurasi hanya divalidasi dan tidak ada key/panggilan provider yang diperlukan. Jika .env/data sudah ada, jangan paksa overwrite; konfigurasi ulang instalasi sendiri dilakukan lewat prosedur berhenti/edit/doctor/restart.

File `.env` berisi `KEY=JSON-string`; **bukan shell syntax bebas**. Jangan gunakan `source`, `eval`, shell interpolasi atau `EnvironmentFile`. Launcher membuang inherited VOICE_* yang stale dan secret/proxy lain yang tidak diperlukan; API key runtime boleh diinjeksi via `VOICE_API_KEY`.

Default acuan baru menjaga engine produksi: Zipformer stateful + Supertonic3 + anchor B, automaticON, numericpacingOFF. Browser online STT di-OFF-kan secara aman pada host baru; ini beda dari deployment lama yang pernah mengizinkan pilihan opt-in. Tidak ada speech fallback otomatis.

## D. Pemeriksaan dan first start

```bash
python3.12 scripts/doctor.py
python3.12 scripts/run.py --check
python3.12 scripts/run.py
```

`doctor` tidak mengimpor aplikasi, mengalokasikan model, membuat akun, me-refresh OAuth, atau memanggil LLM. Nilai ready tidak menjamin kredensial/model/kuota remote valid. `run --check` hanya validasi launch; `run` menjalankan doctor lalu factory aplikasi yang diautentikasi.

Listener selalu `127.0.0.1:8091`. Untuk mengakses dari laptop tanpa membuka port internet, operator bisa membuat SSH port-forward (ganti host):

```bash
ssh -L 8091:127.0.0.1:8091 user@HOST_SERVER
```

Lalu buka `http://localhost:8091` di browser laptop. Localhost dapat menjadi secure context untuk mikrofon; perilaku browser/perangkat perlu diuji, bukan dijamin dari SSH tersambung.

Pada first start, aplikasi membuat state privat `data/` dan bootstrap enrollment token lokal dengan masa berlaku30menit. Buka halaman setup lalu masukkan token secara privat dan buat akun sendiri. Jangan publish isi token atau database. Autentikasi aplikasi ini **terpisah** dari OAuth Codex dan GitHub.

## E. systemd setelah smoke test

Template `deploy/sela-voice.service` tidak dipasang otomatis. Pada host BARU yang telah lolos doctor dan smoke test:

```bash
systemd-analyze verify deploy/sela-voice.service
install -m 0644 deploy/sela-voice.service /etc/systemd/system/sela-voice.service
systemctl daemon-reload
systemctl enable --now sela-voice.service
systemctl is-active sela-voice.service
```

Hentikan server foreground terlebih dahulu agar port tidak bentrok. Jangan memasang template ini di atas `voice-lab.service` produksi lama. Template tidak memakai domain produksi, tidak menyalin path interpreter Hermes lama, dan tidak menganggap `.env` sebagai systemd EnvironmentFile. Capability namespace harus tersedia; jangan mengaktifkan `PrivateNetwork` untuk seluruh web process karena LLM remote membutuhkan jaringan.

## F. HTTPS/domain publik

TLS, DNS dan tunnel/reverse proxy adalah konfigurasi terpisah, tidak diprovisikan repository. Gunakan domain milik operator, origin tetap loopback. Edit `.env` privat dengan JSON string origin tepat, misalnya:

```text
VOICE_PUBLIC_ORIGIN="https://DOMAIN_MILIKMU"
```

Jangan menaruh slash/path/Origin wildcard. Restart, kemudian periksa secara terpisah:
- localhost health200; hostname yang tidak diizinkan ditolak;
- public health200 dan login dapat dibuka;
- config401 dan anonymous WebSocket403 sebelum login;
- authenticated flow sampai audio dan final ACK berhasil;
- microphone bekerja pada secure context browser.

Jangan menghapus guard Host/Origin untuk menyelesaikan salah DNS/tunnel. Public health200 saja bukan bukti inference atau audio berfungsi.

## G. Test dan masalah umum

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -B -m unittest discover -s tests/setup -t . -v
node --test tests/web/*.test.cjs
```

- **Installer menolak existing installation:** jangan paksa; itu jalur fresh-install, bukan updater.
- **SHA mismatch:** stop, periksa manifest/source yang dipin; jangan mengganti expected hash agar lewat.
- **Namespace unavailable:** host/container policy tidak cocok; guard tidak boleh dilonggarkan diam-diam.
- **Missing Hermes modules:** jalankan interpreter Hermes yang benar; web venv API tidak otomatis berisi Hermes.
- **doctor pass tetapi provider gagal:** cek akun/model/kuota pada provider terpilih; doctor tidak melakukan remote inference.
- **TTS resource refusal:** periksa headroom/pekerjaan lain; jangan menurunkan guard untuk memaksa start.
- **Mikrofon remote tidak tersedia:** cek HTTPS/localhost, izin, capture device dan kebijakan browser.
- **Unconfigured snapshot di path lain:** `doctor`/`run --check` gagal adalah expected; source snapshot tidak dianggap instalasi canonical aktif.

Full Python aplikasi: `python -B -m unittest discover -s tests/python -t . -v`. Suite acuan masih mempunyai dependency legacy Piper/Whisper/espeak dan SDK Hermes serta tes native inference. Dependensi legacy bukan runtime default bootstrap Supertonic/Zipformer. Gunakan checkout pengembangan terisolasi yang lengkap. Tes korupsi profil yang diaudit memakai salinan sementara; jangan mengubah aset produksi demi meluluskan tes. Lihat [VERIFICATION.md](VERIFICATION.md).
