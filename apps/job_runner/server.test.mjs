import {test} from 'node:test';
import assert from 'node:assert/strict';
import {scryptSync} from 'node:crypto';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {Readable} from 'node:stream';
import {authenticate, createApplication, validateJob} from './server.mjs';

const password = 'test-password', salt = 'fixed-test-salt';
const config = {username: 'test', passwordSalt: salt, passwordHash: scryptSync(password, salt, 32).toString('hex'), publicOrigin: 'https://jobs.test'};
const authorization = 'Basic ' + Buffer.from('test:' + password).toString('base64');
const id = 'a'.repeat(32);
async function request(app, url, {method = 'GET', data, headers = {}} = {}) {
  const req = Readable.from(data === undefined ? [] : [Buffer.from(JSON.stringify(data))]);
  req.url = url; req.method = method;
  req.headers = {authorization, ...(method === 'POST' ? {'content-type': 'application/json', 'x-lhr-request': 'dashboard', origin: config.publicOrigin} : {}), ...headers};
  const result = {};
  await app(req, {writeHead(status, values) {result.status = status; result.headers = values;}, end(body) {result.raw = body; try {result.body = JSON.parse(body);} catch {result.body = body;}}});
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
  const app = createApplication({config, rpc: async () => {calls++;}});
  assert.equal((await request(app, '/api/state', {headers: {authorization: ''}})).status, 401);
  assert.equal(calls, 0);
});
test('cross-site and non-JSON mutations are rejected before calling HIL', async () => {
  let calls = 0;
  const app = createApplication({config, rpc: async () => {calls++;}});
  for (const headers of [{origin: 'https://evil.test'}, {'x-lhr-request': ''}, {'content-type': 'text/plain'}, {'sec-fetch-site': 'cross-site'}]) {
    assert.equal((await request(app, '/api/pause', {method: 'POST', data: {paused: true}, headers})).status, 403);
  }
  assert.equal(calls, 0);
});
test('submission is bounded and forwards only structured job settings', async () => {
  let received;
  const app = createApplication({config, rpc: async value => {received = value; return value.job;}});
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
  const app = createApplication({config, rpc: async value => {calls.push(value); return {ok: true};}});
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
  const app = createApplication({config: {...config, dataDir}, rpc: async value => {
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
