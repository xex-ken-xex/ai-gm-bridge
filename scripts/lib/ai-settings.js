// AI の設定画面の純粋関数 (Foundry 非依存): サーバーの ai_settings から画面の HTML を作り、入力値から ai_settings_save を作る
//
// API キーの値はこの画面に出さないし、送りもしない。出すのは「設定済み / 未設定」と、キーを入れた環境変数の名前だけ

import { escapeHtml } from "./html.js";

export const SETTINGS_MENU_ITEM = Object.freeze({ id: "ai-settings", icon: "⚙️", label: "AI の設定", gmOnly: true });

// 接続先の欄 (種類ごとに使う欄はサーバーの editable_fields で決まる)
export const PROVIDER_FIELDS = Object.freeze([
  { key: "model", label: "モデル", placeholder: "例: gemini-3.8-flash / sonnet / qwen3-8b" },
  { key: "base_url", label: "URL", placeholder: "http://localhost:1234/v1" },
  { key: "api_key_env", label: "API キーの環境変数", placeholder: "例: GEMINI_API_KEY" },
  { key: "timeout", label: "待ち上限 (秒)", placeholder: "" },
  { key: "concurrency", label: "同時実行", placeholder: "" },
]);

const NEW_ROW = "__new__";

/** "a, b → c" のような並びを名前の配列にする (区切りは , 、 → > 空白) */
export function parseChain(text) {
  return String(text ?? "")
    .split(/[,、→>\s]+/)
    .map(s => s.trim())
    .filter(Boolean);
}

export function formatChain(names) {
  return (names ?? []).join(" → ");
}

function fieldsFor(settings, type) {
  return new Set(settings?.editable_fields?.[type] ?? []);
}

function typeOptions(settings, selected) {
  const labels = settings?.type_labels ?? {};
  return Object.entries(labels)
    .map(([value, label]) => `<option value="${escapeHtml(value)}"${value === selected ? " selected" : ""}>${escapeHtml(label)}</option>`)
    .join("");
}

function providerRow(settings, p, isNew = false) {
  const fields = fieldsFor(settings, p.type);
  const cells = PROVIDER_FIELDS.map(f => {
    const usable = isNew || fields.has(f.key);
    const value = p[f.key] ?? "";
    return `<td><input type="text" data-field="${f.key}" value="${escapeHtml(value)}"
      placeholder="${escapeHtml(usable ? f.placeholder : "使いません")}"${usable ? "" : " disabled"}></td>`;
  }).join("");
  const status = isNew
    ? ""
    : `<span class="aiset-status ${p.available ? "ok" : "ng"}">${escapeHtml(p.key_status ?? "")}${p.available ? "" : " (使えません)"}</span>`;
  const jsonCheck = isNew || fields.has("json_response_format")
    ? `<label class="aiset-check"><input type="checkbox" data-field="json_response_format"${p.json_response_format ? " checked" : ""}> JSON 指定</label>`
    : "";
  return `<tr class="aiset-provider" data-row="${isNew ? NEW_ROW : escapeHtml(p.name)}">
    <td><input type="text" data-field="name" value="${escapeHtml(isNew ? "" : p.name)}" placeholder="${isNew ? "新しい接続先の名前" : ""}"></td>
    <td><select data-field="type">${isNew ? '<option value="">(種類)</option>' : ""}${typeOptions(settings, p.type)}</select></td>
    ${cells}
    <td>${status}${jsonCheck}</td>
    <td>${isNew ? "" : '<label class="aiset-check"><input type="checkbox" data-field="remove"> 削除</label>'}</td>
  </tr>`;
}

/** 設定画面の中身 (ダイアログの content) */
export function buildSettingsHtml(settings) {
  const s = settings ?? {};
  const providers = s.providers ?? [];
  const names = providers.map(p => p.name);
  const message = s.message
    ? `<div class="aiset-message ${s.ok === false ? "ng" : "ok"}">${escapeHtml(s.message)}</div>`
    : "";
  const source = s.config_exists
    ? `設定ファイル: <code>${escapeHtml(s.config_path)}</code>`
    : `設定ファイルはまだありません (今は既定の振り分け)。保存すると <code>${escapeHtml(s.config_path)}</code> を作ります。`;
  const header = ["名前", "種類", ...PROVIDER_FIELDS.map(f => f.label), "キー", ""].map(h => `<th>${escapeHtml(h)}</th>`).join("");
  const taskRows = Object.entries(s.task_labels ?? {}).map(([task, label]) => `<tr>
      <th>${escapeHtml(label)}<br><small>${escapeHtml(task)}</small></th>
      <td><input type="text" data-task="${escapeHtml(task)}" value="${escapeHtml(formatChain(s.tasks?.[task]))}"
        placeholder="${task === "default" ? "例: gemini → agy" : "空なら default と同じ"}" list="aiset-provider-names"></td>
    </tr>`).join("");
  return `<div class="aiset">
    ${message}
    <p class="aiset-note">${source}<br>
      API キーの値はここには書きません。キーは <code>~/.bashrc</code> などで環境変数に入れ、その<strong>環境変数の名前</strong>を書きます。
      キーは「設定済み / 未設定」だけを表示します。</p>
    <h3>接続先</h3>
    <div class="aiset-scroll"><table class="aiset-table"><thead><tr>${header}</tr></thead><tbody>
      ${providers.map(p => providerRow(s, p)).join("")}
      ${providerRow(s, { type: "" }, true)}
    </tbody></table></div>
    <h3>作業ごとの接続先</h3>
    <p class="aiset-note">接続先の名前を、試す順に「→」か「,」で区切って書きます。前の接続先が失敗したら次を試します。</p>
    <datalist id="aiset-provider-names">${names.map(n => `<option value="${escapeHtml(n)}"></option>`).join("")}</datalist>
    <table class="aiset-table aiset-tasks"><tbody>${taskRows}</tbody></table>
    <div class="aiset-actions">
      <button type="button" data-action="aiset-save">💾 保存する</button>
      <button type="button" data-action="aiset-reload">🔄 読み直す</button>
    </div>
  </div>`;
}

/**
 * 画面の入力値から ai_settings_save を作る。
 * rows: [{ row, name, type, model, base_url, api_key_env, timeout, concurrency, json_response_format, remove }]
 * tasks: { 作業: "a → b" }
 * 返り値: { payload, errors }。errors が空でなければ送らない
 */
export function settingsFromForm({ rows = [], tasks = {} } = {}) {
  const errors = [];
  const providers = {};
  for (const r of rows) {
    const name = String(r.name ?? "").trim();
    const isNew = r.row === NEW_ROW;
    if (r.remove) continue;
    if (isNew && !name && !r.type) continue; // 追加の行は空なら無視
    if (!name) {
      errors.push("名前の無い接続先があります");
      continue;
    }
    if (!/^[A-Za-z0-9_-]{1,32}$/.test(name)) {
      errors.push(`接続先の名前「${name}」は英数字と _ - の 32 文字までにしてください`);
      continue;
    }
    if (providers[name]) {
      errors.push(`接続先の名前「${name}」が重なっています`);
      continue;
    }
    if (!r.type) {
      errors.push(`${name} の種類を選んでください`);
      continue;
    }
    const spec = { type: r.type };
    for (const f of PROVIDER_FIELDS) {
      const value = String(r[f.key] ?? "").trim();
      if (value) spec[f.key] = value;
    }
    if (r.json_response_format) spec.json_response_format = true;
    providers[name] = spec;
  }
  const chains = {};
  for (const [task, text] of Object.entries(tasks)) {
    const chain = parseChain(text);
    const unknown = chain.filter(n => !providers[n]);
    if (unknown.length) errors.push(`${task} の接続先 ${unknown.join("・")} がありません`);
    if (chain.length) chains[task] = chain;
  }
  if (!chains.default?.length) errors.push("default (書いていない作業) の接続先を 1 つ以上書いてください");
  return { payload: { event: "ai_settings_save", providers, tasks: chains }, errors };
}
