/* Sela: same-origin voice client. No credentials or persistent secrets. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const ui = Object.fromEntries(['start','ptt','mute','interrupt','provider','send','status','error','history','level','connection'].map(id => [id, $(id)]));
  const {pcm16, tokenKey} = VoiceAudio;
  let ws = null, connecting = null, config = null, server = null;
  let pendingStart = null, startTimer = null, startSequence = 0;
  let running = false, starting = false, muted = false, mode = 'hands_free', lifecycle = 0;
  let stream = null, captureContext = null, micSource = null, worklet = null;
  let playbackContext = null, playbackEpoch = 0, currentSource = null, draining = null;
  let active = null, latestToken = null, sealed = false, queue = [], lastTranscript = '', liveText = '';
  const blocked = new Set();
  let suppressStarts = false;
  let pressed = false, chunks = [], samples = 0, speechSamples = 0, silenceSamples = 0, utterance = false;
  let preRoll = [], preRollSamples = 0, captureGeneration = 0;
  let previewSamples = 0, previewRevision = 0;
  const PRE_ROLL_SECONDS = 0.3;
  let captureRate = 48000, maxSeconds = 20, noiseFloor = 0.003;

  function send(message) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(message));
    return true;
  }
  let deliveryDiagnostic = '', processingStatus = '';
  function status(text) { processingStatus = text; ui.status.textContent = text + deliveryDiagnostic; }
  function error(text) { ui.error.textContent = text; ui.error.hidden = !text; }
  function clearRecording() {
    previewSamples = 0; previewRevision = 0; $('local-draft').textContent = '';
    chunks = []; samples = 0; speechSamples = 0; silenceSamples = 0; utterance = false;
    preRoll = []; preRollSamples = 0;
    worklet?.port.postMessage({type:'reset', generation:++captureGeneration});
  }
  let recognition = null, speechPaused = false, recognitionTimer = null, recognitionRetries = 0, waitingReply = false;
  let spokenParts = [], utteranceDeadline = null;
  const UTTERANCE_PAUSE_MS = 1200;
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  function browserSelected() { return config?.browser_stt_allowed === true && Boolean(Recognition) && $('browser-consent').checked === true; }
  function speechHint() {
    if (config?.browser_stt_allowed === true) return Recognition ? 'Untuk bicara tanpa key STT, centang Pengenalan suara daring browser (eksperimental), lalu Mulai. Audio dapat dikirim ke vendor browser.' : 'Browser ini tidak mendukung pengenalan suara daring. Gunakan browser yang mendukung, konfigurasi STT API/key di server, atau tulis pesan.';
    return config?.stt_reason || 'STT API tidak tersedia. Gunakan pesan tertulis.';
  }
  function speechAvailable() { return browserSelected() || config?.stt_available === true; }
  function localCapture() { return config?.stt_mode === 'local' && !browserSelected(); }
  function previewEnabled() { return localCapture() && config?.local_preview_supported === true; }
  function previewSeconds() {
    if (config?.local_stateful_preview_supported !== true) return 1.5;
    const interval = Number(config.local_preview_interval_ms);
    return (Number.isFinite(interval) ? Math.max(500, Math.min(1500, interval)) : 500) / 1000;
  }
  function browserRecoveryHint() { return config?.stt_mode === 'local' && config?.stt_available === true ? 'Hapus centang pengenalan daring browser, lalu tekan Mulai untuk memakai STT lokal. Pesan teks tetap tersedia.' : 'Gunakan STT API atau teks.'; }
  function speechChoice() {
    if (localCapture()) {
      $('browser-consent').disabled = !config?.browser_stt_allowed || !Recognition || running || starting;
      $('browser-help').textContent = (config.stt_label || 'STT lokal di server aplikasi') + '. Model berjalan di server, bukan di perangkat browser. Tidak perlu pengenalan daring browser atau unduhan model di browser. Bebas tangan: simpan 300 ms sebelum suara melewati ambang dan kirim setelah jeda sekitar 1,2 detik. Ambang menyesuaikan level latar saat hening; ini pengukur level, bukan pendeteksi ucapan. PTT hanya merekam setelah tombol ditekan.';
      if (config?.browser_stt_allowed) $('browser-help').textContent += Recognition ? ' Untuk memakai STT browser seperti versi sebelumnya, centang pilihan pengenalan daring browser. Audio dapat dikirim ke vendor browser; tidak beralih otomatis dan pilihan hanya dapat diubah setelah sesi diakhiri.' : ' Browser ini tidak mendukung pengenalan daring; STT lokal dan pesan teks tetap tersedia.';
      $('privacy-note').textContent = 'Audio diproses di server aplikasi, bukan layanan STT/TTS eksternal. Teks tetap dikirim ke Codex.';
      return;
    }
    $('browser-consent').disabled = !config?.browser_stt_allowed || !Recognition || running || starting;
    $('browser-help').textContent = !Recognition ? 'Browser ini tidak mendukung pengenalan suara daring. Konfigurasikan STT API/key di server atau gunakan teks.' : !config?.browser_stt_allowed ? 'Pengenalan browser tidak diizinkan server. Gunakan STT API atau teks.' : 'Dengan mencentang, saya setuju audio dapat dikirim ke vendor browser. Perlu internet; eksperimental. Tidak mengunduh model lokal. Mode bergantian: saat jawaban diputar, mikrofon dijeda. Gunakan Hentikan jawaban untuk menyela; PTT tidak tersedia.';
    $('privacy-note').textContent = 'Teks dikirim ke penyedia AI API melalui server aplikasi. ' + (browserSelected() ? 'Audio pengenalan daring dapat dikirim ke vendor browser; transkrip final dikirim ke server aplikasi. Tidak ada unduhan model lokal.' : config?.stt_available === true ? 'Audio mikrofon dikirim ke layanan STT API melalui server saat mode suara digunakan.' : 'Mikrofon tidak dikirim sebelum memilih pengenalan daring dan memulai, atau mengonfigurasi STT API.');
  }
  function clearSpokenUtterance() {
    clearTimeout(utteranceDeadline); utteranceDeadline = null; spokenParts = [];
  }
  function coalesceSpeechParts(parts) {
    let merged = [];
    for (const raw of parts) {
      const text = raw.trim().replace(/\s+/g, ' ');
      if (!text) continue;
      const whole = merged.join(' ').toLowerCase(), last = (merged.at(-1) || '').toLowerCase(), next = text.toLowerCase();
      if (whole && (next === whole || whole.startsWith(next + ' '))) continue;
      if (whole && next.startsWith(whole + ' ')) merged = [text];
      else if (last && (next === last || last.startsWith(next + ' '))) continue;
      else if (last && next.startsWith(last + ' ')) merged[merged.length - 1] = text;
      else merged.push(text);
    }
    return merged;
  }
  function abortRecognition() {
    clearSpokenUtterance();
    clearTimeout(recognitionTimer); recognitionTimer = null;
    const old = recognition; recognition = null;
    if (old) { try { old.abort(); } catch (_) { /* already stopped */ } }
  }
  function startRecognition() {
    if (!running || muted || pendingStart || !browserSelected() || recognition || recognitionTimer || speechPaused || waitingReply || ws?.readyState !== WebSocket.OPEN) return;
    const rec = new Recognition();
    recognition = rec;
    rec.lang = 'id-ID'; rec.continuous = true; rec.interimResults = true; rec.maxAlternatives = 1;
    if ('processLocally' in rec) rec.processLocally = false;
    const finalResults = new Map(), priorParts = spokenParts.slice();
    const attempt = lifecycle;
    rec.onresult = event => {
      if (recognition !== rec || attempt !== lifecycle || !running || muted) return;
      clearTimeout(utteranceDeadline); utteranceDeadline = null;
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (!result.isFinal) { interim += ' ' + (result[0]?.transcript || ''); continue; }
        const text = (result[0]?.transcript || '').trim();
        if (text) finalResults.set(i, text);
      }
      spokenParts = coalesceSpeechParts([...priorParts, ...Array.from(finalResults.entries()).sort((a,b) => a[0]-b[0]).map(([,text]) => text)]);
      if (spokenParts.join(' ').length + interim.length > 4000) {
        clearSpokenUtterance(); error('Ucapan terlalu panjang (maksimum 4000 karakter). Ulangi lebih singkat.'); return;
      }
      recognitionRetries = 0;
      status('Mendengarkan: ' + coalesceSpeechParts([...spokenParts, interim]).join(' '));
      // A final recognition result may be a single word, not an entire turn.
      // Keep listening across result events and wait for a pause with no interim.
      if (interim.trim() || !spokenParts.length) return;
      const timer = setTimeout(() => {
        if (utteranceDeadline !== timer || attempt !== lifecycle || !running || muted || waitingReply || pendingStart || ws?.readyState !== WebSocket.OPEN) return;
        const text = spokenParts.join(' ');
        waitingReply = true;
        abortRecognition(); cancelPlayback(); suppressStarts = false; error('');
        send({type:'voice_text', text}); status('Ucapan dikirim. Pengenalan dijeda sampai jawaban selesai.');
      }, UTTERANCE_PAUSE_MS);
      utteranceDeadline = timer;
    };
    rec.onerror = event => {
      if (recognition !== rec || attempt !== lifecycle) return;
      if (['not-allowed','service-not-allowed','audio-capture','language-not-supported'].includes(event.error)) {
        stop(); error('Pengenalan suara browser gagal (' + event.error + '). Periksa izin mikrofon/layanan browser. ' + browserRecoveryHint());
      } else {
        error('Layanan pengenalan daring terganggu (' + event.error + '). Mencoba kembali terbatas; pesan tertulis tetap tersedia.');
      }
    };
    rec.onend = () => {
      if (recognition !== rec || attempt !== lifecycle || !running || muted || speechPaused) return;
      recognition = null;
      if (++recognitionRetries > 5) { stop(); error('Pengenalan daring berhenti berulang kali. ' + browserRecoveryHint()); return; }
      status('Pengenalan daring terjeda. Menghubungkan kembali…');
      const timer = setTimeout(() => {
        if (recognitionTimer !== timer || attempt !== lifecycle) return;
        recognitionTimer = null;
        startRecognition();
      }, Math.min(8000, 400 * 2 ** (recognitionRetries - 1)));
      recognitionTimer = timer;
    };
    try { rec.start(); } catch (e) { stop(); error('Pengenalan daring tidak dapat dimulai: ' + e.message + '. ' + browserRecoveryHint()); }
  }
  function controls() {
    const connected = ws && ws.readyState === WebSocket.OPEN;
    const sttAvailable = speechAvailable();
    speechChoice();
    if (config?.tts_identity?.backend === 'supertonic3') {
      $('privacy-note').textContent += ' Suara Supertonic-3 sintetis; penggunaan mengikuti batasan OpenRAIL-M (lihat pengaturan suara).';
    }
    ui.start.disabled = starting || !sttAvailable;
    ui.start.title = sttAvailable ? '' : speechHint();
    ui.start.textContent = starting ? 'Meminta akses mikrofon…' : running ? 'Akhiri percakapan' : 'Mulai percakapan ↗';
    ui.start.classList.toggle('stopping', running);
    $('mode-options').disabled = running || starting || !sttAvailable || browserSelected();
    $('mode-options').title = browserSelected() ? 'Browser eksperimental hanya mendukung bebas tangan bergantian, bukan PTT.' : '';
    ui.mute.disabled = !running;
    ui.mute.textContent = muted ? 'Aktifkan mikrofon' : 'Bisukan mikrofon';
    ui.mute.setAttribute('aria-pressed', String(muted));
    ui.ptt.hidden = !running || mode !== 'push_to_talk';
    ui.ptt.disabled = !running || muted;
    ui.ptt.classList.toggle('pressed', pressed);
    ui.ptt.textContent = pressed ? 'Merekam… lepas untuk kirim' : 'Tahan untuk bicara';
    ui.interrupt.disabled = !(active || currentSource || server?.responding || server?.speaking);
    ui.send.disabled = !config;
    ui.provider.disabled = !connected || !config;
    ui.connection.textContent = connected ? 'Server terhubung' : 'Server terputus';
    ui.connection.classList.toggle('online', Boolean(connected));
    $('session-dot').classList.toggle('active', running && !muted);
    $('session-label').textContent = !running ? 'MIKROFON BELUM AKTIF' : pendingStart ? 'MENUNGGU KONFIRMASI SESI' : muted ? 'MIKROFON DIBISUKAN' : speechPaused ? 'PENGENALAN DIJEDA · BERGANTIAN' : pressed || utterance ? 'MENDENGARKAN UCAPAN' : 'MIKROFON AKTIF';
    $('voice-title').textContent = !running ? 'Beri pikiranmu sedikit ruang.' : pendingStart ? 'Menyiapkan sesi suara.' : muted ? 'Sejenak, dalam hening.' : speechPaused ? 'Menunggu jawaban selesai.' : pressed || utterance ? 'Aku mendengarkan.' : 'Silakan bicara.';
    $('voice-description').textContent = !running ? (!sttAvailable && config ? speechHint() + ' Pesan tertulis tetap bisa digunakan.' : 'Mulai dengan suara, atau tulis sesuatu di bawah.') : muted ? 'Pesan tertulis tetap bisa dikirim.' : browserSelected() ? 'Pengenalan daring browser eksperimental · bergantian. Jeda saat jawaban diputar; Hentikan jawaban untuk menyela.' : localCapture() ? 'STT lokal di server aplikasi · bergantian. ' + (mode === 'hands_free' ? 'Jeda sekitar 1,2 detik untuk mengirim. ' : 'Tahan tombol bicara, lalu lepaskan untuk mengirim. ') + 'Mikrofon dijeda saat pemrosesan dan pemutaran jawaban; Hentikan jawaban untuk menyela.' : mode === 'hands_free' ? 'Bicara sewajarnya. Jeda sebentar untuk mengirim.' : 'Tahan tombol bicara, lalu lepaskan untuk mengirim.';
    $('voice-orb').classList.toggle('active', running && !muted);
    document.querySelector('.voice-surface').classList.toggle('started', running);
  }
  function message(role, text, note, pending = false) {
    const article = document.createElement('article');
    article.className = 'message ' + (role === 'assistant' ? 'assistant' : 'user');
    const heading = document.createElement('div'); heading.className = 'message-heading';
    const label = document.createElement('span'); label.className = 'message-role';
    label.textContent = role === 'assistant' ? 'Asisten' : role === 'user' ? 'Kamu' : role;
    const meta = document.createElement('span'); meta.textContent = pending ? 'Berlangsung' : '';
    heading.append(label, meta);
    const p = document.createElement('p'); p.textContent = text;
    article.append(heading, p);
    if (note) { const small = document.createElement('small'); small.textContent = note; article.append(small); }
    return article;
  }
  // Presentation only: never assign the formatted value to protocol/history data.
  const voiceAcronyms = new Set(('API AI CPU GPU RAM STT TTS ASR VAD PCM WAV SSE WS WSS CORS CSRF XSS OAUTH LLM SDK HTTP HTTPS SQL PHP JSON HTML CSS UI USB DNS TCP UDP IP SSH CLI XML YAML JWT URL URI ID UUID UTF ASCII PDF CSV DOM JS TS JSX TSX WASM RPC REST CRUD OAuth TLS SSL VPN LAN WAN WIFI NFC GPS OS IO IDE NPM').split(' '));
  function voiceDisplay(text) {
    if (typeof text !== 'string' || !text) return text;
    // Opaque technical spans are not prose: preserve their case and punctuation.
    // No lexical corrections, name guessing, or attempts to repair recognition.
    const opaque = /(`[^`]*`|"[^"\r\n]{0,4000}"|'[^'\r\n]{0,4000}'|<[^>]*>|\b[a-z][a-z\d+.-]*:\/\/[^\s<>`]+|\b[\w.+-]+@[\w.-]+\.[a-z]+\b|(?:[a-z]:[\\/]|\b[\w.-]{1,256}\/|\.{0,2}\/|~\/)[^\s<>`]+|\b[\w-]+(?:\.[\w-]+)+\b|\b[a-z_$][\w$]{0,255}(?=\()|--[\w-]+|[\p{L}\p{N}_]+(?:[_=:\\-][\p{L}\p{N}_./\\-]+)+|[\p{L}\p{N}_]*\d[\p{L}\p{N}_]*)/giu;
    const parts = text.split(opaque);
    const prose = parts.filter((_, i) => i % 2 === 0).join('');
    // Only recognizer-style ALL CAPS prose qualifies. Mixed-case speech is intact.
    if (!/\p{Lu}/u.test(prose) || /\p{Ll}/u.test(prose)) return text;
    let sentenceStart = true;
    return parts.map((part, i) => {
      if (i % 2) { if (part) sentenceStart = /[.!?]$/u.test(part); return part; }
      return part.replace(/[\p{L}\p{M}]+|[^\p{L}\p{M}]+/gu, token => {
        if (!/\p{L}/u.test(token)) {
          if (/[.!?\n\r]/u.test(token)) sentenceStart = true;
          return token;
        }
        const atStart = sentenceStart; sentenceStart = false;
        if (voiceAcronyms.has(token)) return token;
        const word = token.toLocaleLowerCase('id-ID');
        return atStart ? word.replace(/\p{L}/u, letter => letter.toLocaleUpperCase('id-ID')) : word;
      });
    }).join('');
  }
  function renderHistory() {
    const history = Array.isArray(server?.history) ? server.history.map(entry => ({...entry})) : [];
    let streamingRepresented = false;
    const last = history[history.length - 1];
    if (liveText && last?.role === 'assistant' && (last.text === liveText || (tokenKey(server?.active) === tokenKey(active) && (liveText.startsWith(last.text || '') || (last.text || '').startsWith(liveText))))) {
      if (liveText.length > (last.text || '').length) last.text = liveText;
      streamingRepresented = true;
    }
    const nearBottom = ui.history.scrollHeight - ui.history.scrollTop - ui.history.clientHeight < 80;
    const nodes = history.map(entry => message(entry.role, entry.role === 'user' && entry.source === 'voice' ? voiceDisplay(entry.text || '') : entry.text || '', [entry.source === 'voice' ? 'Suara' : '', entry.status === 'interrupted' ? 'Dihentikan' : '', entry.heard_text ? 'Terdengar: ' + entry.heard_text : ''].filter(Boolean).join(' · ')));
    if (lastTranscript && !history.some(e => e.role === 'user' && e.text === lastTranscript)) nodes.push(message('user', voiceDisplay(lastTranscript), 'Transkripsi'));
    // A snapshot is authoritative; only append text not yet represented by it.
    if (liveText && !streamingRepresented) nodes.push(message('assistant', liveText, '', !sealed));
    if (nodes.length) ui.history.replaceChildren(...nodes);
    $('message-count').textContent = nodes.length + ' pesan';
    if (nearBottom) ui.history.scrollTop = ui.history.scrollHeight;
  }
  function cancelPlayback(block = true) {
    deliveryDiagnostic = '';
    status(processingStatus);
    if (block && active) blocked.add(tokenKey(active));
    playbackEpoch++;
    queue = [];
    if (currentSource) {
      const old = currentSource; currentSource = null; old.onended = null;
      try { old.stop(); } catch (_) { /* already ended */ }
      // stop() need not produce an ended event; release the awaiting drain here.
      old._cancel?.();
    }
    active = null; sealed = true;
    speechPaused = false; startRecognition();
    controls();
  }
  function interrupt() {
    suppressStarts = true;
    waitingReply = false; clearRecording();
    cancelPlayback(true);
    send({type:'interrupt'});
    status('Jawaban dihentikan. Kamu bisa melanjutkan.');
  }
  async function playbackReady() {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return;
    if (!playbackContext || playbackContext.state === 'closed') playbackContext = new Audio();
    if (playbackContext.state === 'suspended') await playbackContext.resume();
  }
  async function drainAudio() {
    if (draining === playbackEpoch || !playbackContext) return;
    const drainEpoch = playbackEpoch;
    draining = drainEpoch;
    try {
      while (drainEpoch === playbackEpoch && queue.length) {
        const item = queue.shift(), epoch = playbackEpoch;
        if (tokenKey(item.token) !== tokenKey(active) || blocked.has(tokenKey(item.token))) continue;
        const binary = atob(item.wav_b64);
        const bytes = Uint8Array.from(binary, char => char.charCodeAt(0));
        const buffer = await playbackContext.decodeAudioData(bytes.buffer);
        if (epoch !== playbackEpoch || tokenKey(item.token) !== tokenKey(active) || blocked.has(tokenKey(item.token))) continue;
        if (playbackContext.state === 'suspended') {
          error('Audio ditahan browser. Klik Mulai atau kirim pesan untuk mengaktifkan pemutaran.');
          continue;
        }
        const source = playbackContext.createBufferSource();
        source.buffer = buffer; source.connect(playbackContext.destination); currentSource = source;
        status((browserSelected() || localCapture()) ? 'Asisten sedang berbicara · bergantian. Pengenalan dijeda; klik Hentikan jawaban untuk menyela.' : 'Asisten sedang berbicara. Kamu boleh menyela.'); controls();
        await new Promise(resolve => {
          // Cancellation resolves the queue too, but never sends an acknowledgment.
          source.onended = () => {
            if (epoch === playbackEpoch && currentSource === source) {
              currentSource = null;
              send({type:'ack', token:item.token, chunk_id:item.chunk_id});
            }
            resolve();
          };
          source._cancel = resolve;
          source.start();
        });
      }
    } catch (e) { if (drainEpoch === playbackEpoch) error('Suara jawaban tidak dapat diputar: ' + e.message); }
    finally {
      // A canceled decoder may finish after a new response is already playing.
      if (draining === drainEpoch) {
        draining = null; controls();
        if (queue.length) void drainAudio();
        else if (active && sealed && !currentSource) {
          status('Jawaban selesai. Silakan lanjutkan.');
          if (localCapture()) clearRecording(); waitingReply = false; speechPaused = false; startRecognition(); controls();
        }
      }
    }
  }

  function listeningStatus() {
    if (running) status(browserSelected() ? 'Mendengarkan · pengenalan daring browser, bergantian.' : mode === 'hands_free' ? `Mendengarkan. Jeda sekitar ${localCapture() ? '1,2' : '0,9'} detik untuk mengirim.` : 'Siap. Tahan tombol bicara untuk merekam.');
  }
  function beginSession(attempt) {
    if (config.session_ack_supported === true) {
      pendingStart = `${attempt}-${++startSequence}`;
      const startId = pendingStart;
      startTimer = setTimeout(() => {
        if (pendingStart !== startId || attempt !== lifecycle) return;
        releaseMedia(); error('Server belum mengonfirmasi sesi. Tekan Mulai untuk mencoba lagi.');
      }, previewEnabled() ? 30000 : 8000);
      send({type:'start', mode, provider:ui.provider.value, start_id:pendingStart, ...(previewEnabled() ? {local_preview:true} : {})});
      status('Menunggu konfirmasi sesi mikrofon dari server…');
    } else {
      send({type:'start', mode, provider:ui.provider.value});
      startRecognition(); listeningStatus();
    }
  }
  function handle(event) {
    let data;
    try { data = JSON.parse(event.data); } catch (_) { error('Format pesan server tidak dikenali.'); return; }
    switch (data.type) {
      case 'session_ready':
        if (pendingStart && data.start_id === pendingStart) {
          pendingStart = null; clearTimeout(startTimer); startTimer = null;
          clearRecording(); startRecognition(); listeningStatus(); controls();
        }
        break;
      case 'state': {
        server = data.state;
        // response_start, not a state snapshot, authorizes a new playback token.
        if (server?.muted && !muted) { muted = true; clearRecording(); abortRecognition(); if (stream) stream.getAudioTracks().forEach(track => { track.enabled = false; }); }
        if (server?.open === false && running && !pendingStart) { releaseMedia(); cancelPlayback(); }
        if (lastTranscript && server?.history?.some(e => e.role === 'user' && e.text === lastTranscript)) lastTranscript = '';
        renderHistory(); controls(); break;
      }
      case 'processing':
        if (localCapture() && running && !suppressStarts) { waitingReply = true; speechPaused = true; clearRecording(); controls(); }
        status(({transcribing:'Mengubah ucapan menjadi teks…',thinking:'Menyusun jawaban…',synthesizing:'Teks selesai. Menyiapkan suara…'})[data.stage] || 'Memproses…'); break;
      case 'transcript_partial':
        if (previewEnabled() && running && !muted && !pendingStart && !waitingReply && !speechPaused && data.utterance_id === captureGeneration && data.revision > previewRevision) {
          previewRevision = data.revision;
          $('local-draft').textContent = 'Sementara · dapat direvisi: ' + voiceDisplay(data.text);
        }
        break;
      case 'transcript': $('local-draft').textContent = ''; lastTranscript = data.text; renderHistory(); break;
      case 'response_start':
        if (suppressStarts) { blocked.add(tokenKey(data.token)); break; }
        if (blocked.has(tokenKey(data.token)) || tokenKey(data.token) === tokenKey(latestToken)) break;
        // Epoch/generation only have meaning inside the same server session.
        if (latestToken && data.token?.session_id === latestToken.session_id && data.token?.conversation_id === latestToken.conversation_id && (data.token.epoch < latestToken.epoch || (data.token.epoch === latestToken.epoch && data.token.generation < latestToken.generation))) break;
        latestToken = data.token;
        cancelPlayback(true); active = data.token; sealed = false; liveText = ''; lastTranscript = '';
        if (localCapture() && running) { waitingReply = true; speechPaused = true; clearRecording(); }
        status('Menyusun jawaban…'); controls(); break;
      case 'text_delta':
        if (!sealed && tokenKey(active) === tokenKey(data.token) && !blocked.has(tokenKey(data.token))) { liveText += data.text; renderHistory(); }
        break;
      case 'delivery_status': {
        if (config?.delivery_capabilities?.automatic_conditioning_enabled !== true || !active || sealed
            || suppressStarts || tokenKey(active) !== tokenKey(data.token) || blocked.has(tokenKey(data.token))) break;
        const choice = data.choice;
        if (!choice || !['profile_id','activation','intended_emotion','intensity'].every(key => typeof choice[key] === 'string' && choice[key].length <= 160)
            || typeof choice.supported !== 'boolean') break;
        // Diagnostics only: never modify dialogue, history, or outgoing text.
        deliveryDiagnostic = ' · Aktivasi B otomatis (eksperimental): ' + choice.activation + ' / ' + choice.profile_id
          + ' · intent ' + choice.intended_emotion + ' / ' + choice.intensity
          + (choice.supported ? '' : ' · rute tidak didukung; fallback B');
        status(processingStatus);
        break;
      }
      case 'audio':
        // response_done seals text, not audio still in flight for this token.
        if (active && tokenKey(active) === tokenKey(data.token) && !blocked.has(tokenKey(data.token))) {
          if ((browserSelected() || localCapture()) && running) { speechPaused = true; clearRecording(); abortRecognition(); }
          queue.push(data); void drainAudio();
        }
        break;
      case 'response_done':
        if (active && tokenKey(active) === tokenKey(data.token)) { sealed = true; renderHistory(); if (!currentSource && !queue.length && draining !== playbackEpoch) { status('Jawaban selesai. Silakan lanjutkan.'); if (localCapture()) clearRecording(); waitingReply = false; speechPaused = false; startRecognition(); controls(); } }
        break;
      case 'interrupted': if (localCapture()) clearRecording(); waitingReply = false; cancelPlayback(); server = data.state || server; renderHistory(); status('Jawaban dihentikan.'); controls(); break;
      case 'error': {
        if (data.command === 'start' && data.start_id && data.start_id !== pendingStart) break;
        if (pendingStart) releaseMedia();
        if (localCapture()) clearRecording();
        // An error can arrive after response_done while accepted audio still
        // plays, decodes, or waits in the bounded playback queue. Keep half-
        // duplex capture paused until drainAudio's actual completion path.
        const playbackPending = currentSource || queue.length || draining === playbackEpoch;
        const keepPaused = Boolean(running && (localCapture() || browserSelected()) && playbackPending);
        waitingReply = keepPaused; speechPaused = keepPaused;
        error(data.message || 'Terjadi kesalahan pada server.');
        status('Periksa pesan di atas, lalu coba lagi.');
        if (!keepPaused) startRecognition();
        controls(); break;
      }
    }
  }
  async function connect() {
    if (ws?.readyState === WebSocket.OPEN) return;
    if (connecting) return connecting;
    connecting = new Promise((resolve, reject) => {
      const socket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
      // A reconnect owns a new server session; never reuse its predecessor's open flag.
      server = null;
      latestToken = null;
      ws = socket;
      socket.onmessage = event => { if (ws === socket && socket.readyState === WebSocket.OPEN) handle(event); };
      socket.onopen = () => { controls(); status(speechAvailable() ? 'Siap. Tekan Mulai untuk mengaktifkan mikrofon, atau tulis pesan.' : speechHint()); resolve(); };
      socket.onerror = () => { reject(new Error('Koneksi server gagal. Pastikan server lokal berjalan.')); };
      socket.onclose = event => {
        if (ws !== socket) return;
        connecting = null; lifecycle++; starting = false; releaseMedia(); cancelPlayback();
        if (event?.code === 1008) location.replace('/login');
        else void fetch('/api/config', {cache:'no-store'}).then(response => { if (response.status === 401) location.replace('/login'); }).catch(() => {});
        status('Koneksi terputus. Mulai atau kirim pesan untuk mencoba kembali.'); controls(); reject(new Error('Koneksi server terputus.'));
      };
    });
    try { await connecting; } finally { connecting = null; }
  }
  function releaseMedia() {
    pendingStart = null; clearTimeout(startTimer); startTimer = null;
    running = false; waitingReply = false; abortRecognition(); pressed = false; muted = false; clearRecording();
    if (worklet) { worklet.port.onmessage = null; worklet.disconnect(); worklet = null; }
    if (micSource) { micSource.disconnect(); micSource = null; }
    if (stream) { stream.getTracks().forEach(track => track.stop()); stream = null; }
    if (captureContext) { void captureContext.close().catch(() => {}); captureContext = null; }
    ui.level.value = 0; $('voice-orb').style.setProperty('--level', 0);
    $('level-label').textContent = 'Level mikrofon · tidak aktif'; controls();
  }
  function stop() {
    lifecycle++; starting = false;
    if (pressed) send({type:'release'});
    send({type:'stop'}); releaseMedia(); cancelPlayback();
    status('Percakapan diakhiri. Mikrofon sudah dilepas.');
  }
  function flushUtterance(ptt = false) {
    if (samples && speechSamples / captureRate >= 0.25) {
      const joined = new Float32Array(samples); let offset = 0;
      for (const chunk of chunks) { joined.set(chunk, offset); offset += chunk.length; }
      const pcm = pcm16(joined, captureRate);
      let binary = '';
      for (let i = 0; i < pcm.length; i += 8192) binary += String.fromCharCode(...pcm.subarray(i, i + 8192));
      if (ws?.bufferedAmount > 2000000) error('Koneksi terlalu lambat. Ucapan tidak dikirim; coba lagi.');
      else { suppressStarts = false; if (send({type:'audio', pcm_b64:btoa(binary), sample_rate:16000, ...(previewEnabled() ? {utterance_id:captureGeneration} : {})}) && localCapture()) { waitingReply = true; speechPaused = true; } }
      status('Ucapan dikirim. Menunggu transkripsi…');
    }
    clearRecording();
    if (ptt) { pressed = false; send({type:'release'}); }
    controls();
  }
  function captureBlock(event) {
    if (!running || muted || pendingStart || (localCapture() && (waitingReply || speechPaused)) || ws?.readyState !== WebSocket.OPEN) return;
    if (event.data.generation !== captureGeneration) return;
    const {pcm, rms} = event.data;
    const level = Math.min(1, rms * 5);
    ui.level.value = level; $('voice-orb').style.setProperty('--level', level.toFixed(3));
    $('level-label').textContent = 'Level mikrofon · ' + Math.round(level * 100) + '%';
    // A bounded RMS gate, not a speech classifier. Learn only below-gate idle
    // audio so an ongoing quiet sentence cannot raise its own start threshold.
    const sensitivity = Number($('sensitivity').value);
    const threshold = localCapture() ? Math.max(0.016 - sensitivity * 0.00014, noiseFloor * 2.5) : 0.055 - sensitivity * 0.0005;
    const voiced = rms >= threshold;
    if (localCapture() && !utterance && !pressed && !voiced) {
      noiseFloor += (rms - noiseFloor) * (1 - Math.exp(-pcm.length / captureRate));
    }
    if (mode === 'push_to_talk' && !pressed) return;
    if (mode === 'hands_free' && !utterance) {
      if (!voiced) {
        if (localCapture()) {
          preRoll.push(pcm); preRollSamples += pcm.length;
          const limit = Math.floor(captureRate * PRE_ROLL_SECONDS);
          while (preRollSamples > limit) {
            const excess = preRollSamples - limit;
            if (excess >= preRoll[0].length) preRollSamples -= preRoll.shift().length;
            else { preRoll[0] = preRoll[0].slice(excess); preRollSamples -= excess; }
          }
        }
        return;
      }
      chunks = preRoll; samples = preRollSamples; preRoll = []; preRollSamples = 0;
      utterance = true;
      if (!localCapture() && (active || currentSource || server?.responding || server?.speaking)) { cancelPlayback(); send({type:'interrupt'}); }
      controls();
    }
    const remaining = Math.max(0, Math.floor(maxSeconds * captureRate) - samples);
    const chunk = pcm.length > remaining ? pcm.slice(0, remaining) : pcm;
    chunks.push(chunk); samples += chunk.length;
    if (voiced) { speechSamples += chunk.length; silenceSamples = 0; } else silenceSamples += chunk.length;
    if (samples / captureRate >= maxSeconds || (mode === 'hands_free' && silenceSamples / captureRate >= (localCapture() ? 1.2 : 0.9))) flushUtterance(mode === 'push_to_talk');
    else if (previewEnabled() && samples - previewSamples >= captureRate * previewSeconds() && ws.bufferedAmount <= 200000) {
      // Independent snapshots: only one bounded growing utterance, no text
      // heuristics. A stateful backend consumes only the unseen PCM suffix.
      // Final uses the same stable whole-utterance resampling path.
      const joined = new Float32Array(samples); let offset = 0;
      for (const part of chunks) { joined.set(part, offset); offset += part.length; }
      const bytes = pcm16(joined, captureRate); let binary = '';
      for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
      if (send({type:'audio_preview', utterance_id:captureGeneration, pcm_b64:btoa(binary), sample_rate:16000})) previewSamples = samples;
    }
  }
  async function start() {
    if (running || starting) { if (running) stop(); return; }
    if (!speechAvailable()) { error(speechHint()); return; }
    error('');
    if (!window.isSecureContext) { error('Mikrofon memerlukan HTTPS atau localhost. Pesan tertulis tetap bisa digunakan.'); return; }
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!browserSelected() && (!navigator.mediaDevices?.getUserMedia || !Audio || !window.AudioWorkletNode)) { error('Browser ini tidak mendukung mikrofon + AudioWorklet. Gunakan browser modern, atau kirim pesan tertulis.'); return; }
    const attempt = ++lifecycle; starting = true; controls();
    try {
      await playbackReady();
      await connect();
      if (attempt !== lifecycle) return;
      if (browserSelected()) {
        mode = 'hands_free'; muted = false; running = true; recognitionRetries = 0;
        beginSession(attempt);
        return;
      }
      const acquired = await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
      if (attempt !== lifecycle) { acquired.getTracks().forEach(track => track.stop()); return; }
      stream = acquired;
      captureContext = new Audio(); // Device rate, not forced to 16 kHz.
      if (!captureContext.audioWorklet) throw new Error('AudioWorklet tidak tersedia; pesan tertulis tetap bisa digunakan.');
      const context = captureContext;
      await context.audioWorklet.addModule('/pcm-worklet.js');
      if (attempt !== lifecycle) return;
      await context.resume();
      captureRate = context.sampleRate;
      noiseFloor = 0.003;
      micSource = context.createMediaStreamSource(stream);
      worklet = new AudioWorkletNode(context, 'pcm-collector');
      worklet.port.onmessage = event => { if (attempt === lifecycle && captureContext === context) captureBlock(event); };
      clearRecording();
      micSource.connect(worklet); worklet.connect(context.destination);
      mode = document.querySelector('input[name="mode"]:checked').value;
      muted = false; running = true;
      beginSession(attempt);
      for (const track of stream.getTracks()) track.onended = () => { if (running && attempt === lifecycle && captureContext === context) { stop(); error('Mikrofon terputus. Sambungkan perangkat lalu mulai kembali.'); } };
    } catch (e) {
      if (attempt === lifecycle) {
        releaseMedia();
        const message = ({NotAllowedError:'Izin mikrofon ditolak. Izinkan di pengaturan browser, atau gunakan pesan tertulis.',NotFoundError:'Tidak ada mikrofon yang ditemukan. Sambungkan perangkat, atau gunakan pesan tertulis.',NotReadableError:'Mikrofon tidak dapat dibuka; mungkin sedang dipakai aplikasi lain.'})[e.name] || e.message;
        error(message); status('Suara belum aktif. Pesan tertulis tetap tersedia.');
      }
    } finally { if (attempt === lifecycle) starting = false; controls(); }
  }
  function press(event) {
    if (!running || muted || pressed || mode !== 'push_to_talk') return;
    if (event?.button !== undefined && event.button !== 0) return;
    if (event?.pointerId !== undefined) ui.ptt.setPointerCapture(event.pointerId);
    clearRecording(); pressed = true;
    if (localCapture()) { waitingReply = false; suppressStarts = true; }
    cancelPlayback(); send({type:'interrupt'}); send({type:'press'}); controls();
  }
  function release() { if (pressed) flushUtterance(true); }
  $('browser-consent').addEventListener('change', controls);
  ui.start.addEventListener('click', () => void start());
  ui.ptt.addEventListener('pointerdown', press);
  for (const name of ['pointerup','pointercancel','lostpointercapture']) ui.ptt.addEventListener(name, release);
  ui.ptt.addEventListener('keydown', event => { if ((event.code === 'Space' || event.code === 'Enter') && !event.repeat) { event.preventDefault(); press(); } });
  ui.ptt.addEventListener('keyup', event => { if (event.code === 'Space' || event.code === 'Enter') { event.preventDefault(); release(); } });
  window.addEventListener('blur', release);
  ui.mute.addEventListener('click', () => {
    const next = !muted;
    if (pressed) { clearRecording(); pressed = false; send({type:'release'}); }
    clearRecording(); muted = next;
    if (muted) abortRecognition(); else if (browserSelected()) startRecognition();
    if (stream) stream.getAudioTracks().forEach(track => { track.enabled = !muted; });
    ui.level.value = 0; $('voice-orb').style.setProperty('--level', 0);
    send({type:'mute', muted}); controls(); status(muted ? 'Mikrofon dibisukan. Rekaman yang belum dikirim dibuang.' : 'Mikrofon aktif kembali.');
  });
  ui.interrupt.addEventListener('click', interrupt);
  ui.provider.addEventListener('change', () => { send({type:'provider', provider:ui.provider.value}); updateModel(); });
  function updateModel() { $('model-label').textContent = ui.provider.selectedOptions[0]?.textContent || 'AI API'; }
  $('settings-toggle').addEventListener('click', () => { $('settings').hidden = !$('settings').hidden; $('settings-toggle').setAttribute('aria-expanded', String(!$('settings').hidden)); });
  $('history-toggle').addEventListener('click', () => { const visible = $('history-panel').classList.toggle('visible'); $('history-toggle').setAttribute('aria-expanded', String(visible)); });
  $('sensitivity').addEventListener('input', () => { const value = Number($('sensitivity').value); $('sensitivity-value').textContent = value < 35 ? 'Rendah' : value > 70 ? 'Tinggi' : 'Sedang'; });
  $('text-form').addEventListener('submit', async event => {
    event.preventDefault(); const input = $('text-input'), text = input.value.trim(); if (!text) return;
    error(''); ui.send.disabled = true;
    try {
      await playbackReady(); await connect();
      if (!running && !server?.open) send({type:'start', mode:document.querySelector('input[name="mode"]:checked').value, provider:ui.provider.value});
      cancelPlayback(); suppressStarts = false; send({type:'text', text}); input.value = ''; status('Pesan dikirim. Menyusun jawaban…');
    } catch (e) { error(e.message); } finally { controls(); }
  });
  $('text-input').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('text-form').requestSubmit(); } });
  window.addEventListener('pagehide', () => { stop(); ws?.close(); if (playbackContext) void playbackContext.close().catch(() => {}); });
  $('logout').addEventListener('click', async () => {
    stop(); ws?.close();
    try {
      const response = await fetch('/api/logout', {method:'POST', credentials:'same-origin', cache:'no-store'});
      if (response.ok || response.status === 401) location.replace('/login');
      else error('Keluar gagal. Coba lagi.');
    } catch (_) { error('Koneksi gagal. Coba keluar kembali.'); }
  });
  async function init() {
    try {
      const response = await fetch('/api/config', {cache:'no-store'});
      if (response.status === 401) { location.replace('/login'); return; }
      if (!response.ok) throw new Error('Konfigurasi server tidak dapat dimuat. Muat ulang setelah server siap.');
      config = await response.json();
      if (!Array.isArray(config.providers) || !config.providers.length) throw new Error('Server belum menyediakan model.');
      ui.provider.replaceChildren(...config.providers.map(provider => { const option = document.createElement('option'); option.value = provider.id; option.textContent = provider.label; return option; }));
      ui.provider.value = config.default_provider; maxSeconds = Math.min(20, config.max_audio_seconds || 20); updateModel(); controls();
      $('delivery-note').textContent = 'AI menggunakan API. ' + (config.ai_delivery === 'streaming' ? 'Teks diterima bertahap dari API, lalu suara disiapkan.' : 'Teks ditampilkan setelah jawaban API selesai, lalu suara disiapkan.') + ' Bukan percakapan suara native real-time.';
      $('tts-note').textContent = 'Suara jawaban: ' + (config.tts_label || 'sintesis suara sesuai konfigurasi server') + '. Suara sintetis, bukan suara native model AI.';
      if (config.tts_delivery === 'chunked') $('tts-note').textContent += ' Suara jawaban panjang diputar bertahap tanpa dipotong pada 30 detik.';
      if (config.tts_identity?.backend === 'supertonic3') {
        const styleProfile = config.tts_identity.voice_style_profile;
        if (styleProfile === 'conditioning_b') {
          $('tts-note').textContent += config.delivery_capabilities?.automatic_conditioning_enabled === true
            ? ' Profil B pilihan pengguna dengan aktivasi otomatis eksperimental di sekitar B, berdasarkan intent penyampaian jawaban. Bukan emosi terlatih yang berbeda, bukan bukti identitas pembicara tidak berubah, dan bukan pendeteksi emosi pengguna.'
            : ' Profil conditioning B pilihan pengguna, diterapkan pada teks dinamis; bukan rekaman tetap dan bukan pengendali otomatis semua emosi. Bukan pendeteksi emosi.';
        } else if (styleProfile && styleProfile !== 'original') {
          $('tts-note').textContent += ' Profil gaya ditetapkan server; bukan pendeteksi emosi.';
        } else {
          $('tts-note').textContent += ' Preset M1 tetap; bukan pendeteksi emosi.';
        }
        $('tts-note').textContent += ' Penggunaan suara mengikuti batasan lisensi model: ';
        const terms = document.createElement('a');
        terms.href = '/supertonic3-LICENSE.txt'; terms.target = '_blank'; terms.rel = 'noopener noreferrer';
        terms.textContent = 'OpenRAIL-M (termasuk batasan penggunaan pada Attachment A)';
        $('tts-note').append(terms);
      }
      speechChoice();
      await connect();
    } catch (e) { error(e.message); controls(); }
  }
  void init();
})();
