module.exports = {
  apps: [{
    name: 'lhr-jobs',
    script: process.execPath,
    args: ['--max-old-space-size=128', '--max-semi-space-size=8', './server.mjs'],
    interpreter: 'none',
    exec_mode: 'fork',
    cwd: '/mnt/server_ssd/lhr-jobs/app',
    instances: 1,
    autorestart: true,
    max_memory_restart: '256M',
    max_restarts: 10,
    min_uptime: '10s',
    time: true,
    env: {NODE_ENV: 'production', LHR_JOBS_CONFIG: process.env.LHR_JOBS_CONFIG || '/home/electric/.config/lhr-jobs/dashboard.json'},
  }],
};
