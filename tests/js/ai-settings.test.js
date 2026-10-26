import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSettingsHtml, formatChain, parseChain, settingsFromForm } from "../../scripts/lib/ai-settings.js";

const SETTINGS = {
  config_path: "/home/u/.config/ai-gm-bridge/ai_providers.json",
  config_exists: false,
  providers: [
    { name: "gemini", type: "gemini", model: "gemini-3.8-flash", api_key_env: "GEMINI_API_KEY", key_status: "設定済み", available: true, timeout: 120, concurrency: 3 },
    { name: "agy", type: "agy", key_status: "不要", available: true, timeout: 300 },
    { name: "lm", type: "openai", base_url: "http://localhost:1234/v1", model: "qwen<b>", key_status: "未設定 (LM_KEY)", available: true },
  ],
  tasks: { default: ["gemini", "agy"], npc_dialogue: ["lm"], support_gm: [] },
  task_labels: { default: "書いていない作業 (default)", npc_dialogue: "NPC の会話", support_gm: "サポートGM" },
  type_labels: { gemini: "Gemini API", agy: "agy", claude_code: "Claude Code", openai: "OpenAI 互換" },
  editable_fields: { gemini: ["model", "api_key_env", "timeout", "concurrency"], agy: ["timeout"], claude_code: ["model", "timeout", "concurrency"], openai: ["base_url", "model", "api_key_env", "timeout", "concurrency", "json_response_format"] },
};

test("parseChain / formatChain", () => {
  assert.deepEqual(parseChain("lm → gemini, agy"), ["lm", "gemini", "agy"]);
  assert.deepEqual(parseChain(" 、 "), []);
  assert.equal(formatChain(["a", "b"]), "a → b");
});

test("buildSettingsHtml shows key status only and escapes values", () => {
  const html = buildSettingsHtml(SETTINGS);
  assert.match(html, /設定済み/);
  assert.match(html, /未設定 \(LM_KEY\)/);
  assert.match(html, /qwen&lt;b&gt;/);
  assert.doesNotMatch(html, /qwen<b>/);
  assert.match(html, /gemini → agy/);
  assert.match(html, /保存すると/);
  // agy では使わない欄は書けない
  assert.match(html, /data-row="agy"[\s\S]*?data-field="model" value=""\s+placeholder="使いません" disabled/);
});

test("buildSettingsHtml shows the server message", () => {
  assert.match(buildSettingsHtml({ ...SETTINGS, message: "保存できませんでした: x", ok: false }), /aiset-message ng/);
});

test("settingsFromForm builds the save payload", () => {
  const { payload, errors } = settingsFromForm({
    rows: [
      { row: "gemini", name: "gemini", type: "gemini", model: "gemini-3.8-flash", api_key_env: "GEMINI_API_KEY", timeout: "120" },
      { row: "agy", name: "agy", type: "agy", remove: true },
      { row: "__new__", name: "claude", type: "claude_code", model: "sonnet" },
      { row: "__new__", name: "", type: "" },
    ],
    tasks: { default: "gemini", builder: "claude → gemini", npc_turn: "" },
  });
  assert.deepEqual(errors, []);
  assert.deepEqual(payload, {
    event: "ai_settings_save",
    providers: {
      gemini: { type: "gemini", model: "gemini-3.8-flash", api_key_env: "GEMINI_API_KEY", timeout: "120" },
      claude: { type: "claude_code", model: "sonnet" },
    },
    tasks: { default: ["gemini"], builder: ["claude", "gemini"] },
  });
});

test("settingsFromForm reports problems", () => {
  const { errors } = settingsFromForm({
    rows: [
      { row: "a", name: "a b", type: "agy" },
      { row: "x", name: "x", type: "" },
      { row: "agy", name: "agy", type: "agy", remove: true },
    ],
    tasks: { default: "agy" },
  });
  assert.ok(errors.some(e => e.includes("a b")));
  assert.ok(errors.some(e => e.includes("x の種類")));
  assert.ok(errors.some(e => e.includes("agy がありません")));
});
