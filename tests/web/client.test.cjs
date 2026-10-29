const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {tokenKey} = require('../../web/audio-core.js');

// Execute the shipping client, with only browser/platform boundaries replaced.
// These tests do not establish real-device microphone or audio compatibility.
class Element {
  set innerHTML(value) { throw new Error('HTML injection forbidden: ' + value); }
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.listeners = {}; this.attributes = {};
    this.textContent = ''; this.value = ''; this.disabled = false; this.hidden = false;
    this.scrollHeight = this.scrollTop = this.clientHeight = 0;
    const classes = new Set();
    this.classList = {toggle(name, force) { const next = force === undefined ? !classes.has(name) : force; if (next) classes.add(name); else classes.delete(name); return next; }, contains: name => classes.has(name)};
    this.style = {setProperty() {}};
  }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  async emit(name, event = {}) { for (const listener of this.listeners[name] || []) await listener({preventDefault() {}, ...event}); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  get selectedOptions() { return this.children.filter(child => child.value === this.value); }
  requestSubmit() { return this.emit('submit'); }
}
const settle = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); };
async function boot(overrides = {}, options = {}) {
  const html = fs.readFileSync(require('node:path').resolve(__dirname, '../../web') + '/index.html', 'utf8');
  const elements = {};
  for (const match of html.matchAll(/<([\w-]+)\b([^>]*\bid="([^"]+)"[^>]*)>([^<]*)/g)) {
    const el = elements[match[3]] = new Element(match[1]);
    el.textContent = match[4]; el.disabled = /\bdisabled\b/.test(match[2]); el.hidden = /\bhidden\b/.test(match[2]);
    el.value = /\bvalue="([^"]*)"/.exec(match[2])?.[1] || '';
  }
  const surface = new Element();
  const document = {getElementById: id => elements[id], createElement: tag => new Element(tag), querySelector: selector => selector === '.voice-surface' ? surface : {value:options.mode || 'hands_free'}};
  const sockets = [], sources = [], decodes = [], redirects = [], fetches = [], recognitions = [], timers = [], windowEvents = {};
  class Recognition {
    constructor() { this.processLocally = true; recognitions.push(this); }
    start() { if (options.startError) throw new Error('service unavailable'); this.started = true; }
    abort() { this.aborted = true; }
    result(...entries) { this.onresult?.({resultIndex:0, results:entries.map(([text, final = true]) => Object.assign([{transcript:text}], {isFinal:final}))}); }
    end() { this.onend?.(); }
  }
  let microphoneRequests = 0;
  const tracks = [], contexts = [], worklets = [], modules = [];
  let Collector;
  vm.runInNewContext(fs.readFileSync(require('node:path').resolve(__dirname, '../../web') + '/pcm-worklet.js', 'utf8'), {
    AudioWorkletProcessor:class { constructor() { this.port = {postMessage:data => this.deliver?.(data)}; } },
    registerProcessor(name, processor) { assert.equal(name, 'pcm-collector'); Collector = processor; }, Float32Array
  });
  class Worklet {
    constructor(context, name) {
      assert.equal(name, 'pcm-collector'); this.port = {}; this.context = context;
      this.collector = new Collector(); this.collector.deliver = data => this.port.onmessage?.({data});
      this.port.postMessage = data => this.collector.port.onmessage?.({data}); worklets.push(this);
    }
    connect() {} disconnect() { this.disconnected = true; }
  }
  class Socket {
    static OPEN = 1;
    constructor() { this.bufferedAmount = 0; this.readyState = 0; this.sent = []; sockets.push(this); if (!options.manualOpen) queueMicrotask(() => this.open()); }
    open() { this.readyState = 1; this.onopen?.(); }
    send(value) { const message = JSON.parse(value); this.sent.push(message); if (message.type === 'start' && message.start_id && !options.manualSessionReady) queueMicrotask(() => this.receive({type:'session_ready',start_id:message.start_id})); }
    receive(data) { this.onmessage?.({data:JSON.stringify(data)}); }
    close(code) { this.readyState = 3; this.onclose?.({code}); }
  }
  class Audio {
    constructor() { this.state = 'running'; this.destination = {}; this.sampleRate = 48000; contexts.push(this); this.audioWorklet = {addModule:async path => { modules.push(path); }}; }
    createMediaStreamSource() { return {connect() {}, disconnect() { this.disconnected = true; }}; }
    async resume() { this.state = 'running'; }
    async close() { this.state = 'closed'; }
    decodeAudioData() { return options.deferredDecode ? new Promise(resolve => decodes.push(resolve)) : Promise.resolve({}); }
    createBufferSource() {
      const source = {connect() {}, start() { this.started = true; }, stop() { this.stopped = true; }, end() { this.onended?.(); }};
      sources.push(source); return source;
    }
  }
  const config = {providers:[{id:'hermes', label:'Hermes API · gpt-6.1-sol'}], default_provider:'hermes', max_audio_seconds:20, stt_available:false, stt_reason:'STT API belum dikonfigurasi.', tts_label:'eSpeak · suara sintetis Indonesia', ai_delivery:'buffered', session_ack_supported:true, ...overrides};
  const getUserMedia = async () => {
    microphoneRequests++;
    if (!options.pcm) throw new Error('No real microphone in Node');
    const track = {enabled:true, stop() { this.stopped = true; }}; tracks.push(track);
    return {getTracks:() => [track], getAudioTracks:() => [track]};
  };
  const inbound = [];
  const wireJSON = {stringify:JSON.stringify, parse(value) {
    const data = JSON.parse(value);
    if (options.freezeInbound) {
      const freeze = object => { if (object && typeof object === 'object') { Object.values(object).forEach(freeze); Object.freeze(object); } };
      freeze(data);
    }
    inbound.push(data); return data;
  }};
  const context = {JSON:wireJSON, AudioWorkletNode:Worklet, document, window:{isSecureContext:true, AudioContext:Audio, AudioWorkletNode:Worklet, ...(options.recognition ? {[options.prefixed ? 'webkitSpeechRecognition' : 'SpeechRecognition']:Recognition} : {}), addEventListener(name, fn) { windowEvents[name] = fn; }}, navigator:{mediaDevices:{getUserMedia}}, location:{protocol:'http:',host:'localhost',replace:url=>redirects.push(url)}, WebSocket:Socket, fetch:async (url, settings) => { fetches.push([url, settings]); return options.response || {ok:true,status:200,json:async () => config}; }, VoiceAudio:require('../../web/audio-core.js'), atob:value => Buffer.from(value,'base64').toString('binary'), btoa:value => Buffer.from(value,'binary').toString('base64'), console, Uint8Array, Float32Array, setTimeout:(fn, delay) => { const timer = {fn, delay}; timers.push(timer); return timer; }, clearTimeout:timer => { if (timer) timer.canceled = true; }};
  vm.runInNewContext(fs.readFileSync(require('node:path').resolve(__dirname, '../../web') + '/app.js', 'utf8'), context, {filename:'app.js'});
  await settle();
  return {ui:elements, sockets, sources, decodes, config, redirects, fetches, recognitions, timers, windowEvents, tracks, contexts, worklets, modules, microphoneRequests:() => microphoneRequests,
    pcm(seconds, value = 0.2, node = worklets.at(-1)) {
      assert.ok(node, 'native PCM capture must create an AudioWorklet node');
      for (let frame = 0; frame < Math.ceil(seconds * node.context.sampleRate / 128); frame++) node.collector.process([[new Float32Array(128).fill(value)]], [[new Float32Array(128)]]);
    },
    finishSpeech() { const timer = timers.filter(t => !t.canceled && t.delay === 1200).at(-1); assert.ok(timer, 'utterance silence timer must be scheduled'); timer.fn(); },
    async submit(text = 'Halo') { elements['text-input'].value = text; await elements['text-form'].emit('submit'); await settle(); },
    receive(data) { sockets.at(-1).receive(data); },
    inbound,
    sent() { return sockets.flatMap(socket => socket.sent); }};
}
const token = (generation = 1, epoch = 1) => ({conversation_id:'c',session_id:'s',epoch,generation,provider:'hermes'});
const audio = (t, chunk_id = 0) => ({type:'audio',token:t,chunk_id,wav_b64:'AA=='});

const automaticCapabilities = {automatic_conditioning_enabled:true, automatic_conditioning_scope:'activation_only_experimental', native_emotion_conditioning:false, automatic_conditioning_emotion_ids:['neutral','joy','future_category']};
const deliveryStatus = (t, profile = 'fixture_active') => ({type:'delivery_status',token:t,choice:{profile_id:profile,activation:'active',intended_emotion:'future_category',intensity:'high',supported:true}});
test('automatic delivery is safe status-only, persists during processing, never dialogue', async () => {
  const app = await boot({delivery_capabilities:automaticCapabilities}, {freezeInbound:true});
  const t = token();
  app.receive({type:'response_start',token:t});
  app.receive({type:'text_delta',token:t,text:'Literal reply.'});
  const hostile = '<img src=x onerror=alert(1)>';
  app.receive(deliveryStatus(t, hostile));
  app.receive({type:'processing',stage:'synthesizing'});
  assert.match(app.ui.status.textContent,/aktivasi.*eksperimental/i);
  assert.ok(app.ui.status.textContent.includes(hostile));
  assert.match(app.ui.status.textContent,/future_category/);
  assert.equal(app.ui.status.children.length,0);
  assert.equal(app.ui.history.children.at(-1).children[1].textContent,'Literal reply.');
  assert.ok(!JSON.stringify(app.ui.history.children).includes(hostile));
  app.receive({type:'response_done',token:t});
  const done = app.ui.status.textContent;
  app.receive(deliveryStatus(t,'late'));
  assert.equal(app.ui.status.textContent,done,'sealed response rejects late status');
});
test('automatic delivery ignores absent tokens, replaced responses, epochs, interrupt and old sockets', async () => {
  const app = await boot({delivery_capabilities:automaticCapabilities});
  app.receive(deliveryStatus(token()));
  assert.ok(!app.ui.status.textContent.includes('fixture_active'));
  app.receive({type:'response_start',token:token(1,2)});
  app.receive(deliveryStatus(token(1,2),'current'));
  assert.ok(app.ui.status.textContent.includes('current'));
  app.receive({type:'response_start',token:token(2,2)});
  const clean = app.ui.status.textContent;
  for (const old of [token(1,2),token(9,1)]) app.receive(deliveryStatus(old,'stale'));
  assert.equal(app.ui.status.textContent,clean);
  app.receive(deliveryStatus(token(2,2),'replacement'));
  assert.ok(app.ui.status.textContent.includes('replacement'));
  await app.ui.interrupt.emit('click');
  const interrupted = app.ui.status.textContent;
  app.receive(deliveryStatus(token(2,2),'stale'));
  assert.equal(app.ui.status.textContent,interrupted);
  const oldSocket = app.sockets[0]; oldSocket.close(); await app.submit();
  app.receive({type:'response_start',token:token(3,2)});
  const reconnected = app.ui.status.textContent;
  oldSocket.readyState = 1; oldSocket.receive(deliveryStatus(token(3,2),'old socket'));
  assert.equal(app.ui.status.textContent,reconnected);
});
test('automatic B UI is activation-only experimental, old config remains compatible', async () => {
  const identity = {backend:'supertonic3',voice_style_profile:'conditioning_b'};
  const app = await boot({tts_identity:identity,delivery_capabilities:automaticCapabilities});
  assert.match(app.ui['tts-note'].textContent,/aktivasi.*otomatis.*eksperimental/i);
  assert.match(app.ui['tts-note'].textContent,/bukan.*emosi terlatih/i);
  assert.match(app.ui['tts-note'].textContent,/bukan pendeteksi emosi/i);
  const legacy = await boot({tts_identity:identity});
  const before = legacy.ui.status.textContent;
  legacy.receive({type:'response_start',token:token()});
  const thinking = legacy.ui.status.textContent;
  legacy.receive(deliveryStatus(token()));
  assert.equal(legacy.ui.status.textContent,thinking);
  assert.ok(!legacy.ui['tts-note'].textContent.includes('aktivasi otomatis'));
});

test('browser fallback requires visible explicit consent and feature support; text remains available', async () => {
  const unsupported = await boot({browser_stt_allowed:true});
  assert.ok(unsupported.ui['browser-consent'], 'visible opt-in checkbox is required');
  assert.equal(unsupported.ui['browser-consent'].disabled, true);
  assert.match(unsupported.ui['browser-help'].textContent, /tidak mendukung.*STT API/i);
  const app = await boot({browser_stt_allowed:true}, {recognition:true});
  assert.equal(app.ui.start.disabled, true);
  await app.ui.start.emit('click'); await settle();
  assert.equal(app.recognitions.length, 0);
  app.ui['browser-consent'].checked = true; await app.ui['browser-consent'].emit('change');
  assert.equal(app.ui.start.disabled, false);
  assert.match(app.ui['privacy-note'].textContent, /vendor browser/);
  await app.submit();
  assert.ok(app.sent().some(m => m.type === 'text'));
});

async function browserApp(options = {}) {
  const app = await boot({browser_stt_allowed:true}, {recognition:true, ...options});
  app.ui['browser-consent'].checked = true; await app.ui['browser-consent'].emit('change');
  await app.ui.start.emit('click'); await settle();
  return app;
}
test('client stays compatible with a backend that does not advertise session acknowledgment', async () => {
  const app = await boot({browser_stt_allowed:true,session_ack_supported:false}, {recognition:true});
  app.ui['browser-consent'].checked=true; await app.ui['browser-consent'].emit('change');
  await app.ui.start.emit('click'); await settle();
  assert.equal(app.sent().find(m=>m.type==='start').start_id,undefined);
  assert.equal(app.recognitions.length,1);
});
test('browser capture waits for matching session acknowledgment and ignores stale closed snapshots', async () => {
  const app = await boot({browser_stt_allowed:true}, {recognition:true,manualSessionReady:true});
  app.ui['browser-consent'].checked=true; await app.ui['browser-consent'].emit('change');
  await app.ui.start.emit('click'); await settle();
  const start=app.sent().find(m=>m.type==='start');
  assert.ok(start.start_id, 'start must carry a correlation identifier');
  assert.equal(app.recognitions.length,0);
  app.receive({type:'state',state:{open:false,history:[]}});
  app.receive({type:'session_ready',start_id:'stale-ack'});
  assert.equal(app.recognitions.length,0);
  app.receive({type:'session_ready',start_id:start.start_id});
  assert.equal(app.recognitions.length,1);
  assert.match(app.ui.status.textContent,/Mendengarkan/);
});
test('browser restart sends a fresh start even before the previous stop snapshot arrives', async () => {
  const app = await browserApp();
  app.receive({type:'state',state:{open:true,muted:false,history:[]}});
  await app.ui.start.emit('click');
  await app.ui.start.emit('click'); await settle();
  const kinds = app.sent().map(m=>m.type);
  const stopped = kinds.lastIndexOf('stop');
  assert.equal(kinds[stopped+1], 'start');
});
test('first finalized word does not end a spoken turn; later words and interim activity extend it', async () => {
  const app = await browserApp(); const rec = app.recognitions[0];
  rec.result(['Halo']);
  assert.equal(app.sent().filter(m=>m.type==='voice_text').length,0);
  assert.equal(rec.aborted,undefined);
  const firstDeadline=app.timers.filter(t=>t.delay===1200).at(-1);
  rec.result(['Halo'], ['saya ingin berbicara',false]);
  firstDeadline.fn();
  assert.equal(app.sent().filter(m=>m.type==='voice_text').length,0,'ongoing interim speech blocks an old deadline');
  rec.result(['Halo'], ['saya ingin berbicara dengan kamu']);
  assert.equal(app.sent().filter(m=>m.type==='voice_text').length,0);
  app.finishSpeech();
  assert.deepEqual(app.sent().filter(m=>m.type==='voice_text'),[{type:'voice_text',text:'Halo saya ingin berbicara dengan kamu'}]);
  assert.equal(rec.aborted,true);
});
test('cumulative recognition revisions are not concatenated into repeated prefixes', async () => {
  const app = await browserApp(); const rec = app.recognitions[0];
  rec.result(['Apakah']);
  rec.result(['Apakah'], ['Apakah sekarang']);
  rec.result(['Apakah'], ['Apakah sekarang'], ['Apakah sekarang'], ['Apakah sekarang sudah']);
  rec.result(['Apakah'], ['Apakah sekarang'], ['Apakah sekarang'], ['Apakah sekarang sudah'], ['Apakah sekarang sudah normal'], ['Apakah sekarang sudah normal']);
  app.finishSpeech();
  assert.deepEqual(app.sent().filter(m=>m.type==='voice_text'),[{type:'voice_text',text:'Apakah sekarang sudah normal'}]);
});
test('updated final at one result index replaces its earlier hypothesis', async () => {
  const app=await browserApp(); const rec=app.recognitions[0];
  rec.result(['Apakah']); rec.result(['Apakah sekarang sudah normal']);
  app.finishSpeech();
  assert.deepEqual(app.sent().filter(m=>m.type==='voice_text'),[{type:'voice_text',text:'Apakah sekarang sudah normal'}]);
});
test('intentional repeated words inside a final segment are preserved', async () => {
  const app=await browserApp();
  app.recognitions[0].result(['Saya benar benar ingin mencoba lagi']); app.finishSpeech();
  assert.equal(app.sent().find(m=>m.type==='voice_text').text,'Saya benar benar ingin mencoba lagi');
});
test('finalized speech survives recognizer restart until the utterance pause', async () => {
  const app = await browserApp(); const first = app.recognitions[0];
  first.result(['Halo']); first.end();
  assert.equal(app.sent().filter(m=>m.type==='voice_text').length,0);
  const reconnect = app.timers.filter(t=>!t.canceled && t.delay===400).at(-1);
  reconnect.fn(); await settle();
  app.recognitions[1].result(['saya ingin bertanya']);
  app.finishSpeech();
  assert.deepEqual(app.sent().filter(m=>m.type==='voice_text'),[{type:'voice_text',text:'Halo saya ingin bertanya'}]);
});
for (const action of ['mute','stop','logout','pagehide','socket']) {
  test(`pending utterance cannot leak after ${action}`, async () => {
    const app = await browserApp(); app.recognitions[0].result(['belum dikirim']);
    const deadline=app.timers.filter(t=>t.delay===1200).at(-1);
    if (action==='socket') app.sockets[0].close(1008);
    else if (action==='pagehide') app.windowEvents.pagehide();
    else await app.ui[action==='stop'?'start':action].emit('click');
    deadline.fn(); await settle();
    assert.equal(app.sent().filter(m=>m.type==='voice_text').length,0);
  });
}

test('browser final batch is one voice turn and later results cannot flood busy Codex', async () => {
  const app = await browserApp();
  const rec = app.recognitions[0];
  rec.result(['Kalimat satu.'], ['Kalimat dua.'], ['Kalimat tiga.']);
  app.finishSpeech();
  assert.deepEqual(app.sent().filter(m=>m.type==='voice_text'), [{type:'voice_text',text:'Kalimat satu. Kalimat dua. Kalimat tiga.'}]);
  assert.equal(rec.aborted,true);
  rec.result(['Hasil terlambat.']); rec.end(); await settle();
  assert.equal(app.sent().filter(m=>m.type==='voice_text').length,1);
  assert.equal(app.recognitions.length,1);
  const t=token(); app.receive({type:'response_start',token:t});
  assert.equal(app.recognitions.length,1,'wait for reply playback, not only response start');
  app.receive(audio(t)); app.receive({type:'response_done',token:t}); await settle();
  app.sources[0].end(); await settle();
  assert.equal(app.recognitions.length,2);
});

test('consented browser recognition sends final Indonesian voice_text once without typing or API capture', async () => {
  const app = await browserApp({prefixed:true});
  assert.equal(app.recognitions.length, 1);
  const rec = app.recognitions[0];
  assert.equal(rec.lang, 'id-ID'); assert.equal(rec.processLocally, false);
  assert.equal(rec.continuous, true); assert.equal(rec.interimResults, true);
  assert.equal(app.microphoneRequests(), 0);
  assert.ok(app.sent().some(m => m.type === 'start' && m.mode === 'hands_free'));
  rec.result(['belum final', false]);
  assert.equal(app.sent().filter(m => m.type === 'voice_text').length, 0);
  rec.result([' Halo suara '], [' Halo suara '], ['belum final', false]);
  rec.result([' Halo suara ']); // cumulative result index replay
  app.finishSpeech();
  assert.deepEqual(app.sent().filter(m => m.type === 'voice_text'), [{type:'voice_text',text:'Halo suara'}]);
  assert.equal(app.ui['mode-options'].disabled, true);
  assert.match(app.ui['voice-description'].textContent, /bergantian/);
});

for (const action of ['stop','mute','logout','pagehide','socket','server-mute']) {
  test(`browser ${action} aborts capture and rejects late recognition callbacks`, async () => {
    const app = await browserApp(); const rec = app.recognitions[0];
    if (action === 'socket') app.sockets[0].close(1008);
    else if (action === 'pagehide') app.windowEvents.pagehide();
    else if (action === 'server-mute') app.receive({type:'state',state:{open:true,muted:true,history:[]}});
    else await app.ui[action === 'stop' ? 'start' : action].emit('click');
    await settle(); assert.equal(rec.aborted, true);
    rec.result(['stale final']); rec.end(); await settle();
    assert.equal(app.sent().filter(m => m.type === 'voice_text').length, 0);
    assert.equal(app.recognitions.length, 1);
    if (action === 'mute') {
      await app.ui.mute.emit('click'); await settle();
      assert.equal(app.recognitions.length, 2);
      app.recognitions[1].result(['baru']);
      app.finishSpeech();
      assert.equal(app.sent().filter(m => m.type === 'voice_text').length, 1);
    }
    if (action === 'socket' || action === 'logout') assert.deepEqual(app.redirects, ['/login']);
  });
}

test('browser half-duplex aborts recognition before decoding own audio and resumes only after playback ends', async () => {
  const app = await browserApp({deferredDecode:true}); const rec = app.recognitions[0]; const t = token();
  app.receive({type:'response_start',token:t});
  assert.equal(rec.aborted, undefined, 'may listen while AI thinks');
  app.receive(audio(t)); await settle();
  assert.equal(rec.aborted, true, 'pause before asynchronous decoding');
  rec.result(['own feedback']);
  assert.equal(app.sent().filter(m => m.type === 'voice_text').length, 0);
  app.receive({type:'response_done',token:t});
  assert.equal(app.recognitions.length, 1);
  app.decodes[0]({}); await settle();
  assert.equal(app.recognitions.length, 1);
  assert.match(app.ui['session-label'].textContent, /DIJEDA/);
  assert.match(app.ui.status.textContent, /bergantian/);
  assert.doesNotMatch(app.ui.status.textContent, /boleh menyela/);
  app.sources[0].end(); await settle();
  assert.equal(app.recognitions.length, 2);
  app.recognitions[1].result(['lanjut']);
  app.finishSpeech();
  assert.ok(app.sent().some(m => m.type === 'voice_text' && m.text === 'lanjut'));
});

for (const action of ['interrupt','server-interrupt','text','stop-restart']) {
  test(`browser ${action} releases half-duplex hold`, async () => {
    const app = await browserApp(); const t = token();
    app.receive({type:'response_start',token:t}); app.receive(audio(t)); await settle();
    if (action === 'interrupt') await app.ui.interrupt.emit('click');
    else if (action === 'server-interrupt') app.receive({type:'interrupted'});
    else if (action === 'text') await app.submit('typed turn');
    else { await app.ui.start.emit('click'); await app.ui.start.emit('click'); }
    await settle(); assert.equal(app.sources[0].stopped, true);
    assert.equal(app.recognitions.length, 2);
    app.recognitions[1].result(['lanjut']);
    app.finishSpeech();
    assert.ok(app.sent().some(m => m.type === 'voice_text'));
  });
}

test('browser service end restarts with bounded backoff and stops retries after repeated empty ends', async () => {
  const app = await browserApp();
  for (let i = 0; i < 5; i++) {
    app.recognitions.at(-1).end();
    const timer = app.timers.at(-1); assert.ok(timer && !timer.canceled);
    assert.ok(timer.delay >= 300 && timer.delay <= 8000);
    timer.fn(); await settle();
  }
  assert.equal(app.recognitions.length, 6);
  app.recognitions.at(-1).end(); await settle();
  assert.match(app.ui.error.textContent, /berulang|berkali/);
  assert.equal(app.ui.mute.disabled, true);
  app.timers.at(-1).fn();
  assert.equal(app.recognitions.length, 6);
});
for (const code of ['not-allowed','service-not-allowed','audio-capture','language-not-supported']) {
  test(`browser ${code} error stops without automatic retry and preserves text`, async () => {
    const app = await browserApp(); const rec = app.recognitions[0];
    rec.onerror?.({error:code}); rec.end(); await settle();
    assert.equal(rec.aborted, true); assert.equal(app.ui.mute.disabled, true);
    rec.result(['stale']); assert.equal(app.sent().filter(m => m.type === 'voice_text').length, 0);
    assert.match(app.ui.error.textContent, /teks|tertulis/);
    assert.equal(app.timers.filter(t => !t.canceled).length, 0);
    await app.submit(); assert.ok(app.sent().some(m => m.type === 'text'));
  });
}

test('supported missing-key UI guides consent instead of saying STT key required', async () => {
  const app = await boot({browser_stt_allowed:true,stt_reason:'STT API/key belum dikonfigurasi.'}, {recognition:true});
  assert.match(app.ui['voice-description'].textContent, /centang.*pengenalan.*daring/i);
  assert.doesNotMatch(app.ui['voice-description'].textContent, /key belum|API belum/);
  assert.match(app.ui.status.textContent, /pilih|centang/i);
});
test('typed session enables browser microphone with a fresh start while preserving displayed history', async () => {
  const app = await boot({browser_stt_allowed:true}, {recognition:true});
  await app.submit('histori');
  app.receive({type:'state',state:{open:true,history:[{role:'user',text:'histori'}]}});
  const count = app.sent().filter(m => m.type === 'start').length;
  app.ui['browser-consent'].checked = true; await app.ui['browser-consent'].emit('change');
  await app.ui.start.emit('click'); await settle();
  assert.equal(app.sent().filter(m => m.type === 'start').length, count + 1);
  assert.equal(app.ui.history.children[0].children[1].textContent, 'histori');
  app.recognitions[0].result(['x'.repeat(4001)]);
  assert.equal(app.sent().filter(m => m.type === 'voice_text').length, 0);
});
for (const action of ['mute','stop','playback']) {
  test(`pending browser restart cannot escape ${action}`, async () => {
    const app = await browserApp(); app.recognitions[0].end(); const timer = app.timers.at(-1);
    if (action === 'playback') { const t = token(); app.receive({type:'response_start',token:t}); app.receive(audio(t)); }
    else await app.ui[action === 'stop' ? 'start' : 'mute'].emit('click');
    timer.fn(); await settle(); assert.equal(app.recognitions.length, 1);
  });
}

test('browser startup failure does not falsely claim listening', async () => {
  const app = await browserApp({startError:true});
  assert.equal(app.ui.mute.disabled, true);
  assert.doesNotMatch(app.ui.status.textContent, /Mendengarkan/);
  assert.match(app.ui.error.textContent, /service unavailable/);
});

test('API capability descriptions disclose buffered AI, configured speech, and remote text privacy', async () => {
  const app = await boot();
  assert.match(app.ui['delivery-note']?.textContent || '', /Teks ditampilkan setelah jawaban API selesai/);
  assert.match(app.ui['tts-note']?.textContent || '', /eSpeak · suara sintetis Indonesia/);
  assert.match(app.ui['privacy-note']?.textContent || '', /Teks dikirim ke penyedia AI API/);
  assert.match(app.ui['privacy-note']?.textContent || '', /Mikrofon tidak dikirim/);
  assert.match(app.ui['model-label'].textContent, /Hermes API/);
  assert.equal(app.ui.provider.value, 'hermes');
  assert.equal(app.ui.provider.children.length, 1);
});

test('local server speech needs no browser recognition consent and discloses local audio with remote Codex text', async () => {
  const app = await boot({stt_mode:'local',stt_available:true,stt_label:'local faster-whisper · tiny',tts_label:'local Piper · id',browser_stt_allowed:false});
  assert.equal(app.ui.start.disabled, false);
  assert.equal(app.ui['browser-consent'].disabled, true);
  assert.match(app.ui['privacy-note'].textContent, /Audio diproses di server aplikasi, bukan layanan STT\/TTS eksternal\. Teks tetap dikirim ke Codex\./);
  assert.match(app.ui['browser-help'].textContent, /local faster-whisper/);
  assert.doesNotMatch(app.ui['browser-help'].textContent, /tidak mendukung|key/i);
  assert.match(app.ui['tts-note'].textContent, /local Piper/);
  assert.equal(app.microphoneRequests(), 0);
  assert.equal(app.recognitions.length, 0);
});

test('local STT offers an explicit disclosed browser choice and switches back without leaking capture', async () => {
  const app=await boot({stt_mode:'local',stt_available:true,browser_stt_allowed:true,tts_label:'Piper lokal'}, {recognition:true,pcm:true});
  assert.equal(app.ui['browser-consent'].disabled,false);
  assert.equal(app.recognitions.length,0);
  assert.equal(app.microphoneRequests(),0);
  assert.match(app.ui['browser-help'].textContent,/centang.*vendor browser/i);
  assert.match(app.ui['privacy-note'].textContent,/Audio diproses di server aplikasi/);
  app.ui['browser-consent'].checked=true;await app.ui['browser-consent'].emit('change');
  assert.match(app.ui['privacy-note'].textContent,/vendor browser/);
  await app.ui.start.emit('click');await settle();
  assert.equal(app.recognitions.length,1);
  assert.equal(app.recognitions[0].lang,'id-ID');
  assert.equal(app.recognitions[0].processLocally,false);
  assert.equal(app.microphoneRequests(),0);
  assert.equal(app.ui['browser-consent'].disabled,true);
  app.recognitions[0].result(['Kalimat browser.']);app.finishSpeech();
  assert.equal(app.sent().filter(m=>m.type==='voice_text').length,1);
  assert.equal(app.sent().filter(m=>m.type==='audio').length,0);
  await app.ui.start.emit('click');await settle();
  assert.equal(app.recognitions[0].aborted,true);
  app.ui['browser-consent'].checked=false;await app.ui['browser-consent'].emit('change');
  assert.match(app.ui['privacy-note'].textContent,/Audio diproses di server aplikasi/);
  await app.ui.start.emit('click');await settle();
  assert.equal(app.microphoneRequests(),1);
  assert.equal(app.recognitions.length,1);
  assert.equal(app.worklets.length,1);
});

test('failed browser startup offers local return without silently opening the local microphone', async () => {
  const app=await boot({stt_mode:'local',stt_available:true,browser_stt_allowed:true},{recognition:true,pcm:true,startError:true});
  app.ui['browser-consent'].checked=true;await app.ui['browser-consent'].emit('change');
  await app.ui.start.emit('click');await settle();
  assert.match(app.ui.error.textContent,/hapus centang.*STT lokal/i);
  assert.equal(app.microphoneRequests(),0);
  assert.equal(app.ui['browser-consent'].disabled,false);
  app.ui['browser-consent'].checked=false;await app.ui['browser-consent'].emit('change');
  await app.ui.start.emit('click');await settle();
  assert.equal(app.microphoneRequests(),1);
});

const localConfig = {stt_mode:'local',stt_available:true,stt_label:'local faster-whisper · tiny',tts_label:'local Piper · id',browser_stt_allowed:false};
async function localApp(options = {}, overrides = {}) {
  const app = await boot({...localConfig, ...overrides}, {pcm:true, ...options});
  await app.ui.start.emit('click'); await settle(); return app;
}
test('local quiet speech below the old RMS gate is captured, but quiet steady noise is not submitted', async () => {
  const app = await localApp();
  app.pcm(3, 0.003);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 0);
  app.pcm(0.6, 0.012); app.pcm(1.4, 0.003);
  const messages = app.sent().filter(m=>m.type==='audio');
  assert.equal(messages.length, 1, 'quiet voice must cross the local gate');
  const bytes = Buffer.from(messages[0].pcm_b64, 'base64');
  assert.ok(bytes.includes(Buffer.from([137, 1])), 'quiet speech remains in outgoing PCM');
});

test('local onset retains a bounded 300 ms pre-roll including soft initial phonemes, not the whole idle recording', async () => {
  const app = await localApp();
  app.pcm(4, 0.002); app.pcm(0.2, 0.004); app.pcm(0.6, 0.02); app.pcm(1.4, 0);
  const message = app.sent().find(m=>m.type==='audio'); assert.ok(message);
  const bytes = Buffer.from(message.pcm_b64, 'base64');
  const pcm = Array.from({length:bytes.length / 2}, (_, i) => bytes.readInt16LE(i * 2));
  const soft = pcm.findIndex(sample => sample >= 125 && sample <= 135);
  const loud = pcm.findIndex(sample => sample > 600);
  assert.ok(soft >= 0 && soft < loud, 'soft onset must precede the gate-crossing voice');
  assert.ok(pcm.filter(sample => sample >= 125 && sample <= 135).length >= 0.15 * 16000, 'retain the initial soft phoneme, not only the mixed onset block');
  assert.ok(loud <= 0.35 * 16000, 'pre-roll is bounded instead of accumulating all idle PCM');
});

test('local endpoint keeps a one-second thinking pause inside one turn and flushes after about 1.2 seconds', async () => {
  const app = await localApp();
  app.pcm(0.6); app.pcm(1, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 0, 'natural short pause is not an endpoint');
  app.pcm(0.4); app.pcm(1.1, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 0);
  app.pcm(0.16, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 1);
  assert.match(app.ui['voice-description'].textContent, /1,2 detik/);
});

test('local 20-second protocol limit includes pre-roll and trims the final worklet block exactly', async () => {
  const app = await localApp();
  app.pcm(2, 0.002); app.pcm(21, 0.02);
  const messages = app.sent().filter(m=>m.type==='audio');
  assert.equal(messages.length, 1);
  assert.equal(Buffer.from(messages[0].pcm_b64, 'base64').length, 20 * 16000 * 2);
  assert.deepEqual(Object.keys(messages[0]).sort(), ['pcm_b64','sample_rate','type']);
});

test('local PTT includes only post-press samples even when the worklet has a partly filled block', async () => {
  const app = await localApp({mode:'push_to_talk'});
  app.pcm(0.21, 0.004);
  await app.ui.ptt.emit('keydown', {code:'Space'});
  app.pcm(0.6, 0.02);
  await app.ui.ptt.emit('keyup', {code:'Space'});
  const messages = app.sent().filter(m=>m.type==='audio'); assert.equal(messages.length, 1);
  const bytes = Buffer.from(messages[0].pcm_b64, 'base64');
  for (let i = 0; i < bytes.length; i += 2) assert.ok(bytes.readInt16LE(i) > 600, 'no pre-press sample in payload');
  assert.equal(app.sent().filter(m=>m.type==='release').length, 1);
});

for (const boundary of ['mute','stop-restart','response-done','playback-ended','server-interrupt','server-error']) {
  test(`local ${boundary} discards both pre-roll and partly filled worklet audio before listening again`, async () => {
    const app = await localApp();
    app.pcm(0.21, 0.004);
    if (boundary === 'mute') { await app.ui.mute.emit('click'); app.pcm(0.21, 0.004); await app.ui.mute.emit('click'); }
    else if (boundary === 'stop-restart') { await app.ui.start.emit('click'); await app.ui.start.emit('click'); await settle(); }
    else {
      const t = token(); app.receive({type:'response_start',token:t});
      if (boundary === 'playback-ended') { app.receive(audio(t)); await settle(); }
      app.pcm(0.21, 0.004); // own reply / busy-period partial block must also be discarded
      if (boundary === 'server-interrupt') app.receive({type:'interrupted'});
      else if (boundary === 'server-error') app.receive({type:'error',message:'retry'});
      else { app.receive({type:'response_done',token:t}); if (boundary === 'playback-ended') { app.sources[0].end(); await settle(); } }
    }
    app.pcm(0.6, 0.02); app.pcm(1.3, 0);
    const messages = app.sent().filter(m=>m.type==='audio'); assert.equal(messages.length, 1);
    const bytes = Buffer.from(messages[0].pcm_b64, 'base64');
    assert.ok(!Array.from({length:bytes.length / 2}, (_, i) => bytes.readInt16LE(i * 2)).some(sample => sample >= 125 && sample <= 135), 'old soft marker cannot leak across boundary');
  });
}

test('local queued old-generation worklet messages cannot return after unmute', async () => {
  const app = await localApp(); const node = app.worklets[0], queued = [], deliver = node.collector.deliver;
  node.collector.deliver = data => queued.push(data);
  app.pcm(0.4, 0.2);
  await app.ui.mute.emit('click'); await app.ui.mute.emit('click');
  node.collector.deliver = deliver;
  for (const data of queued) deliver(data);
  app.pcm(0.6, 0.02); app.pcm(1.3, 0);
  const messages = app.sent().filter(m=>m.type==='audio'); assert.equal(messages.length, 1);
  const bytes = Buffer.from(messages[0].pcm_b64, 'base64');
  assert.ok(!Array.from({length:bytes.length / 2}, (_, i) => bytes.readInt16LE(i * 2)).some(sample => sample > 1000), 'old loud blocks must not join the new utterance');
});

test('local idle noise-floor adaptation rejects a modest background rise below the learned gate', async () => {
  const app = await localApp();
  app.pcm(3, 0.006); app.pcm(3, 0.01); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 0);
  app.pcm(0.6, 0.02); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 1, 'speech above the learned noise gate is still captured');
});

test('local capture help explains sensitivity tradeoffs and automatic onset/endpoint timing without promising acoustic VAD', async () => {
  const app = await localApp();
  assert.match(app.ui['sensitivity-help']?.textContent || '', /Naikkan.*suara pelan.*turunkan.*suara sekitar/);
  assert.match(app.ui['browser-help'].textContent, /300 ms/);
  assert.match(app.ui['browser-help'].textContent, /1,2 detik/);
  assert.match(app.ui['browser-help'].textContent, /bukan pendeteksi ucapan/);
});

test('a fresh local microphone session does not inherit the previous room noise gate', async () => {
  const app = await localApp();
  app.pcm(3, 0.006); app.pcm(3, 0.01);
  await app.ui.start.emit('click'); await app.ui.start.emit('click'); await settle();
  app.pcm(0.6, 0.012); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 1);
});

test('local PTT manually interrupts a pending turn, ignores its late start, and keeps the 20-second auto-release limit', async () => {
  const app = await localApp({mode:'push_to_talk'});
  await app.ui.ptt.emit('keydown', {code:'Space'}); app.pcm(0.6, 0.02); await app.ui.ptt.emit('keyup', {code:'Space'});
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 1);
  await app.ui.ptt.emit('keydown', {code:'Space'});
  app.receive({type:'response_start',token:token()});
  app.pcm(21, 0.02);
  const messages = app.sent().filter(m=>m.type==='audio'); assert.equal(messages.length, 2);
  assert.equal(Buffer.from(messages[1].pcm_b64, 'base64').length, 20 * 16000 * 2);
  assert.equal(app.sent().filter(m=>m.type==='release').length, 2);
  await app.ui.ptt.emit('keyup', {code:'Space'});
  assert.equal(app.sent().filter(m=>m.type==='release').length, 2, 'limit and subsequent keyup cannot release twice');
});

test('local PCM waits for correlated session ACK then sends the whole utterance once at 16 kHz', async () => {
  const app = await localApp({manualSessionReady:true});
  assert.equal(app.microphoneRequests(), 1);
  assert.deepEqual(app.modules, ['/pcm-worklet.js']);
  app.pcm(1); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 0, 'pre-ACK PCM cannot leave browser');
  const start = app.sent().find(m=>m.type==='start'); assert.ok(start.start_id);
  app.receive({type:'state',state:{open:false,history:[]}});
  app.receive({type:'session_ready',start_id:'old'});
  app.pcm(1); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 0);
  app.receive({type:'session_ready',start_id:start.start_id});
  assert.match(app.ui.status.textContent, /Mendengarkan.*1,2/);
  app.pcm(0.3); app.pcm(0.3, 0); // first word plus a short pause is not an entire turn
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 0);
  app.pcm(0.7); app.pcm(1.3, 0);
  const commands = app.sent().filter(m=>m.type==='audio'); assert.equal(commands.length, 1);
  assert.equal(commands[0].sample_rate, 16000);
  const bytes = Buffer.from(commands[0].pcm_b64, 'base64');
  assert.ok(bytes.length > 78000 && bytes.length < 85000, 'one whole bounded utterance including short gap and trailing pause');
  assert.ok(Array.from({length:683}, (_, i) => bytes.readInt16LE(i * 2)).some(sample => sample > 6000), 'first word is retained within the first worklet block');
  assert.equal(app.recognitions.length, 0);
  assert.equal(app.sent().some(m=>['voice_text','text'].includes(m.type)), false);
});

test('local PCM half-duplex drops busy audio and resumes after server audio actually ends without reopening microphone', async () => {
  const app = await localApp({deferredDecode:true});
  app.pcm(1); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 1);
  app.pcm(1); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 1, 'one pending transcription/LLM turn only');
  const t = token(); app.receive({type:'processing',stage:'thinking'}); app.receive({type:'response_start',token:t});
  app.pcm(1); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 1, 'thinking drops PCM');
  app.receive(audio(t)); app.receive({type:'response_done',token:t}); await settle();
  app.pcm(1); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 1, 'decode still holds capture');
  app.decodes[0]({}); await settle();
  assert.match(app.ui.status.textContent, /bergantian/);
  assert.doesNotMatch(app.ui.status.textContent, /boleh menyela/);
  assert.match(app.ui['session-label'].textContent, /DIJEDA/);
  app.pcm(1); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 1, 'own playback cannot be transcribed');
  app.sources[0].end(); await settle();
  assert.equal(app.sent().filter(m=>m.type==='ack').length, 1);
  app.pcm(1); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 2);
  assert.equal(app.sent().filter(m=>m.type==='interrupt').length, 0, 'no automatic acoustic barge-in');
  assert.equal(app.microphoneRequests(), 1);
  assert.equal(app.worklets.length, 1);
});

test('local fast stop/start rejects old worklet and track callbacks while matching ACK protects against stale snapshots', async () => {
  const app = await localApp({manualSessionReady:true});
  const first = app.sent().find(m=>m.type==='start'), oldNode = app.worklets[0];
  app.receive({type:'session_ready',start_id:first.start_id});
  const oldCallback = oldNode.port.onmessage, oldEnded = app.tracks[0].onended;
  app.pcm(0.5);
  await app.ui.start.emit('click'); await app.ui.start.emit('click'); await settle();
  const next = app.sent().filter(m=>m.type==='start').at(-1);
  assert.notEqual(first.start_id, next.start_id);
  assert.equal(app.tracks[0].stopped, true); assert.equal(app.contexts[1].state, 'closed');
  app.receive({type:'state',state:{open:false,muted:false,history:[]}});
  app.receive({type:'session_ready',start_id:first.start_id});
  app.pcm(1); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 0);
  app.receive({type:'session_ready',start_id:next.start_id});
  for (let i = 0; i < 48; i++) oldCallback({data:{pcm:new Float32Array(2048).fill(i < 24 ? 0.2 : 0),rms:i < 24 ? 0.2 : 0}});
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 0, 'queued frames from old capture are discarded');
  oldEnded();
  assert.equal(app.tracks[1].stopped, undefined, 'late old track ended cannot stop new capture');
  app.pcm(1); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 1);
});

test('manual local interrupt discards a partial utterance and resumes capture without leaking it', async () => {
  const app = await localApp();
  app.pcm(0.5);
  await app.ui.interrupt.emit('click');
  app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 0, 'interrupted partial buffer must be discarded');
  app.pcm(1); app.pcm(1.3, 0);
  const t = token(); app.receive({type:'response_start',token:t}); app.receive(audio(t)); await settle();
  await app.ui.interrupt.emit('click');
  assert.equal(app.sources[0].stopped, true);
  app.receive({type:'processing',stage:'thinking'}); // canceled response cannot re-pause capture
  app.pcm(1); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 2);
  assert.equal(app.microphoneRequests(), 1);
  assert.equal(app.sent().filter(m=>m.type==='ack').length, 0);
});

test('local status distinguishes pending session, half-duplex pause, and listening without claiming on-device/offline AI', async () => {
  const app = await localApp({manualSessionReady:true});
  assert.match(app.ui['session-label'].textContent, /MENUNGGU KONFIRMASI/);
  assert.doesNotMatch(app.ui['voice-title'].textContent, /Silakan bicara/);
  const start = app.sent().find(m=>m.type==='start'); app.receive({type:'session_ready',start_id:start.start_id});
  assert.match(app.ui['voice-description'].textContent, /server.*bergantian/);
  assert.match(app.ui['voice-description'].textContent, /Jeda/);
  app.pcm(1); app.pcm(1.3, 0);
  assert.match(app.ui['session-label'].textContent, /DIJEDA/);
  const t = token(); app.receive({type:'response_start',token:t}); app.receive({type:'response_done',token:t});
  assert.doesNotMatch(app.ui['session-label'].textContent, /DIJEDA/);
  assert.doesNotMatch(app.ui['privacy-note'].textContent, /offline|perangkat/i);
});

for (const action of ['mute','stop','logout','pagehide','socket','server-mute','timeout']) {
  test(`local PCM ${action} clears pending capture and releases or disables tracks`, async () => {
    const app = await localApp({manualSessionReady:action === 'timeout'});
    const node = app.worklets[0], callback = node.port.onmessage;
    app.pcm(0.5);
    if (action === 'socket') app.sockets[0].close(1008);
    else if (action === 'pagehide') app.windowEvents.pagehide();
    else if (action === 'server-mute') app.receive({type:'state',state:{open:true,muted:true,history:[]}});
    else if (action === 'timeout') app.timers.find(t=>!t.canceled && t.delay===8000).fn();
    else await app.ui[action==='stop'?'start':action].emit('click');
    app.pcm(1, 0, node);
    for (let i = 0; i < 48; i++) callback({data:{pcm:new Float32Array(2048).fill(i < 24 ? 0.2 : 0),rms:i < 24 ? 0.2 : 0}});
    assert.equal(app.sent().filter(m=>m.type==='audio').length, 0);
    if (action === 'mute' || action === 'server-mute') assert.equal(app.tracks[0].enabled, false);
    else { assert.equal(app.tracks[0].stopped, true); assert.equal(app.contexts[1].state, 'closed'); }
    if (action === 'mute') {
      await app.ui.mute.emit('click'); app.pcm(1); app.pcm(1.3, 0);
      assert.equal(app.sent().filter(m=>m.type==='audio').length, 1); assert.equal(app.microphoneRequests(), 1);
    }
    if (action === 'timeout') {
      app.receive({type:'session_ready',start_id:app.sent().find(m=>m.type==='start').start_id});
      assert.match(app.ui.error.textContent, /belum mengonfirmasi/);
      assert.equal(app.ui.mute.disabled, true);
    }
  });
}

test('local PCM supports explicitly legacy no-ACK servers and never silently falls back to browser recognition', async () => {
  const app = await localApp({recognition:true}, {session_ack_supported:false});
  assert.equal(app.sent().find(m=>m.type==='start').start_id, undefined);
  assert.equal(app.recognitions.length, 0);
  app.pcm(1); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, 1);
  app.receive({type:'error',message:'STT lokal gagal'});
  assert.equal(app.recognitions.length, 0);
});

test('configured API STT enables microphone controls and discloses remote audio transcription', async () => {
  const app = await boot({stt_available:true, stt_reason:'', ai_delivery:'streaming'});
  assert.equal(app.ui.start.disabled, false);
  assert.equal(app.ui['mode-options'].disabled, false);
  assert.match(app.ui['privacy-note'].textContent, /Audio mikrofon dikirim.*STT API/);
  assert.doesNotMatch(app.ui['privacy-note'].textContent, /Mikrofon tidak dikirim/);
  assert.match(app.ui['delivery-note'].textContent, /Teks diterima bertahap dari API/);
  assert.doesNotMatch(app.ui['status'].textContent, /Mikrofon aktif/);
});

test('provider selection is disabled offline and reconnect starts a fresh session with selected API', async () => {
  const app = await boot({providers:[{id:'hermes',label:'Hermes API'},{id:'other-api',label:'Other API'}]}, {manualOpen:true});
  assert.equal(app.ui.provider.disabled, true, 'no socket can accept provider changes yet');
  app.sockets[0].open(); await settle();
  assert.equal(app.ui.provider.disabled, false);
  app.ui.provider.value = 'other-api'; await app.ui.provider.emit('change');
  assert.deepEqual(app.sent().at(-1), {type:'provider',provider:'other-api'});
  app.receive({type:'state',state:{open:true,history:[]}});
  app.sockets[0].close(); await settle();
  assert.equal(app.ui.provider.disabled, true);
  const pending = app.submit('Reconnect'); await settle();
  app.sockets[1].open(); await pending;
  assert.ok(app.sockets[1].sent.some(message => message.type === 'start' && message.provider === 'other-api'));
  assert.ok(app.sockets[1].sent.some(message => message.type === 'text' && message.text === 'Reconnect'));
});

test('interrupt during pending decode cannot stall audio for the next response or acknowledge canceled audio', async () => {
  const app = await boot({}, {deferredDecode:true}); await app.submit();
  const old = token(1), next = token(2);
  app.receive({type:'response_start',token:old}); app.receive(audio(old)); await settle();
  assert.equal(app.decodes.length, 1);
  await app.ui.interrupt.emit('click');
  await app.submit('Next response');
  app.receive({type:'response_start',token:next}); app.receive(audio(next)); await settle();
  assert.equal(app.decodes.length, 2, 'new response must decode without waiting for canceled decode');
  app.decodes[1]({}); await settle();
  assert.equal(app.sources.length, 1); assert.equal(app.sources[0].started, true);
  app.decodes[0]({}); await settle();
  assert.equal(app.sources.length, 1, 'late canceled decode cannot play');
  app.sources[0].end(); await settle();
  const acks = app.sent().filter(message => message.type === 'ack');
  assert.equal(acks.length, 1); assert.equal(tokenKey(acks[0].token), tokenKey(next));
});

test('late older response_start cannot replace newer text or playback authorization', async () => {
  const app = await boot(); await app.submit();
  const latest = token(3);
  app.receive({type:'response_start',token:latest});
  app.receive({type:'text_delta',token:latest,text:'Jawaban terbaru'});
  app.receive({type:'response_start',token:token(2)});
  app.receive(audio(token(2))); app.receive(audio(latest)); await settle();
  assert.equal(app.sources.length, 1);
  app.sources[0].end(); await settle();
  const ack = app.sent().find(message => message.type === 'ack');
  assert.equal(tokenKey(ack.token), tokenKey(latest));
  assert.equal(app.ui.history.children.at(-1).children[1].textContent, 'Jawaban terbaru');
});

test('duplicate response_start is idempotent instead of canceling its own audio', async () => {
  const app = await boot(); await app.submit(); const t = token();
  app.receive({type:'response_start',token:t});
  app.receive({type:'text_delta',token:t,text:'Tetap utuh'});
  app.receive({type:'response_start',token:t});
  app.receive(audio(t)); await settle();
  assert.equal(app.sources.length, 1);
  assert.equal(app.ui.history.children.at(-1).children[1].textContent, 'Tetap utuh');
  app.sources[0].end(); await settle();
  assert.equal(app.sent().filter(message => message.type === 'ack').length, 1);
});

test('response_done seals text but still accepts authorized late audio and acknowledges only after playback', async () => {
  const app = await boot(); await app.submit(); const t = token();
  app.receive({type:'response_start',token:t});
  app.receive({type:'text_delta',token:t,text:'Jawaban lengkap'});
  app.receive({type:'response_done',token:t});
  app.receive({type:'text_delta',token:t,text:'abaikan'});
  app.receive(audio(t, 7)); await settle();
  assert.equal(app.sources.length, 1, 'text completion must not drop a queued speech event');
  assert.equal(app.ui.history.children.at(-1).children[1].textContent, 'Jawaban lengkap');
  assert.equal(app.sent().filter(message => message.type === 'ack').length, 0);
  app.sources[0].end(); await settle();
  assert.equal(app.sent().filter(message => message.type === 'ack')[0].chunk_id, 7);
  assert.match(app.ui.status.textContent, /Jawaban selesai/);
});

test('completion status waits for pending audio decode and actual playback end', async () => {
  const app = await boot({}, {deferredDecode:true}); await app.submit(); const t = token();
  app.receive({type:'response_start',token:t}); app.receive(audio(t));
  app.receive({type:'response_done',token:t}); await settle();
  assert.doesNotMatch(app.ui.status.textContent, /Jawaban selesai/, 'decoding audio is not completed playback');
  app.decodes[0]({}); await settle();
  assert.match(app.ui.status.textContent, /berbicara/);
  app.sources[0].end(); await settle();
  assert.match(app.ui.status.textContent, /Jawaban selesai/);
});

test('events from a closed socket cannot authorize playback after reconnect', async () => {
  const app = await boot(); await app.submit();
  const oldSocket = app.sockets[0]; oldSocket.close(); await settle(); await app.submit('New session');
  const current = {...token(),session_id:'new-session'};
  app.receive({type:'response_start',token:current});
  oldSocket.receive({type:'response_start',token:token(99)});
  oldSocket.receive(audio(token(99)));
  app.receive(audio(current)); await settle();
  assert.equal(app.sources.length, 1); app.sources[0].end(); await settle();
  assert.equal(tokenKey(app.sent().find(message => message.type === 'ack').token), tokenKey(current));
});

test('interrupt stops playing source, drops queued speech, and unblocks subsequent playback without an ended event', async () => {
  const app = await boot(); await app.submit(); const old = token(), next = token(2);
  app.receive({type:'response_start',token:old});
  app.receive(audio(old, 1)); app.receive(audio(old, 2)); await settle();
  assert.equal(app.sources.length, 1);
  await app.ui.interrupt.emit('click'); await settle();
  assert.equal(app.sources[0].stopped, true);
  app.sources[0].end();
  app.receive({type:'response_start',token:old}); app.receive(audio(old, 3));
  await app.submit('Next response');
  app.receive({type:'response_start',token:next}); app.receive(audio(next, 4)); await settle();
  assert.equal(app.sources.length, 2, 'no real ended event is required to release the canceled drain');
  assert.equal(app.sent().filter(message => message.type === 'ack').length, 0);
  app.sources[1].end(); await settle();
  assert.deepEqual(app.sent().filter(message => message.type === 'ack').map(message => message.chunk_id), [4]);
});

test('interrupt before response_start rejects the late start until a new user turn is submitted', async () => {
  const app = await boot(); await app.submit();
  app.receive({type:'state',state:{open:true,responding:true,history:[]}});
  await app.ui.interrupt.emit('click');
  app.receive({type:'response_start',token:token()}); app.receive(audio(token())); await settle();
  assert.equal(app.sources.length, 0, 'interrupt must also cancel a response whose token has not arrived');
  await app.submit('Continue');
  app.receive({type:'response_start',token:token(2)}); app.receive(audio(token(2))); await settle();
  assert.equal(app.sources.length, 1); app.sources[0].end(); await settle();
  assert.equal(tokenKey(app.sent().find(message => message.type === 'ack').token), tokenKey(token(2)));
});

test('missing API STT disables microphone, explains why, and keeps text chat available', async () => {
  const app = await boot();
  assert.equal(app.ui.start.disabled, true, 'microphone start must be disabled without STT');
  assert.equal(app.ui['mode-options'].disabled, true);
  assert.match(app.ui['voice-description'].textContent, /STT API belum dikonfigurasi/);
  await app.ui.start.emit('click'); await settle();
  assert.equal(app.microphoneRequests(), 0, 'programmatic start must also respect capability');
  await app.submit();
  assert.equal(app.ui.send.disabled, false);
  assert.ok(app.sent().some(message => message.type === 'text' && message.text === 'Halo'));
  assert.doesNotMatch(app.ui['session-label'].textContent, /^MIKROFON AKTIF$/);
});

test('expired config session redirects to login without opening a websocket', async () => {
  const app = await boot({}, {response:{ok:false,status:401}});
  assert.deepEqual(app.redirects, ['/login']); assert.equal(app.sockets.length, 0);
});
test('websocket policy close redirects expired session and logout POST revokes server session', async () => {
  const app = await boot();
  app.sockets[0].close(1008); await settle();
  assert.deepEqual(app.redirects, ['/login']);
  const other = await boot();
  assert.ok(other.ui.logout, 'logout must be visible in the app');
  await other.ui.logout.emit('click'); await settle();
  assert.ok(other.fetches.some(([url, settings]) => url === '/api/logout' && settings.method === 'POST'));
  assert.deepEqual(other.redirects, ['/login']);
});
test('local preview uploads real growing PCM before endpoint, revises draft without a user turn, then sends one final', async () => {
  const app=await localApp({}, {local_preview_supported:true});
  assert.equal(app.sent().find(m=>m.type==='start').local_preview,true);
  app.pcm(1.6,0.02);
  const first=app.sent().find(m=>m.type==='audio_preview'); assert.ok(first);
  assert.equal(app.sent().filter(m=>m.type==='audio').length,0);
  assert.ok(Buffer.from(first.pcm_b64,'base64').length>=48000);
  app.receive({type:'transcript_partial',utterance_id:first.utterance_id,revision:48000,text:'draf',provisional:true});
  assert.match(app.ui['local-draft'].textContent,/Sementara.*draf/);
  app.receive({type:'transcript_partial',utterance_id:first.utterance_id,revision:64000,text:'draf direvisi',provisional:true});
  assert.match(app.ui['local-draft'].textContent,/draf direvisi/);
  assert.equal(app.sent().some(m=>['text','voice_text'].includes(m.type)),false);
  app.pcm(1.5,0.02);app.pcm(1.3,0);
  const final=app.sent().filter(m=>m.type==='audio'); assert.equal(final.length,1);
  assert.equal(final[0].utterance_id,first.utterance_id);
  app.receive({type:'transcript',utterance_id:first.utterance_id,text:'final'});
  app.receive({type:'transcript_partial',utterance_id:first.utterance_id,revision:99999,text:'late',provisional:true});
  assert.equal(app.ui['local-draft'].textContent,'');
});
for(const boundary of ['mute','stop','socket','playback','interrupt']) {
  test(`local preview rejects stale revisions after ${boundary}`,async()=>{
    const app=await localApp({}, {local_preview_supported:true}); app.pcm(1.6,0.02);
    const p=app.sent().find(m=>m.type==='audio_preview'); assert.ok(p);
    if(boundary==='socket') app.sockets[0].close();
    else if(boundary==='playback') {app.receive({type:'response_start',token:token()});app.receive(audio(token()));}
    else await app.ui[boundary==='stop'?'start':boundary].emit('click');
    app.receive({type:'transcript_partial',utterance_id:p.utterance_id,revision:48000,text:'stale',provisional:true});
    assert.equal(app.ui['local-draft'].textContent,'');
  });
}

test('enabled local previews never change the explicitly consented browser-recognition route',async()=>{
  const app=await localApp({recognition:true},{local_preview_supported:true,browser_stt_allowed:true});
  await app.ui.start.emit('click');await settle();
  app.ui['browser-consent'].checked=true;await app.ui['browser-consent'].emit('change');
  await app.ui.start.emit('click');await settle();
  assert.equal(app.sent().filter(m=>m.type==='start').at(-1).local_preview,undefined);
  app.recognitions[0].result(['browser fixture']);app.finishSpeech();
  assert.equal(app.sent().filter(m=>m.type==='voice_text').length,1);
  assert.equal(app.sent().filter(m=>m.type==='audio_preview').length,0);
});
test('local preview backpressure drops snapshots but final PCM remains bounded and capture pauses',async()=>{
  const app=await localApp({}, {local_preview_supported:true});
  app.sockets[0].bufferedAmount=300000;app.pcm(2,0.02);
  assert.equal(app.sent().filter(m=>m.type==='audio_preview').length,0);
  app.sockets[0].bufferedAmount=0;app.pcm(21,0.02);
  const previews=app.sent().filter(m=>m.type==='audio_preview');
  assert.ok(previews.length>0 && previews.length<=14);
  assert.ok(previews.every(m=>Buffer.from(m.pcm_b64,'base64').length<=640000));
  const finals=app.sent().filter(m=>m.type==='audio');assert.equal(finals.length,1);
  assert.equal(Buffer.from(finals[0].pcm_b64,'base64').length,640000);
  app.pcm(5,0.02);
  assert.equal(app.sent().filter(m=>m.type==='audio_preview').length,previews.length);
});

test('local preview startup keeps a 30s deadline and rejects late readiness after timeout', async () => {
  const app=await localApp({manualSessionReady:true}, {local_preview_supported:true});
  const start=app.sent().find(m=>m.type==='start');
  assert.equal(start.local_preview,true);
  const timer=app.timers.find(t=>!t.canceled && t.delay===30000);
  assert.ok(timer, '25s local readiness needs transport/reaping margin');
  // No capture while readiness is delayed beyond the normal 8s browser budget.
  app.pcm(2,0.02); assert.equal(app.sent().filter(m=>m.type==='audio_preview').length,0);
  timer.fn();
  assert.ok(app.tracks.every(t=>t.stopped));
  app.receive({type:'session_ready',start_id:start.start_id});
  app.pcm(2,0.02);
  assert.equal(app.sent().filter(m=>m.type==='audio_preview'||m.type==='audio').length,0);
  assert.match(app.ui.start.textContent,/^Mulai/);
});

test('correlated stale start errors cannot abort a newer local preview start', async () => {
  const app=await localApp({manualSessionReady:true}, {local_preview_supported:true});
  const old=app.sent().find(m=>m.type==='start');
  await app.ui.start.emit('click'); await app.ui.start.emit('click'); await settle();
  const fresh=app.sent().filter(m=>m.type==='start').at(-1);
  assert.notEqual(old.start_id,fresh.start_id);
  const track=app.tracks.at(-1);
  app.receive({type:'error',command:'start',start_id:old.start_id,code:'local_audio_busy'});
  assert.notEqual(track.stopped,true);
  app.receive({type:'session_ready',start_id:old.start_id});
  app.pcm(2,0.02); assert.equal(app.sent().filter(m=>m.type==='audio_preview').length,0);
  app.receive({type:'session_ready',start_id:fresh.start_id});
  app.pcm(2,0.02); assert.ok(app.sent().some(m=>m.type==='audio_preview'));
});

test('stateful local capture sends stable cumulative snapshots at the advertised half-second interval', async () => {
  const app = await localApp({}, {local_preview_supported:true, local_stateful_preview_supported:true, local_preview_interval_ms:500});
  app.pcm(.7, .02);
  const first = app.sent().find(m => m.type === 'audio_preview');
  assert.ok(first, 'stateful capture should not wait the old 1.5s prefix interval');
  app.pcm(.6, .03);
  const previews = app.sent().filter(m => m.type === 'audio_preview');
  assert.ok(previews.length >= 2);
  const prior = Buffer.from(first.pcm_b64, 'base64');
  const next = Buffer.from(previews[1].pcm_b64, 'base64');
  assert.deepEqual(next.subarray(0, prior.length), prior, 'native state requires stable previously fed PCM');
  assert.equal(previews[1].utterance_id, first.utterance_id);
  assert.equal(app.sent().filter(m => m.type === 'audio').length, 0);
  app.pcm(1.3, 0);
  const finals = app.sent().filter(m => m.type === 'audio');
  assert.equal(finals.length, 1);
  assert.equal(finals[0].utterance_id, first.utterance_id);
  assert.deepEqual(Buffer.from(finals[0].pcm_b64, 'base64').subarray(0, prior.length), prior);
});

test('a short interval without explicit stateful support cannot change legacy preview cadence', async () => {
  const app = await localApp({}, {local_preview_supported:true, local_preview_interval_ms:500});
  app.pcm(.7, .02);
  assert.equal(app.sent().filter(m => m.type === 'audio_preview').length, 0);
});

test('Supertonic voice discloses synthetic fixed identity and links complete model terms', async () => {
  const app = await boot({tts_label:'Supertonic3 lokal · M1', tts_identity:{backend:'supertonic3', voice:'M1'}});
  const link = app.ui['tts-note'].children.find(e => e.tagName === 'a');
  assert.ok(link, 'Supertonic deployment must expose model license terms');
  assert.equal(link.href, '/supertonic3-LICENSE.txt');
  assert.equal(link.rel, 'noopener noreferrer');
  assert.match(app.ui['tts-note'].textContent, /sintetis/i);
  assert.match(app.ui['tts-note'].textContent, /bukan pendeteksi emosi/i);
  assert.match(app.ui['privacy-note'].textContent, /Supertonic-3 sintetis.*OpenRAIL-M/i);
});

test('selected conditioning B describes dynamic style without claiming all emotions', async () => {
  const app = await boot({tts_label:'Supertonic3 lokal · M1 · gaya B', tts_identity:{backend:'supertonic3', voice:'M1', voice_style_profile:'conditioning_b'}});
  assert.match(app.ui['tts-note'].textContent, /Profil conditioning B/i);
  assert.match(app.ui['tts-note'].textContent, /teks dinamis/i);
  assert.match(app.ui['tts-note'].textContent, /bukan pengendali otomatis semua emosi/i);
  assert.doesNotMatch(app.ui['tts-note'].textContent, /Preset M1 tetap/i);
  assert.equal(app.ui['tts-note'].children.find(e => e.tagName === 'a').href, '/supertonic3-LICENSE.txt');
});

test('unknown server style does not falsely assert unchanged original M1 conditioning', async () => {
  const app = await boot({tts_identity:{backend:'supertonic3', voice:'M1', voice_style_profile:'future_style'}});
  assert.match(app.ui['tts-note'].textContent, /Profil gaya ditetapkan server/i);
  assert.doesNotMatch(app.ui['tts-note'].textContent, /Preset M1 tetap/i);
});

test('chunked speech keeps one response active and microphone paused between playback pieces', async () => {
  const app = await localApp();
  const t = token();
  app.receive({type:'response_start', token:t});
  app.receive({type:'text_delta', token:t, text:'Bagian pertama. Bagian kedua.'});
  app.receive({...audio(t, 1), last_chunk:false});
  await settle();
  assert.equal(app.sources.length, 1);
  app.sources[0].end(); await settle();
  assert.deepEqual(app.sent().filter(m=>m.type==='ack').map(m=>m.chunk_id), [1]);
  assert.doesNotMatch(app.ui.status.textContent, /Jawaban selesai/);
  const count = app.sent().filter(m=>m.type==='audio').length;
  app.pcm(2, .02); app.pcm(1.3, 0);
  assert.equal(app.sent().filter(m=>m.type==='audio').length, count, 'capture must stay paused between chunks');
  app.receive({...audio(t, 2), last_chunk:true});
  app.receive({type:'response_done', token:t});
  await settle();
  assert.equal(app.sources.length, 2);
  assert.doesNotMatch(app.ui.status.textContent, /Jawaban selesai/);
  app.sources[1].end(); await settle();
  assert.deepEqual(app.sent().filter(m=>m.type==='ack').map(m=>m.chunk_id), [1,2]);
  assert.match(app.ui.status.textContent, /Jawaban selesai/);
  assert.equal(app.ui.history.children.at(-1).children[1].textContent, 'Bagian pertama. Bagian kedua.');
});

test('chunked delivery is disclosed separately from buffered LLM text', async () => {
  const app = await boot({tts_delivery:'chunked', ai_delivery:'buffered'});
  assert.match(app.ui['tts-note'].textContent, /jawaban panjang.*bertahap/i);
  assert.match(app.ui['delivery-note'].textContent, /setelah jawaban API selesai/);
});

test('untrusted transcript and model text stay inert DOM text', async () => {
  const app=await boot(); const attack='<img src=x onerror=alert(1)>';
  app.receive({type:'state',state:{open:true,history:[{role:'user',text:attack}]}});
  assert.equal(app.ui.history.children[0].children[1].textContent,attack);
  assert.equal(app.ui.history.children[0].children[1].children.length,0);
});


test('voice casing: uppercase voice history is sentence case, never typed or assistant text', async () => {
  const app = await boot();
  const history = [
    {role:'user', source:'voice', text:'APA KABAR? SAYA INGIN MENCOBA LAGI.'},
    {role:'user', source:'text', text:'PESAN TERTULIS TETAP BESAR'},
    {role:'assistant', source:'voice', text:'JAWABAN TETAP BESAR'}
  ];
  app.receive({type:'state',state:{open:true,history}});
  assert.equal(app.ui.history.children[0].children[1].textContent, 'Apa kabar? Saya ingin mencoba lagi.');
  assert.equal(app.ui.history.children[1].children[1].textContent, history[1].text);
  assert.equal(app.ui.history.children[2].children[1].textContent, history[2].text);
});


test('voice casing: preserves technical acronyms, identifiers, URLs, emails, paths and code verbatim', async () => {
  const app = await localApp({freezeInbound:true}, {local_preview_supported:true});
  app.pcm(1.6, .02);
  const preview = app.sent().find(m => m.type === 'audio_preview');
  const raw = 'TOLONG CEK API AI CPU GPU RAM STT TTS ASR VAD PCM WAV SSE WS WSS CORS CSRF XSS OAUTH LLM SDK HTTP HTTPS SQL PHP JSON HTML CSS UI USB DNS TCP IP SSH CLI XML YAML JWT URL ID UTF-8 GPT-4 M1 ERR_AUTH AB12 foo_bar /ROOT/Voice/app.js C:\\Users\\ME\\A.TXT HTTPS://EXAMPLE.COM/Case?Key=ABC User@Example.COM `CONST X = "ABC";` --FLAG ENV=VALUE.';
  const expected = 'Tolong cek' + raw.slice('TOLONG CEK'.length);
  app.receive({type:'transcript_partial',utterance_id:preview.utterance_id,revision:1,text:raw});
  assert.equal(app.ui['local-draft'].textContent, 'Sementara · dapat direvisi: ' + expected);
  assert.equal(app.inbound.at(-1).text, raw);
  app.receive({type:'transcript',text:raw});
  assert.equal(app.ui.history.children.at(-1).children[1].textContent, expected);
  assert.equal(app.inbound.at(-1).text, raw);
  app.receive({type:'state',state:{history:[{role:'user',source:'voice',text:raw}]}});
  assert.equal(app.ui.history.children[0].children[1].textContent, expected);
});

test('voice casing: local interim, temporary final and history agree without changing raw inbound objects', async () => {
  const app = await localApp({freezeInbound:true}, {local_preview_supported:true});
  app.pcm(1.6, .02);
  const preview = app.sent().find(m => m.type === 'audio_preview');
  const raw = 'TOLONG CEK API. APA SUDAH NORMAL?';
  const expected = 'Tolong cek API. Apa sudah normal?';
  app.receive({type:'transcript_partial',utterance_id:preview.utterance_id,revision:1,text:raw});
  assert.equal(app.ui['local-draft'].textContent, 'Sementara · dapat direvisi: ' + expected);
  const partial = app.inbound.at(-1);
  app.receive({type:'transcript',text:raw});
  assert.equal(app.ui['local-draft'].textContent, '');
  assert.equal(app.ui.history.children.at(-1).children[1].textContent, expected);
  const final = app.inbound.at(-1);
  const snapshot = {type:'state',state:{open:true,history:[{role:'user',source:'voice',text:raw}]}};
  app.receive(snapshot);
  const stored = app.inbound.at(-1);
  assert.equal(app.ui.history.children.length, 1, 'deduplication still uses raw transcript identity');
  assert.equal(app.ui.history.children[0].children[1].textContent, expected);
  app.receive({type:'state',state:stored.state});
  assert.deepEqual(stored, snapshot);
  assert.equal(partial.text, raw); assert.equal(final.text, raw);
  assert.ok(Object.isFrozen(stored.state.history[0]));
  assert.equal(app.sent().some(m => ['voice_text','text'].includes(m.type)), false, 'local display must not send rewritten text');
});

test('voice casing: preserves bare relative path literals', async () => {
  const app = await boot({}, {freezeInbound:true});
  const raw = 'TOLONG BUKA SRC/APP.JS SEKARANG.';
  app.receive({type:'transcript',text:raw});
  assert.equal(app.ui.history.children.at(-1).children[1].textContent, 'Tolong buka SRC/APP.JS sekarang.');
  assert.equal(app.inbound.at(-1).text, raw);
});

test('voice casing: preserves function-call identifier literals', async () => {
  const app = await boot({}, {freezeInbound:true});
  const raw = 'TOLONG PANGGIL MYFUNCTION() SEKARANG.';
  app.receive({type:'transcript',text:raw});
  assert.equal(app.ui.history.children.at(-1).children[1].textContent, 'Tolong panggil MYFUNCTION() sekarang.');
  assert.equal(app.inbound.at(-1).text, raw);
});

test('voice casing: preserves quoted literals across preview, final and history', async () => {
  const app = await localApp({freezeInbound:true}, {local_preview_supported:true});
  app.pcm(1.6, .02);
  const preview = app.sent().find(m => m.type === 'audio_preview');
  const raw = `TOLONG SIMPAN 'TOKEN' DAN "ABC" SEKARANG.`;
  const expected = `Tolong simpan 'TOKEN' dan "ABC" sekarang.`;
  app.receive({type:'transcript_partial',utterance_id:preview.utterance_id,revision:1,text:raw});
  assert.equal(app.ui['local-draft'].textContent, 'Sementara · dapat direvisi: ' + expected);
  assert.equal(app.ui['local-draft'].children.length, 0);
  assert.equal(app.inbound.at(-1).text, raw);
  app.receive({type:'transcript',text:raw});
  assert.equal(app.ui.history.children.at(-1).children[1].textContent, expected);
  assert.equal(app.inbound.at(-1).text, raw);
  const snapshot = {type:'state',state:{history:[{role:'user',source:'voice',text:raw}]}};
  app.receive(snapshot);
  assert.equal(app.ui.history.children[0].children[1].textContent, expected);
  assert.equal(app.ui.history.children[0].children[1].children.length, 0);
  assert.deepEqual(app.inbound.at(-1), snapshot);
  assert.equal(app.sent().some(m => ['voice_text','text'].includes(m.type)), false);
});

test('voice casing: sentence boundaries after opaque URLs and paths still capitalize prose', async () => {
  const app = await boot();
  const raw = 'BUKA HTTPS://EXAMPLE.COM/Case. LALU CEK /ROOT/Voice/app.js! APA API NORMAL?';
  app.receive({type:'transcript',text:raw});
  assert.equal(app.ui.history.children.at(-1).children[1].textContent,
    'Buka HTTPS://EXAMPLE.COM/Case. Lalu cek /ROOT/Voice/app.js! Apa API normal?');
});

test('voice casing: mixed case, Unicode, whitespace, empty input and misheard words are safe', async () => {
  const app = await boot({}, {freezeInbound:true});
  const cases = [
    ['', ''], ['   ', '   '], ['123 😀 中文', '123 😀 中文'],
    ['Apa API sudah normal?', 'Apa API sudah normal?'],
    ['SAYA memakai API', 'SAYA memakai API'], ['sudah kecil', 'sudah kecil'],
    ['SAYA MEMAKAI iPhone', 'SAYA MEMAKAI iPhone'],
    ['  “HALO DUNIA!”\nÉLOK SEKALI. 😀 SUDAH NORMAL?', '  “Halo dunia!”\nÉlok sekali. 😀 Sudah normal?'],
    ['E\u0301LOK SEKALI', 'E\u0301lok sekali'],
    ['PELAFALANNYA SUDAH JELAS.', 'Pelafalannya sudah jelas.'],
    ['API AI STT TTS', 'API AI STT TTS'],
    ['TOLONG CEK fooBar', 'TOLONG CEK fooBar']
  ];
  for (const [raw, expected] of cases) {
    const snapshot = {type:'state',state:{history:[{role:'user',source:'voice',text:raw}]}};
    app.receive(snapshot);
    assert.equal(app.ui.history.children[0].children[1].textContent, expected, JSON.stringify(raw));
    assert.deepEqual(app.inbound.at(-1), snapshot);
    assert.equal(app.ui.history.children[0].children[1].children.length, 0);
  }
});

test('voice casing: browser outgoing transcript, typed payload and streaming assistant stay untouched', async () => {
  const app = await browserApp({freezeInbound:true});
  const raw = 'TOLONG CEK API SEKARANG.';
  app.recognitions[0].result([raw]); app.finishSpeech();
  assert.deepEqual(app.sent().filter(m => m.type === 'voice_text'), [{type:'voice_text',text:raw}]);
  app.receive({type:'transcript',text:raw});
  assert.equal(app.ui.history.children.at(-1).children[1].textContent, 'Tolong cek API sekarang.');
  assert.equal(app.inbound.at(-1).text, raw);
  app.receive({type:'state',state:{open:true,history:[{role:'user',source:'voice',text:raw}]}});
  const t = token(); app.receive({type:'response_start',token:t});
  app.receive({type:'text_delta',token:t,text:'JAWABAN API TETAP BESAR.'});
  assert.equal(app.ui.history.children.at(-1).children[1].textContent, 'JAWABAN API TETAP BESAR.');
  const typed = 'SAYA MENGETIK HURUF BESAR'; await app.submit(typed);
  assert.deepEqual(app.sent().filter(m => m.type === 'text'), [{type:'text',text:typed}]);
  app.receive({type:'state',state:{open:true,history:[{role:'user',text:typed},{role:'user',source:'text',text:typed}]}});
  assert.deepEqual(app.ui.history.children.map(e => e.children[1].textContent), [typed,typed,'JAWABAN API TETAP BESAR.']);
});

test('voice casing: uppercase markup stays inert in preview and final DOM', async () => {
  const app = await localApp({}, {local_preview_supported:true}); app.pcm(1.6, .02);
  const preview = app.sent().find(m => m.type === 'audio_preview');
  const raw = '<IMG SRC=X ONERROR=ALERT(1)> HALO';
  app.receive({type:'transcript_partial',utterance_id:preview.utterance_id,revision:1,text:raw});
  assert.equal(app.ui['local-draft'].children.length, 0);
  assert.equal(app.ui['local-draft'].textContent, 'Sementara · dapat direvisi: <IMG SRC=X ONERROR=ALERT(1)> halo');
  app.receive({type:'transcript',text:raw});
  assert.equal(app.ui.history.children.at(-1).children[1].textContent, '<IMG SRC=X ONERROR=ALERT(1)> halo');
  assert.equal(app.ui.history.children.at(-1).children[1].children.length, 0);
});
