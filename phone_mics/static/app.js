import { PhoneCapture } from "./capture.js";
const $ = id => document.getElementById(id);
let socket, capture, wakeLock, heartbeat, watchdog, sentFrames = 0;
let id, state, ready = false, connecting = false, lastMessage = 0;
let hubMetrics, pingSent = 0, roundTrip = null, testElapsed = 0, testUpdated = 0, testReport;
let connectionGeneration = 0;
let supportsTest = false;
$("name").value = localStorage.getItem("jarvis-mic-name") || "";
$("token").value = sessionStorage.getItem("jarvis-mic-token") || "";
// Optional private pairing link. Fragments never reach the HTTP server; remove
// the key from this history entry immediately. Connecting still needs a user tap.
const pairing = new URLSearchParams(location.hash.slice(1));
const pairingKey = pairing.get("key");
if (pairingKey) {
  history.replaceState(null, "", location.pathname + location.search);
  if (/^[A-Za-z0-9_-]{24,128}$/.test(pairingKey)) $("token").value = pairingKey;
}

function notice(message) { $("notice").textContent = message; }
function captureStatus() {
  const audio = capture?.status();
  $("audioStatus").textContent = audio
    ? `Audio: ${audio.audio} · input: ${audio.input} · frames: ${audio.frames} · sent: ${sentFrames} · v7`
    : "Audio off · app v7";
  if (audio?.issue) $("level").style.width = "0%";
  render();
}
function audioFrame(pcm, rms) {
  $("level").style.width = `${Math.min(100,rms*500)}%`;
  if (!ready || socket?.readyState !== WebSocket.OPEN || !state) return;
  if (!(state.phase === "idle" && state.speaker_connected) && !(["recording","testing"].includes(state.phase) && state.owner === id)) return;
  if (socket.bufferedAmount > 25600) { disconnect("Wi-Fi cannot keep up with live audio. Reconnect when the signal improves."); return; }
  const packet = new ArrayBuffer(pcm.byteLength+4), view = new DataView(packet);
  view.setUint32(0,state.epoch,true);
  for (let i=0;i<pcm.length;i++) view.setInt16(4+i*2,pcm[i],true);
  socket.send(packet); sentFrames++;
}
function render() {
  $("count").textContent = `${state?.microphones?.length || 0} / 3`;
  $("speaker").textContent = state?.speaker_connected ? "Connected · all sound plays here" : "Not connected · wake detection waiting";
  $("devices").replaceChildren();
  for (const peer of state?.microphones || []) {
    const li = document.createElement("li"), name = document.createElement("span"), status = document.createElement("span");
    name.textContent = peer.name + (peer.id === id ? " (this phone)" : "");
    status.textContent = peer.id === state.owner ? (state.phase === "testing" ? "Distance check" : "Active turn") : state.owner ? "Paused" : "Connected";
    li.append(name, status); $("devices").append(li);
  }
  renderDiagnostics();
  if (!ready) return;
  let title = "Listening for Hey Jarvis", detail = "Say the wake word, then wait for the IoT beep.";
  if (!state?.speaker_connected) { title = "Waiting for your IoT"; detail = "The phone is connected, but the IoT speaker must connect before a conversation can start."; }
  else if (state.phase === "beeping") { title = "Wait for the beep"; detail = "Jarvis is getting ready on the IoT speaker."; }
  else if (state.phase === "recording") { title = state.owner === id ? "Ask your question" : "Another phone is speaking"; detail = state.owner === id ? "This microphone has the turn. Speak normally." : "Your microphone is paused for this conversation."; }
  else if (state.phase === "thinking") { title = "Jarvis is thinking"; detail = "Your question is being processed. Listen for the answer on your IoT."; }
  else if (state.phase === "speaking") { title = "Jarvis is answering"; detail = "The IoT speaker is playing the reply. Microphones resume afterward."; }
  else if (state.phase === "cooldown") { title = "Getting ready again"; detail = "Waiting briefly for the speaker to finish."; }
  if (state?.phase === "testing") { title = "Distance check in progress"; detail = state.owner === id ? "Follow the prompts in Distance check. No beep or reply will play." : "Another phone is testing. This microphone is paused."; }
  const issue = capture?.status().issue;
  if (issue) { title = "Check phone microphone"; detail = issue; }
  $("statusTitle").textContent = title; $("statusDetail").textContent = detail;
}
function renderDiagnostics() {
  $("hubStatus").textContent = ready && hubMetrics
    ? `Hub received ${hubMetrics.frames} frames · ${hubMetrics.gaps} audio gaps · round trip ${roundTrip ?? "…"} ms`
    : "Connect to see whether audio reaches the hub.";
  $("wakeStatus").textContent = ready && hubMetrics
    ? `Session wake peak ${hubMetrics.peak_score.toFixed(2)} / cutoff ${hubMetrics.threshold.toFixed(2)} · input ${hubMetrics.level_dbfs} dBFS · clipping ${hubMetrics.clipped_percent}% · inference ${hubMetrics.inference_ms} ms`
    : "Wake settings unchanged.";
  const testing = ready && state?.phase === "testing", mine = testing && state.owner === id;
  $("testStart").disabled = !ready || !supportsTest || state?.phase !== "idle" || !!capture?.status().issue;
  $("testDistance").disabled = testing;
  $("testCancel").hidden = !mine; $("testStart").hidden = mine; $("testProgress").hidden = !mine;
  if (mine) {
    const elapsed = Math.min(20, testElapsed + (performance.now()-testUpdated)/1000);
    $("testProgress").value = elapsed;
    $("testPrompt").textContent = elapsed < 5
      ? `Room-noise check: stay quiet for ${Math.ceil(5-elapsed)} seconds.`
      : `Attempt ${Math.min(3,Math.floor((elapsed-5)/5)+1)} of 3: say “Hey Jarvis” once. ${Math.max(0,Math.ceil(5-elapsed%5))} seconds left in this attempt.`;
  } else $("testPrompt").textContent = testing ? "Another phone is testing. Please wait." : ready ? "Choose your distance, stand there, then start. Keep the screen visible." : "Connect your microphone to begin.";
}
function showTestReport(report) {
  testReport = report;
  $("copyTest").hidden = report.cancelled;
  if (report.cancelled) { $("testResult").textContent = "Test cancelled. No settings were changed."; return; }
  const count = report.hits.filter(Boolean).length;
  let advice = "Repeat in your usual room noise before relying on this distance.";
  if (!report.sufficient_audio) advice = "Incomplete audio: check the microphone and connection, then repeat.";
  else if (report.gaps) advice = "Audio gaps occurred. Improve Wi-Fi or keep the app in the foreground, then repeat.";
  else if (report.clipped_frames) advice = "Input clipping occurred. Try a different phone position and repeat.";
  else if (report.background_trigger) advice = "Room noise crossed the wake cutoff. Do not increase sensitivity; move the phone away from noise and repeat.";
  else if (count < 3) advice = "Not reliable at this distance in this test. Move the phone closer or add another phone, then repeat.";
  $("testResult").textContent = `${count}/3 attempts detected at ${report.distance_m} m. Peaks: ${report.attempt_peaks.map(x=>x.toFixed(2)).join(", ")}; room-noise peak ${report.background_peak.toFixed(2)}; cutoff ${report.threshold.toFixed(2)}. ${advice}`;
}
async function keepAwake() {
  if (ready && document.visibilityState === "visible" && "wakeLock" in navigator) {
    try { wakeLock = await navigator.wakeLock.request("screen"); }
    catch { notice("Could not keep the screen awake. Keep this app open and the phone unlocked."); }
  }
}
async function disconnect(message = "Microphone off. You can reconnect whenever you like.") {
  connectionGeneration++;
  ready = false; connecting = false; clearInterval(heartbeat); clearInterval(watchdog);
  if (socket) { socket.onclose = null; socket.close(); socket = null; }
  capture?.stop(); capture = null;
  await wakeLock?.release().catch(()=>{}); wakeLock = null;
  $("connectForm").hidden = false; $("stopButton").hidden = true; $("connectButton").disabled = false;
  $("restartButton").hidden = true; $("audioStatus").textContent = "Audio off · app v4";
  $("connection").textContent = "Offline"; $("privacyIndicator").textContent = "Mic off";
  $("orb").classList.remove("active"); $("level").style.width = "0%";
  $("statusTitle").textContent = "Microphone disconnected"; $("statusDetail").textContent = message;
  if (state?.phase === "testing" && state.owner === id) showTestReport({cancelled:true});
  state = null; hubMetrics = null; roundTrip = null; supportsTest = false; render();
}
$("connectForm").addEventListener("submit", async event => {
  event.preventDefault(); if (connecting || ready) return;
  connecting = true; notice(""); $("connectButton").disabled = true;
  const generation = ++connectionGeneration;
  try {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error("Microphone access requires HTTPS on your phone. A plain http://PC-address link will not work.");
    sentFrames = 0;
    capture = new PhoneCapture(audioFrame, captureStatus);
    await capture.start();
    if (generation !== connectionGeneration) return;
    const token = $("token").value.trim(), name = $("name").value.trim();
    socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/mic`);
    const thisSocket = socket;
    socket.onopen = () => socket.send(JSON.stringify({token,name}));
    socket.onmessage = async event => {
      if (socket !== thisSocket) return;
      lastMessage = Date.now();
      let message; try { message = JSON.parse(event.data); } catch { return; }
      if (message.type === "welcome") id = message.id;
      if (message.type === "state") {
        if (message.phase === "testing" && state?.phase !== "testing") { testElapsed = 0; testUpdated = performance.now(); }
        state = message; render();
      }
      if (message.type === "pong" && message.metrics) {
        hubMetrics = message.metrics;
        if (pingSent) roundTrip = Math.round(performance.now()-pingSent);
        if (Number.isFinite(hubMetrics.test_elapsed)) { testElapsed = hubMetrics.test_elapsed; testUpdated = performance.now(); }
        renderDiagnostics();
      }
      if (message.type === "test_result") showTestReport(message);
      if (message.type === "notice") notice(message.message);
      if (message.type === "ready") {
        supportsTest = message.capabilities?.includes("distance-test") || false;
        ready = true; connecting = false;
        localStorage.setItem("jarvis-mic-name", name); sessionStorage.setItem("jarvis-mic-token", token);
        $("connectForm").hidden = true; $("stopButton").hidden = false;
        $("restartButton").hidden = false;
        $("connection").textContent = "Connected"; $("privacyIndicator").textContent = "Mic on"; $("orb").classList.add("active");
        await keepAwake(); render();
      }
    };
    socket.onclose = () => { if (socket === thisSocket) disconnect("Connection lost. Tap Connect to resume; the microphone has been stopped."); };
    socket.onerror = () => notice("Could not connect. Check HTTPS, Wi-Fi and the microphone access key.");
    lastMessage = Date.now();
    heartbeat = setInterval(()=>{ if(socket?.readyState===WebSocket.OPEN) { pingSent = performance.now(); socket.send("ping"); } },2000);
    watchdog = setInterval(()=>{ if(Date.now()-lastMessage>20000) disconnect("Hub stopped responding. Microphone capture has been stopped."); },2000);
  } catch (error) { if (generation === connectionGeneration) { await disconnect(); notice(error.message || "Microphone permission was denied."); } }
});
$("stopButton").addEventListener("click",()=>disconnect());
$("testStart").addEventListener("click",()=>{
  if (!ready || state?.phase !== "idle" || socket?.readyState !== WebSocket.OPEN) return;
  $("testResult").textContent = ""; $("copyTest").hidden = true;
  socket.send(`test:${$("testDistance").value}`);
});
$("testCancel").addEventListener("click",()=>{
  if (socket?.readyState === WebSocket.OPEN) socket.send("test:cancel");
});
$("copyTest").addEventListener("click",async()=>{
  if (!testReport) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify({app_version:4, ...testReport},null,2));
    notice("Test report copied. It contains measurements only, no audio or access keys.");
  } catch { notice("Clipboard unavailable. You can share the displayed test results instead."); }
});
$("restartButton").addEventListener("click", async () => {
  if (!ready || !capture) return;
  $("restartButton").disabled = true; notice("");
  try { await capture.start(); }
  catch (error) { notice(error.message || "Could not restart microphone. Check Android microphone permission."); }
  finally { $("restartButton").disabled = false; captureStatus(); }
});
document.addEventListener("visibilitychange",()=>{
  if(document.visibilityState === "visible") { if(ready) capture?.resume(); keepAwake(); }
  else if(ready) notice("Keep this app visible. Background microphone capture is not guaranteed.");
});
window.addEventListener("pagehide",()=>{disconnect();});
