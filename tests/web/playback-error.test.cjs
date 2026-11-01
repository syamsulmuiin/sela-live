'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
// Read only the existing harmless platform harness definitions. Do not register
// its historical tests again or modify their byte-exact migration source.
const fixturePath = path.join(__dirname, 'client.test.cjs');
const fixtureSource = fs.readFileSync(fixturePath, 'utf8');
const marker = fixtureSource.indexOf('\nconst automaticCapabilities');
assert.ok(marker > 0, 'expected unchanged client fixture prelude');
const fixture = {exports:{}};
vm.runInNewContext(fixtureSource.slice(0,marker) + '\nmodule.exports={boot,token,audio,settle};', {
  require:createRequire(fixturePath),module:fixture,__dirname,console,setImmediate,queueMicrotask,Buffer
}, {filename:fixturePath});
const {boot,token,audio,settle} = fixture.exports;

for (const state of ['playing','decoding','queued']) {
  test(`TTS failure keeps local capture paused during ${state} and resumes after final playback`, async () => {
    const app = await boot({stt_available:true,stt_mode:'local'}, {pcm:true,deferredDecode:state==='decoding'});
    await app.ui.start.emit('click');await settle();
    const t=token();
    app.receive({type:'response_start',token:t});
    app.receive(audio(t));
    if(state==='queued')app.receive(audio(t,1));
    await settle();
    app.receive({type:'response_done',token:t,audio_available:false});
    app.receive({type:'error',stage:'tts',code:'tts_failed',message:'Suara jawaban gagal.'});
    app.pcm(1);app.pcm(1.3,0);
    assert.equal(app.sent().filter(e=>e.type==='audio').length,0,'playing assistant audio must never become a microphone utterance after error');
    assert.match(app.ui['session-label'].textContent,/DIJEDA/);
    if(state==='decoding'){app.decodes[0]({});await settle();}
    app.sources[0].end();await settle();
    if(state==='queued'){app.sources[1].end();await settle();}
    app.pcm(1);app.pcm(1.3,0);
    assert.equal(app.sent().filter(e=>e.type==='audio').length,1,'a fresh user utterance is accepted after actual playback ends');
    assert.equal(app.sent().filter(e=>e.type==='ack').length,state==='queued'?2:1);
  });
}

test('TTS failure cannot restart browser recognition until existing playback ends',async()=>{
  const app=await boot({browser_stt_allowed:true},{recognition:true});
  app.ui['browser-consent'].checked=true;
  await app.ui.start.emit('click');await settle();
  const t=token();app.receive({type:'response_start',token:t});app.receive(audio(t));await settle();
  const before=app.recognitions.length;
  app.receive({type:'response_done',token:t,audio_available:false});
  app.receive({type:'error',stage:'tts',code:'tts_failed',message:'Suara jawaban gagal.'});
  assert.equal(app.recognitions.length,before,'do not restart browser STT during own playback');
  app.sources[0].end();await settle();
  assert.equal(app.recognitions.length,before+1,'recognition resumes after actual audio completion');
});
