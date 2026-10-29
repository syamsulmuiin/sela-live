/* Shared, dependency-free audio helpers; also exercised by Node tests. */
(function (root) {
  'use strict';
  function pcm16(input, rate, target = 16000) {
    const count = Math.floor(input.length * target / rate);
    const bytes = new Uint8Array(count * 2);
    const view = new DataView(bytes.buffer);
    // Area averaging gives a simple low-pass before device-rate downsampling.
    for (let i = 0; i < count; i++) {
      const from = i * rate / target, to = (i + 1) * rate / target;
      let sum = 0;
      for (let j = Math.floor(from); j < Math.ceil(to); j++) {
        sum += (input[Math.min(j, input.length - 1)] || 0) * (Math.min(to, j + 1) - Math.max(from, j));
      }
      const value = Math.max(-1, Math.min(1, sum / (to - from)));
      view.setInt16(i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
    }
    return bytes;
  }
  function tokenKey(t) {
    return t ? JSON.stringify([t.conversation_id, t.session_id, t.epoch, t.generation, t.provider]) : null;
  }
  const api = { pcm16, tokenKey };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.VoiceAudio = api;
})(globalThis);
