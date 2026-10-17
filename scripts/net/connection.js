// Python サーバーとの WebSocket 接続 (自動再接続・受信順序の保証)
// WebSocket 実装と通知関数は注入式にして Node でもテストできるようにする

export const DEFAULT_SERVER_URL = "ws://127.0.0.1:8765";
const OPEN = 1;

export class BridgeConnection {
  /**
   * @param {object} opts
   * @param {string} [opts.url]
   * @param {Function} opts.onMessage   async (data) => void  受信 JSON ごとに呼ばれる (直列実行)
   * @param {Function} [opts.WebSocketImpl]
   * @param {number} [opts.reconnectMs]  切断時の再接続間隔
   * @param {object} [opts.notify]      { info(msg), warn(msg) }
   * @param {object} [opts.logger]
   * @param {Function} [opts.setTimeoutImpl]
   */
  constructor({ url = DEFAULT_SERVER_URL, onMessage, WebSocketImpl = globalThis.WebSocket, reconnectMs = 5000, notify = {}, logger = console, setTimeoutImpl = setTimeout } = {}) {
    this.url = url;
    this.onMessage = onMessage;
    this.WebSocketImpl = WebSocketImpl;
    this.reconnectMs = reconnectMs;
    this.notify = notify;
    this.logger = logger;
    this.setTimeoutImpl = setTimeoutImpl;
    this.ws = null;
    this.closed = false;
    // 受信メッセージを順番に処理する Promise チェーン
    // (シーン構築の途中に play_sound などが割り込まないようにする)
    this.queue = Promise.resolve();
  }

  get isOpen() {
    return Boolean(this.ws && this.ws.readyState === OPEN);
  }

  connect() {
    this.closed = false;
    const ws = new this.WebSocketImpl(this.url);
    this.ws = ws;

    ws.onopen = () => {
      this.logger.log?.(`AI GM Bridge | WebSocket接続完了 (${this.url})`);
      this.notify.info?.("AI GM Bridge: 接続成功（サポートGM連携有効）");
    };
    ws.onmessage = event => {
      this.queue = this.queue.then(() => this._handle(event.data));
      return this.queue;
    };
    ws.onclose = () => {
      if (this.ws !== ws || this.closed) return;
      this.setTimeoutImpl(() => this.connect(), this.reconnectMs);
    };
    ws.onerror = () => {};
    return ws;
  }

  async _handle(raw) {
    let data;
    try {
      data = JSON.parse(raw);
    } catch (err) {
      this.logger.error?.("AI GM Bridge | 不正なJSONを受信:", err);
      return;
    }
    try {
      await this.onMessage?.(data);
    } catch (err) {
      this.logger.error?.("AI GM Bridge | エラー:", err);
    }
  }

  /** サーバーへ送信。未接続なら警告を出して false */
  send(payload) {
    if (!this.isOpen) {
      this.notify.warn?.("AI GM Bridge: Python サーバー (8765) に接続されていません。");
      return false;
    }
    this.ws.send(JSON.stringify(payload));
    return true;
  }

  close() {
    this.closed = true;
    this.ws?.close?.();
  }
}
