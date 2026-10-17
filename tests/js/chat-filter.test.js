import { test } from "node:test";
import assert from "node:assert/strict";
import { generatedFlags, isAiAlias, playerMessageToForward } from "../../scripts/lib/chat-filter.js";

test("isAiAlias は AI GM / サポートGM を検知する", () => {
  assert.ok(isAiAlias("AI GM"));
  assert.ok(isAiAlias("AI GM (判定)"));
  assert.ok(isAiAlias("サポートGM アギィ"));
  assert.ok(isAiAlias("サポートGM (agy)")); // 名前をそろえる前のメッセージも
  assert.ok(!isAiAlias("ゴブリン"));
  assert.ok(!isAiAlias(undefined));
});

test("通常のプレイヤー発言はタグを除去して転送する", () => {
  assert.equal(playerMessageToForward({ alias: "戦士", content: "<p>扉を調べる</p>" }), "扉を調べる");
});

test("AI 由来・コマンド・ロール・空の発言は転送しない", () => {
  assert.equal(playerMessageToForward({ alias: "AI GM", content: "描写" }), null);
  assert.equal(playerMessageToForward({ alias: "アギィ", content: "描写", flags: generatedFlags() }), null);
  assert.equal(playerMessageToForward({ alias: "PL", content: "/r 1d20" }), null);
  assert.equal(playerMessageToForward({ alias: "PL", content: "[[1d20]]" }), null);
  assert.equal(playerMessageToForward({ alias: "PL", content: "攻撃", isRoll: true }), null);
  assert.equal(playerMessageToForward({ alias: "PL", content: "<p> </p>" }), null);
});
