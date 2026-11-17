const $ = id => document.getElementById(id);
const titles = {cpu: 'CPU smoke test', fmu: 'FMU timing benchmark', gpu: 'GPU rendering check'};
const descriptions = {cpu: 'A short prime-number workload to verify CPU execution and parallel jobs. This is not the recorded sysbench comparison.', fmu: 'The recorded 12-second pedal profile at 1e-6 tolerance, using the native x86 rebuild. Solver warnings remain visible in the output.', gpu: 'A short NVIDIA EGL rendering check. Confirms a hardware renderer and pixel output; it is not a Gazebo performance test.'};
const terminal = new Set(['succeeded', 'failed', 'cancelled']);
let state, selected, filter = 'all', refreshing = false, launching = false;

function message(text) {$('message').textContent = text; $('message').hidden = !text;}
async function api(url, data) {
  const response = await fetch(url, data === undefined ? {cache: 'no-store'} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-LHR-Request': 'dashboard'}, body: JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
function element(tag, text, className) {const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node;}
function elapsed(job) {
  if (!job.started) return 'Waiting in queue';
  const seconds = Math.max(0, Math.round(((job.finished ? Date.parse(job.finished) : Date.now()) - Date.parse(job.started)) / 1000));
  return seconds < 60 ? seconds + 's' : Math.floor(seconds / 60) + 'm ' + seconds % 60 + 's';
}
function render() {
  const connected = state.connection === 'connected' && state.online;
  $('connection').textContent = !connected ? 'Offline' : state.stale ? 'Busy · cached status' : state.paused ? 'Queue paused' : 'Ready';
  $('connection').className = 'badge' + (!connected ? ' offline' : '');
  $('hardware').textContent = state.cpu_threads ? `${state.cpu_threads} CPU threads · ${state.gpu?.split(',')[0] || 'GPU status unavailable'} · ${state.limits?.cpu_slots || 8} job CPU slots` : 'Waiting for worker details';
  $('running').textContent = state.jobs.filter(j => ['starting', 'running', 'cancelling'].includes(j.status)).length;
  $('queued').textContent = state.jobs.filter(j => j.status === 'queued').length;
  $('disk').textContent = state.disk_free_bytes ? (state.disk_free_bytes / 1024 ** 4).toFixed(1) + ' TiB' : '—';
  $('pause').disabled = !connected; $('pause').textContent = state.paused ? 'Resume queue' : 'Pause new jobs';
  $('submit').disabled = !connected || launching || !state.templates?.[$('template').value];
  $('updated').textContent = (state.stale ? 'Cached status from ' : connected ? 'Updated ' : 'Last connected ') + (state.checked_at ? new Date(state.checked_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : '—');
  if (!connected) message(state.error || 'The HIL worker is offline. Existing jobs remain on HIL; reconnect before submitting new work.');
  const jobs = state.jobs.filter(j => filter === 'all' || (filter === 'finished' ? terminal.has(j.status) : !terminal.has(j.status)));
  const focusedRun = document.activeElement?.dataset.runId;
  $('runs').replaceChildren();
  if (!jobs.length) $('runs').append(element('div', filter === 'all' ? 'No runs yet. Queue a workload to get started.' : 'No runs in this view.', 'empty'));
  for (const job of jobs) {
    const row = element('div', undefined, 'run'), button = element('button', undefined, 'run-main');
    button.dataset.runId = job.id;
    button.append(element('strong', titles[job.settings.template]), element('span', `${job.id.slice(0, 8)} · ${job.settings.parallel} process${job.settings.parallel === 1 ? '' : 'es'} · ${new Date(job.created).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})}`));
    button.addEventListener('click', () => {selected = job.id; showDetails(); $('details').scrollIntoView({behavior: 'smooth', block: 'nearest'});});
    const status = element('div', undefined, 'run-status'); status.append(element('span', job.cancel_requested && !terminal.has(job.status) ? 'cancelling' : job.status, 'badge ' + job.status), element('small', elapsed(job)));
    row.append(button, status); $('runs').append(row);
    if (focusedRun === job.id) button.focus();
  }
}
async function showDetails() {
  const id = selected, job = state?.jobs.find(j => j.id === id);
  $('details').hidden = !job;
  if (!job) return;
  $('detail-title').textContent = titles[job.settings.template] + ' / ' + job.id.slice(0, 8);
  $('detail-meta').textContent = `${job.status} · ${job.settings.parallel} process(es) · ${elapsed(job)}${job.exit_code !== null ? ' · exit ' + job.exit_code : ''}`;
  $('detail-error').textContent = job.error || ''; $('detail-error').hidden = !job.error;
  $('cancel').hidden = terminal.has(job.status); $('cancel').disabled = !!job.cancel_requested || state.connection !== 'connected' || !state.online;
  $('download').hidden = !terminal.has(job.status); $('download').href = `/api/runs/${id}/results`;
  try {
    const logs = await api(`/api/runs/${id}/logs`);
    if (selected !== id) return;
    $('logs').textContent = (logs.stdout + (logs.stderr ? '\n--- stderr ---\n' + logs.stderr : '')).trim() || (job.status === 'queued' ? 'Queued. Output will appear when the job starts.' : 'No output yet.');
  } catch (error) {if (selected === id) $('logs').textContent = error.message;}
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {state = await api('/api/state'); render(); if (selected) await showDetails();}
  catch (error) {state = {...(state || {jobs: [], templates: {}}), connection: 'disconnected', online: false, error: error.message}; render(); $('cancel').disabled = true;}
  finally {refreshing = false;}
}
function workload() {
  const template = $('template').value;
  $('description').textContent = descriptions[template]; $('duration').hidden = template !== 'cpu';
  $('seconds').disabled = template !== 'cpu';
  if (template !== 'cpu') $('seconds').value = 15;
  $('parallel').disabled = template === 'gpu'; if (template === 'gpu') $('parallel').value = 1;
  if (state) render();
}
$('launch').addEventListener('submit', async event => {
  event.preventDefault(); launching = true; $('submit').disabled = true; message('');
  try {
    const job = await api('/api/runs', {id: crypto.randomUUID().replaceAll('-', ''), template: $('template').value, parallel: Number($('parallel').value), seconds: Number($('seconds').value)});
    selected = job.id; await refresh();
  } catch (error) {message(error.message);} finally {launching = false; if (state) render();}
});
$('pause').addEventListener('click', async () => {try {$('pause').disabled = true; await api('/api/pause', {paused: !state.paused}); await refresh();} catch (error) {message(error.message);}});
$('cancel').addEventListener('click', async () => {try {$('cancel').disabled = true; await api(`/api/runs/${selected}/cancel`, {}); await refresh();} catch (error) {message(error.message);}});
$('close').addEventListener('click', () => {selected = null; $('details').hidden = true;});
$('template').addEventListener('change', workload); $('refresh').addEventListener('click', () => {message(''); refresh();});
document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {filter = button.dataset.filter; document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('selected', b === button)); if (state) render();}));
workload(); refresh(); setInterval(refresh, 3000);
