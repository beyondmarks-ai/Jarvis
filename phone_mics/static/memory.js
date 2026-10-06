const $ = id => document.getElementById(id);
let busy = false;
async function request(path = '', options = {}) {
  const token = $('token').value.trim();
  if (!token) throw new Error('Enter your microphone access key above first.');
  const response = await fetch('/api/memory' + path, {...options, cache:'no-store',
    headers:{Authorization:'Bearer ' + token, 'Content-Type':'application/json', ...options.headers}});
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Memory request failed.');
  return result;
}
async function refresh() {
  const result = await request();
  $('memoryFacts').replaceChildren();
  for (const fact of result.facts) {
    const item = document.createElement('li'), content = document.createElement('div');
    const text = document.createElement('span'), stamp = document.createElement('small');
    text.textContent = fact.text;
    stamp.textContent = 'Saved ' + new Date(fact.updated_at).toLocaleString();
    content.append(text, stamp);
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'secondary'; button.textContent = 'Forget';
    button.setAttribute('aria-label', 'Forget: ' + fact.text);
    button.addEventListener('click', () => run(async () => {
      if (!window.confirm('Delete this shared household memory and its search vector?\n\n' + fact.text)) return;
      await request('/' + encodeURIComponent(fact.id), {method:'DELETE', headers:{'If-Match':fact._etag}});
      $('memoryAnswer').textContent = 'Deleted from live household memory.';
      await refresh();
    }));
    item.append(content, button); $('memoryFacts').append(item);
  }
  if (!result.facts.length) {
    const empty = document.createElement('li'); empty.textContent = 'No saved facts yet.';
    $('memoryFacts').append(empty);
  }
}
async function run(operation) {
  if (busy) return;
  busy = true; $('memorySend').disabled = true; $('memoryRefresh').disabled = true;
  try { await operation(); }
  catch (error) { $('memoryAnswer').textContent = error.message; }
  finally { busy = false; $('memorySend').disabled = false; $('memoryRefresh').disabled = false; }
}
$('memoryForm').addEventListener('submit', event => {
  event.preventDefault();
  run(async () => {
    $('memoryAnswer').textContent = 'Checking household memory…';
    const result = await request('/command', {method:'POST', body:JSON.stringify({text:$('memoryText').value.trim()})});
    $('memoryAnswer').textContent = result.answer; $('memoryText').value = '';
    await refresh();
  });
});
$('memoryRefresh').addEventListener('click', () => run(refresh));
