import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdtemp, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const require = createRequire(import.meta.url);
const {apps: [application]} = require('./deploy/ecosystem.config.cjs');

for (const [label, override, expected] of [
  ['unset', undefined, '/home/electric/.config/lhr-jobs/dashboard.json'],
  ['empty', '', '/home/electric/.config/lhr-jobs/dashboard.json'],
  ['custom', '/tmp/custom-dashboard.json', '/tmp/custom-dashboard.json'],
]) {
  test(`PM2 selects the dashboard config when LHR_JOBS_CONFIG is ${label}`, () => {
    const configPath = fileURLToPath(new URL('./deploy/ecosystem.config.cjs', import.meta.url));
    const child = spawnSync(process.execPath, [
      '-e', 'console.log(require(process.argv[1]).apps[0].env.LHR_JOBS_CONFIG)', configPath,
    ], {
      env: override === undefined ? {} : {LHR_JOBS_CONFIG: override},
      encoding: 'utf8',
      timeout: 10000,
    });
    assert.equal(child.status, 0, child.stderr || child.error?.message);
    assert.equal(child.stdout.trim(), expected);
  });
}

test('PM2 launches a native Node process whose ESM entrypoint guard runs', async t => {
  const directory = await mkdtemp(path.join(tmpdir(), 'lhr-native-entrypoint-'));
  t.after(() => rm(directory, {recursive: true, force: true}));
  await writeFile(path.join(directory, 'server.mjs'), `
    import path from 'node:path';
    import {fileURLToPath} from 'node:url';
    import v8 from 'node:v8';
    if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
      console.log(JSON.stringify({started: true, heapLimit: v8.getHeapStatistics().heap_size_limit}));
    }
  `);
  // PM2's JavaScript wrapper changes argv[1]; native execution preserves the entrypoint.
  assert.equal(application.interpreter, 'none');
  assert.equal(application.exec_mode, 'fork');
  assert.equal(application.instances, 1);
  const child = spawnSync(application.script, application.args, {cwd: directory, encoding: 'utf8', timeout: 10000});
  assert.equal(child.status, 0, child.stderr || child.error?.message);
  const result = JSON.parse(child.stdout);
  assert.equal(result.started, true);
  assert.ok(result.heapLimit < 256 * 1024 * 1024);
});
