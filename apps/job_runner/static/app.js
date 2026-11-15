const $ = id => document.getElementById(id);
const titles = {cpu: 'CPU smoke test', fmu: 'FMU timing benchmark', gpu: 'GPU rendering check'};
const descriptions = {cpu: 'A short prime-number workload to verify CPU execution and parallel jobs. This is not the recorded sysbench comparison.', fmu: 'Independent copies of the recorded 12-second pedal profile at 1e-6 tolerance, using the native x86 rebuild. Solver warnings remain visible in the output.', gpu: 'A short NVIDIA EGL rendering check. Confirms a hardware renderer and pixel output; it is not a Gazebo performance test.'};
const terminal = new Set(['succeeded', 'failed', 'cancelled']);
let catalogSignature, parameterInputs = {};
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
function catalog() {
  const entries = state.catalog || [];
  const signature = JSON.stringify(entries);
  if (signature === catalogSignature) return;
  const initial = catalogSignature === undefined;
  catalogSignature = signature;
  const previous = $('template').value;
  $('template').replaceChildren();
  if (entries.length) {
    const group = element('optgroup'); group.label = 'Autonomy jobs';
    for (const entry of entries) {
      const option = element('option', entry.title); option.value = entry.id; group.append(option);
    }
    $('template').append(group);
  }
  const group = element('optgroup'); group.label = 'Infrastructure checks';
  for (const [id, title] of Object.entries(titles)) {
    const option = element('option', title); option.value = id; group.append(option);
  }
  $('template').append(group);
  $('template').value = initial && entries.length ? entries[0].id :
    [...Object.keys(titles), ...entries.map(e => e.id)].includes(previous) ? previous : 'cpu';
  workload(false);
}
function allocation(job) {return job.settings.definition ? `${job.settings.parallel} CPU slots${job.settings.definition.gpu ? ' · GPU reserved' : ''}` : `${job.settings.parallel} process${job.settings.parallel === 1 ? '' : 'es'}`;}
function title(job) {return job.settings.definition?.title || titles[job.settings.template] || job.settings.template;}
function render() {
  catalog();
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
  if (!jobs.length) $('runs').append(element('div', filter === 'all' ? 'No runs yet. Queue a job to get started.' : 'No runs in this view.', 'empty'));
  for (const job of jobs) {
    const row = element('div', undefined, 'run'), button = element('button', undefined, 'run-main');
    button.dataset.runId = job.id;
    button.append(element('strong', title(job)), element('span', `${job.id.slice(0, 8)} · ${allocation(job)} · ${new Date(job.created).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})}`));
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
  $('detail-title').textContent = title(job) + ' / ' + job.id.slice(0, 8);
  $('detail-meta').textContent = `${job.status} · ${allocation(job)} · ${elapsed(job)}${job.exit_code !== null ? ' · exit ' + job.exit_code : ''}`;
  $('detail-settings').textContent = job.settings.definition ? `Revision ${job.settings.revision} · ${Object.entries(job.settings.parameters).map(([k,v]) => `${k}: ${v}`).join(' · ')}` : '';
  $('artifacts').replaceChildren();
  if (job.settings.definition && terminal.has(job.status)) {
    try {
      const files = await api(`/api/runs/${id}/artifacts`);
      if (selected !== id) return;
      $('artifacts').append(element('h3', 'Campaign files'));
      for (const file of files) {
        const link = element('a', `${file.path} (${(file.bytes / 1024).toFixed(1)} KiB)`);
        link.href = `/api/runs/${id}/files?path=${encodeURIComponent(file.path)}`;
        const row = element('p'); row.append(link); $('artifacts').append(row);
      }
    } catch (error) {if (selected === id) $('artifacts').textContent = error.message;}
  }
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
function workload(renderState = true) {
  const template = $('template').value;
  const definition = state?.catalog?.find(entry => entry.id === template);
  $('job-settings').hidden = !definition; $('check-settings').hidden = !!definition;
  $('parallel').required = !definition; $('seconds').required = !definition && template === 'cpu';
  $('parameters').replaceChildren(); parameterInputs = {};
  if (definition) {
    $('revision').value = definition.revision;
    for (const [key, spec] of Object.entries(definition.parameters)) {
      const label = element('label', spec.label); label.htmlFor = 'parameter-' + key;
      const input = element(spec.type === 'enum' ? 'select' : 'input'); input.id = label.htmlFor; input.required = true;
      if (spec.type === 'enum') {for (const value of spec.choices) {const option = element('option', value); option.value = value; input.append(option);}}
      else {input.type = 'number'; input.min = spec.min; input.max = spec.max; input.step = 1;}
      input.value = spec.default; parameterInputs[key] = {input, spec};
      $('parameters').append(label, input);
    }
  }
  $('description').textContent = definition?.description || descriptions[template]; $('duration').hidden = template !== 'cpu';
  $('seconds').disabled = !!definition || template !== 'cpu';
  if (template !== 'cpu') $('seconds').value = 15;
  $('parallel').disabled = !!definition || template === 'gpu'; if (template === 'gpu') $('parallel').value = 1;
  if (state && renderState) render();
}
$('launch').addEventListener('submit', async event => {
  event.preventDefault(); launching = true; $('submit').disabled = true; message('');
  try {
    const template = $('template').value, definition = state?.catalog?.find(entry => entry.id === template);
    const settings = definition ? {revision: definition.revision, parameters: Object.fromEntries(Object.entries(parameterInputs).map(([key, {input, spec}]) => [key, spec.type === 'integer' ? Number(input.value) : input.value]))} : {parallel: Number($('parallel').value), seconds: Number($('seconds').value)};
    const job = await api('/api/runs', {id: crypto.randomUUID().replaceAll('-', ''), template, ...settings});
    selected = job.id; await refresh();
  } catch (error) {message(error.message);} finally {launching = false; if (state) render();}
});
$('pause').addEventListener('click', async () => {try {$('pause').disabled = true; await api('/api/pause', {paused: !state.paused}); await refresh();} catch (error) {message(error.message);}});
$('cancel').addEventListener('click', async () => {try {$('cancel').disabled = true; await api(`/api/runs/${selected}/cancel`, {}); await refresh();} catch (error) {message(error.message);}});
$('close').addEventListener('click', () => {selected = null; $('details').hidden = true;});
$('template').addEventListener('change', () => workload()); $('refresh').addEventListener('click', () => {message(''); refresh();});
document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {filter = button.dataset.filter; document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('selected', b === button)); if (state) render();}));
workload(); refresh(); setInterval(refresh, 3000);
