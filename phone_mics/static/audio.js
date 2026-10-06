// Fractional box-filter resampler, with phase carried across AudioWorklet blocks.
export class PCMFramer {
  constructor(rate, emit, target = 16000, frameSize = 1280) {
    if (rate < target) throw new Error("Microphone sample rate must be at least 16 kHz");
    this.ratio = rate / target; this.emit = emit; this.left = this.ratio;
    this.sum = 0; this.index = 0; this.frame = new Int16Array(frameSize);
  }
  push(input) {
    for (let value of input) {
      if (!Number.isFinite(value)) value = 0;
      let remaining = 1;
      while (remaining > 1e-9) {
        const weight = Math.min(remaining, this.left);
        this.sum += value * weight; this.left -= weight; remaining -= weight;
        if (this.left < 1e-9) {
          const sample = Math.max(-1, Math.min(1, this.sum / this.ratio));
          this.frame[this.index++] = Math.round(sample * (sample < 0 ? 32768 : 32767));
          this.left = this.ratio; this.sum = 0;
          if (this.index === this.frame.length) {
            // postMessage transfers (detaches) this buffer in the real worklet.
            // Allocate the next frame before emit: afterward completed.length is 0.
            const completed = this.frame;
            this.frame = new Int16Array(completed.length); this.index = 0;
            this.emit(completed);
          }
        }
      }
    }
  }
}
