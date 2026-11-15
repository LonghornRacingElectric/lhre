import {test} from 'node:test';
import {EventEmitter} from 'node:events';
import assert from 'node:assert/strict';
import {scryptSync} from 'node:crypto';
import {mkdtemp, rm, mkdir, writeFile, readdir, stat} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {Readable} from 'node:stream';
import {authenticate, createApplication, validateJob} from './server.mjs';

const password = 'test-password', salt = 'fixed-test-salt';
const config = {username: 'test', passwordSalt: salt, passwordHash: scryptSync(password, salt, 32).toString('hex'), publicOrigin: 'https://jobs.test'};
const authorization = 'Basic ' + Buffer.from('test:' + password).toString('base64');
const id = 'a'.repeat(32);
async function request(app, url, {method = 'GET', data, headers = {}, source = '192.0.2.1'} = {}) {
  const req = Readable.from(data === undefined ? [] : [Buffer.from(JSON.stringify(data))]);
  req.url = url; req.method = method; req.socket = {remoteAddress: source};
  req.headers = {authorization, ...(method === 'POST' ? {'content-type': 'application/json', 'x-lhr-request': 'dashboard', origin: config.publicOrigin} : {}), ...headers};
  const result = {}, chunks = [];
  await app(req, {write(bytes) {chunks.push(bytes); return true;}, writeHead(status, values) {result.status = status; result.headers = values;}, end(body) {if (chunks.length) body = Buffer.concat(chunks); result.raw = body; try {result.body = JSON.parse(body);} catch {result.body = body;}}});
  return result;
}

test('credentials are checked and malformed authentication is rejected', () => {
  assert.equal(authenticate(authorization, config), true);
  for (const value of ['', 'Bearer a', 'Basic !!!', 'Basic ' + Buffer.from('test:wrong').toString('base64')]) assert.equal(authenticate(value, config), false);
});
test('job bounds reject injection, unknown settings, booleans and oversubscription', () => {
  const job = {id, template: 'cpu', parallel: 1, seconds: 15};
  for (const extra of [{id: '../etc'}, {template: 'shell'}, {parallel: true}, {parallel: 9}, {seconds: 0}, {command: 'echo hi'}, {template: 'gpu', parallel: 2}]) assert.throws(() => validateJob({...job, ...extra}));
  assert.deepEqual(validateJob(job), job);
});
test('unauthenticated access cannot reach the runner', async () => {
  let calls = 0;
  const app = createApplication({config, now: () => 10000, rpc: async () => {calls++;}});
  assert.equal((await request(app, '/api/state', {headers: {authorization: ''}})).status, 401);
  assert.equal(calls, 0);
});
test('cross-site and non-JSON mutations are rejected before calling HIL', async () => {
  let calls = 0;
  const app = createApplication({config, now: () => 10000, rpc: async () => {calls++;}});
  for (const headers of [{origin: 'https://evil.test'}, {'x-lhr-request': ''}, {'content-type': 'text/plain'}, {'sec-fetch-site': 'cross-site'}]) {
    assert.equal((await request(app, '/api/pause', {method: 'POST', data: {paused: true}, headers})).status, 403);
  }
  assert.equal(calls, 0);
});
test('submission is bounded and forwards only structured job settings', async () => {
  let received;
  const app = createApplication({config, now: () => 10000, rpc: async value => {received = value; return value.job;}});
  const job = {id, template: 'cpu', parallel: 2, seconds: 5};
  const response = await request(app, '/api/runs', {method: 'POST', data: job});
  assert.equal(response.status, 201);
  assert.deepEqual(received, {op: 'submit', job});
  assert.equal((await request(app, '/api/runs', {method: 'POST', data: {...job, parallel: 99}})).status, 400);
});
test('offline state preserves history and marks it stale; reconnect recovers', async t => {
  const dataDir = await mkdtemp(path.join(tmpdir(), 'lhr-jobs-'));
  t.after(() => rm(dataDir, {recursive: true, force: true}));
  let online = true, clock = 10000;
  const app = createApplication({config: {...config, dataDir}, now: () => clock, rpc: async () => {
    if (!online) throw new Error('HIL connection unavailable');
    return {online: true, jobs: [{id}], templates: {cpu: true}};
  }});
  const live = await request(app, '/api/state');
  assert.equal(live.body.connection, 'connected');
  online = false; clock += 5000;
  const stale = await request(app, '/api/state');
  assert.equal(stale.body.connection, 'disconnected');
  assert.equal(stale.body.online, false);
  assert.equal(stale.body.jobs[0].id, id);
  online = true; clock += 5000;
  assert.equal((await request(app, '/api/state')).body.connection, 'connected');
});
test('cancel and pause operations cannot select executable commands or paths', async () => {
  const calls = [];
  const app = createApplication({config, now: () => 10000, rpc: async value => {calls.push(value); return {ok: true};}});
  assert.equal((await request(app, `/api/runs/${id}/cancel`, {method: 'POST', data: {}})).status, 200);
  assert.equal((await request(app, '/api/pause', {method: 'POST', data: {paused: true}})).status, 200);
  assert.deepEqual(calls, [{op: 'cancel', id}, {op: 'pause', paused: true}]);
  assert.equal((await request(app, '/api/runs/../../etc/logs')).status, 404);
});
test('result downloads are stored on telemetry and reused without contacting HIL', async t => {
  const dataDir = await mkdtemp(path.join(tmpdir(), 'lhr-archive-'));
  t.after(() => rm(dataDir, {recursive: true, force: true}));
  let calls = 0;
  const bytes = Buffer.from('archive fixture');
  const app = createApplication({config: {...config, dataDir}, now: () => 10000, rpc: async value => {
    calls++;
    assert.deepEqual(value, {op: 'archive', id});
    return {base64: bytes.toString('base64')};
  }});
  const first = await request(app, `/api/runs/${id}/results`);
  assert.equal(first.status, 200);
  assert.equal(first.headers['Content-Type'], 'application/gzip');
  assert.deepEqual(first.raw, bytes);
  assert.deepEqual((await request(app, `/api/runs/${id}/results`)).raw, bytes);
  assert.equal(calls, 1);
});

test('failed sign-ins are limited per source, cached credentials still work, and fixed time resets the limit', async () => {
  let clock = 10000;
  const app = createApplication({config, now: () => clock, rpc: async () => ({})});
  assert.equal((await request(app, '/missing')).status, 404);
  const headers = {authorization: 'Basic ' + Buffer.from('test:wrong').toString('base64')};
  for (let attempt = 0; attempt < 5; attempt++) assert.equal((await request(app, '/api/state', {headers})).status, 401);
  assert.equal((await request(app, '/api/state', {headers})).status, 429);
  assert.equal((await request(app, '/missing', {source: '192.0.2.2'})).status, 404);
  assert.equal((await request(app, '/api/state', {headers, source: '192.0.2.2'})).status, 401);
  assert.equal((await request(app, '/missing')).status, 404);
  clock += 60000;
  assert.equal((await request(app, '/api/state', {headers})).status, 401);
});

test('logs are coalesced and cached with a fixed expiry', async () => {
  let clock = 10000, calls = 0, complete;
  const app = createApplication({config, now: () => clock, rpc: () => {calls++; return new Promise(resolve => {complete = resolve;});}});
  const first = request(app, `/api/runs/${id}/logs`);
  const second = request(app, `/api/runs/${id}/logs`);
  assert.equal(calls, 1);
  complete({stdout: 'cached', stderr: ''});
  assert.equal((await first).status, 200);
  assert.equal((await second).body.stdout, 'cached');
  assert.equal((await request(app, `/api/runs/${id}/logs`)).status, 200);
  assert.equal(calls, 1);
  clock += 3000;
  const next = request(app, `/api/runs/${id}/logs`);
  assert.equal(calls, 2); complete({stdout: 'new', stderr: ''});
  assert.equal((await next).body.stdout, 'new');
});

test('dashboard queues RPCs without exceeding two in-flight requests and releases slots after failures', async () => {
  const pending = [];
  const app = createApplication({config, now: () => 10000, rpc: () => new Promise((resolve, reject) => pending.push({resolve, reject}))});
  const first = request(app, `/api/runs/${id}/logs`);
  const second = request(app, `/api/runs/${'b'.repeat(32)}/logs`);
  const third = request(app, `/api/runs/${'c'.repeat(32)}/logs`);
  assert.equal(pending.length, 2);
  pending[0].reject(new Error('connection unavailable'));
  assert.equal((await first).status, 503);
  assert.equal(pending.length, 3);
  pending[1].resolve({stdout: '', stderr: ''}); pending[2].resolve({stdout: 'queued', stderr: ''});
  assert.equal((await second).status, 200); assert.equal((await third).body.stdout, 'queued');
  const next = request(app, `/api/runs/${id}/logs`);
  pending[3].resolve({stdout: 'recovered', stderr: ''});
  assert.equal((await next).status, 200);
});

test('archive downloads are serialized and cache pruning enforces bytes as well as count', async t => {
  const dataDir = await mkdtemp(path.join(tmpdir(), 'lhr-cache-bounds-'));
  t.after(() => rm(dataDir, {recursive: true, force: true}));
  const directory = path.join(dataDir, 'results'); await mkdir(directory);
  // Sparse files exercise byte accounting without allocating large buffers or downloading them.
  const {open} = await import('node:fs/promises');
  for (let number = 0; number < 13; number++) {
    const file = await open(path.join(directory, number.toString(16).padStart(32, '0') + '.tar.gz'), 'w');
    await file.truncate(11 * 1024 * 1024); await file.close();
  }
  let finish, entered;
  const ready = new Promise(resolve => {entered = resolve;});
  const app = createApplication({config: {...config, dataDir}, now: () => 10000, rpc: () => {entered(); return new Promise(resolve => {finish = resolve;});}});
  const first = request(app, `/api/runs/${id}/results`);
  await ready;
  assert.equal((await request(app, `/api/runs/${'b'.repeat(32)}/results`)).status, 429);
  finish({base64: Buffer.from('archive').toString('base64')});
  assert.equal((await first).status, 200);
  const files = await readdir(directory);
  let bytes = 0;
  for (const file of files) bytes += (await stat(path.join(directory, file))).size;
  assert.ok(bytes <= 128 * 1024 * 1024);
  assert.ok(files.length < 14);
  assert.equal((await request(app, `/api/runs/${id}/results`)).status, 200);
});

test('cache pruning retains at most 200 completed archives and leaves unrelated files alone', async t => {
  const dataDir = await mkdtemp(path.join(tmpdir(), 'lhr-cache-count-'));
  t.after(() => rm(dataDir, {recursive: true, force: true}));
  const directory = path.join(dataDir, 'results'); await mkdir(directory);
  for (let number = 0; number < 202; number++) await writeFile(path.join(directory, number.toString(16).padStart(32, '0') + '.tar.gz'), 'fixture');
  await writeFile(path.join(directory, 'operator-notes.txt'), 'keep');
  const app = createApplication({config: {...config, dataDir}, now: () => 10000, rpc: async () => ({base64: Buffer.from('archive').toString('base64')})});
  assert.equal((await request(app, `/api/runs/${id}/results`)).status, 200);
  const files = await readdir(directory);
  assert.equal(files.filter(file => file.endsWith('.tar.gz')).length, 200);
  assert.ok(files.includes('operator-notes.txt'));
  assert.ok(files.includes(id + '.tar.gz'));
});

test('empty browser challenges never consume login attempts or prevent a correct password', async () => {
  const app = createApplication({config, now: () => 10000});
  for (let attempt = 0; attempt < 10; attempt++) {
    const response = await request(app, '/', {headers: {authorization: ''}});
    assert.equal(response.status, 401); assert.ok(response.headers['WWW-Authenticate']);
  }
  assert.equal((await request(app, '/')).status, 200);
});

test('trusted loopback proxy uses the final forwarded source and ignores spoofed prefixes', async () => {
  const app = createApplication({config, now: () => 10000});
  const bad = 'Basic ' + Buffer.from('test:wrong').toString('base64');
  for (let attempt = 0; attempt < 5; attempt++) {
    assert.equal((await request(app, '/', {source: '127.0.0.1', headers: {authorization: bad, 'x-forwarded-for': `192.0.2.${attempt + 10}, 192.0.2.1`}})).status, 401);
  }
  assert.equal((await request(app, '/', {source: '127.0.0.1', headers: {authorization: bad, 'x-forwarded-for': '192.0.2.50, 192.0.2.1'}})).status, 429);
  assert.equal((await request(app, '/', {source: '127.0.0.1', headers: {'x-forwarded-for': '192.0.2.2'}})).status, 200);
  assert.equal((await request(app, '/', {source: '192.0.2.1', headers: {authorization: bad, 'x-forwarded-for': '192.0.2.99'}})).status, 429);
});

test('state waits for an available SSH slot instead of marking a busy worker offline', async t => {
  const dataDir = await mkdtemp(path.join(tmpdir(), 'lhr-busy-state-'));
  t.after(() => rm(dataDir, {recursive: true, force: true}));
  let clock = 10000;
  const pending = [];
  const app = createApplication({config: {...config, dataDir}, now: () => clock, rpc: async value => {
    if (value.op === 'status') return {online: true, jobs: [], templates: {cpu: true}};
    return new Promise(resolve => pending.push(resolve));
  }});
  assert.equal((await request(app, '/api/state')).body.connection, 'connected');
  clock += 5000;
  const first = request(app, `/api/runs/${id}/logs`), second = request(app, `/api/runs/${'b'.repeat(32)}/logs`);
  const state = request(app, '/api/state');
  pending[0]({stdout: '', stderr: ''});
  assert.equal((await state).body.connection, 'connected');
  pending[1]({stdout: '', stderr: ''}); await Promise.all([first, second]);
});

test('a full bounded RPC queue returns connected stale state and rejects excess mutations', async t => {
  const dataDir = await mkdtemp(path.join(tmpdir(), 'lhr-rpc-queue-'));
  t.after(() => rm(dataDir, {recursive: true, force: true}));
  let clock = 10000;
  const pending = [];
  const app = createApplication({config: {...config, dataDir}, now: () => clock, rpc: async value => {
    if (value.op === 'status') return {online: true, jobs: [], templates: {cpu: true}};
    return new Promise(resolve => pending.push(resolve));
  }});
  assert.equal((await request(app, '/api/state')).body.connection, 'connected');
  clock += 5000;
  const requests = Array.from({length: 10}, (_, number) => request(app, `/api/runs/${number.toString(16).padStart(32, '0')}/logs`));
  assert.equal(pending.length, 2);
  const stale = (await request(app, '/api/state')).body;
  assert.equal(stale.connection, 'connected'); assert.equal(stale.online, true); assert.equal(stale.stale, true);
  assert.equal((await request(app, '/api/pause', {method: 'POST', data: {paused: true}})).status, 429);
  for (let index = 0; index < requests.length; index++) {
    pending[index]({stdout: '', stderr: ''});
    assert.equal((await requests[index]).status, 200);
  }
  assert.equal(pending.length, 10);
});


test('catalog jobs forward typed parameters and pinned revisions without permitting executable settings', async () => {
  const job = {id, template: 'campaign', revision: 'a'.repeat(40), parameters: {seed: 3, course: 'autocross'}};
  assert.deepEqual(validateJob(job), job);
  for (const extra of [{revision: 'main'}, {parameters: {seed: true}}, {parameters: []}, {parameters: {'../command': 'id'}}, {command: 'id'}, {parallel: 8}]) assert.throws(() => validateJob({...job, ...extra}));
  const calls = [];
  const app = createApplication({config, rpc: async value => {calls.push(value); return value.job;}});
  assert.equal((await request(app, '/api/runs', {method: 'POST', data: job})).status, 201);
  assert.deepEqual(calls, [{op: 'submit', job}]);
});

test('campaign file download streams all binary chunks without the compact archive limit', async () => {
  const content = Buffer.alloc(1200000, 177), calls = [];
  const app = createApplication({config, rpc: async value => {
    calls.push(value);
    if (value.op === 'artifacts') return [{path: 'runs/bag/run.mcap', bytes: content.length}];
    return {size: content.length, base64: content.subarray(value.offset, value.offset + 512 * 1024).toString('base64')};
  }});
  const listing = await request(app, `/api/runs/${id}/artifacts`);
  assert.equal(listing.body[0].bytes, content.length);
  const download = await request(app, `/api/runs/${id}/files?path=runs%2Fbag%2Frun.mcap`);
  assert.equal(download.status, 200);
  assert.equal(download.headers['Content-Length'], content.length);
  assert.deepEqual(download.raw, content);
  assert.deepEqual(calls.slice(1).map(value => value.offset), [0, 524288, 1048576]);
  assert.ok(calls.slice(1).every(value => value.path === 'runs/bag/run.mcap'));
  const previous = calls.length;
  assert.equal((await request(app, `/api/runs/${id}/files?path=x`, {headers: {authorization: ''}})).status, 401);
  assert.equal(calls.length, previous);
});

test('malformed campaign download is rejected before sending headers', async () => {
  const app = createApplication({config, rpc: async () => ({size: 500, base64: ''})});
  assert.equal((await request(app, `/api/runs/${id}/files?path=x`)).status, 400);
});


test('closing a backpressured artifact download stops reads and releases the download lock', async () => {
  let calls = 0;
  const app = createApplication({config, rpc: async () => {calls++; return {size: 1000000, base64: Buffer.alloc(512 * 1024).toString('base64')};}});
  const run = async () => {
    const req = Readable.from([]); req.url = `/api/runs/${id}/files?path=bag.mcap`; req.method = 'GET';
    req.headers = {authorization}; req.socket = {remoteAddress: '192.0.2.1'};
    const res = new EventEmitter(); res.destroyed = false;
    res.writeHead = status => {assert.equal(status, 200);};
    res.write = () => {queueMicrotask(() => {res.destroyed = true; res.emit('close');}); return false;};
    res.end = () => {};
    await app(req, res);
  };
  await run();
  assert.equal(calls, 1);
  await run();
  assert.equal(calls, 2);
});
