# Keamanan, privasi dan batas backup

## Repository bukan backup rahasia/account

Yang disimpan: source aplikasi, web client, pengujian, salinan profil conditioning dan manifest/prosedur pemasangan. Model besar dan environment Python dipasang ulang dari sumber/manifest terverifikasi, bukan dimasukkan ke Git.

Yang sengaja tidak disimpan:
- API keys, OAuth access/refresh token, credential GitHub/Hermes, SSH private key.
- `.env` lokal, database akun/session aplikasi, bootstrap token.
- Riwayat chat, log produksi, rekaman mikrofon atau private fixtures.
- Seluruh direktori eksperimen, virtualenv dan model besar dari host produksi.

Maka `git clone` membuat instalasi aplikasi yang dapat dikonfigurasi, bukan menyalin login/akun/percakapan lama. Autentikasi LLM dan akun aplikasi harus dibuat pada host baru. Backup state privat, bila dibutuhkan, adalah prosedur terpisah terenkripsi dengan izin eksplisit, bukan commit Git.

## Pengoperasian

- Jangan commit secret meskipun repo private. `.gitignore` bukan scanner keamanan; periksa staged files sebelum push.
- API key hanya di server; gunakan prompt tersembunyi/environment untuk konfigurasi. Jangan taruh key di command-line, URL, browser JavaScript, screenshot, issue atau chat.
- HTTPS Git menggunakan credential helper resmi `gh`; jangan menaruh token dalam URL remote.
- Backend default tidak boleh diam-diam berubah menjadi LLM lokal. Tidak ada llama.cpp atau model bahasa lokal pada prosedur ini.
- Native speech memerlukan Linux network-namespace isolation/capability yang diuji. Bila sandbox tidak didukung, gagal dengan jelas; jangan mematikan guard untuk membuatnya jalan.
- Resource/admission/output/deadline/cancellation guards dipertahankan. Satu native worker yang dibatalkan harus tetap direap sebelum kapasitas dilepas.
- Jalankan web di 127.0.0.1; jangan membuka port origin langsung ke internet. Browser microphone remote memerlukan secure context HTTPS, kecuali localhost yang diizinkan browser.
- Public domain/origin dipilih operator; jangan menyalin domain produksi orang lain ke instalasi baru.
- Persetujuan browser online STT berbeda dari izin local STT; jangan mengirim audio ke vendor baru tanpa persetujuan eksplisit.

## Lisensi dan pihak ketiga

- Profil conditioning adalah turunan dari input style Supertonic; bukan suara bebas syarat lisensi. Copy terms model tersedia di `web/supertonic3-LICENSE.txt`; manifest mengikat source revision dan hash.
- SDK Supertonic, model Supertonic, runtime ONNX, model Zipformer dan dataset punya lapisan terms yang berbeda. Manifest/provenance tidak menghapus kewajiban terms masing-masing.
- Jangan menganggap repo private atau satu tautan lisensi cukup sebagai legal clearance distribusi/hosting. Tinjau paket dan kewajiban downstream sebelum publikasi luas.
- Tidak ada pemberian lisensi ulang menyeluruh atas semua komponen melalui snapshot ini.

## Proyek berikutnya: Sela Live

Repository ini menjaga aplikasi/current baseline sampai snapshot sekarang. Proyek baru bernama **Sela Live**, berada di repository terpisah, dan mencakup **seluruh alur STT → LLM API/Codex → TTS → playback/interaksi live**, bukan hanya engine TTS. Pengembangan TTS di dalam Sela Live tetap berbasis Supertonic sesuai arahan pengguna. Integrasi komponen/version pin harus direview; jangan mengganti aset baseline secara diam-diam. Model turunan tetap disebut turunan, bukan model yang seluruhnya diciptakan dari nol.
