import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const {instance} = await WebAssembly.instantiate(await readFile(process.argv[2] || '.tmp_audio_dsp.wasm'));
const dsp = instance.exports;
dsp.__wasm_call_ctors?.();
// Real production startup policy: reserve threshold, deadline and short replies.
for (let elapsed=0; elapsed<200; elapsed++) {
  assert.equal(dsp.startup_ready(576, elapsed, 0), 0);
  assert.equal(dsp.startup_ready(5999, elapsed, 0), 0);
  assert.equal(dsp.startup_ready(6000, elapsed, 0), 1);
  assert.equal(dsp.startup_ready(576, elapsed, 1), 1);
}
assert.equal(dsp.startup_ready(576, 200, 0), 1);
assert.equal(dsp.startup_ready(576, 1000, 0), 1);
assert.equal(dsp.startup_ready(0, 0, 1), 1);
console.log('PASS production startup policy: early reserve, 200 ms deadline, short reply EOF');
assert.equal(dsp.decode(255), 0);
assert.equal(dsp.decode(127), 0);
assert.equal(dsp.decode(128), 32124);
assert.equal(dsp.decode(0), -32124);
let previous = 0;
for (let x = 0; x <= 32767; x++) {
  const value = dsp.limit(x);
  assert(value >= previous && value < 28000);
  assert(dsp.limit(-x) === -value);
  assert(value-previous <= 3);
  if (x <= 6000) assert.equal(value, x*3);
  previous = value;
}
function render(input) {
  new Uint8Array(dsp.memory.buffer, dsp.input_pointer(), input.length).set(input);
  dsp.render(input.length);
  return Array.from(new Int32Array(dsp.memory.buffer, dsp.output_pointer(), input.length*2));
}
const input = Uint8Array.from({length:2048}, (_, i) => i%256);
dsp.reset();
const whole = render(input);
dsp.reset();
const split = [];
for (let at=0; at<input.length;) {
  const count = Math.min(at%73+1, input.length-at);
  split.push(...render(input.slice(at,at+count))); at+=count;
}
assert.deepEqual(whole, split);
assert(whole.every(x => Math.abs(x) < 28000*65536));
dsp.restart();
const fade = render(Uint8Array.of(128,128));
assert(fade[0]>0 && fade[1] < dsp.limit(32124)*65536);
console.log('PASS production C++ DSP: decoding, all input amplitudes, smooth limiter, chunk continuity, restart fade');
