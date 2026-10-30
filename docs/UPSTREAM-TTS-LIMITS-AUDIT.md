# Audit upstream Supertonic — durasi, waktu, jeda, dan intonasi

Tanggal pemeriksaan UTC: `2026-10-30T15:26:15.093457+00:00`.

## Kesimpulan terbatas

**Tidak ditemukan explicit per-word pause limiter, emotion/pitch slider, atau SSML parser pada Python wrapper/reference yang diaudit.** Ada guard yang menyebabkan hard failure, modifier teks/chunk/join, serta duration/style/noise yang mengondisikan model; ketiganya harus dibedakan. Lihat temuan `absent-prosody-api`, `speed`, `predicted-duration`, dan `silence` beserta filename:line di bawah.

Audit ini **bukan inference model, bukan listening test, dan bukan bukti acoustic fix**. Hanya dua berkas laporan ini dibuat oleh audit. Tidak mengubah production source/service, assets, konfigurasi Hermes, speaker, atau teks pengguna; tidak push Git. Scope Sela Live tetap end-to-end pada akhirnya, TTS-first untuk tahap aktif. Sumber scope lokal dicantumkan dalam ledger.

## Provenance: dibuktikan, bukan asumsi newest

- SDK local HEAD dan GitHub `main` yang dibaca langsung: `df0f9686dac7fbbde391b759e2ee5286a3737622`; origin `https://github.com/supertone-oss-archive/supertonic-py.git`. Local status awal bersih.
- Native repository GitHub `main` yang dibaca langsung: `1e9799e964ea4c0dad7cde993b65c3c813a7b373`; pemanggilan repo lama `supertone-inc/supertonic` mengembalikan canonical `supertone-oss-archive/supertonic`.
- GitHub contents API pada commit tepat dipakai mengambil bytes penuh. Setiap Git blob SHA1 dihitung ulang dengan header `blob <byte-length>\0` lalu dibandingkan response API; SHA256 bytes juga dihitung. Empat file wajib SDK dan loader local semuanya byte-identical remote. Tidak git fetch/pull/checkout atau clone baru.
- Keadaan main ini hanya snapshot saat pemeriksaan, bukan janji remote tidak akan bergerak. Source commit ≠ model artifact revision. Receipt parent dikonfirmasi hash-consistent; klaim installed-SDK-identical dari parent dilabeli belum diverifikasi ulang path installed oleh audit ini.

### Hashmap sumber

| ID | File | Lines | SHA256 | Local vs pinned remote |
|---|---|---:|---|---|
| [1] | `supertonic/config.py` | 247 | `9684abef9023d305b87d8b511a5c955624f5b7a92c76b57aa8b3e06f8e18ce2e` | True |
| [2] | `supertonic/pipeline.py` | 415 | `13b16fb8556632b5985f8ba1e961096ca1ad921d01aed171972dd106df51a7cd` | True |
| [3] | `supertonic/core.py` | 529 | `26664d490509edc1cc1feca2fc0f72b1ae067fd5c5b11d39c1daeba9c3e81ff4` | True |
| [4] | `supertonic/utils.py` | 217 | `8bf2e0a14783de9266edba1135597e6b77b143907e445b08dcc45f694ff51ed3` | True |
| [5] | `supertonic/loader.py` | 462 | `9478b085ccae88fd44849a87d8a55d0ddada03e4fbb7f47f1707f04ac06e621f` | True |
| [6] | `py/helper.py` | 429 | `b14b1da8a018aa4db6ca8cbb927c32590bfc889b49116ea344c56e8351d94279` | local evidence / native remote only |
| [7] | `py/example_onnx.py` | 116 | `2ef5371dc02b9425b130ecc480abeb4714781fabe2059e4f9c68dd5e546dec49` | local evidence / native remote only |
| [8] | `py/example_pypi.py` | 22 | `edadd0110ddc564f9903d8609404e5389ac4f5f0b32d7d6e6622be815df27b04` | local evidence / native remote only |
| [9] | `py/README.md` | 157 | `799e439ca614506c9d76ba2043ec28f3be6bf09ecffa98d88a3e0267ede1a770` | local evidence / native remote only |
| [10] | `/root/voice-lab/autonomous/expressive-tts/assets/onnx/tts.json` | 311 | `42078d3aef1cd43ab43021f3c54f47d2d75ceb4e75f627f118890128b06a0d09` | local evidence / native remote only |
| [11] | `/root/sela-live/docs/UPSTREAM-CONTENT-VERIFICATION.json` | 34 | `1061079bdad591ac53326ea3ab7a5b910103ed7da09d66cced95da4648d89fed` | local evidence / native remote only |
| [12] | `/root/sela-live/docs/NATIVE-CONTROL-FLOW-PROBE.json` | 39 | `d512323baf17a9fceecc974940ac3f0d8b319bfaf9c483ce40b13cbdae55f6bf` | local evidence / native remote only |
| [13] | `/root/sela-live/tools/audit_native_controls.py` | 53 | `9a2bc6ec41ca918af63bcffe0a39d1dafbe38be74ad7de759c76c7c4bcce8082` | local evidence / native remote only |
| [14] | `/root/sela-live/docs/REPOSITORY-SCOPE.md` | 19 | `85c365b0fea055e3f5d2f5904f0b1206b61f2b5007fd58e752ef79dfc9960127` | local evidence / native remote only |

Git blob SHA1, ukuran byte, URL commit immutable, dan SHA256 lengkap juga tersedia pada JSON `source_hashmap`. Semua empat file SDK wajib dibaca penuh (247/415/529/217 lines); helper native429, example_onnx116 dan example_pypi22 juga dibaca penuh.

## Klasifikasi dan inventaris lengkap

`hard_failure` = reject/error, bukan pemangkasan suara; `content_modifier/postprocess` = input atau waveform diubah; `modeled_prosody` = conditioning/keluaran neural, tidak setara kontrol pause langsung. Bounds yang tidak ada disebut eksplisit; tidak berarti aman melepas guard aplikasi.

### text-call-limit — Batas teks satu panggilan SDK
- **Kelas:** `hard_failure`.
- **Default:** MAX_TEXT_LENGTH=100_000
- **Bounds:** len(raw text) > 100000 → ValueError; 100000 lolos guard ini
- **Efek kode:** Diperiksa sebelum chunking/normalisasi. Empty atau whitespace-only ditolak. Core langsung dan native helper tidak punya guard ini. Bukan batas detik atau per kata.
- **Observasi statik:** Guard length bukan pemotongan/truncation; lolos guard bukan jaminan model/resource mampu.
- **Sumber filename:line:** `supertonic/config.py:242-243`[1] `supertonic/pipeline.py:204-206`[2] `supertonic/pipeline.py:235-239`[2] `py/helper.py:177-254`[6]
- **Check terkait:** `text_limit_comparison`.

### chunk-target — Target chunk bukan hard maximum
- **Kelas:** `content_modifier`.
- **Default:** SDK: 300; ko:120; max_chunk_length=None memilih otomatis. Native: ko/ja:120, lainnya:300
- **Bounds:** SDK max_len >=10; tanpa upper bound eksplisit atau integer/finite check. Native chunk_text tidak punya minimum
- **Efek kode:** Split paragraf dua newline atau lebih; split kalimat .!? diikuti whitespace dengan pengecualian singkatan; pack kalimat utuh. Kalimat terlalu panjang tetap utuh, tidak split kata atau paksa potong karakter. Klausa koma tidak dipecah. +1 pada packing juga dihitung saat chunk kosong.
- **Observasi statik:** SDK dan native benar-benar mengembalikan satu chunk 301 karakter pada target300; penurunan target tidak memecah kalimat tunggal. Default Japanese berbeda antara SDK dan native.
- **Sumber filename:line:** `supertonic/config.py:203-204`[1] `supertonic/pipeline.py:261-268`[2] `supertonic/utils.py:17-40`[4] `supertonic/utils.py:154-217`[4] `py/helper.py:229-230`[6] `py/helper.py:388-429`[6]
- **Check terkait:** `sdk_oversize_sentence`, `native_oversize_sentence`, `sdk_min_chunk_9`, `sdk_min_chunk_10`, `native_no_chunk_min`, `sdk_comma_not_boundary`, `native_comma_not_boundary`, `sdk_sentence_packing`, `native_sentence_packing`.

### chunk-boundary — Boundary paragraf dan preprocessing per chunk
- **Kelas:** `content_modifier`.
- **Default:** Selalu aktif
- **Bounds:** Tidak ada toggle preserve-whitespace pada API ini
- **Efek kode:** Paragraf tidak digabung walau target panjang cukup. Semua chunk disintesis independen; text processor menormalkan masing-masing chunk dan menambah titik jika perlu. Memperbesar target tetap tidak menghapus boundary paragraf.
- **Observasi statik:** Dua paragraf Satu./Dua. tetap dua chunk pada target300; newline tunggal dalam satu chunk menjadi spasi. Ini bukan state prosodi kontinu antarchunk.
- **Sumber filename:line:** `supertonic/utils.py:183-215`[4] `supertonic/pipeline.py:284-291`[2] `supertonic/core.py:215-219`[3] `supertonic/core.py:344-345`[3] `py/helper.py:233-234`[6] `py/helper.py:401-429`[6]
- **Check terkait:** `sdk_paragraph_boundary`, `native_paragraph_boundary`, `sdk_newlines_collapse`, `native_newlines_collapse`.

### speed — Speed: guard vs durasi model
- **Kelas:** `hard_failure_and_modeled_timing`.
- **Default:** 1.05 (SDK high/low, native)
- **Bounds:** SDK core menerima inklusif [0.7,2.0], di luar → ValueError; native tanpa range guard. NaN lolos perbandingan SDK; bukan input valid untuk eksperimen
- **Efek kode:** duration_predictor menghasilkan dur_onnx, lalu dur_onnx / speed sebelum ukuran latent dan masking. Tidak resample/time-stretch WAV yang sudah jadi.
- **Observasi statik:** speed memperbesar/memperkecil target total durasi, bukan maximum pause per kata. Tidak dapat disimpulkan pitch konstan atau prosodi membaik.
- **Sumber filename:line:** `supertonic/config.py:201-211`[1] `supertonic/core.py:467-468`[3] `supertonic/core.py:495-512`[3] `py/helper.py:177-198`[6] `py/example_onnx.py:29-34`[7]
- **Check terkait:** `speed_guard_nan`, `speed_guard_endpoints`.

### steps — total_steps / total_step
- **Kelas:** `hard_failure_and_compute`.
- **Default:** SDK TTS=8; SDK core langsung=5; native CLI=8; native helper wajib arg total_step
- **Bounds:** SDK high-level [1,100]; tidak memeriksa isinstance int (1.5/True lolos guard). Core langsung/native tidak membatasi min/max
- **Efek kode:** range(total_step) memanggil vector estimator berulang; current_step dan total_step float32 dikirim ke ONNX. Tidak menentukan target durasi: DP terjadi sebelum loop.
- **Observasi statik:** Float1.5 kemudian gagal range; direct0/negatif berarti loop kosong menurut Python, bukan mode yang tervalidasi. Klaim kualitas lebih baik dari komentar bukan hasil akustik audit.
- **Sumber filename:line:** `supertonic/config.py:201-211`[1] `supertonic/pipeline.py:247-251`[2] `supertonic/core.py:467`[3] `supertonic/core.py:504-528`[3] `py/helper.py:182-214`[6] `py/example_onnx.py:26-28`[7]
- **Check terkait:** `step_guard_types`, `noninteger_steps_range`.

### silence — silence_duration adalah zero gap antarchunk
- **Kelas:** `hard_failure_and_postprocess`.
- **Default:** 0.3 detik
- **Bounds:** SDK hanya <0 ditolak; tidak ada upper/finite guard. Native tanpa guard eksplisit. NaN/Infinity akhirnya bermasalah pada int; nilai sangat besar berisiko alokasi
- **Efek kode:** SDK membentuk zeros((1,int(silence_duration*sample_rate)),float32), disisipkan hanya antara chunk; tidak sesudah terakhir. Bahkan satu chunk SDK tetap mengalokasikan silence sementara walau tidak dipakai. Native mengalokasikan hanya saat join berikutnya.
- **Observasi statik:** Jika satu chunk, silence_duration tidak mengubah waveform join (namun alokasi SDK tetap terjadi). Tidak menyentuh jeda internal model.
- **Sumber filename:line:** `supertonic/config.py:205`[1] `supertonic/pipeline.py:253-254`[2] `supertonic/pipeline.py:306-320`[2] `py/helper.py:224`[6] `py/helper.py:233-244`[6]
- **Check terkait:** `silence_guard`.

### predicted-duration — Duration predictor dan style_dp
- **Kelas:** `modeled_prosody`.
- **Default:** Output ONNX; bukan angka default Python
- **Bounds:** Tidak ada duration min/max/clip/isfinite/nonnegative guard di wrapper yang dibaca
- **Efek kode:** DP input text_ids/style_dp/text_mask → dur_onnx; dibagi speed; dipakai menyiapkan latent. Tidak ada target duration API per kalimat/per kata.
- **Observasi statik:** Wrapper tidak menunjukkan explicit limiter 23/69 detik atau durasi kata. Validitas/batas internal graph DP belum diperiksa. Mencabut guard bukan solusi yang ditunjukkan source.
- **Sumber filename:line:** `supertonic/core.py:502-512`[3] `py/helper.py:188-198`[6]

### latent-length — Length allocation / integer quantization
- **Kelas:** `length_quantization`.
- **Default:** R=cfg.ae.sample_rate; B=cfg.ae.base_chunk_size; C=cfg.ttl.chunk_compress_factor; D=cfg.ttl.latent_dim
- **Bounds:** Tidak ada max/min allocation length eksplisit; casts int64/int32 membawa batas representasi, bukan guard keselamatan
- **Efek kode:** wav_lengths=int64(duration*R); Q=B*C; latent_len=int32((duration.max()*R+Q-1)/Q); channel=D*C. Ini ceil-style expression berbasis float, bukan jaminan mathematical ceil untuk semua fractional sample.
- **Observasi statik:** Nilai negatif/tidak finite/overflow tidak disanitasi. Tidak menjalankan alokasi model; formula saja diverifikasi statik.
- **Sumber filename:line:** `supertonic/core.py:434-438`[3] `supertonic/core.py:451-460`[3] `py/helper.py:156-175`[6]

### mask — Text/latent masks bukan pemotong kata
- **Kelas:** `length_mask`.
- **Default:** length_to_mask max_len=None → lengths.max(); max_len=0 juga fallback karena or
- **Bounds:** Mask ids < lengths; tidak ada duration cap
- **Efek kode:** latent_lengths=(wav_lengths+Q-1)//Q. Noisy latent dikali latent_mask sekali; mask juga input VE. Text IDs di-zero-pad sampai panjang teks terpanjang yang sudah dinormalisasi.
- **Observasi statik:** Zero di noise luar mask bukan bukti WAV tail dipaksa nol. Tidak ada final WAV multiply-mask/tail-zero di return core.
- **Sumber filename:line:** `supertonic/core.py:81-105`[3] `supertonic/core.py:344-353`[3] `supertonic/core.py:451-460`[3] `supertonic/core.py:519-525`[3] `py/helper.py:257-280`[6]

### seed-noise — Seed dan noise
- **Kelas:** `stochastic_conditioning`.
- **Default:** np.random.randn(...) standard normal, cast float32; tidak seed di wrapper
- **Bounds:** Tidak ada seed/temperature/noise_scale API pada signature yang dibaca
- **Efek kode:** Memakai NumPy global RNG; pengulangan panggilan mengonsumsi state baru. Noise dibentuk sesuai latent_len, lalu masking. Native --n-test=4 hanya repeat, bukan seed fixed.
- **Observasi statik:** Kontrol seed masa depan harus eksplisit pada isolated harness. Seed yang sama pada chunk layout/shape berbeda tidak menjamin noise identik per segmen atau reproducibility backend penuh.
- **Sumber filename:line:** `supertonic/core.py:451-460`[3] `supertonic/core.py:463-470`[3] `supertonic/pipeline.py:158-168`[2] `py/helper.py:161-175`[6] `py/example_onnx.py:35-37`[7] `py/example_onnx.py:98-108`[7]

### style — style_ttl dan style_dp bukan slider emosi
- **Kelas:** `modeled_prosody`.
- **Default:** Wajib Style dari speaker terpilih; tidak ada default voice pada synthesize
- **Bounds:** SDK Style hanya ndarray type-check; tidak clip/norm/bound angka. Core memeriksa jumlah teks vs ttl batch, tidak memverifikasi dp shape penuh
- **Efek kode:** style_dp hanya ke DP; style_ttl ke text encoder dan vector estimator. Loader JSON dims/data → float32 reshape, bukan normalisasi vektor. Native sama routingnya.
- **Observasi statik:** Dapat mengondisikan timing/karakter suara tetapi tidak ada arti sumbu atau rentang emosi yang didokumentasikan oleh code ini. Ganti/interpolasi dp/ttl bukan kontrol pause yang tervalidasi dan berisiko mengubah identitas speaker.
- **Sumber filename:line:** `supertonic/core.py:356-379`[3] `supertonic/core.py:489-505`[3] `supertonic/core.py:508-525`[3] `supertonic/loader.py:398-425`[5] `supertonic/utils.py:133-151`[4] `py/helper.py:134-137`[6] `py/helper.py:190-211`[6] `py/helper.py:339-367`[6]

### normalization — Normalisasi teks dan tanda baca
- **Kelas:** `content_modifier`.
- **Default:** Selalu NFKD + rangkaian cleanup
- **Bounds:** Tidak ada opsi raw bypass pada API public ini
- **Efek kode:** Hapus emoji/dekoratif; dash→-; underscore/bracket/slash/hash/arrows→spasi; quotes→ASCII; @→ at ; e.g.,→for example, ; i.e.,→that is, ; whitespace collapse/strip; spacing punctuation fix; duplicate quotes collapse; append period bila akhir tidak di set punctuation/quotes/closing brackets; language tags setelah cleanup. SDK tambahan macron→spasi yang native tidak punya.
- **Observasi statik:** Mengubah model input/potensi prosodi, bukan explicit duration clamp. Chunking dilakukan sebelum normalisasi, sehingga normalized length/tags dapat melebihi target chunk.
- **Sumber filename:line:** `supertonic/core.py:25-78`[3] `supertonic/core.py:169-285`[3] `py/helper.py:21-105`[6]
- **Check terkait:** `sdk_newlines_collapse`, `native_newlines_collapse`, `sdk_abbreviation_expansion`, `native_abbreviation_expansion`.

### language — Language token / default
- **Kelas:** `content_conditioning_and_validation`.
- **Default:** SDK multilingual lang=None→na; v1→en lalu effective_lang=None. Native caller wajib lang; CLI default en
- **Bounds:** SDK membership AVAILABLE_LANGUAGES (31+na), bukan kemampuan per model; invalid → ValueError
- **Efek kode:** id menghasilkan <id>...</id>; na berbeda conditioning. SDK Korean target120; native Japanese juga120. v1 mengabaikan non-en parameter.
- **Observasi statik:** Bahasa adalah kontrol eksperimen relevan tetapi pertahankan baseline sebenarnya, jangan diam-diam default na/en atau mengganti id.
- **Sumber filename:line:** `supertonic/config.py:156-198`[1] `supertonic/pipeline.py:208-226`[2] `supertonic/core.py:221-238`[3] `py/helper.py:13`[6] `py/helper.py:102-104`[6] `py/example_onnx.py:59-65`[7]

### validation — Unsupported text / Unicode indexer
- **Kelas:** `hard_failure`.
- **Default:** SDK validate_text selalu dipanggil, bukan hanya verbose meski komentar menyebut verbose
- **Bounds:** Unsupported character setelah individual-character preprocess → ValueError high-level; core/native langsung tidak punya validasi set ini
- **Efek kode:** SDK preprocessing per karakter menentukan supported chars; transform actual chunk dilakukan lagi pada core. Unicode values di uint16, indexing bukan token duration.
- **Observasi statik:** Guard ini bukan limiter jeda. Non-BMP/unsupported behavior tidak diuji terhadap indexer asli; tidak mengakses model/indexer.
- **Sumber filename:line:** `supertonic/pipeline.py:256-259`[2] `supertonic/core.py:154-163`[3] `supertonic/core.py:291-321`[3] `supertonic/core.py:344-353`[3] `py/helper.py:111-131`[6]

### wav-joins — Concatenate tanpa per-chunk trim/crossfade
- **Kelas:** `postprocess`.
- **Default:** Seluruh WAV tiap chunk + zero gap
- **Bounds:** SDK memastikan wav.shape[0]==1; tidak ada shape time cap
- **Efek kode:** SDK concatenate sekali, native concatenate bertahap. Tidak ada fade, overlap, crossfade, VAD trim, loudness normalization, limiter amplitudo, np.clip, atau pemaksaan tail WAV nol pada jalur return yang dibaca.
- **Observasi statik:** Padding/codec tail jika ada ikut di-concat; tidak ada perbaikan join akustik yang dapat dibuktikan dari source ini.
- **Sumber filename:line:** `supertonic/pipeline.py:296-320`[2] `supertonic/core.py:528-529`[3] `py/helper.py:214-244`[6]

### duration-accounting — Returned duration bukan selalu samples/sample_rate
- **Kelas:** `measurement_semantics`.
- **Default:** sum(DP/speed) + silence_duration*(n-1)
- **Bounds:** Gap aktual int(silence_duration*R), metadata memakai float detik penuh
- **Efek kode:** SDK/native menjumlah prediksi durasi dan silence, bukan mengukur panjang WAV. Vocoder dapat mengeluarkan panjang quantized; wrapper tidak memaksa persamaan samplecount.
- **Observasi statik:** Ukur terpisah wall inference, metadata predicted duration, actual WAV seconds dan pause internal. Gap fractional sample menimbulkan rounding <1 sample per join; perbedaan padding model belum diukur.
- **Sumber filename:line:** `supertonic/pipeline.py:307-320`[2] `supertonic/utils.py:105-116`[4] `py/helper.py:239-244`[6]

### native-final-trim — Native example melakukan satu final trim; SDK save tidak
- **Kelas:** `postprocess_length`.
- **Default:** Native w=wav[b,:int(R*duration[b].item())]; SDK sf.write(wav.squeeze(),R)
- **Bounds:** Python slice hanya memotong bila bound < panjang; tidak padding bila bound lebih panjang
- **Efek kode:** Native CLI membatasi akhir keseluruhan WAV ke sum predicted duration; tidak tiap chunk sebelum join. SDK save_audio tidak melakukan trim berdasarkan dur.
- **Observasi statik:** Inferred risk: bila chunk awal mengandung extra padded samples yang tetap dijoin, final trim dapat menghapus equivalent samples dari ujung last chunk. Bukan bukti kata terpotong pada rekaman pengguna; belum ukur WAV. Tailzero tidak eksplisit.
- **Sumber filename:line:** `py/example_onnx.py:111-114`[7] `supertonic/pipeline.py:331-362`[2] `py/example_pypi.py:14-21`[8]

### save-normalize — Save normalization / clipping
- **Kelas:** `postprocess_io`.
- **Default:** sf.write tanpa subtype eksplisit pada dua path
- **Bounds:** Tidak ada peak/RMS/LUFS normalization atau amplitude clip eksplisit di wrapper
- **Efek kode:** Soundfile menerima float waveform; format/subtype/konversi PCM mengikuti library dan target ekstensi, bukan knob prosodi di sini.
- **Observasi statik:** Efek clipping/quantization actual IO belum diuji dan tidak boleh disamakan dengan explicit np.clip di code. Tidak menulis audio pada audit.
- **Sumber filename:line:** `supertonic/pipeline.py:342-361`[2] `py/example_onnx.py:113-114`[7]

### batch — Native batch bypass chunking/join
- **Kelas:** `execution_path`.
- **Default:** CLI --batch=False; native batch speed=1.05, total_step wajib
- **Bounds:** Helper batch hanya meneruskan _infer; lang_list memakai zip tanpa validasi panjang eksplisit. CLI memeriksa voice count=text count
- **Efek kode:** Batch native tidak menerapkan chunk target/silence sama sekali. SDK public synthesize selalu list satu chunk per call, bukan native batch.
- **Observasi statik:** Batch vs nonbatch bukan perbandingan satu variabel bila chunk layout berbeda; batas resource/mask batch perlu terpisah.
- **Sumber filename:line:** `py/helper.py:117-123`[6] `py/helper.py:185-189`[6] `py/helper.py:246-254`[6] `py/example_onnx.py:86-108`[7] `supertonic/pipeline.py:284-291`[2]

### time-tools — timer/format_duration/sanitize filename
- **Kelas:** `measurement_only`.
- **Default:** SDK timer verbose=True; filename max_len=50; native example prefix20
- **Bounds:** Format <60 detik/<3600 detik hanya formatting; filename slice bukan text synthesis slice
- **Efek kode:** timer time.time sebelum/sesudah yield; tidak timeout/cancel/deadline. get_audio_duration=samplecount/sample_rate. Tidak ada wall time stop di empat SDK files atau native helper.
- **Observasi statik:** Nama file 20/50 karakter dan format 60/3600 bukan limiter ujaran. Guard produksi/deadline eksternal tidak diaudit ulang dan tetap dipertahankan.
- **Sumber filename:line:** `supertonic/utils.py:43-116`[4] `py/helper.py:370-385`[6] `py/example_onnx.py:112`[7]

### runtime — Thread/provider controls bukan prosody limiter
- **Kelas:** `compute_only`.
- **Default:** CPUExecutionProvider; intra/inter threads=None atau env SUPERTONIC_INTRA_OP_THREADS/INTER_OP_THREADS
- **Bounds:** Env invalid int→fallback default; tidak ada min/max lokal. Native use_gpu→NotImplementedError
- **Efek kode:** SDK loader sequential + ORT_ENABLE_ALL dan thread selection. Ini memengaruhi eksekusi/latency, bukan explicit durasi kata. SUPERTONIC_ONNX_PROVIDERS masih TODO.
- **Observasi statik:** Tidak menjalankan thread/session/model. Jangan menghapus resource guards aplikasi hanya karena source wrapper tidak memiliki deadline.
- **Sumber filename:line:** `supertonic/config.py:213-240`[1] `supertonic/loader.py:228-260`[5] `py/helper.py:322-335`[6]

### model-provenance — Model selection/revision
- **Kelas:** `provenance_not_prosody`.
- **Default:** SDK DEFAULT_MODEL=supertonic-3; revision=724fb5abbf5502583fb520898d45929e62f02c0b
- **Bounds:** SUPERTONIC_MODEL_REVISION override disnapshot saat import; konfigurasi model v1/v2 punya pin berbeda
- **Efek kode:** Repo configured Supertone/supertonic-3; native README justru merujuk archive model revision aafc6e32416a594460b32413efc49d7fe4ce6d46. Kedua pin tidak dinyatakan sama. Existing assets tidak diverifikasi ulang ke kedua remote pin pada audit ini.
- **Observasi statik:** Commit source SDK sama remote main bukan jaminan assets installed sama release default. Tidak download/replace model, tidak menjalankan auto_download.
- **Sumber filename:line:** `supertonic/config.py:14-57`[1] `supertonic/config.py:114-129`[1] `supertonic/loader.py:108-125`[5] `py/README.md:3-6`[9]

### installed-config — Config aset installed dibaca saja
- **Kelas:** `model_metadata`.
- **Default:** sample_rate=44100; base_chunk_size=512; ttl.chunk_compress_factor=6; ttl.latent_dim=24
- **Bounds:** Q=3072 samples; latent channels=144. Nilai ini local metadata, bukan default universal semua model
- **Efek kode:** Wrapper hanya mengambil empat cfg keys untuk dimensi di core. JSON juga memuat ae.n_delay=0, ttl.normalizer.scale=0.25, dp.normalizer.scale=1.0, ttl.flow_matching.sig_min=1e-8; tidak dibaca sebagai knob oleh wrapper yang diaudit.
- **Observasi statik:** Jangan mengedit normalizer/sig_min/n_delay/model JSON untuk mengklaim prosodi berubah: exported ONNX internal tidak dianalisis. Quantization nominal Q/R dihitung pada checks, bukan durasi pause.
- **Sumber filename:line:** `installed/onnx/tts.json:4-12`[10] `installed/onnx/tts.json:45-46`[10] `installed/onnx/tts.json:174-179`[10] `installed/onnx/tts.json:238-243`[10] `supertonic/core.py:434-438`[3] `py/helper.py:156-159`[6]

### absent-prosody-api — Tidak ada per-word pause/emotion/pitch/SSML knob eksplisit
- **Kelas:** `absence_in_audited_scope`.
- **Default:** Tidak ada di signature SDK synthesize/core atau native TextToSpeech
- **Bounds:** Tidak ada pause duration clamp per kata, emotion enum/intensity, pitch/f0 shift, SSML parser, phoneme/word duration override, global max_audio_seconds di wrapper yang dibaca
- **Efek kode:** Punctuation dan text/language/style masuk model; modeled pauses/intonation tidak sama dengan explicit control. <break time="1s"/> dinormalisasi menjadi literal rusak <break time="1s" >. lalu tag id; tidak dijadwalkan menjadi 1 detik silence.
- **Observasi statik:** Kesimpulan absence hanya Python wrapper/reference di scope ini, bukan semua graph/model/komponen produksi. Akustik model dan internal ONNX belum diperiksa.
- **Sumber filename:line:** `supertonic/pipeline.py:158-168`[2] `supertonic/core.py:240-285`[3] `supertonic/core.py:463-529`[3] `py/helper.py:177-254`[6]
- **Check terkait:** `sdk_not_ssml`, `native_not_ssml`.

### fixture-evidence — Bukti source control flow dari parent, bukan suara model
- **Kelas:** `controlled_synthetic_evidence`.
- **Default:** Probe synthetic sessions; source hash persis cocok
- **Bounds:** Tidak ada native model inference/listening; receipt dan script hanya dibaca, tidak dijalankan ulang
- **Efek kode:** Synthetic fixture: 2 SDK chunks gap0.3=13230 zeros pada44100Hz; single chunk identik saat silence0.3 vs0; VE loop8 vs12 dan DP durasi/shape tetap pada speed tetap; core speed0.6/2.1 dan high-level steps0/101 ditolak.
- **Observasi statik:** PipelineFixture mengembalikan ones100; Session DP=2.1 dan vocoder zeros synthetic. Semua nilai ini fixture eksplisit, bukan hasil model atau teks pengguna. Shape tetap hanya terjamin pada fixture tersebut.
- **Sumber filename:line:** `evidence/control-flow-probe:1-38`[12] `evidence/probe-script:11-51`[13] `evidence/upstream-content-verification:1-34`[11]

## Verifikasi yang benar-benar dilakukan

- **26 checks lulus**: extracted pure text helpers dari AST, evaluasi comparison guards persis, dan formula numerik; tidak import module upstream/model/ORT, tidak load indexer/voice, tidak inference. Input sintetis sengaja diisolasi dari paragraf pengguna; hasil detail `input/actual/expected/method` di JSON.
- Parent control-flow receipt dan script dibaca penuh; digest empat source cocok API/local. Synthetic ORT fixtures bukan model sesungguhnya: DP fixture2.1, WAV fixture ones/zeros; bukti itu hanya menguji routing/loop/guard/join. Script receipt tidak dijalankan ulang karena ia menulis receipt dan audit hanya boleh menulis dua report.
- Synthetic receipt: 13230 zero samples pada join0.3detik/44100Hz; single-chunk output identik pada silence0.3 vs0; loop8/12 benar dengan predicted duration dan fixture shape sama; invalid steps0/101 dan speed0.6/2.1 ditolak. `NATIVE-CONTROL-FLOW-PROBE.json:1-38` dan `tools/audit_native_controls.py:11-51` menjadi bukti terpisah, bukan klaim native acoustic fix.
- Installed metadata geometry: Q=512×6=3072 samples; channels=24×6=144; Q/R=0.069659863946detik. Itu granularity latent nominal, **bukan pause minimum/maksimum**. Hanya JSON config dibaca; exported graph tidak diubah/dijalankan.

## Kontrol eksperimen potensial — belum dijalankan

Semua eksperimen harus mempertahankan speaker/style bytes dan seluruh paragraf asli; bukan mengganti ke contoh native M1/M4 atau string fixture. Jangan mengaktifkan trial context yang sebelumnya ditolak. Baseline aktual harus dibuktikan dulu, bukan diasumsikan sama default SDK.

### baseline
- Variabel: none.
- Nilai/rencana: ["baseline aktual tanpa perubahan"].
- Tetap: Full paragraf asli byte-for-byte, punctuation/newlines/order sama; speaker identity dan byte hash style_ttl/style_dp tetap; model assets+tts.json+indexer hashes tetap; SDK/core call path dan lang aktual tetap; RNG seed/state dan software/provider/thread config dicatat; seluruh guard CPU/RSS/deadline/outputsize tetap.
- Ukur: raw and normalized text per actual SDK chunk; SDK chunk count + application chunk mapping; DP pre-speed duration and post-speed duration; waveform sample count/R per chunk and final join; word-internal pause locations/durations separately from join gaps; wall and CPU time separately from WAV seconds
- Catatan: Belum dijalankan; fixture strings bukan pengganti teks asli.

### gap
- Variabel: silence_duration.
- Nilai/rencana: [0.3, 0.0].
- Tetap: baseline speaker/text/speed/steps/lang/RNG dan chunk layout.
- Ekspektasi kode: Satu SDK chunk: tidak ada join gap untuk dihapus. Multi SDK chunks: hilangkan tepat int(0.3*R) zero samples setiap join; tidak mengubah internal pause model.
- Batas klaim: Tidak menjanjikan gap bangku…depan/pucuk…jambu berkurang.

### steps
- Variabel: total_steps.
- Nilai/rencana: [8, 12].
- Tetap: baseline speaker/text/speed=baseline, lang, chunk layout dan seed/state.
- Ekspektasi kode: Loop count berbeda, DP dievaluasi sebelum loop sehingga tidak dipengaruhi total_steps pada input identik.
- Batas klaim: Output VE aktual/waveform/prosodi dan biaya CPU belum diukur. Default core langsung5 harus eksplisit, jangan tak sengaja banding5 vs8.

### speed
- Variabel: speed.
- Nilai/rencana: [1.05, 0.95, 1.15].
- Tetap: baseline speaker, full text, total_steps, lang, chunk layout, seed/state.
- Ekspektasi kode: Contoh sweep hanya jika baseline memang1.05; selalu dalam SDK[0.7,2.0]. DP output dibagi speed; target total durasi berubah.
- Batas klaim: Tidak ada selective per-word pause control; evaluasi rasio pause vs speech, bukan panjang total saja.

### seed
- Variabel: NumPy global RNG seed in isolated harness only.
- Nilai/rencana: ["baseline recorded seed/state", "same fixed seed repeated", "predeclared paired alternative seeds"].
- Tetap: speaker dan kedua style arrays byte-exact, full original text, all inference args.
- Ekspektasi kode: Source upstream tidak menyediakan seed parameter; set/restore global RNG hanya dalam harness terisolasi. Klarifikasi dari source aplikasi: worker produksi `voice_app/supertonic3_worker.py:187` (SHA256 `eb0e6c9cd8655ff1eb2cb5c64f5379945295f86b5c971a79d90ed3ccda03d625`) secara eksplisit memakai `np.random.seed(20261026)` sebelum synthesis. Angka20261026 juga dipakai fixture parent, tetapi bukan hanya milik fixture; kandidat harus mempertahankan seed aplikasi tersebut.
- Batas klaim: Seed sama tidak memastikan pairing noise identik jika shape/chunk layout berubah atau backend nondeterministik.

### chunk
- Variabel: max_chunk_length.
- Nilai/rencana: ["baseline target", "higher target only if needed and within unchanged resource guards"].
- Tetap: speaker and full original text unchanged; do not insert/delete punctuation/newlines.
- Ekspektasi kode: Jalankan chunk_text murni dahulu dan simpan daftar chunk/normalized inputs. Bila tetap satu SDK chunk, target berbeda bukan intervensi relevan. Oversize sentence tetap utuh, paragraf tetap boundary.
- Batas klaim: Ini bukan izin full-context trial yang sudah ditolak; tidak dijalankan tanpa persetujuan baru.

### tail-analysis
- Variabel: measurement-only comparison of SDK return vs native example final trim.
- Nilai/rencana: ["full returned arrays", "non-destructive sample-count accounting against int(R*predicted_duration)"].
- Tetap: same generated recording if already authorized, speaker/text unchanged.
- Ekspektasi kode: Hitung padding/join positions dan calon final-trim range tanpa menghapus sampel/merilis audio baru.
- Batas klaim: Jangan force tail-zero/VAD-trim/pause compression atau mengklaim akhir kata terpotong tanpa waveform evidence.

### conditioning
- Variabel: none by default.
- Nilai/rencana: ["preserve actual lang and style_ttl/style_dp"].
- Tetap: speaker identity and source text remain exact.
- Ekspektasi kode: Jangan mengganti speaker atau campur/interpolasi arrays. Perubahan language tagging atau bypass normalization adalah eksperimen terpisah dan membutuhkan persetujuan; bukan parameter emotion/SSML yang sudah tersedia.
- Batas klaim: Tidak ada safe documented style_dp multiplier/pitch slider/emotion preset untuk direkomendasikan.

## Unknowns dan larangan inferensi

- Internal ONNX DP/vector estimator/vocoder graph belum dibaca/diinstrumentasi: learned duration range, hidden clipping, duration normalization, F0/emotion/attention behavior tidak diketahui.
- Tidak menjalankan inference atau listening. Audit tidak menentukan penyebab jeda di bangku…depan atau pucuk…jambu dan tidak membuktikan perbaikan akustik.
- Observasi historis 69 detik/7 application chunks dan23 detik/full sentence berasal konteks pengguna/parent, bukan pengukuran ulang audit. Keduanya dilaporkan terjadi di dalam satu native SDK chunk dengan input SDK utuh. Jangan menyamakan application chunk, SDK chunk, dan word boundary.
- Context trial sebelumnya ditolak pengguna; tidak ada bukti bahwa memperpanjang kalimat/context menyelesaikan prosodi.
- Lengkap paragraf asli, baseline speaker identifier/hash, RNG state/version, runtime provider dan nilai production aktual harus disalin dari receipt baseline sebelum eksperimen; jangan mengisi dari contoh M1/M4 atau fixture teks.
- Parent menyatakan SDK installed core/pipeline/config/utils identik clone. Audit ini mengonfirmasi clone vs GitHub bytes dan hash receipt; tidak menemukan/membaca ulang path installed SDK secara independen.
- Model installed seluruh assets dan manifest tidak dihash ulang; hanya tts.json dibaca/di-hash. Kesetaraan ke pinned HF model default SDK atau archive README tidak dibuktikan.
- Konversi soundfile/subtype/PCM clipping serta pengaruh final trim/padding terhadap rekaman asli belum diuji.
- Angka guard aplikasi berikut diterima sebagai klarifikasi parent, bukan source-app audit di pekerjaan ini: TTS_TIMEOUT45 hanya EDGE; Supertonic _local_request90; native wall75/CPU60/65; output-duration guard30 detik terpisah. Tidak boleh menyamakan request/CPU/wall deadlines dengan panjang WAV. Semua guard tetap utuh.
- Tidak menyatakan seluruh repository/server/CLI SDK atau sistem produksi bebas limiter: cakupan executable dibaca penuh terbatas empat file SDK wajib + loader dan tiga file Python reference native; native README sebagai provenance model.

## Sources

Ledger ini dibentuk secara mekanis dari sumber yang benar-benar diambil/dibaca; nomor di temuan merujuk ke entri berikut. Scope write dua file berarti tidak membuat citation-ledger tambahan.
[1] `https://github.com/supertone-oss-archive/supertonic-py/blob/df0f9686dac7fbbde391b759e2ee5286a3737622/supertonic/config.py` — SHA256 `9684abef9023d305b87d8b511a5c955624f5b7a92c76b57aa8b3e06f8e18ce2e`.
[2] `https://github.com/supertone-oss-archive/supertonic-py/blob/df0f9686dac7fbbde391b759e2ee5286a3737622/supertonic/pipeline.py` — SHA256 `13b16fb8556632b5985f8ba1e961096ca1ad921d01aed171972dd106df51a7cd`.
[3] `https://github.com/supertone-oss-archive/supertonic-py/blob/df0f9686dac7fbbde391b759e2ee5286a3737622/supertonic/core.py` — SHA256 `26664d490509edc1cc1feca2fc0f72b1ae067fd5c5b11d39c1daeba9c3e81ff4`.
[4] `https://github.com/supertone-oss-archive/supertonic-py/blob/df0f9686dac7fbbde391b759e2ee5286a3737622/supertonic/utils.py` — SHA256 `8bf2e0a14783de9266edba1135597e6b77b143907e445b08dcc45f694ff51ed3`.
[5] `https://github.com/supertone-oss-archive/supertonic-py/blob/df0f9686dac7fbbde391b759e2ee5286a3737622/supertonic/loader.py` — SHA256 `9478b085ccae88fd44849a87d8a55d0ddada03e4fbb7f47f1707f04ac06e621f`.
[6] `https://github.com/supertone-oss-archive/supertonic/blob/1e9799e964ea4c0dad7cde993b65c3c813a7b373/py/helper.py` — SHA256 `b14b1da8a018aa4db6ca8cbb927c32590bfc889b49116ea344c56e8351d94279`.
[7] `https://github.com/supertone-oss-archive/supertonic/blob/1e9799e964ea4c0dad7cde993b65c3c813a7b373/py/example_onnx.py` — SHA256 `2ef5371dc02b9425b130ecc480abeb4714781fabe2059e4f9c68dd5e546dec49`.
[8] `https://github.com/supertone-oss-archive/supertonic/blob/1e9799e964ea4c0dad7cde993b65c3c813a7b373/py/example_pypi.py` — SHA256 `edadd0110ddc564f9903d8609404e5389ac4f5f0b32d7d6e6622be815df27b04`.
[9] `https://github.com/supertone-oss-archive/supertonic/blob/1e9799e964ea4c0dad7cde993b65c3c813a7b373/py/README.md` — SHA256 `799e439ca614506c9d76ba2043ec28f3be6bf09ecffa98d88a3e0267ede1a770`.
[10] `/root/voice-lab/autonomous/expressive-tts/assets/onnx/tts.json` — SHA256 `42078d3aef1cd43ab43021f3c54f47d2d75ceb4e75f627f118890128b06a0d09`.
[11] `/root/sela-live/docs/UPSTREAM-CONTENT-VERIFICATION.json` — SHA256 `1061079bdad591ac53326ea3ab7a5b910103ed7da09d66cced95da4648d89fed`.
[12] `/root/sela-live/docs/NATIVE-CONTROL-FLOW-PROBE.json` — SHA256 `d512323baf17a9fceecc974940ac3f0d8b319bfaf9c483ce40b13cbdae55f6bf`.
[13] `/root/sela-live/tools/audit_native_controls.py` — SHA256 `9a2bc6ec41ca918af63bcffe0a39d1dafbe38be74ad7de759c76c7c4bcce8082`.
[14] `/root/sela-live/docs/REPOSITORY-SCOPE.md` — SHA256 `85c365b0fea055e3f5d2f5904f0b1206b61f2b5007fd58e752ef79dfc9960127`.
