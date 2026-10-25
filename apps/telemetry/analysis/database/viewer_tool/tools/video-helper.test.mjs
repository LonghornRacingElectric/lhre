import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, access } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import ffmpeg from 'ffmpeg-static';
import { allowedOrigin, compressionArgs, createVideoHelper } from './video-helper.mjs';

test('origin restrictions and fixed conversion arguments', () => {
  assert.equal(allowedOrigin('http://localhost:3002'), true);
  assert.equal(allowedOrigin('https://evil.example'), false);
  assert.equal(allowedOrigin('http://localhost.evil.example'), false);
  assert.equal(allowedOrigin('null'), false);
  assert.equal(allowedOrigin('https://lapdog.example', ['https://lapdog.example']), true);
  const args = compressionArgs('input.bin', 'output.mp4');
  assert.equal(args.includes('libx264'), true);
  assert.equal(args[args.indexOf('-fpsmax') + 1], '60');
  assert.equal(args.includes('-t'), false);
});

test('real conversion, failure, auth and cancellation', { timeout: 30000 }, async () => {
  const root = await mkdtemp(join(tmpdir(), 'lapdog-helper-test-'));
  const source = join(root, 'sample.mp4');
  const generated = spawnSync(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24', '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '2', '-c:v', 'libx264', '-c:a', 'aac', source], { windowsHide: true });
  assert.equal(generated.status, 0, generated.stderr?.toString());
  const helper = createVideoHelper();
  await new Promise(resolve => helper.server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${helper.server.address().port}`;
  const origin = { Origin: 'http://localhost:3002' };
  try {
    assert.equal((await fetch(base + '/health', { headers: { Origin: 'https://evil.example' } })).status, 403);
    assert.equal((await fetch(base + '/jobs', { method: 'POST', headers: origin })).status, 401);
    const health = await (await fetch(base + '/health', { headers: origin })).json();
    const headers = { ...origin, Authorization: 'Bearer ' + health.token };
    const job = await (await fetch(base + '/jobs', { method: 'POST', headers })).json();
    assert.equal((await fetch(base + '/jobs', { method: 'POST', headers })).status, 409);
    const input = await readFile(source);
    assert.equal((await fetch(base + '/jobs/' + job.id + '/source', { method: 'PUT', headers, body: input })).status, 202);
    let state;
    for (let i = 0; i < 100; i++) {
      state = await (await fetch(base + '/jobs/' + job.id, { headers })).json();
      if (['ready', 'error'].includes(state.state)) break;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(state.state, 'ready', state.error);
    assert.ok(Math.abs(state.duration - 2) < 0.1);
    const output = Buffer.from(await (await fetch(base + '/jobs/' + job.id + '/output', { headers })).arrayBuffer());
    assert.equal(output.subarray(4, 8).toString(), 'ftyp');
    assert.equal((await fetch(base + '/jobs/' + job.id, { method: 'DELETE', headers })).status, 200);
    assert.equal((await fetch(base + '/jobs/' + job.id, { headers })).status, 404);
    assert.deepEqual(await readFile(source), input);
    const bad = await (await fetch(base + '/jobs', { method: 'POST', headers })).json();
    await fetch(base + '/jobs/' + bad.id + '/source', { method: 'PUT', headers, body: Buffer.from('not a video') });
    for (let i = 0; i < 100; i++) {
      state = await (await fetch(base + '/jobs/' + bad.id, { headers })).json();
      if (state.state === 'error') break;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(state.state, 'error');
    await fetch(base + '/jobs/' + bad.id, { method: 'DELETE', headers });
    const cancel = await (await fetch(base + '/jobs', { method: 'POST', headers })).json();
    const submitted = await (await fetch(base + '/jobs/' + cancel.id + '/source', { method: 'PUT', headers, body: input })).json();
    assert.equal(submitted.state, 'encoding');
    assert.equal((await fetch(base + '/jobs/' + cancel.id, { method: 'DELETE', headers })).status, 200);
    assert.equal((await fetch(base + '/jobs/' + cancel.id, { headers })).status, 404);
    await access(source);
  } finally { await helper.close(); await rm(root, { recursive: true, force: true }); }
});
