# Verifikasi dan arti “siap clone”

## Bukti yang sudah ada

Snapshot source ini diambil dari aplikasi produksi yang sudah menjalankan Zipformer Indonesia + remote API/Codex + Supertonic3 dengan automatic conditioning di sekitar B. `PRODUCTION-SOURCE-MANIFEST.json` mengikat byte source/aset yang disalin. Tool setup/dokumen yang ditambahkan kemudian bukan bukti perubahan runtime produksi.

Pengujian parent pada `/root/sela-voice` sebelum publikasi awal:
- Python discovery: 225 run, 221 pass, 4 skip opt-in.
- Node shipping client: 121 pass, 0 fail.
- Pengujian ini berjalan pada host yang sudah mempunyai dependency native dan interpreter Hermes. Bukan bukti instalasi pada komputer kosong.

Acceptance produksi sebelumnya, terpisah dari hasil snapshot:
- Tiga turn jaringan nyata dengan Codex dan native speech, termasuk fixture audio manusia berlisensi untuk STT; metadata dipisah dari ucapan, profile selection mencapai worker, ACK/history cocok, ASR worker dipertahankan.
- Browser nyata: tiga turn satu sesi, active-high → calm-medium → B netral; 7 chunk dimainkan dan di-ACK, balasan panjang sekitar55detik. Provider browser scripted, remoteCodex diuji terpisah.
- Public health200/config401/login200/anonymousWS403.
- Tidak membuktikan emosi terlatih, kualitas subjektif semua kategori, mikrofon/speaker fisik semua perangkat, atau aplikasi native Android/Windows.

## Uji clone source terpisah

Parent membuat clone Git bersih dari commit baseline `27810ec89072593749c0a51a87f3370f57329f3c` pada host yang sama. Hasil aktual:
- 40 tes auth/protocol/delivery/chunking lulus; 121 tes Node shipping client lulus.
- Disposable authenticated network fixture menjalankan session start yang benar, memanggil native Supertonic dengan profil `conditioning_b_active_high`, menghasilkan WAV 44100 Hz mono PCM16 sekitar 4,25 detik, dan mencocokkan seluruh teks dengan source ACK/history. Fixture dihentikan dan auth disposable dihapus.
- Provider fixture scripted, tidak ada panggilan LLM eksternal; bukan pengujian speaker fisik/browser playback.
- Model dan speech venv berasal dari dependency terverifikasi yang sudah ada pada host. Ini **bukan fresh-host install** dan bukan pengujian launcher canonical di arbitrary clone path.

## Relokasi tes terstruktur

Semua 39 file tes/helper awal dipindahkan ke `tests/python`, `tests/web`, dan `tests/setup`. Audit AST Python dan pembalikan perubahan path Node memverifikasi fungsi/asersi/subtest asli tetap utuh. Hasil eksekusi baru: 19 tes setup asli + 3 tes layout/inventory = 22 pass, Node121 pass, subset protocol40 pass dan stateful-worker8 pass. Discovery aplikasi menemukan225 ID unik tanpa error import; full225 belum dijalankan ulang oleh tugas relokasi. Inventory migrasi terpisah ada di `tests/move-inventory.json`.

```bash
python3.12 -B -m unittest discover -s tests/setup -t . -v
node --test tests/web/*.test.cjs
# Full aplikasi, butuh dependency/native inference — bukan default setup test
PYTHONPATH=tests/python python -B -m unittest discover -s tests/python -t . -v
```

## Delta audio continuity — 29 Oktober 2026

Tiga file continuity disalin byte-exact dari versi produksi yang sudah terpasang; bukan deployment baru oleh tugas package ini. Detail ambang dan batas klaim: [AUDIO-CONTINUITY.md](AUDIO-CONTINUITY.md). Punctuation sudah digunakan sebelumnya; trimming hanya edge floor `abs(PCM) <= 1` (minimum250ms, keep120ms, cap750ms per edge), bukan penghilangan seluruh jeda. ACK/transport floor tetap ada; tidak ada perubahan voice atau pembuktian listening fisik.

Bukti browser terdahulu adalah replay capture native: tiga chunk, source ACK exact setelah natural end, dan cancellation tanpa false ACK/successor. Replay itu tidak melakukan fresh synthesis, STT atau remote LLM. Bukti tersebut bukan hasil menjalankan browser ulang pada sinkronisasi package ini.

Hasil aktual sinkronisasi lokal: **22 setup/layout pass, 40 safe protocol pass, 11 continuity pass, 121 Node pass**, tanpa skip/failure. Interpreter dipilih setelah probe import SDK Hermes + FastAPI/HTTPX, bukan diasumsikan dari nama venv. Full native suite tidak dijalankan; tidak ada service, commit atau push. Perintah dari root checkout (`SDK_PYTHON` = interpreter SDK aktual operator):

```bash
PYTHONDONTWRITEBYTECODE=1 "$SDK_PYTHON" -B -m unittest discover -s tests/setup -t . -v
node --test tests/web/*.test.cjs
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests/python "$SDK_PYTHON" -B -m unittest tests.python.test_auth tests.python.test_chunked_tts tests.python.test_delivery_protocol tests.python.test_automatic_conditioning_server tests.python.test_browser_choice tests.python.test_response_stages -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests/python "$SDK_PYTHON" -B -m unittest tests.python.test_audio_continuity -v
```

`PYTHONPATH=tests/python` hanya menyelesaikan import fixture legacy dalam tes continuity yang wajib disalin persis; tidak menambah test shim/discovery ganda. TDD layout: setelah file tes baru ditambahkan, layout lama menjalankan3 tes dengan1 failure karena file itu di luar inventori; setelah allowlist hashbound terpisah, 22 setup/layout pass. Inventori migrasi39 file dan source manifest historis tidak diubah. Delta source beserta hash receipt historis terikat di `CONTINUITY-DELTA-MANIFEST.json`; log/fixture/audio/auth privat tidak disertakan.

## Lapisan pemeriksaan di instalasi baru

1. **Source dan setup unit tests:** pastikan konfigurasi dibaca sebagai data (bukan shell), secret tidak keluar, default bootstrap tidak menulis/mengunduh, dan guard path/hash bekerja.
2. **Asset verification:** cocokkan seluruh model/package/profile hash; hash mismatch wajib gagal, tidak diganti latest otomatis.
3. **Native readiness:** jalankan check worker pada sandbox yang sama; periksa network isolation dan resource admission. Asset hash yang benar belum membuktikan kernel mengizinkan worker.
4. **Provider:** pilih API atau Hermes/Codex dan kredensial sendiri. `doctor` tanpa inference tidak membuktikan quota/izin/model provider masih tersedia. Live test memakai quota dan harus dipilih eksplisit.
5. **Auth dan web:** `/health`200, config401 sebelum login, akun aplikasi dibuat dengan bootstrap-token baru, Origin/Host mismatch ditolak.
6. **Percakapan:** ucapkan satu turn normal, tunggu audio selesai, lakukan turn kedua pada sesi yang sama; cek konteks, final-only dispatch, interrupt dan tidak ada late audio.
7. **Long reply:** pastikan seluruh teks mempunyai ordered source span dan final ACK; bukan hanya file WAV valid.
8. **Publik:** ulangi auth checks melalui HTTPS domain milik operator. Jangan menganggap localhost berhasil berarti tunnel/domain benar.

Perintah persis ditulis di README dan panduan pemasangan sesuai CLI tooling yang disertakan. Pengujian berat/manual tidak berjalan otomatis saat `git clone` atau memulai server. Empat skip pada suite acuan tidak boleh dihitung sebagai empat test lulus.

## Menjaga rollback

Jangan menimpa instalasi yang sedang aktif dengan bootstrap fresh-install. Simpan revision sebelumnya, environment privat secara aman, manifest model, dan snapshot state terpisah jika memang diotorisasi. Restore seluruh source/config sebagai satu transaksi; jangan mencampur worker versi lama dengan pinned helper versi baru. Setelah restart, baca kembali config efektif dan uji flow sebelum menyebut deploy berhasil.
