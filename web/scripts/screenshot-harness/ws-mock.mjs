/**
 * Minimal, dependency-free WebSocket server for screenshot mocks (mode D).
 *
 * The repo intentionally has NO `ws` npm package (founder guardrail — the
 * harness must not add a top-level dependency). Node 24 ships a global
 * WebSocket *client* but no server, so this implements just enough of RFC 6455
 * on top of the built-in `http` upgrade event + `crypto` to:
 *   - complete the opening handshake (Sec-WebSocket-Accept),
 *   - echo the offered subprotocol back (the dock authenticates via the
 *     `happyranch.bearer.<token>` subprotocol — the real daemon echoes it),
 *   - send server->client text frames (unmasked, per spec),
 *   - drain client->server frames enough to surface text messages and answer
 *     pings / handle close cleanly.
 *
 * This is TEST TOOLING, not a production WS implementation — it handles the
 * frame shapes the A-mode dock actually exchanges and nothing more.
 */
import { createHash } from 'node:crypto';

const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

function acceptKey(key) {
  return createHash('sha1')
    .update(key + GUID)
    .digest('base64');
}

/** Encode one unmasked server->client text frame (opcode 0x1, FIN set). */
function encodeTextFrame(str) {
  const payload = Buffer.from(str, 'utf8');
  const len = payload.length;
  let header;
  if (len < 126) {
    header = Buffer.from([0x81, len]);
  } else if (len < 65536) {
    header = Buffer.from([0x81, 126, (len >> 8) & 0xff, len & 0xff]);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x81;
    header[1] = 127;
    header.writeBigUInt64BE(BigInt(len), 2);
  }
  return Buffer.concat([header, payload]);
}

/** A single mock connection wrapping the raw TCP socket. */
class MockWsConnection {
  constructor(socket) {
    this.socket = socket;
    this._buf = Buffer.alloc(0);
    this._onMessage = null;
    socket.on('data', (chunk) => this._ingest(chunk));
    socket.on('error', () => this.close());
  }

  /** Register a text-message handler: `(text) => void`. */
  onMessage(fn) {
    this._onMessage = fn;
  }

  /** Send a JS value as a JSON text frame. */
  sendJson(value) {
    if (this.socket.writable) this.socket.write(encodeTextFrame(JSON.stringify(value)));
  }

  close() {
    try {
      // 0x88 = FIN + close opcode, empty payload.
      if (this.socket.writable) this.socket.write(Buffer.from([0x88, 0x00]));
      this.socket.end();
    } catch {
      /* already gone */
    }
  }

  _ingest(chunk) {
    this._buf = Buffer.concat([this._buf, chunk]);
    // Parse as many whole frames as are buffered.
    for (;;) {
      if (this._buf.length < 2) return;
      const b0 = this._buf[0];
      const b1 = this._buf[1];
      const opcode = b0 & 0x0f;
      const masked = (b1 & 0x80) !== 0;
      let len = b1 & 0x7f;
      let offset = 2;
      if (len === 126) {
        if (this._buf.length < offset + 2) return;
        len = this._buf.readUInt16BE(offset);
        offset += 2;
      } else if (len === 127) {
        if (this._buf.length < offset + 8) return;
        len = Number(this._buf.readBigUInt64BE(offset));
        offset += 8;
      }
      let mask;
      if (masked) {
        if (this._buf.length < offset + 4) return;
        mask = this._buf.subarray(offset, offset + 4);
        offset += 4;
      }
      if (this._buf.length < offset + len) return;
      let payload = this._buf.subarray(offset, offset + len);
      if (masked) {
        const unmasked = Buffer.alloc(len);
        for (let i = 0; i < len; i++) unmasked[i] = payload[i] ^ mask[i & 3];
        payload = unmasked;
      }
      this._buf = this._buf.subarray(offset + len);

      if (opcode === 0x8) {
        // close
        this.close();
        return;
      } else if (opcode === 0x9) {
        // ping -> pong (0x8a)
        if (this.socket.writable) this.socket.write(Buffer.from([0x8a, 0x00]));
      } else if (opcode === 0x1) {
        // text
        if (this._onMessage) {
          try {
            this._onMessage(payload.toString('utf8'));
          } catch {
            /* handler error is not fatal to the mock */
          }
        }
      }
      // opcode 0x0 (continuation) / 0x2 (binary) / 0xa (pong): ignored.
    }
  }
}

/**
 * Attach a WebSocket mock to an existing http.Server on `path`.
 *
 * @param {import('node:http').Server} server
 * @param {object} opts
 * @param {string} opts.path      URL pathname to accept upgrades on (others are 404'd).
 * @param {(conn: MockWsConnection, req: import('node:http').IncomingMessage) => void} opts.onConnect
 */
export function attachWsMock(server, { path, onConnect }) {
  server.on('upgrade', (req, socket) => {
    let pathname;
    try {
      pathname = new URL(req.url, 'http://localhost').pathname;
    } catch {
      pathname = req.url;
    }
    if (pathname !== path) {
      socket.destroy();
      return;
    }
    const key = req.headers['sec-websocket-key'];
    if (!key) {
      socket.destroy();
      return;
    }
    // Echo the first offered subprotocol back (the dock offers the bearer one).
    const offered = String(req.headers['sec-websocket-protocol'] || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    const responseLines = [
      'HTTP/1.1 101 Switching Protocols',
      'Upgrade: websocket',
      'Connection: Upgrade',
      `Sec-WebSocket-Accept: ${acceptKey(key)}`,
    ];
    if (offered[0]) responseLines.push(`Sec-WebSocket-Protocol: ${offered[0]}`);
    socket.write(responseLines.join('\r\n') + '\r\n\r\n');

    const conn = new MockWsConnection(socket);
    onConnect(conn, req);
  });
}
