# Perbaikan terarah: delivery dinamis pada jalur live

## Target

Perbaiki penyampaian balasan AI yang berubah-ubah, bukan membagi cerita contoh secara manual atau menetapkan emosi balasan tertentu. AI tetap menghasilkan teks dan metadata delivery melalui provider yang sudah dipilih. Jangan mengganti engine, speaker, prompt, registry, atau pengamanan produksi dalam perbaikan ini.

## Implementasi kandidat

`voice_app/tts_chunks.py` hanya memecah ketika batas native benar-benar terlampaui. Utterance yang masih muat tidak dipecah lebih awal. Saat overflow, utamakan akhir kalimat termasuk penutup kutipan/kurung, lalu batas klausa koma/titik dua/titik koma yang sudah ada bila memenuhi batas native; jika tidak tersedia, fallback terbatas sebelumnya tetap berlaku. Semua karakter sumber tetap dipertahankan untuk ACK. Tidak ada penambahan tanda baca, penggantian kata, pilihan emosi baru, atau angka tempo/jeda baru.

Teks panjang tanpa batas klausa yang sesuai masih dapat memerlukan pemisahan berbasis panjang. Perbaikan ini tidak membuktikan penekanan kata atau ekspresi emosional sudah alami.

## Verifikasi

- Tiga tes baru: delapan variasi penutup dialog, tiga tanda batas klausa, dan dua kasus negatif judul/atribusi dialog yang masih muat. Tiap perilaku terlihat gagal sebelum perbaikannya, kemudian lulus.
- 44 tes regresi terkait chunk, continuity, conditioning dan delivery lulus; bersama tiga tes baru menjadi 47.
- 22 setup/layout dan 121 tes Node lulus.
- Dua balasan baru dari Codex yang sebenarnya dikirim lewat WebSocket loopback terautentikasi ke Supertonic native. AI memilih `joy/low` dan `trust/low`; request worker menerima `conditioning_b_active_low` dan `conditioning_b_calm_low` tanpa tag metadata masuk teks. Teks respons dan source ACK sama persis.
- Probe final revisi overflow-only memakai dua balasan dinamis, masing-masing dua chunk native, pada satu WebSocket/session. Keempat chunk menerima source ACK yang cocok. Ini pemeriksaan protokol, bukan bukti playback browser.
- Probe jaringan terautentikasi memakai ACK mesin setelah menerima WAV. Pemeriksaan terpisah pada shipping UI/browser dengan provider dan native TTS nyata menyelesaikan dua turn pada satu WebSocket: tiga WAV didecode, tiga playback mulai/berakhir, dan tiga ACK asli setelah playback cocok dengan source lengkap. Fixture browser bypass login lokal secara eksplisit; bukan bypass autentikasi produksi. Lebar390px tidak overflow, tidak ada error browser. Satu jeda antar-chunk terukur306.5ms; bukan gapless. Mikrofon fisik, speaker fisik, penekanan kata, dan kualitas subjektif belum diverifikasi.
- Akses konfigurasi tanpa autentikasi menghasilkan401. Server probe dihentikan; hash source produksi tidak berubah.

## Status

Perbaikan satu file `voice_app/tts_chunks.py` sudah dipasang pada produksi `/root/voice-lab` setelah review runtime dan pengaman pemasangan terpisah. Hash terpasang `57a4b5d90e2b5096fe0cf7b561495c0592841115b06174e5a0bc9134b6490d26`, mode0644; file aplikasi lain, aset style, web dan unit layanan tetap cocok dengan baseline terlindungi. Tiga tes boundary dijalankan ulang terhadap modul terpasang dan lulus. Layanan aktif; domain publik health200, konfigurasi tanpa login401 dan WebSocket tanpa login403. Playback terautentikasi di domain produksi belum diuji karena tidak ada login tersimpan; akun produksi tidak dibuat/reset. Uji browser dan native dinamis di atas dilakukan pada clone terisolasi dengan source runtime yang sama. Clone `/root/sela-live-fix` dan upstream `/root/supertonic-live-fix` tetap terpisah; perubahan belum dipush ke GitHub. Penerimaan intonasi/kualitas subjektif tetap belum terbukti. Receipt privat: `/root/sela-live-fix-evidence/deploy/{receipt.json,post-readback.json}`.
