# Pilih LLM yang dipakai Sela

Pilihan konfigurasi terjadi **di server sebelum start/restart**, bukan pemilih backend dinamis dalam browser. Provider yang benar-benar didukung oleh source snapshot (`voice_app/providers.py`) hanya dua:

## 1. `api`: remote Chat Completions

Membutuhkan `VOICE_API_BASE_URL`, `VOICE_MODEL` dan `VOICE_API_KEY`. Adapter memanggil `<base-url>/chat/completions` dengan SSE `stream=true`, `max_tokens=768` dan teks percakapan. Endpoint harus menyediakan protocol tersebut; native endpoint provider lain belum tentu kompatibel tanpa gateway/adapter.

Model harus bisa mengikuti instruksi structured envelope `text` + `delivery` milik aplikasi. Dukungan streaming saja belum membuktikan model memenuhi kontrak ini; lakukan live test yang dipilih operator setelah setup. Response malformed ditolak, bukan dibacakan sebagai JSON.

```bash
python3.12 scripts/configure.py \
  --backend api --model MODEL_ID \
  --base-url https://ENDPOINT_PROVIDER/v1 --write
```

`MODEL_ID` dan domain placeholder harus diganti sesuai akun/provider. `configure` tanpa `--write` hanya memvalidasi opsi dan tidak menghubungi provider. `--write` meminta key di terminal secara tersembunyi apabila tidak disediakan melalui environment. Tidak ada `--api-key` flag. Jangan menulis key literal ke shell history atau issue GitHub.

Provider ini tidak memerlukan instalasi Hermes. Venv web yang dibuat bootstrap digunakan untuk server API. Biaya/kuota/ketersediaan model adalah tanggung jawab akun sendiri; pemeriksaan modul bukan bukti provider dapat dipanggil.

## 2. `hermes`: remote openai-codex

Adapter saat ini secara eksplisit menggunakan `resolve_provider_client(provider='openai-codex', model=...)`. Memilih `hermes` **bukan** berarti semua provider di konfigurasi global Hermes otomatis dapat dipilih. Model Sela dipilih oleh `VOICE_MODEL`; jangan mengira mengganti model chat Hermes pasti mengganti Sela.

Pasang dan autentikasikan Hermes sendiri. Perintah yang diverifikasi dari CLI pada audit:

```bash
hermes auth add openai-codex --type oauth
```

Dokumentasi resmi: https://hermes-agent.nousresearch.com/docs/getting-started/installation dan https://hermes-agent.nousresearch.com/docs/integrations/providers . Ikuti versi resmi dan periksa `hermes auth add --help` jika CLI berubah. Installer/SDK Hermes tidak divendor dalam snapshot ini; versi terpasang di host acuan adalah editable0.0.0 tanpa Git revision yang dapat dipulihkan, sehingga reproducibility jalur Hermes mempunyai dependency eksternal yang belum dipin sepenuhnya.

Gunakan interpreter absolut milik instalasi Hermes yang dapat mengimpor `agent`, `hermes_cli`, `openai`, `fastapi`, `uvicorn`, `httpx` dan `websockets`. Jangan memaksakan wheel lock CPython3.12 aplikasi ini ke environment Hermes dengan versi Python berbeda. Pilih environment yang sudah memiliki dependency tersebut atau pasang web dependencies sesuai Python instalasi Hermes secara terpisah dan uji lagi.

```bash
python3.12 scripts/configure.py \
  --backend hermes --model MODEL_CODEX_DI_AKUNMU \
  --hermes-python /PATH/KE/HERMES/bin/python --write
```

Path/model tersebut placeholder. Konfigurasi hanya menyimpan path dan model, **tidak menyalin OAuth credentials**. Runtime membaca autentikasi melalui SDK resmi dan mencoba refresh sekali pada401, bukan berpindah diam-diam ke model lain. Proses memakai HOME/HERMES_HOME yang sesuai dengan akun Hermes pada host baru. `doctor` memeriksa presence saja, bukan validitas OAuth/kuota.

## Ganti model/backend setelah instalasi

`configure --write` sengaja hanya membuat `.env` baru dan menolak overwrite maupun data akun yang sudah ada. Jangan menghapus `data` atau memodifikasi bootstrap guard hanya untuk mengganti model.

Pada instalasi sendiri: hentikan service aplikasi terlebih dahulu, backup privat `.env` dengan izin0600, lalu edit nilai konfigurasi menggunakan editor lokal. Nilai **wajib JSON string**, misalnya:

```text
VOICE_AI_BACKEND="api"
VOICE_MODEL="MODEL_BARU"
VOICE_API_BASE_URL="https://ENDPOINT_PROVIDER/v1"
```

Pastikan field/key backend baru lengkap, hapus field backend lama yang tidak dibutuhkan, pertahankan semua setting speech dan path yang tidak berubah. Jangan menghapus data/history. Jalankan `doctor.py` dan `run.py --check`, start kembali, lalu verifikasi config dan satu turn nyata. Tidak ada shell sourcing, secret di command line, atau perubahan engine STT/TTS yang diperlukan untuk mengganti LLM.

## Yang tidak didukung oleh pilihan ini

- LLM lokal/llama.cpp sebagai fallback atau prosedur default.
- Menganggap API key teks juga credential STT API, atau GitHub OAuth sebagai Codex OAuth.
- Membaca/mengambil kredensial model dari akun lain.
- Mengklaim semua provider/model sudah diuji. Jalur API dan Codex berbeda; keduanya harus diuji pada akun/endpoint yang dipilih.
- Native audio/live multimodal hanya karena model remote bisa menghasilkannya. Snapshot ini adalah pipeline STT → text LLM → TTS.
