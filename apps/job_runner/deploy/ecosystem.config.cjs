module.exports = {
  apps: [{
    name: 'lhr-jobs',
    script: './server.mjs',
    cwd: '/mnt/server_ssd/lhr-jobs/app',
    node_args: '--max-old-space-size=128',
    instances: 1,
    autorestart: true,
    max_memory_restart: '256M',
    max_restarts: 10,
    min_uptime: '10s',
    time: true,
    env: {NODE_ENV: 'production', LHR_JOBS_CONFIG: '/home/electric/.config/lhr-jobs/dashboard.json'},
  }],
};
