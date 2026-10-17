import { test } from "node:test";
import assert from "node:assert/strict";
import { escapeHtml, stripHtml, textToHtml } from "../../scripts/lib/html.js";

test("escapeHtml は HTML 特殊文字をすべて置換する", () => {
  assert.equal(escapeHtml(`<img src=x onerror="alert('x')">&`), "&lt;img src=x onerror=&quot;alert(&#39;x&#39;)&quot;&gt;&amp;");
  assert.equal(escapeHtml(null), "");
  assert.equal(escapeHtml(undefined), "");
  assert.equal(escapeHtml(12), "12");
});

test("stripHtml はタグを除去する", () => {
  assert.equal(stripHtml("<p>扉を<b>開ける</b></p> "), "扉を開ける");
  assert.equal(stripHtml(""), "");
});

test("textToHtml はエスケープ後に改行を <br> にする", () => {
  assert.equal(textToHtml("a<b>\nc\r\nd"), "a&lt;b&gt;<br>c<br>d");
});
