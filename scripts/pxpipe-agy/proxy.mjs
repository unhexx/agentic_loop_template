import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

export const PINNED_UPSTREAM = 'daily-cloudcode-pa.googleapis.com';
export const DEFAULT_MAX_BODY = 8 * 1024 * 1024;

function resolveGoogleJs() {
  if (process.env.PXPIPE_GOOGLE_JS && fs.existsSync(process.env.PXPIPE_GOOGLE_JS)) {
    return process.env.PXPIPE_GOOGLE_JS;
  }
  const guesses = [];
  try {
    const bin = execFileSync('/usr/bin/env', ['bash', '-lc', 'command -v pxpipe'], {
      encoding: 'utf8',
    }).trim();
    if (bin) {
      guesses.push(path.resolve(path.dirname(fs.realpathSync(bin)), '..', 'dist', 'core', 'google.js'));
    }
  } catch {
    // user systemd PATH often omits ~/.local/bin; the home guess below covers that.
  }
  guesses.push(path.join(os.homedir(), '.local/lib/node_modules/pxpipe-proxy/dist/core/google.js'));
  for (const file of guesses) {
    if (fs.existsSync(file)) return file;
  }
  throw new Error('pxpipe dist/core/google.js not found; set PXPIPE_GOOGLE_JS');
}

let transformGoogleGenerateContent = null;

async function loadTransform() {
  if (transformGoogleGenerateContent) return transformGoogleGenerateContent;
  const mod = await import(pathToFileURL(resolveGoogleJs()).href);
  transformGoogleGenerateContent = mod.transformGoogleGenerateContent;
  return transformGoogleGenerateContent;
}

export function maxBodyBytes(raw = process.env.PXPIPE_MAX_BODY) {
  if (raw == null || String(raw).trim() === '') return DEFAULT_MAX_BODY;
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_MAX_BODY;
  return Math.floor(n);
}

export function assertLoopbackBind(host) {
  let h = String(host || '').trim().toLowerCase();
  if (h.startsWith('[') && h.endsWith(']')) h = h.slice(1, -1);
  if (h.startsWith('::ffff:')) h = h.slice('::ffff:'.length);
  if (h === 'localhost' || h === '::1') return;
  const m = /^127\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(h);
  if (m && m.slice(1).every((part) => Number(part) <= 255)) return;
  throw new Error(`refusing non-loopback bind: ${host}`);
}

function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    const declared = req.headers['content-length'];
    if (declared != null && declared !== '') {
      const n = Number(declared);
      if (!Number.isFinite(n) || n < 0) {
        reject(Object.assign(new Error('bad content-length'), { status: 400 }));
        return;
      }
      if (n > limit) {
        reject(Object.assign(new Error('body too large'), { status: 413 }));
        return;
      }
    }
    const chunks = [];
    let received = 0;
    let settled = false;
    const fail = (err) => {
      if (settled) return;
      settled = true;
      reject(err);
    };
    req.on('data', (chunk) => {
      received += chunk.length;
      if (received > limit) {
        req.destroy();
        fail(Object.assign(new Error('body too large'), { status: 413 }));
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (settled) return;
      settled = true;
      resolve(Buffer.concat(chunks));
    });
    req.on('error', (err) => fail(err));
  });
}

function sendJson(res, status, payload) {
  if (res.headersSent || res.writableEnded) return;
  const raw = JSON.stringify(payload);
  res.writeHead(status, {
    'content-type': 'application/json',
    'content-length': Buffer.byteLength(raw),
  });
  res.end(raw);
}

export function createProxyServer(options = {}) {
  const httpsRequest = options.httpsRequest || https.request;
  const limit = options.maxBody ?? maxBodyBytes();
  const logFile = options.logFile || process.env.PXPIPE_LOG || path.join(os.homedir(), '.pxpipe-agy', 'events.jsonl');
  const transformFn = options.transform || null;
  const port = options.port ?? parseInt(process.env.SHIM_IN_PORT || process.env.PORT || '8101', 10);
  const stats = options.stats || {
    requests: 0,
    compressed: 0,
    skipped: 0,
    errors: 0,
    totalSavedTokens: 0,
    totalBaselineTokens: 0,
    totalImageTokens: 0,
  };

  function logEvent(entry) {
    try {
      fs.mkdirSync(path.dirname(logFile), { recursive: true });
      fs.appendFileSync(logFile, JSON.stringify(entry) + '\n');
    } catch (err) {
      console.error('[pxpipe-agy] Error writing to events.jsonl:', err);
    }
  }

  const server = http.createServer(async (req, res) => {
    stats.requests += 1;
    if (req.method === 'GET' && (req.url === '/health' || req.url === '/')) {
      sendJson(res, 200, {
        ok: true,
        service: 'pxpipe-agy-proxy',
        port,
        upstream: PINNED_UPSTREAM,
        stats,
        uptimeSec: process.uptime(),
      });
      return;
    }

    let rawBody;
    try {
      rawBody = await readBody(req, limit);
    } catch (err) {
      const status = err.status || 400;
      req.resume();
      sendJson(res, status, { error: { message: err.message } });
      return;
    }

    const reqPath = req.url || '/';
    const upstreamHost = PINNED_UPSTREAM;
    let bodyToSend = rawBody;
    let forwardMeta = null;

    if (req.method === 'POST' && (reqPath.includes('streamGenerateContent') || reqPath.includes('generateContent'))) {
      try {
        const text = rawBody.toString('utf8');
        const parsed = JSON.parse(text);
        if (parsed && parsed.request) {
          const modelId = parsed.model || 'gemini-3.8-flash-high';
          const innerBytes = Buffer.from(JSON.stringify(parsed.request));
          const transformModel = 'gemini-3.7-flash';
          const tTransform0 = Date.now();
          const run = transformFn || (await loadTransform());
          const r = await run(innerBytes, transformModel);
          const transformMs = Date.now() - tTransform0;
          if (r.info && r.info.compressed) {
            parsed.request = JSON.parse(new TextDecoder().decode(r.body));
            bodyToSend = Buffer.from(JSON.stringify(parsed));
            stats.compressed += 1;
            const savedTokens = (r.info.baselineImagedTokens || 0) - (r.info.imageTokens || 0);
            stats.totalSavedTokens += savedTokens;
            stats.totalBaselineTokens += (r.info.baselineImagedTokens || 0);
            stats.totalImageTokens += (r.info.imageTokens || 0);
            forwardMeta = {
              model: modelId,
              transform_ms: transformMs,
              accounting_provider: 'google',
              compressed: true,
              orig_chars: r.info.origChars || 0,
              compressed_chars: r.info.compressedChars || 0,
              image_count: r.info.imageCount || 0,
              image_bytes: r.info.imageBytes || 0,
              image_tokens: r.info.imageTokens || 0,
              baseline_imaged_tokens: r.info.baselineImagedTokens || 0,
              saved_tokens: savedTokens,
              static_chars: r.info.staticChars || 0,
              bucket_chars: r.info.bucketChars || {},
              history_reason: r.info.historyReason || 'ok',
            };
          } else {
            stats.skipped += 1;
            forwardMeta = {
              model: modelId,
              compressed: false,
              history_reason: r.info?.reason || 'not_profitable',
            };
          }
        }
      } catch (err) {
        stats.errors += 1;
        console.error('[pxpipe-agy] Error in request transformation (falling back to uncompressed):', err);
        bodyToSend = rawBody;
      }
    }

    const outHeaders = { ...req.headers };
    delete outHeaders.host;
    delete outHeaders.connection;
    delete outHeaders['transfer-encoding'];
    outHeaders.host = upstreamHost;
    outHeaders['content-length'] = String(bodyToSend.length);

    const started = Date.now();
    const writeEvent = (status, extra) => {
      logEvent({
        ts: new Date().toISOString(),
        method: req.method,
        path: reqPath,
        status,
        duration_ms: forwardMeta?.transform_ms ?? (Date.now() - started),
        compressed: Boolean(forwardMeta?.compressed),
        upstream: upstreamHost,
        ...(forwardMeta || {}),
        ...(extra || {}),
      });
    };

    const upstreamReq = httpsRequest({
      hostname: upstreamHost,
      port: 443,
      path: reqPath,
      method: req.method,
      headers: outHeaders,
    }, (upstreamRes) => {
      const status = upstreamRes.statusCode || 500;
      writeEvent(status);
      res.writeHead(status, upstreamRes.headers);
      upstreamRes.pipe(res);
    });

    upstreamReq.on('error', (err) => {
      console.error('[pxpipe-agy] Upstream request error:', err);
      writeEvent(502, { error: `upstream_error: ${err.message}` });
      sendJson(res, 502, { error: { message: `pxpipe-agy upstream error: ${err.message}` } });
    });

    upstreamReq.write(bodyToSend);
    upstreamReq.end();
  });

  server.pxpipeStats = stats;
  return server;
}

export function listenProxy(host = process.env.HOST || '127.0.0.1') {
  assertLoopbackBind(host);
  const port = parseInt(process.env.SHIM_IN_PORT || process.env.PORT || '8101', 10);
  const server = createProxyServer({ port });
  server.listen(port, host, () => {
    console.error(`[pxpipe-agy] Proxy listening on http://${host}:${port}`);
    console.error(`[pxpipe-agy] Forwarding upstream to https://${PINNED_UPSTREAM}`);
    console.error(`[pxpipe-agy] Logging events to ${process.env.PXPIPE_LOG || path.join(os.homedir(), '.pxpipe-agy', 'events.jsonl')}`);
  });
  return server;
}

const invokedDirectly = process.argv[1]
  && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;

if (invokedDirectly) {
  try {
    listenProxy();
  } catch (err) {
    console.error(`[pxpipe-agy] ${err.message}`);
    process.exit(1);
  }
}
