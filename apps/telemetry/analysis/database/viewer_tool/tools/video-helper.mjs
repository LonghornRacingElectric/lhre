import { createServer } from 'node:http';
import { randomBytes, randomUUID } from 'node:crypto';
import { mkdtemp, rm, stat, unlink } from 'node:fs/promises';
import { createReadStream, createWriteStream } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pipeline } from 'node:stream/promises';
import { Transform } from 'node:stream';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import ffmpegStatic from 'ffmpeg-static';

export const HELPER_PORT = 43129;
const MAX_BYTES = 8 * 1024 ** 3;

export function allowedOrigin(origin, extra = []) {
  try {
    const url = new URL(origin);
    return url.origin === origin && ((url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) || extra.includes(origin));
  } catch { return false; }
}

export function compressionArgs(input, output) {
  return ['-hide_banner', '-nostdin', '-y', '-threads', '2', '-protocol_whitelist', 'file,pipe', '-i', input,
    '-map', '0:v:0', '-map', '0:a:0?', '-vf', "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
    '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '24', '-pix_fmt', 'yuv420p', '-fpsmax', '60',
    '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', '-map_metadata', '-1', '-progress', 'pipe:1', '-nostats', output];
}

export function createVideoHelper({ origins = [], ffmpeg = process.env.LAPDOG_FFMPEG_PATH || ffmpegStatic } = {}) {
  const token = randomBytes(32).toString('hex');
  const jobs = new Map();
  const clean = async job => {
    if (job.cleaned) return;
    job.cleaned = true;
    job.process?.kill(); job.upload?.destroy();
    // Only directories minted by mkdtemp below ever enter the job registry.
    if (job.process && !job.exited) await new Promise(resolve => job.process.once('close', resolve));
    await rm(job.directory, { recursive: true, force: true });
  };
  const summary = job => ({ id: job.id, state: job.state, progress: job.duration ? Math.min(99, job.seconds / job.duration * 100) : null, error: job.error, bytes: job.bytes, duration: job.duration });
  const convert = job => {
    job.state = 'encoding';
    const child = spawn(ffmpeg, compressionArgs(job.input, job.output), { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    job.process = child;
    let progressBuffer = '', diagnostics = '';
    child.stdout.on('data', chunk => {
      progressBuffer += chunk.toString();
      const lines = progressBuffer.split('\n'); progressBuffer = lines.pop() || '';
      for (const line of lines) if (line.startsWith('out_time_us=')) job.seconds = Math.max(0, Number(line.slice(12)) / 1e6 || 0);
    });
    child.stderr.on('data', chunk => {
      diagnostics = (diagnostics + chunk.toString()).slice(-16000);
      const match = diagnostics.match(/Duration: (\d+):(\d+):([\d.]+)/);
      if (match) job.duration = +match[1] * 3600 + +match[2] * 60 + +match[3];
    });
    child.on('error', () => { job.state = 'error'; job.error = 'FFmpeg could not start. Reinstall dependencies or set LAPDOG_FFMPEG_PATH.'; });
    child.on('close', async code => {
      job.exited = true;
      if (job.cleaned) return;
      if (code === 0) {
        try { job.bytes = (await stat(job.output)).size; job.state = 'ready'; await unlink(job.input); }
        catch { job.state = 'error'; job.error = 'Could not read the playback copy.'; }
      } else { job.state = 'error'; job.error ||= 'This video could not be converted. It may be incomplete, damaged, or unsupported.'; }
      if (job.state === 'error') await clean(job).catch(() => {});
      job.updated = Date.now();
    });
  };
  const server = createServer(async (req, res) => {
    const origin = req.headers.origin;
    const host = req.headers.host || '';
    if (!/^127\.0\.0\.1:\d+$/.test(host) || !allowedOrigin(origin, origins)) { res.writeHead(403).end(); return; }
    res.setHeader('Access-Control-Allow-Origin', origin);
    res.setHeader('Vary', 'Origin'); res.setHeader('Cache-Control', 'no-store');
    if (req.method === 'OPTIONS') {
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
      res.setHeader('Access-Control-Allow-Headers', 'Authorization, Content-Type');
      res.setHeader('Access-Control-Allow-Private-Network', 'true');
      res.writeHead(204).end(); return;
    }
    const send = (status, value) => { if (!res.destroyed) { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); } };
    const url = new URL(req.url, 'http://127.0.0.1');
    try {
      if (req.method === 'GET' && url.pathname === '/health') { send(200, { token, version: 1, maxBytes: MAX_BYTES }); return; }
      if (req.headers.authorization !== `Bearer ${token}`) { send(401, { error: 'Reconnect to the video helper.' }); return; }
      if (req.method === 'POST' && url.pathname === '/jobs') {
        if ([...jobs.values()].some(j => ['waiting', 'uploading', 'encoding'].includes(j.state))) { send(409, { error: 'Another video is being compressed. Wait for it to finish or cancel it.' }); return; }
        const directory = await mkdtemp(join(tmpdir(), 'lapdog-video-'));
        const id = randomUUID();
        const job = { id, directory, input: join(directory, 'source.bin'), output: join(directory, 'playback.mp4'), state: 'waiting', seconds: 0, duration: 0, bytes: 0, updated: Date.now() };
        jobs.set(id, job); send(201, summary(job)); return;
      }
      const match = url.pathname.match(/^\/jobs\/([a-f0-9-]+)(?:\/(source|output))?$/);
      const job = match && jobs.get(match[1]);
      if (!job) { send(404, { error: 'The video conversion expired. Select the video again.' }); return; }
      job.updated = Date.now();
      if (req.method === 'DELETE' && !match[2]) { await clean(job); jobs.delete(job.id); send(200, { ok: true }); return; }
      if (req.method === 'GET' && !match[2]) { send(200, summary(job)); return; }
      if (req.method === 'PUT' && match[2] === 'source') {
        if (job.state !== 'waiting') { send(409, { error: 'This video has already been submitted.' }); return; }
        const size = Number(req.headers['content-length']);
        if (!Number.isFinite(size) || size <= 0 || size > MAX_BYTES) { send(413, { error: 'Choose a nonempty video smaller than 8 GB.' }); await clean(job); jobs.delete(job.id); return; }
        job.state = 'uploading'; job.upload = req;
        let received = 0;
        const limit = new Transform({ transform(chunk, encoding, callback) { received += chunk.length; callback(received > MAX_BYTES ? new Error('Video exceeds 8 GB.') : null, chunk); } });
        try { await pipeline(req, limit, createWriteStream(job.input, { flags: 'wx' })); }
        catch { await clean(job); jobs.delete(job.id); return; }
        if (job.cleaned) return;
        job.upload = null; convert(job); send(202, summary(job)); return;
      }
      if (req.method === 'GET' && match[2] === 'output' && job.state === 'ready') {
        res.writeHead(200, { 'Content-Type': 'video/mp4', 'Content-Length': job.bytes });
        await pipeline(createReadStream(job.output), res); return;
      }
      send(409, { error: 'The playback copy is not ready.' });
    } catch { if (!res.headersSent) send(500, { error: 'Local video conversion failed. Check available disk space and restart the helper.' }); }
  });
  const sweep = setInterval(() => {
    for (const [id, job] of jobs) if (Date.now() - job.updated > 60 * 60 * 1000) { void clean(job).catch(() => {}); jobs.delete(id); }
  }, 60000);
  sweep.unref();
  server.on('close', () => clearInterval(sweep));
  return { server, async close() { clearInterval(sweep); await Promise.all([...jobs.values()].map(clean)); await new Promise(resolve => server.close(resolve)); } };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const origins = [...(process.env.LAPDOG_VIDEO_ORIGINS || '').split(',').filter(Boolean)];
  for (let i = 2; i < process.argv.length; i++) if (process.argv[i] === '--origin' && process.argv[i + 1]) origins.push(process.argv[++i]);
  const helper = createVideoHelper({ origins });
  helper.server.on('error', error => { console.error(error.code === 'EADDRINUSE' ? 'Video helper is already running on port 43129.' : error.message); process.exitCode = 1; });
  helper.server.listen(HELPER_PORT, '127.0.0.1', () => console.log('LAPDOG video helper ready on 127.0.0.1:43129. Videos stay on this computer.'));
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { void helper.close().finally(() => process.exit()); });
}
