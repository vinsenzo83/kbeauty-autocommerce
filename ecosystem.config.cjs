module.exports = {
  apps: [
    {
      name: 'kbeauty-api',
      interpreter: '/home/user/kbeauty-autocommerce/.venv/bin/python',
      script: '/home/user/kbeauty-autocommerce/.venv/bin/uvicorn',
      args: 'app.main:app --host 0.0.0.0 --port 8000 --log-level info',
      cwd: '/home/user/kbeauty-autocommerce',
      env: {
        PYTHONPATH: '/home/user/kbeauty-autocommerce',
      },
      watch: false,
      instances: 1,
      exec_mode: 'fork',
    },
    {
      name: 'kbeauty-worker',
      interpreter: '/home/user/kbeauty-autocommerce/.venv/bin/python',
      script: '/home/user/kbeauty-autocommerce/.venv/bin/celery',
      args: '-A app.workers.celery_app:celery_app worker --loglevel=info --concurrency=2 -Q celery',
      cwd: '/home/user/kbeauty-autocommerce',
      env: {
        PYTHONPATH: '/home/user/kbeauty-autocommerce',
      },
      watch: false,
      instances: 1,
      exec_mode: 'fork',
    },
  ],
}
