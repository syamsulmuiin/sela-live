# Pembagian repository yang dikoreksi pengguna

## 1. Sela — snapshot sistem sekarang

Repository aplikasi saat ini (`sela-voice`): source seluruh alur mikrofon/STT → remote LLM API/Codex → Supertonic TTS → playback/ACK, beserta UI, auth, lifecycle dan dokumentasi reproduksi sampai keadaan snapshot. Ini acuan yang dipertahankan, bukan repository khusus TTS.

## 2. Sela Live — proyek pengembangan berikutnya

Nama proyek: **Sela Live**. Repository baru terpisah; scope juga **end-to-end dari STT sampai TTS dan interaksi live**, bukan “Sela-TTS” atau modul TTS saja. Komponen LLM tetap remote API/Codex, bukan LLM lokal. Supertonic menjadi dasar pengembangan TTS; pemilihan STT tidak otomatis berubah oleh penamaan ini.

Repository private `syamsulmuiin/sela-live` dibuat untuk proyek end-to-end terpisah ini. Komponen TTS quality12 telah dipasang ke Sela setelah pengujian dan review; status sinkronisasi source serta transaksi ada di `LIVE-INSTALL-STATE.json`. `syamsulmuiin/sela-voice` tetap snapshot acuan. Tahap snapshot/backup sebelumnya sudah selesai; fokus tersebut bersifat historis dan tidak membatasi pengembangan baru.

### Tahap aktif yang dipilih pengguna

Sela Live dikembangkan bertahap. **Tahap pertama menyediakan komponen TTS berbasis Supertonic untuk Sela yang sedang berjalan**, sambil mengaudit limiter durasi, panjang teks, pemotongan, jeda, conditioning dan parameter native. Ini pembatasan tahap implementasi, bukan penggantian tujuan akhir end-to-end. Sebutan pengguna `sera live`/`tts sera` pada permintaan terbaru dicatat sebagai konteks proyek yang sama; identifier engine dan path tidak dinormalisasi sembarangan.

Audit source tidak otomatis mengaktifkan backend baru di produksi. Jangan menghapus guard CPU/RSS/deadline/ukuran, mengganti speaker/model, menulis ulang tanda baca, atau menyatakan masalah prosodi selesai tanpa bukti. Uji pembanding harus memakai seluruh paragraf asli dan urutan yang sama. Workspace pengembangan baru dipisahkan dari produksi dan receipt perbaikan lama; status terpasang, kandidat uji, dan rencana integrasi wajib dibedakan.

Koreksi ini menggantikan pernyataan sebelumnya bahwa repository kedua hanya untuk TTS. Dokumen lama tentang penelitian TTS tetap referensi satu komponen di dalam cakupan Sela Live, bukan seluruh definisi proyek baru.
