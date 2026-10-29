'use strict';
class PCMCollector extends AudioWorkletProcessor {
  constructor() {
    super();
    this.block = new Float32Array(2048);
    this.offset = 0;
    this.sum = 0;
    this.generation = 0;
    // Reset partial device blocks at privacy/turn boundaries as well as the
    // main-thread buffer. Generation tags reject already queued old blocks.
    this.port.onmessage = ({data}) => {
      if (data?.type !== 'reset') return;
      this.offset = 0; this.sum = 0; this.generation = data.generation;
    };
  }
  process(inputs, outputs) {
    // Keep graph alive without feeding the captured microphone to speakers.
    for (const output of outputs) for (const channel of output) channel.fill(0);
    const channels = inputs[0];
    if (!channels || !channels.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let sample = 0;
      for (const channel of channels) sample += channel[i] / channels.length;
      this.block[this.offset++] = sample;
      this.sum += sample * sample;
      if (this.offset === this.block.length) {
        this.port.postMessage({pcm: this.block, rms: Math.sqrt(this.sum / this.offset), generation: this.generation}, [this.block.buffer]);
        this.block = new Float32Array(2048);
        this.offset = 0;
        this.sum = 0;
      }
    }
    return true;
  }
}
registerProcessor('pcm-collector', PCMCollector);
