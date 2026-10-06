const $ = id => document.getElementById(id);
const buttons = [$('modeNormal'), $('modeGenz')];
let busy = false, loaded = false, queued = null;
function render(mode) {
  loaded = ['normal', 'genz'].includes(mode);
  $('modeCurrent').textContent = loaded ? (mode === 'genz' ? 'Gen Z' : 'Normal') : 'Not loaded';
  buttons.forEach((button, index) => {
    button.setAttribute('aria-pressed', String(mode === (index ? 'genz' : 'normal')));
    button.disabled = busy || !loaded;
  });
}
async function sync(mode) {
  if (document.hidden && !mode) return;
  const token = $('token').value.trim();
  if (!token) { render(null); return; }
  if (busy) {
    if (mode) {
      queued = {mode, token};
      buttons.forEach(button => button.disabled = true);
    }
    return;
  }
  busy = true;
  // A background refresh must not disable a button between pointer-down and click.
  buttons.forEach(button => button.disabled = Boolean(mode) || !loaded);
  try {
    const response = await fetch('/api/mode', {method: mode ? 'PUT' : 'GET', cache:'no-store',
      headers:{Authorization:'Bearer ' + token, 'Content-Type':'application/json'},
      body:mode ? JSON.stringify({mode}) : undefined, signal:AbortSignal.timeout(12000)});
    const result = await response.json();
    // Never display results for an access key that has since been changed.
    if ($('token').value.trim() !== token) { render(null); return; }
    if (!response.ok) throw new Error(result.detail || 'Could not load mode.');
    if (!['normal', 'genz'].includes(result.mode)) throw new Error('Invalid mode response.');
    render(result.mode);
    $('modeStatus').textContent = (mode ? 'Saved. ' : '') + (result.mode === 'genz' ? 'Gen Z' : 'Normal') + ' is active for all household phones.';
  } catch (error) {
    render(null);
    $('modeStatus').textContent = error.name === 'TimeoutError' ? 'Mode request timed out; checking again shortly.' : error.message;
  } finally {
    busy = false;
    buttons.forEach(button => button.disabled = !loaded);
    const next = queued;
    queued = null;
    if (next && $('token').value.trim() === next.token) sync(next.mode);
  }
}
$('modeNormal').addEventListener('click', () => sync('normal'));
$('modeGenz').addEventListener('click', () => sync('genz'));
$('token').addEventListener('change', () => sync());
document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(); });
setInterval(() => sync(), 15000);
sync();
