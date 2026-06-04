#!/usr/bin/env node

function clearProxyEnvironment() {
  if (process.env.QUANTBASE_ORBIT_USE_SYSTEM_PROXY === '1') {
    return;
  }
  for (const key of ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']) {
    delete process.env[key];
  }
  process.env.NO_PROXY = '*';
  process.env.no_proxy = '*';
}

const browserArgs = ['--no-proxy-server', '--proxy-server=direct://'];

function readStdin() {
  return new Promise((resolve) => {
    let body = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => {
      body += chunk;
    });
    process.stdin.on('end', () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (error) {
        resolve({ action: 'invalid', error: String(error && error.message ? error.message : error) });
      }
    });
  });
}

function responseFor(payload) {
  const action = payload && payload.action ? String(payload.action) : 'status';
  const message = 'OKX Orbit browser publishing is not configured in this community checkout.';
  return {
    status: 'not_configured',
    available: false,
    logged_in: false,
    action,
    browser_args: browserArgs,
    error: message,
  };
}

async function main() {
  clearProxyEnvironment();
  const payload = await readStdin();
  process.stdout.write(`${JSON.stringify(responseFor(payload))}\n`);
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({
    status: 'failed',
    available: false,
    error: String(error && error.message ? error.message : error),
  }));
  process.exitCode = 1;
});
