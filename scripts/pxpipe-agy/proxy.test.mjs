import assert from 'node:assert/strict';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { mkdtempSync } from 'node:fs';
import test from 'node:test';
import { once } from 'node:events';

import {
  PINNED_UPSTREAM,
  assertLoopbackBind,
  createProxyServer,
} from './proxy.mjs';

function fakeHttps() {
  const calls = [];
  function request(opts, cb) {
    calls.push(opts);
    const res = new (class {
      statusCode = 200;
      headers = { 'content-type': 'application/json' };
      pipe(dest) {
        dest.end('{"ok":true}');
        return dest;
      }
    })();
    queueMicrotask(() => cb(res));
    return {
      on() { return this; },
      write() {},
      end() {},
    };
  }
  return { request, calls };
}

async function withServer(httpsRequest, fn, extra = {}) {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'pxpipe-agy-'));
  const server = createProxyServer({
    httpsRequest,
    logFile: path.join(dir, 'events.jsonl'),
    maxBody: extra.maxBody ?? 64,
    port: 0,
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try {
    await fn(server.address().port);
  } finally {
    server.close();
  }
}

function request({ port, method = 'POST', path: reqPath = '/v1internal:generateContent', host = 'evil.example', body = '{}', headers = {} }) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: '127.0.0.1',
      port,
      path: reqPath,
      method,
      headers: { host, 'content-length': Buffer.byteLength(body), ...headers },
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString('utf8') }));
    });
    req.on('error', reject);
    req.end(body);
  });
}

test('bind refuses non-loopback', () => {
  assert.throws(() => assertLoopbackBind('0.0.0.0'), /non-loopback/);
  assert.throws(() => assertLoopbackBind('10.1.2.3'), /non-loopback/);
  assert.doesNotThrow(() => assertLoopbackBind('127.0.0.1'));
  assert.doesNotThrow(() => assertLoopbackBind('::1'));
  assert.doesNotThrow(() => assertLoopbackBind('localhost'));
});

test('Host header does not choose upstream', async () => {
  const fake = fakeHttps();
  await withServer(fake.request, async (port) => {
    const res = await request({ port, host: 'attacker.example', body: '{}' });
    assert.equal(res.status, 200);
    assert.equal(fake.calls.length, 1);
    assert.equal(fake.calls[0].hostname, PINNED_UPSTREAM);
    assert.equal(fake.calls[0].headers.host, PINNED_UPSTREAM);
    assert.equal(PINNED_UPSTREAM, 'daily-cloudcode-pa.googleapis.com');
  });
});

test('body over the cap is 413 and is not dialed', async () => {
  const fake = fakeHttps();
  await withServer(fake.request, async (port) => {
    const res = await request({ port, body: 'x'.repeat(80) });
    assert.equal(res.status, 413);
    assert.equal(fake.calls.length, 0);
  }, { maxBody: 64 });
});
