import http from 'node:http';
import {spawn} from 'node:child_process';
import {createHash, randomBytes, scryptSync, timingSafeEqual} from 'node:crypto';
import {readFile, writeFile, mkdir, rename, readdir, unlink, stat} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const idPattern = /^[a-f0-9]{32}$/;
const staticFiles = new Map([['/', ['index.html', 'text/html']], ['/app.js', ['app.js', 'text/javascript']], ['/style.css', ['style.css', 'text/css']]]);

export function validateJob(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input) || Object.keys(input).some(k => !['id', 'template', 'parallel', 'seconds'].includes(k))) throw new Error('Unknown job settings');
  if (!idPattern.test(input.id) || !['cpu', 'fmu', 'gpu'].includes(input.template)) throw new Error('Invalid workload or request ID');
  if (!Number.isInteger(input.parallel) || input.parallel < 1 || input.parallel > 8) throw new Error('Parallelism must be 1 to 8');
  if (!Number.isInteger(input.seconds) || input.seconds < 5 || input.seconds > 120) throw new Error('CPU duration must be 5 to 120 seconds');
  if (input.template === 'gpu' && input.parallel !== 1) throw new Error('GPU checks reserve the whole GPU');
  return {id: input.id, template: input.template, parallel: input.parallel, seconds: input.seconds};
}

export function sshRpc(config, request, {spawnProcess = spawn, schedule = setTimeout, unschedule = clearTimeout} = {}) {
  return new Promise((resolve, reject) => {
    const args = ['-T', '-i', config.sshKey, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
      '-o', 'StrictHostKeyChecking=yes', '-o', `UserKnownHostsFile=${config.knownHosts}`,
      '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2', config.sshHost];
    const child = spawnProcess('/usr/bin/ssh', args, {stdio: ['pipe', 'pipe', 'pipe']});
    const chunks = [];
    let size = 0, errors = '', settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      unschedule(timer);
      if (error) reject(error); else resolve(value);
    };
    const timer = schedule(() => {child.kill('SIGKILL'); finish(new Error('HIL did not respond within 40 seconds'));}, 40000);
    child.stdout.on('data', chunk => {
      if (settled) return;
      size += chunk.length;
      if (size > 16 * 1024 * 1024) {child.kill('SIGKILL'); finish(new Error('HIL response is too large'));}
      else chunks.push(chunk);
    });
    child.stderr.on('data', chunk => {errors = (errors + chunk).slice(-1000);});
    child.stdin.on('error', () => {});
    child.on('error', error => finish(error));
    child.on('close', code => {
      if (settled) return;
      let response;
      try {response = JSON.parse(Buffer.concat(chunks).toString());}
      catch {return finish(new Error('HIL connection unavailable' + (errors ? ': ' + errors.trim() : '')));}
      if (!response || typeof response !== 'object' || Array.isArray(response) || typeof response.ok !== 'boolean') return finish(new Error('HIL connection returned an invalid response'));
      if (code !== 0 || !response.ok) return finish(new Error(response.error || 'Runner request failed'));
      finish(null, response.result);
    });
    child.stdin.end(JSON.stringify(request));
  });
}

export function authenticate(header, config) {
  if (typeof header !== 'string' || !header.startsWith('Basic ') || header.length > 2048) return false;
  const decoded = Buffer.from(header.slice(6), 'base64').toString();
  const separator = decoded.indexOf(':');
  if (separator < 0 || decoded.slice(0, separator) !== config.username) return false;
  const actual = scryptSync(decoded.slice(separator + 1), config.passwordSalt, 32);
  const expected = Buffer.from(config.passwordHash, 'hex');
  return expected.length === actual.length && timingSafeEqual(actual, expected);
}

function respond(res, status, value, extra = {}) {
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    ...extra,
  });
  res.end(Buffer.isBuffer(value) || typeof value === 'string' ? value : JSON.stringify(value));
}

async function body(req) {
  let bytes = 0, result = '';
  for await (const chunk of req) {
    bytes += chunk.length;
    if (bytes > 8192) throw new Error('Request is too large');
    result += chunk;
  }
  try {return JSON.parse(result);} catch {throw new Error('Invalid JSON request');}
}

export function createApplication({config, rpc = request => sshRpc(config, request), now = () => Date.now(), staticRoot = path.join(here, 'static')}) {
  const authCache = new Map();
  let authWindow = now(), authFailures = 0, inFlight = 0, downloading = false;
  const transport = rpc;
  rpc = async request => {
    if (inFlight >= 2) throw new Error('HIL connection busy; retry shortly');
    inFlight++;
    try {return await transport(request);} finally {inFlight--;}
  };
  const logCache = new Map();
  let snapshot = null, checkedAt = 0, pending = null;
  async function state() {
    if (pending) return pending;
    if (snapshot && now() - checkedAt < 2500) return snapshot;
    pending = (async () => {
      try {
        const live = await rpc({op: 'status'});
        snapshot = {...live, connection: 'connected', checked_at: new Date(now()).toISOString()};
        checkedAt = now();
        await mkdir(config.dataDir, {recursive: true});
        await writeFile(path.join(config.dataDir, 'state.tmp'), JSON.stringify(snapshot));
        await rename(path.join(config.dataDir, 'state.tmp'), path.join(config.dataDir, 'state.json'));
      } catch (error) {
        if (!snapshot) {
          try {snapshot = JSON.parse(await readFile(path.join(config.dataDir, 'state.json'), 'utf8'));} catch {snapshot = {jobs: [], templates: {}};}
        }
        snapshot = {...snapshot, online: false, connection: 'disconnected', error: error.message};
        checkedAt = now();
      }
      return snapshot;
    })().finally(() => {pending = null;});
    return pending;
  }
  return async function handle(req, res) {
    try {
      const url = new URL(req.url, 'http://localhost');
      if (url.pathname === '/healthz' && req.method === 'GET') return respond(res, 200, {service: 'lhr-jobs', ok: true});
      const authorization = req.headers.authorization || '';
      const fingerprint = createHash('sha256').update(authorization).digest('hex');
      if (!(authCache.has(fingerprint) && authCache.get(fingerprint) > now())) {
        if (now() - authWindow >= 60000) {authWindow = now(); authFailures = 0;}
        if (authFailures >= 5) return respond(res, 429, {error: 'Too many sign-in attempts; retry in a minute'}, {'Retry-After': '60'});
        if (!authenticate(authorization, config)) {
          authFailures++;
          return respond(res, 401, {error: 'Sign in with your dashboard credentials'}, {'WWW-Authenticate': 'Basic realm="LHR Jobs", charset="UTF-8"'});
        }
        if (authCache.size >= 32) authCache.clear();
        authCache.set(fingerprint, now() + 300000);
      }
      if (req.method === 'POST') {
        if (req.headers['x-lhr-request'] !== 'dashboard' || !req.headers['content-type']?.startsWith('application/json') || req.headers['sec-fetch-site'] === 'cross-site' || (req.headers.origin && req.headers.origin !== config.publicOrigin)) {
          return respond(res, 403, {error: 'Requests must come from the dashboard'});
        }
      }
      if (req.method === 'GET' && staticFiles.has(url.pathname)) {
        const [file, type] = staticFiles.get(url.pathname);
        return respond(res, 200, await readFile(path.join(staticRoot, file)), {'Content-Type': type + '; charset=utf-8'});
      }
      if (req.method === 'GET' && url.pathname === '/api/state') return respond(res, 200, await state());
      if (req.method === 'POST' && url.pathname === '/api/runs') {
        const job = validateJob(await body(req));
        const result = await rpc({op: 'submit', job});
        checkedAt = 0;
        return respond(res, 201, result);
      }
      if (req.method === 'POST' && url.pathname === '/api/pause') {
        const input = await body(req);
        if (Object.keys(input).length !== 1 || typeof input.paused !== 'boolean') throw new Error('paused must be true or false');
        const result = await rpc({op: 'pause', paused: input.paused});
        checkedAt = 0;
        return respond(res, 200, result);
      }
      const match = url.pathname.match(/^\/api\/runs\/([a-f0-9]{32})\/(cancel|logs|results)$/);
      if (match) {
        const [, id, action] = match;
        if (action === 'cancel' && req.method === 'POST') {
          await body(req);
          const result = await rpc({op: 'cancel', id});
          checkedAt = 0;
          return respond(res, 200, result);
        }
        if (action === 'logs' && req.method === 'GET') {
          const cached = logCache.get(id);
          if (cached && now() - cached.at < 2500) return respond(res, 200, await cached.value);
          if (logCache.size >= 32) logCache.clear();
          const value = rpc({op: 'logs', id});
          logCache.set(id, {at: now(), value});
          try {return respond(res, 200, await value);} catch (error) {logCache.delete(id); throw error;}
        }
        if (action === 'results' && req.method === 'GET') {
          if (downloading) return respond(res, 429, {error: 'Another result download is in progress; retry shortly'});
          downloading = true;
          try {
            const directory = path.join(config.dataDir, 'results');
            await mkdir(directory, {recursive: true});
            const filename = path.join(directory, id + '.tar.gz');
            let content;
            try {content = await readFile(filename);} catch {
              const result = await rpc({op: 'archive', id});
              content = Buffer.from(result.base64, 'base64');
              if (content.length > 11 * 1024 * 1024) throw new Error('Result archive is too large');
              const temporary = filename + '.' + randomBytes(4).toString('hex');
              await writeFile(temporary, content, {mode: 0o600});
              await rename(temporary, filename);
              // A single download owns pruning; bound both file count and SSD bytes.
              const files = [];
              for (const name of (await readdir(directory)).filter(name => /^[a-f0-9]{32}\.tar\.gz$/.test(name)).sort()) {
                try {files.push({name, bytes: (await stat(path.join(directory, name))).size});}
                catch (error) {if (error.code !== 'ENOENT') throw error;}
              }
              let total = files.reduce((sum, file) => sum + file.bytes, 0), remaining = files.length;
              for (const file of files) {
                if (remaining <= 200 && total <= 128 * 1024 * 1024) break;
                if (file.name === id + '.tar.gz') continue;
                try {await unlink(path.join(directory, file.name));}
                catch (error) {if (error.code !== 'ENOENT') throw error;}
                remaining--; total -= file.bytes;
              }
            }
            return respond(res, 200, content, {'Content-Type': 'application/gzip', 'Content-Disposition': `attachment; filename="lhr-${id}.tar.gz"`});
          } finally {downloading = false;}
        }
      }
      return respond(res, 404, {error: 'Not found'});
    } catch (error) {
      const unavailable = /connection|respond|unavailable/i.test(error.message);
      return respond(res, unavailable ? 503 : 400, {error: error.message.slice(0, 1000)});
    }
  };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const config = JSON.parse(await readFile(process.env.LHR_JOBS_CONFIG, 'utf8'));
  for (const name of ['username', 'passwordSalt', 'passwordHash', 'publicOrigin', 'sshHost', 'sshKey', 'knownHosts', 'dataDir']) {
    if (!config[name]) throw new Error('Missing setting: ' + name);
  }
  const server = http.createServer(createApplication({config}));
  server.requestTimeout = 45000;
  server.headersTimeout = 10000;
  server.maxHeadersCount = 40;
  server.listen(config.port || 8091, '127.0.0.1', () => console.log('LHR Jobs listening on loopback:' + (config.port || 8091)));
}
