# Sela Live natural_v1 — satu kebijakan otomatis

`natural_v1` sudah dipasang pada produksi melalui installer yang disetujui. Byte/mode/inventory, selector proses, health dan guard autentikasi sudah diperiksa; jalur Speech native dari root terpasang mereproduksi WAV B byte-exact. Status transaksi ada di `NATURAL-INSTALL-STATE.json` dan `NATURAL-POLICY-STATE.json`, bukan disimpulkan dari keberadaan source.

## Perubahan

- Satu masukan `SpeechRequest(text, delivery)` menggabungkan teks dinamis dengan metadata penyampaian AI yang sudah ada. Operator memilih kebijakan `natural_v1`, bukan menyetel jeda/speed per cerita.
- Jalur aplikasi `Speech.synthesize_chunks()` memakai planner yang memilih konteks SDK-kompatibel berdasarkan duration predictor Supertonic yang nyata. Utamakan konteks lengkap; pecah pada paragraf/kalimat/klausa ketika batas native mengharuskan. Pemisah paragraf mengikuti SDK persis.
- Sumber kata, urutan, tanda baca dan ACK dipertahankan; normalisasi pengucapan hanya pada salinan native. Bagian whitespace-only tidak membuat audio kosong. Tidak ada potongan sunyi internal atau tag emosi yang dimasukkan ke ucapan.
- Pilihan conditioning ditangkap sekali untuk seluruh respons. Anchor B, profil otomatis lama, quality12, seed20261026 dan speed1.05 tetap. Ini bukan model baru, training ulang atau pembuktian emosi independen.
- Slot aplikasi, quiescence ASR dan lock host persisten dimiliki selama planning/sintesis/antar-chunk. Worker DP direap sebelum graph TTS dialokasikan. Pembatalan berulang menunggu reaping; kegagalan cleanup tidak boleh mengaku kapasitas bebas.

## Batas internal, bukan slider pengguna

Permintaan tetap paling banyak4000 karakter. Setiap argumen/konteks native dibatasi800 karakter/3200 byte UTF-8,512 token hasil preprocessing SDK, prediksi paling lama28 detik dan audio nyata paling lama30 detik. Maksimal64 permintaan prediksi; penolakan token mengembalikan jumlah nyata tanpa durasi fiktif dan tanpa menjalankan DP di atas batas. Budget fallback tidak diklaim terverifikasi; guard native tetap wajib. Batas CPU/RSS/AS/network/output/deadline lama tidak dilonggarkan dan tidak ada engine fallback.

## Bukti

- Pengguna menerima sampel B dengan tanggapan `B ok`. WAV sumber B memiliki SHA256 `f1905590111e8f62657fa87a8c0da346974ff212af475920ba4cf8f87cd34bc6`.
- Cerita utuh yang sama: baseline9 konteks versus kandidat3 konteks model yang lebih utuh. Penerimaan ini bukan jaminan semua ucapan/emosi berikutnya.
- Jalur Speech dari relokasi staging **dan root produksi terpasang** mereproduksi PCM B **byte-exact**; tiga panggilan native pascapemasangan selesai, lock direbut kembali lalu dilepas dan readback produksi diulang.
- Dua giliran Codex/API nyata dan dua giliran shipping browser selesai, dengan playback/ACK penuh dan riwayat berlanjut. Android viewport390px tanpa overflow. Ini bukan pengujian mikrofon atau speaker fisik.
- DP-only preflight nyata berhasil dalam transient unit dengan properti sandbox/user/resource service produksi yang sama; unit uji dikoleksi dan service produksi tidak diubah.
- Detail sanitasi: `NATURAL-NATIVE-BROWSER-VERIFICATION.json`, `NATURAL-SERVICE-SANDBOX-VERIFICATION.json`, `NATURAL-INTEGRATION-REGRESSION.json`, serta review di `reviews/NATURAL-*`. Rekaman, account fixture, cookie dan receipt teks privat tidak dipublikasikan.

## Packaging dan operasi

`tools/build_live_runtime.py` menghasilkan payload baru dan manifest runtime eksak28 dependensi. Relokasi hanya mengubah tiga root manifest dan literal digest manifest pada dua worker; file DP worker tidak diubah. Worker/helper/model/profile tetap terpin. `voice_app/speech.py` dan bridge diikat terpisah oleh review/rencana instalasi.

`VOICE_SELA_LIVE_POLICY=natural_v1` adalah pemilih kebijakan internal. Digest worker, duration worker, source manifest dan runtime manifest merupakan input deployment server-owned, bukan parameter dari LLM/browser. Installer menyediakannya lewat drop-in terikat hash. Jangan mengaktifkan flag sendiri tanpa payload/plan yang cocok, dan jangan menyalin source worker tanpa rebinding.

Installer default read-only; hanya `--apply` pada plan yang disetujui melakukan observe→archive→stop→write→restart→readback. Snapshot baru mempertahankan baseline quality12. Rollback harus berhasil stop, memulihkan/mengecek seluruh baseline, lalu restart; kegagalan pemulihan meninggalkan status gagal/stopped, bukan restart tree campuran. Dependensi model/venv produksi tetap dipakai; fresh-host natural_v1 belum diklaim diverifikasi.
