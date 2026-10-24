import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

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

const { transformGoogleGenerateContent } = await import(pathToFileURL(resolveGoogleJs()).href);

const PORT = parseInt(process.env.SHIM_IN_PORT || process.env.PORT || '8101', 10);
const HOST = process.env.HOST || '127.0.0.1';
const DEFAULT_UPSTREAM = 'daily-cloudcode-pa.googleapis.com';
const LOG_FILE = process.env.PXPIPE_LOG || path.join(os.homedir(), '.pxpipe-agy', 'events.jsonl');

const stats = {
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
    fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
    fs.appendFileSync(LOG_FILE, JSON.stringify(entry) + '\n');
  } catch (err) {
    console.error('[pxpipe-agy] Error writing to events.jsonl:', err);
  }
}

const server = http.createServer(async (req, res) => {
  stats.requests += 1;
  const t0 = Date.now();

  // Health check
  if (req.method === 'GET' && (req.url === '/health' || req.url === '/')) {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({
      ok: true,
      service: 'pxpipe-agy-proxy',
      port: PORT,
      stats,
      uptimeSec: process.uptime()
    }, null, 2));
    return;
  }

  // Collect request body
  const chunks = [];
  req.on('data', chunk => chunks.push(chunk));
  req.on('end', async () => {
    const rawBody = Buffer.concat(chunks);
    const reqPath = req.url || '/';
    const upstreamHost = req.headers['host'] || DEFAULT_UPSTREAM;

    let bodyToSend = rawBody;
    let forwardMeta = null;

    if (req.method === 'POST' && (reqPath.includes('streamGenerateContent') || reqPath.includes('generateContent'))) {
      try {
        const text = rawBody.toString('utf8');
        const parsed = JSON.parse(text);

        // Check if CCPA wrapper is present: { project, requestId, model, request: { ... } }
        if (parsed && parsed.request) {
          const modelId = parsed.model || 'gemini-3.8-flash-high';
          const innerBytes = Buffer.from(JSON.stringify(parsed.request));

          // Resolve model profile
          const transformModel = (modelId.toLowerCase().includes('flash')) ? 'gemini-3.7-flash' : 'gemini-3.7-flash';
          const tTransform0 = Date.now();
          const r = await transformGoogleGenerateContent(innerBytes, transformModel);
          const transformMs = Date.now() - tTransform0;

          if (r.info && r.info.compressed) {
            parsed.request = JSON.parse(new TextDecoder().decode(r.body));
            bodyToSend = Buffer.from(JSON.stringify(parsed));

            stats.compressed += 1;
            const savedTokens = (r.info.baselineImagedTokens || 0) - (r.info.imageTokens || 0);
            stats.totalSavedTokens += savedTokens;
            stats.totalBaselineTokens += (r.info.baselineImagedTokens || 0);
            stats.totalImageTokens += (r.info.imageTokens || 0);

            console.error(`[pxpipe-agy] COMPRESSED ${modelId} in ${transformMs}ms:`);
            console.error(`  - Baseline tokens: ${r.info.baselineImagedTokens}`);
            console.error(`  - Imaged tokens:   ${r.info.imageTokens} (${r.info.imageCount} PNG images, ${r.info.imageBytes} bytes)`);
            console.error(`  - Tokens saved:    ${savedTokens} (~${Math.round((savedTokens / (r.info.baselineImagedTokens || 1)) * 100)}%)`);

            // Status is filled in when the upstream responds. Logging 200 here
            // recorded a success before Google had answered.
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
            console.error(`[pxpipe-agy] Skipped compression for ${modelId}: ${r.info?.reason || 'not_profitable'}`);
          }
        }
      } catch (err) {
        stats.errors += 1;
        console.error('[pxpipe-agy] Error in request transformation (falling back to uncompressed):', err);
        bodyToSend = rawBody;
      }
    }

    // Forward to upstream
    const outHeaders = { ...req.headers };
    delete outHeaders['host'];
    delete outHeaders['connection'];
    delete outHeaders['transfer-encoding'];
    outHeaders['host'] = upstreamHost;
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
        ...(forwardMeta || {}),
        ...(extra || {}),
      });
    };

    const upstreamReq = https.request({
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
      if (!res.headersSent) {
        res.writeHead(502, { 'content-type': 'application/json' });
      }
      res.end(JSON.stringify({ error: { message: `pxpipe-agy upstream error: ${err.message}` } }));
    });

    upstreamReq.write(bodyToSend);
    upstreamReq.end();
  });
});

server.listen(PORT, HOST, () => {
  console.error(`[pxpipe-agy] Proxy listening on http://${HOST}:${PORT}`);
  console.error(`[pxpipe-agy] Forwarding upstream to https://${DEFAULT_UPSTREAM}`);
  console.error(`[pxpipe-agy] Logging events to ${LOG_FILE}`);
});
