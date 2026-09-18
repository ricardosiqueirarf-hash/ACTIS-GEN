import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import qrcode from 'qrcode-terminal';
import makeWASocket, {
  DisconnectReason,
  getContentType,
  jidDecode,
  useMultiFileAuthState,
} from 'baileys';
import { DatabaseSync } from 'node:sqlite';

const HOST = process.env.ACTIS_BAILEYS_HOST || '127.0.0.1';
const PORT = Number(process.env.ACTIS_BAILEYS_PORT || 8789);
const ROOT = process.env.ACTIS_BAILEYS_ROOT
  || path.join(os.homedir(), '.local', 'share', 'actis-gen', 'whatsapp-baileys');
const AUTH_DIR = process.env.ACTIS_BAILEYS_AUTH_DIR || path.join(ROOT, 'auth');
const DB_PATH = process.env.ACTIS_BAILEYS_DB || path.join(ROOT, 'events.sqlite3');
const TOKEN_FILE = process.env.ACTIS_BAILEYS_TOKEN_FILE || path.join(ROOT, 'token');
const QR_FILE = process.env.ACTIS_BAILEYS_QR_FILE || path.join(ROOT, 'qr-ascii.txt');
const DATA_ROOT = path.resolve(process.env.ACTIS_DATA_ROOT || path.join(os.homedir(), '.local', 'share', 'actis-gen'));

for (const dir of [ROOT, AUTH_DIR]) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(dir, 0o700); } catch {}
}
if (!fs.existsSync(TOKEN_FILE)) {
  throw new Error(`ACTIS Baileys token missing: ${TOKEN_FILE}`);
}
const API_TOKEN = fs.readFileSync(TOKEN_FILE, 'utf8').trim();
if (!API_TOKEN) throw new Error('ACTIS Baileys token is empty');

const db = new DatabaseSync(DB_PATH);
db.exec(`
  PRAGMA journal_mode=WAL;
  CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    remote_jid TEXT NOT NULL,
    phone TEXT,
    peer_kind TEXT NOT NULL,
    direction TEXT NOT NULL,
    sender TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    message_type TEXT NOT NULL DEFAULT 'text',
    message_ts TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS deliveries (
    message_id TEXT PRIMARY KEY,
    client_id TEXT,
    remote_jid TEXT NOT NULL,
    status TEXT NOT NULL,
    status_code INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );
`);

let sock = null;
let connectionState = 'starting';
let qrValue = '';
let accountId = '';
let reconnectAttempt = 0;
let reconnectTimer = null;
let generation = 0;
let lastTransportActivityAt = null;
let lastMessageActivityAt = null;
let lastOpenAt = null;
let lastError = '';

const now = () => new Date().toISOString();
const silentLogger = {
  level: 'silent',
  child() { return this; },
  trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {},
};

function touchTransport() {
  lastTransportActivityAt = now();
}

function json(res, status, body) {
  const payload = Buffer.from(JSON.stringify(body));
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': payload.length,
    'cache-control': 'no-store',
  });
  res.end(payload);
}

function unauthorized(res) {
  json(res, 401, { ok: false, error: 'unauthorized' });
}

function authorized(req) {
  const value = String(req.headers.authorization || '');
  return value === `Bearer ${API_TOKEN}`;
}

async function readJson(req, limit = 2 * 1024 * 1024) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) throw new Error('request_too_large');
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function phoneFromJid(jid) {
  const value = String(jid || '');
  if (!value) return '';
  try {
    const decoded = jidDecode(value);
    const user = String(decoded?.user || '').replace(/\D/g, '');
    if (user && (decoded?.server === 's.whatsapp.net' || decoded?.server === 'c.us')) return user;
  } catch {}
  const [user, server] = value.split('@', 2);
  if (!['s.whatsapp.net', 'c.us'].includes(server)) return '';
  return String(user || '').replace(/\D/g, '');
}

function phoneFromKey(key = {}) {
  for (const jid of [
    key.remoteJidAlt,
    key.participantAlt,
    key.remoteJid,
    key.participant,
  ]) {
    const phone = phoneFromJid(jid);
    if (phone) return phone;
  }
  return '';
}

function peerKind(jid) {
  const value = String(jid || '');
  if (value.endsWith('@g.us')) return 'group';
  if (value.endsWith('@newsletter')) return 'channel';
  return 'direct';
}

function unwrapMessage(message) {
  let current = message || {};
  for (let i = 0; i < 4; i += 1) {
    if (current.ephemeralMessage?.message) current = current.ephemeralMessage.message;
    else if (current.viewOnceMessage?.message) current = current.viewOnceMessage.message;
    else if (current.viewOnceMessageV2?.message) current = current.viewOnceMessageV2.message;
    else break;
  }
  return current;
}

function extractContent(message) {
  const body = unwrapMessage(message);
  const type = getContentType(body) || 'unknown';
  if (type === 'conversation') return { content: String(body.conversation || ''), messageType: 'text' };
  if (type === 'extendedTextMessage') {
    return { content: String(body.extendedTextMessage?.text || ''), messageType: 'text' };
  }
  if (type === 'imageMessage') {
    return { content: String(body.imageMessage?.caption || ''), messageType: 'image' };
  }
  if (type === 'videoMessage') {
    return { content: String(body.videoMessage?.caption || ''), messageType: 'video' };
  }
  if (type === 'documentMessage') {
    return {
      content: String(body.documentMessage?.caption || body.documentMessage?.fileName || ''),
      messageType: 'document',
    };
  }
  if (type === 'audioMessage') return { content: '', messageType: 'audio' };
  return { content: '', messageType: type.replace(/Message$/, '') || 'unknown' };
}

function messageTimestamp(value) {
  if (value == null) return null;
  const number = typeof value === 'number' ? value : Number(value?.toString?.() || value);
  if (!Number.isFinite(number) || number <= 0) return null;
  return new Date(number * 1000).toISOString();
}

function storeMessage(message, upsertType) {
  const key = message?.key || {};
  const remoteJid = String(key.remoteJid || key.remoteJidAlt || '');
  if (!remoteJid || remoteJid.endsWith('@status') || remoteJid.endsWith('@broadcast')) return;
  if (peerKind(remoteJid) === 'channel') return;
  const messageId = String(key.id || '').trim();
  if (!messageId) return;
  const { content, messageType } = extractContent(message?.message);
  if (['protocol', 'senderKeyDistribution', 'reaction', 'unknown'].includes(messageType)) return;
  const direction = key.fromMe ? 'outgoing' : 'incoming';
  const sender = String(message?.pushName || '');
  const ts = messageTimestamp(message?.messageTimestamp);
  const raw = {
    upsert_type: upsertType,
    remote_jid_alt: key.remoteJidAlt || null,
    participant: key.participant || null,
    participant_alt: key.participantAlt || null,
  };
  db.prepare(`
    INSERT OR IGNORE INTO events
    (message_id,remote_jid,phone,peer_kind,direction,sender,content,message_type,message_ts,raw_json,created_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?)
  `).run(
    messageId,
    remoteJid,
    phoneFromKey(key) || null,
    peerKind(remoteJid),
    direction,
    sender,
    content,
    messageType,
    ts,
    JSON.stringify(raw),
    now(),
  );
  lastMessageActivityAt = now();
  touchTransport();
}

function writeQr(qr) {
  qrValue = qr || '';
  if (!qrValue) {
    try { fs.unlinkSync(QR_FILE); } catch {}
    return;
  }
  qrcode.generate(qrValue, { small: true }, (text) => {
    fs.writeFileSync(QR_FILE, text, { mode: 0o600 });
    try { fs.chmodSync(QR_FILE, 0o600); } catch {}
    console.log('\nACTIS WhatsApp Baileys — escaneie o QR abaixo no WhatsApp > Dispositivos conectados\n');
    console.log(text);
  });
}

function disconnectCode(error) {
  return Number(
    error?.output?.statusCode
    || error?.data?.statusCode
    || error?.statusCode
    || 0,
  );
}

function scheduleReconnect() {
  if (reconnectTimer || connectionState === 'logged_out') return;
  const base = Math.min(30000, 2000 * (1.8 ** reconnectAttempt));
  const jitter = base * 0.25 * ((Math.random() * 2) - 1);
  const delay = Math.max(500, Math.round(base + jitter));
  reconnectAttempt += 1;
  connectionState = 'reconnecting';
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    startSocket().catch((error) => {
      lastError = String(error?.message || error);
      scheduleReconnect();
    });
  }, delay);
}

async function startSocket() {
  const myGeneration = ++generation;
  connectionState = 'connecting';
  lastError = '';
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const next = makeWASocket({
    auth: state,
    logger: silentLogger,
    printQRInTerminal: false,
    keepAliveIntervalMs: 25000,
    connectTimeoutMs: 60000,
    defaultQueryTimeoutMs: 60000,
    syncFullHistory: false,
    markOnlineOnConnect: false,
    generateHighQualityLinkPreview: false,
  });
  sock = next;

  next.ev.on('creds.update', saveCreds);
  next.ev.on('connection.update', (update) => {
    if (myGeneration !== generation) return;
    touchTransport();
    if (update.qr) {
      connectionState = 'qr';
      writeQr(update.qr);
    }
    if (update.connection === 'open') {
      connectionState = 'open';
      reconnectAttempt = 0;
      lastOpenAt = now();
      accountId = String(next.user?.id || '');
      writeQr('');
      console.log(`ACTIS Baileys connected as ${accountId || 'unknown'}`);
    }
    if (update.connection === 'close') {
      const code = disconnectCode(update.lastDisconnect?.error);
      lastError = String(update.lastDisconnect?.error?.message || update.lastDisconnect?.error || '');
      if (code === DisconnectReason.loggedOut) {
        connectionState = 'logged_out';
        console.error('ACTIS Baileys logged out; QR link required again');
      } else {
        scheduleReconnect();
      }
    }
  });

  next.ev.on('messages.upsert', ({ messages, type }) => {
    if (myGeneration !== generation) return;
    touchTransport();
    // `notify` is the live path. After a disconnect/reboot WhatsApp may replay
    // messages that arrived while ACTIS was offline as `append`. Both must enter
    // the durable bridge; message_id UNIQUE keeps the replay idempotent.
    if (!['notify', 'append'].includes(type)) return;
    for (const message of messages || []) storeMessage(message, type);
  });

  next.ev.on('messages.update', (updates) => {
    if (myGeneration !== generation) return;
    touchTransport();
    const statement = db.prepare(
      'UPDATE deliveries SET status=?,status_code=?,updated_at=? WHERE message_id=?',
    );
    for (const row of updates || []) {
      const id = String(row?.key?.id || '');
      const status = row?.update?.status;
      if (!id || status == null) continue;
      statement.run('ack', Number(status), now(), id);
    }
  });
}

function targetJid(body) {
  const explicit = String(body.jid || '').trim();
  if (explicit && /^[^@\s]+@(s\.whatsapp\.net|g\.us|lid)$/.test(explicit)) return explicit;
  const digits = String(body.phone || '').replace(/\D/g, '');
  if (digits.length < 10 || digits.length > 15) throw new Error('invalid_recipient');
  return `${digits}@s.whatsapp.net`;
}

function safeDocumentPath(value) {
  const resolved = path.resolve(String(value || ''));
  if (!resolved.startsWith(DATA_ROOT + path.sep)) throw new Error('document_outside_actis_data');
  if (!fs.statSync(resolved).isFile()) throw new Error('document_not_found');
  return resolved;
}

async function sendText(body) {
  if (connectionState !== 'open' || !sock) {
    return { status: 503, body: { ok: false, status: 'unavailable', attempted: false, safe_fallback: true } };
  }
  const jid = targetJid(body);
  const content = String(body.content || '').trim();
  if (!content) throw new Error('empty_content');
  const clientId = String(body.client_id || '');
  let attempted = false;
  try {
    attempted = true;
    const result = await sock.sendMessage(jid, { text: content });
    const messageId = String(result?.key?.id || '').trim();
    if (!messageId) {
      return { status: 502, body: { ok: false, status: 'ambiguous', attempted: true, safe_fallback: false } };
    }
    const stamp = now();
    db.prepare(`
      INSERT OR REPLACE INTO deliveries
      (message_id,client_id,remote_jid,status,status_code,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?)
    `).run(messageId, clientId || null, jid, 'accepted', null, stamp, stamp);
    return {
      status: 200,
      body: { ok: true, status: 'accepted', attempted: true, safe_fallback: false, message_id: messageId, jid },
    };
  } catch (error) {
    return {
      status: 502,
      body: {
        ok: false,
        status: attempted ? 'ambiguous' : 'failed',
        attempted,
        safe_fallback: !attempted,
        error: String(error?.message || error).slice(0, 500),
      },
    };
  }
}

async function sendDocument(body) {
  if (connectionState !== 'open' || !sock) {
    return { status: 503, body: { ok: false, status: 'unavailable', attempted: false, safe_fallback: true } };
  }
  const jid = targetJid(body);
  const filePath = safeDocumentPath(body.path);
  const filename = path.basename(String(body.filename || path.basename(filePath)));
  const caption = String(body.caption || '').trim();
  const clientId = String(body.client_id || '');
  let attempted = false;
  try {
    const document = fs.readFileSync(filePath);
    attempted = true;
    const result = await sock.sendMessage(jid, {
      document,
      fileName: filename,
      mimetype: 'application/pdf',
      caption,
    });
    const messageId = String(result?.key?.id || '').trim();
    if (!messageId) {
      return { status: 502, body: { ok: false, status: 'ambiguous', attempted: true, safe_fallback: false } };
    }
    const stamp = now();
    db.prepare(`
      INSERT OR REPLACE INTO deliveries
      (message_id,client_id,remote_jid,status,status_code,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?)
    `).run(messageId, clientId || null, jid, 'accepted', null, stamp, stamp);
    return {
      status: 200,
      body: { ok: true, status: 'accepted', attempted: true, safe_fallback: false, message_id: messageId, jid },
    };
  } catch (error) {
    return {
      status: 502,
      body: {
        ok: false,
        status: attempted ? 'ambiguous' : 'failed',
        attempted,
        safe_fallback: !attempted,
        error: String(error?.message || error).slice(0, 500),
      },
    };
  }
}

const server = http.createServer(async (req, res) => {
  try {
    if (!authorized(req)) return unauthorized(res);
    const url = new URL(req.url || '/', `http://${HOST}:${PORT}`);

    if (req.method === 'GET' && url.pathname === '/health') {
      return json(res, 200, {
        ok: connectionState === 'open',
        connected: connectionState === 'open',
        status: connectionState,
        account_id: accountId,
        qr_available: Boolean(qrValue),
        last_transport_activity_at: lastTransportActivityAt,
        last_message_activity_at: lastMessageActivityAt,
        last_open_at: lastOpenAt,
        last_error: lastError,
      });
    }

    if (req.method === 'GET' && url.pathname === '/events') {
      const after = Math.max(0, Number(url.searchParams.get('after') || 0));
      const limit = Math.max(1, Math.min(500, Number(url.searchParams.get('limit') || 100)));
      const rows = db.prepare('SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?').all(after, limit);
      return json(res, 200, { ok: true, events: rows });
    }

    if (req.method === 'GET' && url.pathname === '/delivery') {
      const id = String(url.searchParams.get('id') || '');
      const row = id ? db.prepare('SELECT * FROM deliveries WHERE message_id=?').get(id) : null;
      return json(res, row ? 200 : 404, { ok: Boolean(row), delivery: row || null });
    }

    if (req.method === 'POST' && url.pathname === '/send') {
      const result = await sendText(await readJson(req));
      return json(res, result.status, result.body);
    }

    if (req.method === 'POST' && url.pathname === '/send-document') {
      const result = await sendDocument(await readJson(req));
      return json(res, result.status, result.body);
    }

    return json(res, 404, { ok: false, error: 'not_found' });
  } catch (error) {
    return json(res, 400, { ok: false, error: String(error?.message || error).slice(0, 500) });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`ACTIS Baileys bridge listening on http://${HOST}:${PORT}`);
  startSocket().catch((error) => {
    lastError = String(error?.message || error);
    scheduleReconnect();
  });
});

function shutdown() {
  try { sock?.end?.(new Error('service_shutdown')); } catch {}
  try { db.close(); } catch {}
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 1500).unref();
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
