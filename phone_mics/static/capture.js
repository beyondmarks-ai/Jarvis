// Audio capture has its own lifecycle: a live WebSocket does not prove a live mic.
export class PhoneCapture {
  constructor(onFrame, onChange) {
    this.onFrame = onFrame; this.onChange = onChange; this.generation = 0;
    this.frames = 0; this.lastFrame = 0; this.lastSound = 0; this.error = "";
  }
  stop() {
    this.generation++; clearInterval(this.timer);
    if (this.track) this.track.onmute = this.track.onunmute = this.track.onended = null;
    this.stream?.getTracks().forEach(track => track.stop());
    this.node?.disconnect(); this.source?.disconnect();
    if (this.context) { this.context.onstatechange = null; this.context.close().catch(()=>{}); }
    this.stream = this.track = this.node = this.source = this.context = null;
  }
  async start() {
    this.stop(); this.error = "";
    const generation = this.generation;
    this.frames = 0; this.lastFrame = this.lastSound = 0; this.started = Date.now();
    // Unlock synchronously during the Connect/Restart tap, before permission awaits.
    const context = this.context = new AudioContext({latencyHint:"interactive"});
    context.resume().catch(()=>{});
    context.onstatechange = () => this.onChange();
    this.timer = setInterval(()=>this.onChange(), 500);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
      if (generation !== this.generation) { stream.getTracks().forEach(t=>t.stop()); return; }
      this.stream = stream; this.track = stream.getAudioTracks()[0];
      if (!this.track) throw new Error("No microphone track was provided by Android.");
      this.track.onmute = this.track.onunmute = this.track.onended = () => this.onChange();
      await context.audioWorklet.addModule("/static/mic-worklet.js");
      if (generation !== this.generation) return;
      const node = this.node = new AudioWorkletNode(context, "jarvis-capture");
      node.onprocessorerror = () => { this.error = "Audio processor stopped. Tap Restart microphone."; this.onChange(); };
      node.port.onmessage = event => {
        if (generation !== this.generation) return;
        const pcm = new Int16Array(event.data);
        const rms = Math.sqrt(pcm.reduce((sum,x)=>sum+x*x,0)/pcm.length)/32768;
        this.frames++; this.lastFrame = Date.now();
        if (pcm.some(x => x !== 0)) this.lastSound = this.lastFrame;
        this.onFrame(pcm, rms);
      };
      this.source = context.createMediaStreamSource(stream);
      this.source.connect(node); node.connect(context.destination); // Worklet output is always zero.
      this.onChange();
    } catch (error) {
      if (generation === this.generation) { this.stop(); this.error = error.message; this.onChange(); }
      throw error;
    }
  }
  resume() {
    if (this.context && this.context.state !== "running") this.context.resume().catch(()=>{});
  }
  status(now = Date.now()) {
    const age = now - (this.lastFrame || this.started || now);
    let issue = "";
    if (this.error) issue = this.error;
    else if (!this.context || !this.track || this.track.readyState !== "live") issue = "Microphone is not active. Tap Restart microphone.";
    else if (this.context.state !== "running") issue = "Phone audio is paused. Tap Restart microphone.";
    else if (this.track.muted) issue = "Android muted this microphone. Check Microphone access and close other recording apps.";
    else if (age > 2500) issue = "No audio frames received. Tap Restart microphone.";
    else if (now - (this.lastSound || this.started) > 6000) issue = "Only silence received. Speak near the phone; check Android Microphone access if the bar stays still.";
    else if (!this.frames) issue = "Waiting for microphone audio...";
    return {issue, frames:this.frames, audio:this.context?.state || "off",
      input:this.track?.readyState !== "live" ? "off" : this.track.muted ? "muted" : "active"};
  }
}
