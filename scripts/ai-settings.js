// AI の設定画面 (Foundry 依存): Geminiちゃんのメニューの「AI の設定」から開く
//
// - 開くとサーバーに ai_settings_request を送り、返ってきた ai_settings で画面を描く
// - 「保存する」で ai_settings_save を送る。サーバーが設定ファイルに書いて読み直し、結果を ai_settings で返す
// - API キーの値は扱わない (表示は「設定済み / 未設定」、書けるのは環境変数の名前だけ)
// 純粋関数は scripts/lib/ai-settings.js (tests/js)

import { buildSettingsHtml, PROVIDER_FIELDS, SETTINGS_MENU_ITEM, settingsFromForm } from "./lib/ai-settings.js";
import { registerMascotMenuItem } from "./mascot.js";

const TITLE = "⚙️ AI の設定 (作業ごとの接続先)";
let sendToServer = null;
let current = { app: null };

function send(payload) {
  if (sendToServer?.(payload)) return true;
  ui.notifications.warn("AI GM Bridge: Python サーバー (8765) に接続されていないため、AI の設定を開けません。");
  return false;
}

/** メニューに「AI の設定」を足す (サーバーと繋ぐ GM だけ) */
export function registerAiSettingsMenu(sender) {
  sendToServer = sender;
  registerMascotMenuItem({ ...SETTINGS_MENU_ITEM, onSelect: () => send({ event: "ai_settings_request" }) });
}

async function openDialog(content) {
  const DialogV2 = foundry?.applications?.api?.DialogV2;
  if (DialogV2) {
    const app = new DialogV2({
      // 見た目は styles/ai-settings.css (module.json の styles)
      classes: ["ai-gm-settings"],
      window: { title: TITLE, resizable: true },
      position: { width: 1000, height: "auto" },
      content,
      buttons: [{ action: "close", label: "閉じる", default: true }],
    });
    await app.render({ force: true });
    return { app, root: app.element };
  }
  // v11: 旧 Dialog
  return new Promise(resolve => {
    const app = new Dialog({
      title: TITLE,
      content,
      buttons: { close: { label: "閉じる" } },
      render: html => resolve({ app, root: html instanceof HTMLElement ? html : html[0] }),
    }, { classes: ["dialog", "ai-gm-settings"], width: 1000, height: "auto", resizable: true });
    app.render(true);
  });
}

function readForm(root) {
  const rows = [...root.querySelectorAll("tr.aiset-provider")].map(tr => {
    const value = key => tr.querySelector(`[data-field="${key}"]`);
    const row = { row: tr.dataset.row };
    for (const key of ["name", "type", ...PROVIDER_FIELDS.map(f => f.key)]) row[key] = value(key)?.value ?? "";
    row.json_response_format = Boolean(value("json_response_format")?.checked);
    row.remove = Boolean(value("remove")?.checked);
    return row;
  });
  const tasks = {};
  root.querySelectorAll("[data-task]").forEach(el => { tasks[el.dataset.task] = el.value; });
  return { rows, tasks };
}

function bindEvents(root) {
  // DialogV2 は内容を <form> で包むため、入力欄の Enter で画面が閉じないようにする
  root.addEventListener("keydown", ev => {
    if (ev.key === "Enter" && ev.target?.tagName === "INPUT") ev.preventDefault();
  });
  // 種類を変えたら、その行の欄をすべて書けるようにする (使わない欄はサーバーが捨てる)
  root.querySelectorAll('tr.aiset-provider select[data-field="type"]').forEach(sel => sel.addEventListener("change", () => {
    sel.closest("tr")?.querySelectorAll("input[disabled]").forEach(el => {
      el.disabled = false;
      el.placeholder = "";
    });
  }));
  root.querySelector('[data-action="aiset-save"]')?.addEventListener("click", () => {
    const { payload, errors } = settingsFromForm(readForm(root));
    if (errors.length) {
      ui.notifications.warn(`AI の設定: ${errors[0]}`);
      return;
    }
    if (send(payload)) ui.notifications.info("AI の設定を保存しています…");
  });
  root.querySelector('[data-action="aiset-reload"]')?.addEventListener("click", () => send({ event: "ai_settings_request" }));
}

/** サーバーからの ai_settings: 画面を開く (開いていれば描き直す) */
export async function handleAiSettings(data) {
  const old = current.app;
  current.app = null;
  if (old) await old.close?.();
  const { app, root } = await openDialog(buildSettingsHtml(data));
  current.app = app;
  bindEvents(root);
  if (data?.message) (data.ok === false ? ui.notifications.warn : ui.notifications.info).call(ui.notifications, `AI の設定: ${data.message}`);
}
