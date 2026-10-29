const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
test('response token identity ignores key order and includes every contract field', () => {
  assert.equal(typeof audio.tokenKey, 'function');
  const a = {conversation_id:'c',session_id:'s',epoch:1,generation:2,provider:'local'};
  assert.equal(audio.tokenKey(a), audio.tokenKey({provider:'local',generation:2,epoch:1,session_id:'s',conversation_id:'c'}));
  assert.notEqual(audio.tokenKey(a), audio.tokenKey({...a, epoch:2}));
  assert.equal(audio.tokenKey(null), null);
});
test('worklet averages channels, emits bounded blocks with real RMS, and stays silent', () => {
  let Processor;
  const context = {AudioWorkletProcessor: class { constructor() { this.port = {postMessage: (m) => messages.push(m)}; } }, registerProcessor: (_, cls) => {Processor = cls;}, Float32Array};
  const messages = [];
  vm.runInNewContext(fs.readFileSync(__dirname + '/pcm-worklet.js', 'utf8'), context);
  const processor = new Processor();
  for (let i = 0; i < 16; i++) {
    const output = new Float32Array(128).fill(1);
    assert.equal(processor.process([[new Float32Array(128).fill(0.2), new Float32Array(128).fill(0.6)]], [[output]]), true);
    assert.equal(output[0], 0);
  }
  assert.equal(messages.length, 1);
  assert.equal(messages[0].pcm.length, 2048);
  assert.ok(Math.abs(messages[0].rms - 0.4) < 0.001);
});
const audio = fs.existsSync(__dirname + '/audio-core.js') ? require('./audio-core.js') : {};
test('resamples device PCM to 16 kHz signed little endian with clipping', () => {
  assert.equal(typeof audio.pcm16, 'function', 'PCM converter exists');
  const out = audio.pcm16(new Float32Array(480).fill(0.5), 48000);
  assert.equal(out.byteLength, 320);
  assert.equal(new DataView(out.buffer).getInt16(0, true), 16384);
  const clipped = audio.pcm16(new Float32Array([-2, 2]), 16000);
  assert.equal(new DataView(clipped.buffer).getInt16(0, true), -32768);
  assert.equal(new DataView(clipped.buffer).getInt16(2, true), 32767);
});
