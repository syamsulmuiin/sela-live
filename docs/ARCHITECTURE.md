# Arsitektur dan titik acuan snapshot

## Alur produksi yang disimpan

Browser dengan AudioWorklet → autentikasi HTTP/WebSocket → stateful Zipformer Indonesia → LLM remote (Codex melalui Hermes, atau API Chat Completions) → parser text/delivery ketat → pemilihan conditioning otomatis → Supertonic-3 → WAV mono PCM16 44100Hz → playback browser → ACK per token/chunk → history heard_text.

- Hanya transkrip final memicu LLM; interim tidak menjadi turn terpisah.
- Metadata emosi/intensitas bukan diagnosis emosi suara pengguna dan tidak masuk teks yang dibacakan.
- Speaker dasar Supertonic M1, bahasa aktif Indonesia, neutral anchor conditioning B. Automatic conditioning memilih tujuh profil B-anchored; ini experimental activation, bukan tujuh emosi independen yang sudah dipelajari.
- Native numeric speed/pause experiments tetap OFF; tidak ada fallback ke suara browser/OS/provider lain.
- Server menghasilkan audio yang sama untuk browser, calon Android dan calon desktop. Aplikasi native Android/Windows belum dibuat; mikrofon/speaker fisik semua perangkat tidak diklaim sudah diuji.
- Balasan panjang dipecah dengan guard per piece dan exact source ACK. Bukan batas 30 detik untuk seluruh jawaban, bukan eksekusi tanpa batas.
- Half-duplex: capture ditahan selama playback. Ini tidak identik dengan native live-audio model atau barge-in akustik.

## Komponen kode

- `voice_app/server.py`: session, routes, auth, provider/TTS handoff, status.
- `voice_app/providers.py`: pilihan `hermes` (Codex) atau `api` (Chat Completions SSE).
- `voice_app/delivery.py`, registry: text dipisah dari intended delivery.
- `voice_app/automatic_conditioning.py`, assets: pemilihan style lokal bounded.
- `voice_app/speech.py`: routing speech, resource admission, lifecycle.
- `voice_app/zipformer_stateful_worker.py`, state helpers: STT stateful.
- `voice_app/supertonic3_worker.py`: native inference, asset hash, fail-closed.
- `voice_app/session_core.py`, `tts_chunks.py`: token, history dan ACK source.
- `web/`: capture, playback, lifecycle dan UI.

## Urutan untuk mencapai konfigurasi snapshot

1. Pasang dependency web dan dependency speech terisolasi.
2. Unduh model/roda paket yang dipin lalu verifikasi hash; tidak ada inference-time download.
3. Pilih local STT Zipformer dengan preview dan stateful mode.
4. Pilih Supertonic3 dengan asset asli yang dipin dan salinan conditioning B milik aplikasi.
5. Aktifkan automatic conditioning; biarkan numeric pacing OFF.
6. Pilih model/provider LLM remote dan autentikasinya di server.
7. Siapkan akun aplikasi sendiri; baru uji sesi berulang, transcript final, TTS, ACK, interrupt dan jawaban panjang.
8. Bila akan diakses publik, gunakan HTTPS/tunnel/reverse proxy terpisah dengan origin persis; listener origin tetap loopback dan auth tidak dimatikan.

README dan panduan instalasi berisi perintah tooling; dokumen rollout lain di root menyimpan penjelasan keputusan historis. Catatan historis yang menyebut path receipt tidak berarti semua log/rekaman produksi ikut di repository.

## Batas multilingual

Supertonic-3 dasarnya multibahasa; worker aplikasi ini masih memanggil `lang='id'` dan STT terpilih khusus Indonesia. Snapshot ini belum percakapan multilingual end-to-end. Pemilihan model LLM tidak mengubah kemampuan bahasa STT/TTS secara otomatis.
