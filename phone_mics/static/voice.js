const $ = id => document.getElementById(id);
const buttons = [$('voiceMale'), $('voiceFemale')];
let busy = false, loaded = false, queued = null;
function render(voice) {
  loaded = ['male', 'female'].includes(voice);
  $('voiceCurrent').textContent = loaded ? (voice === 'female' ? 'Female' : 'Male') : 'Not loaded';
  buttons.forEach((button, index) => {
    button.setAttribute('aria-pressed', String(voice === (index ? 'female' : 'male')));
    button.disabled = busy || !loaded;
  });
}
async function sync(voice) {
  if (document.hidden && !voice) return;
  const token = $('token').value.trim();
  if (!token) { render(null); return; }
  if (busy) {
    if (voice) {
      queued = {voice, token};
      buttons.forEach(button => button.disabled = true);
    }
    return;
  }
  busy = true;
  // Preserve taps during periodic refresh; queue explicit requests until it finishes.
  buttons.forEach(button => button.disabled = Boolean(voice) || !loaded);
  try {
    const response = await fetch('/api/voice', {method:voice ? 'PUT' : 'GET', cache:'no-store',
      headers:{Authorization:'Bearer ' + token, 'Content-Type':'application/json'},
      body:voice ? JSON.stringify({voice}) : undefined, signal:AbortSignal.timeout(12000)});
    const result = await response.json();
    if ($('token').value.trim() !== token) { render(null); return; }
    if (!response.ok) throw new Error(result.detail || 'Could not load voice.');
    if (!['male', 'female'].includes(result.voice)) throw new Error('Invalid voice response.');
    render(result.voice);
    $('voiceStatus').textContent = (voice ? 'Saved. ' : '') + (result.voice === 'female' ? 'Female (Priya)' : 'Male (Shubh)') + ' is active for the household.';
  } catch (error) {
    render(null);
    $('voiceStatus').textContent = error.name === 'TimeoutError' ? 'Voice request timed out; checking again shortly.' : error.message;
  } finally {
    busy = false;
    buttons.forEach(button => button.disabled = !loaded);
    const next = queued;
    queued = null;
    if (next && $('token').value.trim() === next.token) sync(next.voice);
  }
}
$('voiceMale').addEventListener('click', () => sync('male'));
$('voiceFemale').addEventListener('click', () => sync('female'));
$('token').addEventListener('change', () => sync());
document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(); });
setInterval(() => sync(), 15000);
sync();
