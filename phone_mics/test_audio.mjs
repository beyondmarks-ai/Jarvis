import assert from "node:assert/strict";
import { PCMFramer } from "./static/audio.js";
for (const rate of [16000,44100,48000]) {
  const frames = [], resampler = new PCMFramer(rate, frame=>frames.push(frame));
  const samples = new Float32Array(rate).fill(.5);
  for (let i=0;i<samples.length;i+=128) resampler.push(samples.subarray(i,i+128));
  assert.equal(frames.length, 12, `frame count at ${rate}`);
  for (const frame of frames) {
    assert.equal(frame.length,1280);
    assert.ok(frame.every(x=>Math.abs(x-16384)<=1)); // floating-point rounding, at most one PCM LSB
  }
}
assert.throws(()=>new PCMFramer(8000,()=>{}));
// Match the actual worklet's transferable-buffer semantics. Merely retaining
// frames in an array does not catch an accidentally reused detached buffer.
for (const rate of [16000,44100,48000]) {
  const frames = [], framer = new PCMFramer(rate, frame => {
    frames.push(structuredClone(frame, {transfer:[frame.buffer]}));
    assert.equal(frame.length, 0, 'transfer must detach the source buffer');
  });
  const samples = new Float32Array(rate*3).fill(.5);
  for (let i=0;i<samples.length;i+=128) framer.push(samples.subarray(i,i+128));
  assert.equal(frames.length, 37, `continuous transferable frames at ${rate}`);
  for (const frame of frames) {
    assert.equal(frame.length,1280);
    assert.ok(frame.every(x=>Math.abs(x-16384)<=1));
  }
}
console.log("Audio framing passed: 16/44.1/48 kHz, block-boundary continuity, PCM amplitude.");
console.log("Transfer regression passed: continuous 3-second audio after buffer detachment.");
