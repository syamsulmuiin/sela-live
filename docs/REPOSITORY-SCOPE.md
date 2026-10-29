# Pembagian repository yang dikoreksi pengguna

## 1. Sela — snapshot sistem sekarang

Repository aplikasi saat ini (`sela-voice`): source seluruh alur mikrofon/STT → remote LLM API/Codex → Supertonic TTS → playback/ACK, beserta UI, auth, lifecycle dan dokumentasi reproduksi sampai keadaan snapshot. Ini acuan yang dipertahankan, bukan repository khusus TTS.

## 2. Sela Live — proyek pengembangan berikutnya

Nama proyek: **Sela Live**. Repository baru terpisah; scope juga **end-to-end dari STT sampai TTS dan interaksi live**, bukan “Sela-TTS” atau modul TTS saja. Komponen LLM tetap remote API/Codex, bukan LLM lokal. Supertonic menjadi dasar pengembangan TTS; pemilihan STT tidak otomatis berubah oleh penamaan ini.

Penamaan repository GitHub proyek baru dapat memakai `sela-live`; belum dibuat hanya karena dokumen ini. Fokus pekerjaan berjalan sekarang tetap menyelesaikan snapshot/reproduksi dan upload repository sistem yang sudah ada. Jangan mengganti tugas backup dengan pembangunan Sela Live atau mengubah produksi secara diam-diam.

Koreksi ini menggantikan pernyataan sebelumnya bahwa repository kedua hanya untuk TTS. Dokumen lama tentang penelitian TTS tetap referensi satu komponen di dalam cakupan Sela Live, bukan seluruh definisi proyek baru.
