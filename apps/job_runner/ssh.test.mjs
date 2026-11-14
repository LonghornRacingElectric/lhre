import {test} from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {sshRpc} from './server.mjs';

function transport() {
  const child = new EventEmitter();
  child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
  const killed = [], calls = [], timers = new Map();
  child.kill = signal => killed.push(signal);
  const config = {sshKey: 'fixture-key', knownHosts: 'fixture-hosts', sshHost: 'worker@hil'};
  const promise = sshRpc(config, {op: 'status'}, {
    spawnProcess: (...args) => {calls.push(args); return child;},
    schedule: (callback, delay) => {assert.equal(delay, 40000); timers.set(1, callback); return 1;},
    unschedule: id => timers.delete(id),
  });
  return {child, killed, calls, timers, promise};
}

test('SSH uses fixed argv and bounded JSON; split UTF-8 decodes after concatenation', async () => {
  const fake = transport();
  const [executable, args, options] = fake.calls[0];
  assert.equal(executable, '/usr/bin/ssh');
  assert.deepEqual(args, ['-T', '-i', 'fixture-key', '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=fixture-hosts', '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2', 'worker@hil']);
  assert.deepEqual(options, {stdio: ['pipe', 'pipe', 'pipe']});
  assert.equal(fake.child.stdin.read().toString(), '{"op":"status"}');
  const bytes = Buffer.from(JSON.stringify({ok: true, result: '✓'}));
  const split = bytes.indexOf(Buffer.from('✓')) + 1;
  fake.child.stdout.write(bytes.subarray(0, split)); fake.child.stdout.write(bytes.subarray(split));
  fake.child.emit('close', 0);
  assert.equal(await fake.promise, '✓');
  assert.equal(fake.timers.size, 0);
});

test('SSH timeout kills the child and clears its timer even after late close', async () => {
  const fake = transport();
  const rejection = assert.rejects(fake.promise, /40 seconds/);
  fake.timers.get(1)();
  await rejection;
  assert.deepEqual(fake.killed, ['SIGKILL']);
  assert.equal(fake.timers.size, 0);
  fake.child.emit('close', 0);
});

test('SSH rejects oversized stdout and cleans up', async () => {
  const fake = transport();
  const rejection = assert.rejects(fake.promise, /too large/);
  fake.child.stdout.emit('data', Buffer.alloc(16 * 1024 * 1024 + 1));
  await rejection;
  assert.deepEqual(fake.killed, ['SIGKILL']);
  assert.equal(fake.timers.size, 0);
});

test('SSH handles process errors, invalid JSON, failed replies, nonzero exits and early stdin closure', async () => {
  for (const scenario of ['process', 'json', 'reply', 'exit', 'stdin']) {
    const fake = transport();
    const rejection = assert.rejects(fake.promise, scenario === 'process' ? /spawn failed/ : scenario === 'json' || scenario === 'stdin' ? /connection unavailable/ : /rejected|Runner request failed/);
    if (scenario === 'process') fake.child.emit('error', new Error('spawn failed'));
    else {
      if (scenario === 'stdin') fake.child.stdin.emit('error', Object.assign(new Error('closed'), {code: 'EPIPE'}));
      fake.child.stderr.write('bounded SSH diagnostic');
      fake.child.stdout.write(scenario === 'reply' ? '{"ok":false,"error":"rejected"}' : scenario === 'exit' ? '{"ok":true,"result":1}' : 'not JSON');
      fake.child.emit('close', scenario === 'exit' ? 255 : 0);
    }
    await rejection;
    assert.equal(fake.timers.size, 0);
  }
});

test('SSH rejects JSON with an invalid response envelope', async () => {
  for (const value of ['null', '[]', '1', '{}', '{"ok":"yes"}']) {
    const fake = transport();
    const rejection = assert.rejects(fake.promise, /invalid response/);
    fake.child.stdout.write(value); fake.child.emit('close', 0);
    await rejection;
    assert.equal(fake.timers.size, 0);
  }
});
