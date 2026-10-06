import { PCMFramer } from "./audio.js";
class JarvisCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.framer = new PCMFramer(sampleRate, frame => this.port.postMessage(frame.buffer, [frame.buffer]));
  }
  process(inputs, outputs) {
    for (const channel of (outputs[0] || [])) channel.fill(0); // Never play mic audio.
    const channels = inputs[0];
    if (channels?.length) {
      const mono = new Float32Array(channels[0].length);
      for (const channel of channels) for (let i=0;i<mono.length;i++) mono[i] += channel[i]/channels.length;
      this.framer.push(mono);
    }
    return true;
  }
}
registerProcessor("jarvis-capture", JarvisCapture);
