import http from "node:http";
import { randomUUID, randomBytes } from "node:crypto";
import { spawn } from "node:child_process";

const PUBLIC_PORT = Number(process.env.PORT || 10000);
const MCP_PORT = 18770;
const TUNNEL_HEALTH_PORT = 18771;
const FIXED_TUNNEL_ID = process.env.FIXED_TUNNEL_ID || "";
const EDGE_BENCH_TOKEN = process.env.EDGE_BENCH_TOKEN || "";
const MAX_BODY = 6 * 1024 * 1024;
const LINK_STALE_MS = 35_000;
const WORK_TIMEOUT_MS = 35_000;

let runtimeKey = null;
let linkToken = null;
let initResult = null;
let toolsListResult = null;
let lastLinkAt = 0;
let tunnelChild = null;

const workQueue = [];
const workPending = new Map();
const pollWaiters = [];

function json(res, status, body, headers = {}) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(payload),
    ...headers,
  });
  res.end(payload);
}

function empty(res, status = 204) {
  res.writeHead(status);
  res.end();
}

async function readJson(req) {
  let size = 0;
  const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY) throw new Error("body_too_large");
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function authorizedLink(req) {
  if (!linkToken) return false;
  const value = req.headers.authorization || "";
  return value === `Bearer ${linkToken}`;
}

function linkFresh() {
  return Boolean(linkToken && Date.now() - lastLinkAt < LINK_STALE_MS);
}

function killTunnel() {
  if (tunnelChild && !tunnelChild.killed) {
    try { tunnelChild.kill("SIGTERM"); } catch {}
  }
  tunnelChild = null;
}

function clearLink(reason = "link_closed") {
  killTunnel();
  linkToken = null;
  runtimeKey = null;
  initResult = null;
  toolsListResult = null;
  lastLinkAt = 0;

  while (pollWaiters.length) {
    const waiter = pollWaiters.shift();
    clearTimeout(waiter.timer);
    try { empty(waiter.res, 401); } catch {}
  }

  while (workQueue.length) {
    const item = workQueue.shift();
    const pending = workPending.get(item.id);
    if (pending) {
      clearTimeout(pending.timer);
      pending.reject(new Error(reason));
      workPending.delete(item.id);
    }
  }
  for (const [id, pending] of workPending) {
    clearTimeout(pending.timer);
    pending.reject(new Error(reason));
    workPending.delete(id);
  }
}

function dispatchWork() {
  while (workQueue.length && pollWaiters.length) {
    const item = workQueue.shift();
    const waiter = pollWaiters.shift();
    clearTimeout(waiter.timer);
    lastLinkAt = Date.now();
    json(waiter.res, 200, item);
  }
}

function enqueueWork(method, params) {
  if (!linkFresh()) return Promise.reject(new Error("pc_link_unavailable"));
  const id = randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      workPending.delete(id);
      reject(new Error("pc_work_timeout"));
    }, WORK_TIMEOUT_MS);
    workPending.set(id, { resolve, reject, timer });
    workQueue.push({ id, method, params: params ?? {} });
    dispatchWork();
  });
}

async function validateRuntimeKey(tunnelId, key) {
  if (!tunnelId || tunnelId !== FIXED_TUNNEL_ID || !key) return false;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8_000);
  try {
    const response = await fetch(
      `https://api.openai.com/v1/tunnels/${encodeURIComponent(tunnelId)}`,
      {
        method: "GET",
        headers: {
          authorization: `Bearer ${key}`,
          accept: "application/json",
          "user-agent": "mcp-edge-relay-bootstrap/1.0",
          "x-tunnel-client-name": "mcp-edge-relay-bootstrap",
          "x-tunnel-client-version": "1.0.0",
        },
        signal: controller.signal,
      },
    );
    return response.status === 200;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function waitTunnelReady(timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${TUNNEL_HEALTH_PORT}/readyz`, {
        signal: AbortSignal.timeout(800),
      });
      if (r.ok) return true;
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

async function startTunnelClient(tunnelId, key) {
  killTunnel();
  const env = {
    ...process.env,
    CONTROL_PLANE_TUNNEL_ID: tunnelId,
    CONTROL_PLANE_API_KEY: key,
    MCP_SERVER_URL: `channel=main,url=http://127.0.0.1:${MCP_PORT}/mcp`,
    MCP_STARTUP_WAIT_TIMEOUT: "5s",
    MCP_CONNECTION_MAX_TTL: "24h",
    CONTROL_PLANE_POLL_CHANNELS: "main",
    CONTROL_PLANE_MAX_INFLIGHT_REQUESTS: "25",
    MCP_MAX_CONCURRENT_REQUESTS: "25",
    LOG_LEVEL: "warn",
    LOG_FILE: "/dev/null",
    ADMIN_UI_LOG_BUFFER_EVENTS: "1",
    HEALTH_LISTEN_ADDR: `127.0.0.1:${TUNNEL_HEALTH_PORT}`,
    HEALTH_URL_FILE: "/tmp/tunnel-health.url",
  };

  const child = spawn("/usr/local/bin/tunnel-client", ["run"], {
    env,
    stdio: "ignore",
  });
  tunnelChild = child;
  child.once("exit", () => {
    if (tunnelChild === child) tunnelChild = null;
  });

  if (!(await waitTunnelReady())) {
    killTunnel();
    throw new Error("tunnel_start_timeout");
  }
}

const mcpServer = http.createServer(async (req, res) => {
  if (req.url !== "/mcp") return empty(res, 404);
  if (req.method === "GET" || req.method === "DELETE") return empty(res, 405);
  if (req.method !== "POST") return empty(res, 405);

  let body;
  try { body = await readJson(req); }
  catch { return json(res, 400, { error: "invalid_json" }); }

  if (!body || Array.isArray(body) || body.jsonrpc !== "2.0") {
    return json(res, 400, { error: "invalid_jsonrpc" });
  }

  const method = body.method;
  const hasId = Object.prototype.hasOwnProperty.call(body, "id");

  if (method === "notifications/initialized") return empty(res, 202);
  if (!hasId) return empty(res, 202);

  if (method === "initialize") {
    if (!initResult) {
      return json(res, 200, {
        jsonrpc: "2.0",
        id: body.id,
        error: { code: -32000, message: "PC link not ready" },
      });
    }
    return json(
      res,
      200,
      { jsonrpc: "2.0", id: body.id, result: initResult },
      { "mcp-session-id": `edge-${randomUUID()}` },
    );
  }

  if (method === "tools/list" && toolsListResult) {
    return json(res, 200, {
      jsonrpc: "2.0",
      id: body.id,
      result: toolsListResult,
    });
  }

  if (method === "ping") {
    return json(res, 200, { jsonrpc: "2.0", id: body.id, result: {} });
  }

  try {
    const reply = await enqueueWork(method, body.params ?? {});
    if (reply?.error) {
      return json(res, 200, { jsonrpc: "2.0", id: body.id, error: reply.error });
    }
    return json(res, 200, { jsonrpc: "2.0", id: body.id, result: reply?.result ?? {} });
  } catch (error) {
    return json(res, 200, {
      jsonrpc: "2.0",
      id: body.id,
      error: { code: -32603, message: String(error?.message || error) },
    });
  }
});

mcpServer.listen(MCP_PORT, "127.0.0.1");

function sanitizeMetadata(value, depth = 0) {
  if (depth > 4) return "[max_depth]";
  if (Array.isArray(value)) return value.slice(0, 32).map((v) => sanitizeMetadata(v, depth + 1));
  if (value && typeof value === "object") {
    const out = {};
    for (const [key, val] of Object.entries(value)) {
      if (/key|token|secret|credential|authorization|cookie/i.test(key)) continue;
      out[key] = sanitizeMetadata(val, depth + 1);
    }
    return out;
  }
  if (typeof value === "string" && value.length > 512) return value.slice(0, 512) + "…";
  return value;
}

function summarizeTunnelMetrics(text) {
  const methods = {};
  let responsePost = { sum_ms: 0, count: 0 };
  let commandAge = { sum_ms: 0, count: 0 };

  for (const line of text.split("\n")) {
    let m = line.match(/^command_end_to_end_latency_milliseconds_(sum|count)\{([^}]*)\}\s+([0-9.eE+-]+)$/);
    if (m) {
      const kind = m[1];
      const labels = m[2];
      const value = Number(m[3]);
      const method = labels.match(/request_method="([^"]+)"/)?.[1] || "unknown";
      const latencyType = labels.match(/latency_type="([^"]+)"/)?.[1] || "unknown";
      const key = method + ":" + latencyType;
      methods[key] ||= { method, latency_type: latencyType, sum_ms: 0, count: 0 };
      if (kind === "sum") methods[key].sum_ms = value;
      else methods[key].count = value;
      continue;
    }

    m = line.match(/^commands_age_seconds_(sum|count)\{[^}]*\}\s+([0-9.eE+-]+)$/);
    if (m) {
      if (m[1] === "sum") commandAge.sum_ms += Number(m[2]) * 1000;
      else commandAge.count += Number(m[2]);
      continue;
    }

    if (line.startsWith("http_client_request_duration_seconds_") && line.includes("/response")) {
      m = line.match(/^http_client_request_duration_seconds_(sum|count)\{[^}]*\}\s+([0-9.eE+-]+)$/);
      if (m) {
        if (m[1] === "sum") responsePost.sum_ms += Number(m[2]) * 1000;
        else responsePost.count += Number(m[2]);
      }
    }
  }

  const methodRows = Object.values(methods).map((row) => ({
    ...row,
    avg_ms: row.count ? row.sum_ms / row.count : 0,
  }));

  return {
    methods: methodRows,
    response_post: {
      ...responsePost,
      avg_ms: responsePost.count ? responsePost.sum_ms / responsePost.count : 0,
    },
    command_age: {
      ...commandAge,
      avg_ms: commandAge.count ? commandAge.sum_ms / commandAge.count : 0,
    },
  };
}

const publicServer = http.createServer(async (req, res) => {
  const url = new URL(req.url || "/", "http://localhost");

  if (req.method === "GET" && url.pathname === "/health") {
    return json(res, 200, {
      status: "ok",
      bootstrapped: Boolean(linkToken),
      link_fresh: linkFresh(),
      tunnel_client_running: Boolean(tunnelChild && !tunnelChild.killed),
      queued_work: workQueue.length,
      pending_work: workPending.size,
    });
  }

  if (req.method === "GET" && url.pathname === "/diag") {
    try {
      const [metricsResponse, mcpResponse, readyResponse] = await Promise.all([
        fetch(`http://127.0.0.1:${TUNNEL_HEALTH_PORT}/metrics`, { signal: AbortSignal.timeout(1500) }),
        fetch(`http://127.0.0.1:${TUNNEL_HEALTH_PORT}/health/mcp`, { signal: AbortSignal.timeout(1500) }),
        fetch(`http://127.0.0.1:${TUNNEL_HEALTH_PORT}/readyz`, { signal: AbortSignal.timeout(1500) }),
      ]);
      if (!metricsResponse.ok) return json(res, 503, { error: "metrics_unavailable" });
      const body = await metricsResponse.text();
      const summary = summarizeTunnelMetrics(body);
      const polls = body.split("\n").filter((line) =>
        /poll|command|control_plane/i.test(line) &&
        /_(count|total)\{/.test(line)
      ).slice(0, 40);
      let mcp = null;
      try { mcp = await mcpResponse.json(); } catch { mcp = { status: mcpResponse.status }; }
      let tunnelMetadata = null;
      try {
        if (runtimeKey && FIXED_TUNNEL_ID) {
          const metaResponse = await fetch(
            `https://api.openai.com/v1/tunnels/${encodeURIComponent(FIXED_TUNNEL_ID)}`,
            {
              headers: {
                authorization: `Bearer ${runtimeKey}`,
                accept: "application/json",
                "user-agent": "mcp-edge-relay-diag/1.0",
                "x-tunnel-client-name": "mcp-edge-relay-diag",
                "x-tunnel-client-version": "1.0.0",
              },
              signal: AbortSignal.timeout(2500),
            },
          );
          if (metaResponse.ok) {
            tunnelMetadata = sanitizeMetadata(await metaResponse.json());
          } else {
            tunnelMetadata = { status: metaResponse.status };
          }
        }
      } catch {
        tunnelMetadata = { error: "metadata_unavailable" };
      }

      return json(res, 200, {
        ...summary,
        ready: readyResponse.ok,
        mcp,
        poll_metrics: polls,
        tunnel_metadata: tunnelMetadata,
      });
    } catch {
      return json(res, 503, { error: "metrics_unavailable" });
    }
  }

  if (req.method === "POST" && url.pathname === "/bench/get-config") {
    const auth = req.headers.authorization || "";
    if (!EDGE_BENCH_TOKEN || auth !== `Bearer ${EDGE_BENCH_TOKEN}`) return empty(res, 401);
    if (!linkFresh()) return json(res, 503, { ok: false, error: "pc_link_unavailable" });

    const started = performance.now();
    try {
      const reply = await enqueueWork("tools/call", { name: "get_config", arguments: {} });
      const elapsedMs = performance.now() - started;
      return json(res, 200, {
        ok: !reply?.error,
        elapsed_ms: Math.round(elapsedMs * 10) / 10,
        error_code: reply?.error?.code ?? null,
      });
    } catch (error) {
      return json(res, 503, {
        ok: false,
        elapsed_ms: Math.round((performance.now() - started) * 10) / 10,
        error: String(error?.message || error),
      });
    }
  }

  if (req.method === "POST" && url.pathname === "/bootstrap") {
    let body;
    try { body = await readJson(req); }
    catch { return json(res, 400, { error: "invalid_json" }); }

    if (
      typeof body.runtimeKey !== "string" ||
      typeof body.tunnelId !== "string" ||
      !body.initializeResult ||
      !body.toolsListResult
    ) {
      return json(res, 400, { error: "missing_bootstrap_fields" });
    }

    if (!(await validateRuntimeKey(body.tunnelId, body.runtimeKey))) {
      return json(res, 403, { error: "invalid_tunnel_credentials" });
    }

    clearLink("rebootstrap");
    runtimeKey = body.runtimeKey;
    initResult = body.initializeResult;
    toolsListResult = body.toolsListResult;
    linkToken = randomBytes(32).toString("base64url");
    lastLinkAt = Date.now();

    try {
      await startTunnelClient(body.tunnelId, runtimeKey);
    } catch {
      clearLink("tunnel_start_failed");
      return json(res, 503, { error: "tunnel_start_failed" });
    }

    return json(res, 200, {
      ok: true,
      linkToken,
      tools: Array.isArray(toolsListResult?.tools) ? toolsListResult.tools.length : 0,
    });
  }

  if (req.method === "POST" && url.pathname === "/link/poll") {
    if (!authorizedLink(req)) return empty(res, 401);
    lastLinkAt = Date.now();

    if (workQueue.length) {
      const item = workQueue.shift();
      return json(res, 200, item);
    }

    const waiter = { res, timer: null };
    waiter.timer = setTimeout(() => {
      const index = pollWaiters.indexOf(waiter);
      if (index >= 0) pollWaiters.splice(index, 1);
      lastLinkAt = Date.now();
      empty(res, 204);
    }, 15_000);
    pollWaiters.push(waiter);
    return;
  }

  if (req.method === "POST" && url.pathname === "/link/response") {
    if (!authorizedLink(req)) return empty(res, 401);
    lastLinkAt = Date.now();
    let body;
    try { body = await readJson(req); }
    catch { return json(res, 400, { error: "invalid_json" }); }

    const pending = workPending.get(body.id);
    if (!pending) return empty(res, 404);
    workPending.delete(body.id);
    clearTimeout(pending.timer);
    pending.resolve({ result: body.result, error: body.error });
    return empty(res, 204);
  }

  if (req.method === "POST" && url.pathname === "/link/disconnect") {
    if (!authorizedLink(req)) return empty(res, 401);
    clearLink("pc_disconnect");
    return empty(res, 204);
  }

  return empty(res, 404);
});

setInterval(() => {
  if (linkToken && Date.now() - lastLinkAt > LINK_STALE_MS) {
    clearLink("pc_link_stale");
  }
}, 5_000).unref();

function shutdown() {
  clearLink("edge_shutdown");
  publicServer.close();
  mcpServer.close();
}
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

publicServer.listen(PUBLIC_PORT, "0.0.0.0");
