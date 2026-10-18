import { test } from "node:test";
import assert from "node:assert/strict";
import { BridgeConnection } from "../../scripts/net/connection.js";

class FakeWebSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    FakeWebSocket.instances.push(this);
  }
  send(msg) { this.sent.push(msg); }
  open() { this.readyState = 1; this.onopen?.(); }
  close() { this.readyState = 3; this.onclose?.(); }
}

const silent = { log() {}, error() {} };

test("send は未接続なら警告して false", () => {
  const warns = [];
  const c = new BridgeConnection({ WebSocketImpl: FakeWebSocket, notify: { warn: m => warns.push(m) }, logger: silent });
  assert.equal(c.send({ event: "x" }), false);
  c.connect();
  assert.equal(c.send({ event: "x" }), false);
  assert.equal(warns.length, 2);
});

test("接続後は JSON を送信する", () => {
  const c = new BridgeConnection({ WebSocketImpl: FakeWebSocket, logger: silent });
  const ws = c.connect();
  ws.open();
  assert.equal(c.send({ event: "player_message", content: "こんにちは" }), true);
  assert.deepEqual(JSON.parse(ws.sent[0]), { event: "player_message", content: "こんにちは" });
});

test("受信メッセージは到着順に直列処理される", async () => {
  const order = [];
  const c = new BridgeConnection({
    WebSocketImpl: FakeWebSocket,
    logger: silent,
    onMessage: async data => {
      order.push(`start:${data.n}`);
      if (data.n === 1) await new Promise(r => setTimeout(r, 20));
      order.push(`end:${data.n}`);
    },
  });
  const ws = c.connect();
  ws.onmessage({ data: JSON.stringify({ n: 1 }) });
  ws.onmessage({ data: "not json" });
  await ws.onmessage({ data: JSON.stringify({ n: 2 }) });
  assert.deepEqual(order, ["start:1", "end:1", "start:2", "end:2"]);
});

test("ハンドラの例外で後続の処理が止まらない", async () => {
  const seen = [];
  const c = new BridgeConnection({
    WebSocketImpl: FakeWebSocket,
    logger: silent,
    onMessage: async data => { if (data.n === 1) throw new Error("boom"); seen.push(data.n); },
  });
  const ws = c.connect();
  ws.onmessage({ data: JSON.stringify({ n: 1 }) });
  await ws.onmessage({ data: JSON.stringify({ n: 2 }) });
  assert.deepEqual(seen, [2]);
});

test("切断されたら再接続し、close() 後は再接続しない", () => {
  const timers = [];
  const c = new BridgeConnection({ WebSocketImpl: FakeWebSocket, logger: silent, setTimeoutImpl: (fn, ms) => timers.push({ fn, ms }) });
  const ws = c.connect();
  ws.close();
  assert.equal(timers.length, 1);
  assert.equal(timers[0].ms, 5000);
  timers[0].fn();
  assert.notEqual(c.ws, ws);
  c.close();
  assert.equal(timers.length, 1);
});

test("onStatus は接続と切断を 1 回ずつ知らせる (再接続の失敗が続いても切断は 1 回)", () => {
  const status = [];
  const c = new BridgeConnection({ WebSocketImpl: FakeWebSocket, logger: silent, onStatus: open => status.push(open), setTimeoutImpl: () => {} });
  const ws = c.connect();
  ws.open();
  ws.close();
  const retry = c.connect();
  retry.close();
  assert.deepEqual(status, [true, false]);
});

test("切断後の再接続の既定のタイマーは this を持たずに呼ぶ (ブラウザの Illegal invocation 対策)", () => {
  const realSet = globalThis.setTimeout;
  const scheduled = [];
  // ブラウザの setTimeout と同じく、this が window (globalThis) 以外なら例外にする
  globalThis.setTimeout = function (fn, ms) {
    if (this !== undefined && this !== globalThis) throw new TypeError("Illegal invocation");
    scheduled.push(ms);
    return 0;
  };
  try {
    const c = new BridgeConnection({ WebSocketImpl: FakeWebSocket, logger: silent, reconnectMs: 1234 });
    const ws = c.connect();
    ws.open();
    ws.close();
  } finally {
    globalThis.setTimeout = realSet;
  }
  assert.deepEqual(scheduled, [1234]);
});
