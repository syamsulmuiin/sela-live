# Audit limiter Sela: batas waktu bukan kontrol jeda per kata

## Scope dan bukti

Tahap aktif: Sela Live menyediakan TTS lebih dahulu, sedangkan STT, LLM remote dan aplikasi Sela yang berjalan tetap dipertahankan. Tujuan akhir tetap percakapan Live end-to-end. Kode eksperimen berada di `/root/sela-live`, SDK clone di `/root/supertonic-live-fix`; tidak ada pengalihan produksi dalam audit ini.

Sumber GitHub SDK diperiksa pada commit `df0f9686dac7fbbde391b759e2ee5286a3737622`; empat file inti dari Contents API cocok byte demi byte dengan clone dan SDK yang terpasang. Bukti mesin ada di `UPSTREAM-CONTENT-VERIFICATION.json`. Tidak mengunduh model atau mengubah versi aset terpin.

## Peta batas aplikasi yang terpasang

- `voice_app/tts_chunks.py:20,27,39-41`: target normalisasi180 karakter per potongan; batas balasan4000 karakter; sumber native800 karakter/3200 byte. Ini membatasi konteks yang diberikan ke model. Bisa memengaruhi prosodi melalui perubahan konteks, tetapi bukan kode yang menyisipkan jeda setelah kata tertentu.
- `voice_app/supertonic3_worker.py:81-92`: validasi teks/tag, bukan pengatur intonasi. Tidak menambahkan koma ke `bangku depan warung` atau `pucuk jambu`.
- `supertonic3_worker.py:187-191`: seed20261026, langkah inferensi8, speed1.05. Tanpa delivery numerik, SDK memakai silence_duration0.3. Angka langkah adalah iterasi pembentukan audio, bukan detik atau batas durasi ucapan.
- `supertonic3_worker.py:195,262`: lebih dari44100×30 sampel atau2,646,044 byte WAV menyebabkan error. Ini batas keluaran per worker, bukan pemotongan diam-diam dan bukan batas seluruh percakapan. Sampel69detik sebelumnya diterima melalui tujuh potongan.
- `supertonic3_worker.py:237`: CPU60/65detik, address-space3GiB, alarm75detik dan file-size4,000,000byte. Ini pengamanan komputasi/proses, bukan tempo suara. Penghapusan guard tidak mengajarkan prosodi pada model.
- `voice_app/speech.py:564-602`: jalur Supertonic memakai operasi lokal dengan timeout90detik dan serialisasi/cleanup. Konstanta `TTS_TIMEOUT=45` di file yang sama dipakai jalur Edge, sehingga tidak boleh dilaporkan sebagai batas durasi Supertonic.
- `voice_app/server.py:59`: timeout ACK60detik per potongan. ACK yang benar tetap setelah audio diputar. Ia dapat menimbulkan lantai jeda antar-potongan melalui transport, tetapi tidak menambahkan jeda di tengah satu WAV.
- `voice_app/audio_continuity.py`: hanya tepi WAV dengan floor PCM absolut≤1 yang memenuhi syarat dipangkas; tidak mengubah jeda internal. Jangan memperluasnya menjadi pemotong semua bagian berlevel rendah karena dapat merusak bunyi kata.
- `voice_app/delivery.py:81-91` dan prompt server: panduan maksimal tiga kalimat memengaruhi susunan jawaban LLM. Ini terpisah dari kemampuan TTS mengucapkan tanda baca. Tidak diubah pada tahap ini.

## Peta kontrol SDK upstream

- `supertonic/config.py:200-211,243`: default steps8, speed1.05, max_chunk_length300 (Korea120), silence_duration0.3; speed0.7–2.0, steps1–100, input maksimum100000 karakter pada API tingkat tinggi. Aplikasi tetap memakai batas worker yang lebih kecil.
- `pipeline.py:261-319`: pemotongan SDK terjadi sebelum panggilan model; silence_duration menyisipkan array nol hanya antar-potongan SDK. Ini bukan pengatur semua koma atau jarak antar-kata.
- `core.py:502-512`: duration predictor menerima text_ids, style_dp, text_mask; durasi hasilnya dibagi speed. Tidak ada clamp per-kata pada jalur Python ini.
- `core.py:512-529`: noisy latent, text embedding dan style_ttl masuk ke vector estimator sejumlah total_step, lalu vocoder membuat WAV. Inilah pembentukan audio yang dipelajari; menambah langkah bukan kontrol eksplisit emosi atau jaminan jeda tepat.
- Representasi style mempunyai `ttl` dan `dp`. Ia bukan sekadar nama speaker, tetapi keberadaannya juga bukan bukti emosi tertentu dapat dikontrol secara andal.

`NATIVE-CONTROL-FLOW-PROBE.json` menguji alur Python dengan fixture sintetis, bukan inference model: silence0.3 menambahkan13230 sampel pada dua potongan SDK dan tidak mengubah satu potongan; steps8/12 mengubah jumlah loop tetapi tidak durasi predictor ketika speed/input/style sama. Hasil ini membedakan mekanisme kode, bukan membuktikan suara sudah alami.

## Hubungan dengan keluhan pendengar

Sampel paragraf asli: `bangku depan warung` tetap utuh di satu potongan, termasuk setelah preprocessing SDK. Ada interval berlevel rendah sekitar0.32detik dalam WAV. Pada uji konteks satu kalimat, keluhan berpindah ke `pucuk ... jambu`, sekitar detik13 dalam WAV23.34detik; ada interval berlevel rendah sekitar0.35detik. Uji konteks tersebut ditolak dan belum dipasang. Tidak ada cap30detik atau ACK yang terpicu pada dua lokasi internal ini.

Jadi belum ada bukti bahwa menghapus limiter waktu akan memperbaiki masalah tersebut. Kandidat yang perlu diuji adalah kualitas pembentukan audio/konteks native dengan kontrol lain tetap, bukan penghilangan guard atau pengeditan tanda baca satu frasa.

## Uji tahap berikutnya yang dibatasi

Gunakan seluruh paragraf148kata yang sama, urutan dan tujuh source-part yang sama. Pastikan baseline8 dari SDK clone identik byte dengan native capture sebelumnya. Setelah itu ubah satu variabel, langkah8→12, tetap profile conditioning_b, seed20261026, speed1.05 dan silence0.3; jangan mengubah teks, speaker atau PCM internal. Periksa kedua frasa yang ditandai dan jeda baru di seluruh paragraf. Jika jeda hanya berpindah, hasil tetap gagal untuk naturalness.

Status saat dokumen ini ditulis: enam tes unit kandidat,22 setup/layout dan preflight worker terisolasi lulus. Preflight aset/network-denial bukan bukti inference/parity native atau penerimaan suara; keduanya harus dicatat terpisah sebelum aktivasi TTS Live di Sela.
