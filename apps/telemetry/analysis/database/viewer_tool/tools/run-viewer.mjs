import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { createVideoHelper, HELPER_PORT } from './video-helper.mjs';

const require = createRequire(import.meta.url);
const mode = process.argv[2] === 'start' ? 'start' : 'dev';
const helper = createVideoHelper();
helper.server.on('error', error => console.log(error.code === 'EADDRINUSE' ? 'Using the existing local video helper.' : `Video helper: ${error.message}`));
helper.server.listen(HELPER_PORT, '127.0.0.1');
const next = spawn(process.execPath, [require.resolve('next/dist/bin/next'), mode, ...(mode === 'dev' ? ['--turbopack'] : []), ...(mode === 'dev' ? ['--port', '3001'] : []), ...process.argv.slice(3)], { stdio: 'inherit', windowsHide: true });
let stopping = false;
const stop = async code => {
  if (stopping) return; stopping = true;
  next.kill(); await helper.close(); process.exit(code);
};
next.on('exit', code => { void stop(code ?? 0); });
next.on('error', () => { void stop(1); });
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { void stop(0); });
