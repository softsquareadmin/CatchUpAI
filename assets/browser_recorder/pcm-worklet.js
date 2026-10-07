/* Streaming conversion: fixed 24 kHz mono, little-endian signed 16-bit PCM. */
class PCMRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = [];
    this.position = 0;
    this.pcm = [];
    this.port.onmessage = event => {
      if (event.data === "flush") {
        this.emit();
        this.samples = [];
        this.position = 0;
        this.port.postMessage({flushed: true});
      }
    };
  }
  emit() {
    if (!this.pcm.length) return;
    const buffer = new ArrayBuffer(this.pcm.length * 2);
    const view = new DataView(buffer);
    this.pcm.forEach((value, index) => view.setInt16(index * 2, value, true));
    this.pcm = [];
    this.port.postMessage({pcm: buffer}, [buffer]);
  }
  process(inputs) {
    const channels = inputs[0];
    if (!channels || !channels.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let value = 0;
      for (const channel of channels) value += channel[i];
      this.samples.push(value / channels.length);
    }
    const step = sampleRate / 24000;
    while (this.position + 1 < this.samples.length) {
      const index = Math.floor(this.position);
      const fraction = this.position - index;
      const value = this.samples[index] * (1 - fraction) + this.samples[index + 1] * fraction;
      this.pcm.push(Math.round(Math.max(-1, Math.min(1, value)) * 32767));
      this.position += step;
      if (this.pcm.length === 4800) this.emit();
    }
    const consumed = Math.floor(this.position);
    this.samples.splice(0, consumed);
    this.position -= consumed;
    return true;
  }
}
registerProcessor("pcm-recorder", PCMRecorder);
