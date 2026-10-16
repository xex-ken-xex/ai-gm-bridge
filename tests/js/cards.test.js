import { test } from "node:test";
import assert from "node:assert/strict";
import { buildChoiceCardHtml, buildRoomNarrationHtml, buildSceneSuccessCardHtml } from "../../scripts/lib/cards.js";

test("buildChoiceCardHtml は属性値とラベルをエスケープする", () => {
  const html = buildChoiceCardHtml({
    title: "規模",
    message: "選んで<b>",
    choices: [{ label: `小" onclick="x`, value: "dungeon_size:small" }],
  });
  assert.ok(html.includes('data-choice-value="dungeon_size:small"'));
  assert.ok(html.includes("小&quot; onclick=&quot;x"));
  assert.ok(!html.includes('onclick="x'));
  assert.ok(html.includes("選んで&lt;b&gt;"));
});

test("buildSceneSuccessCardHtml は壁省略時に警告を出す", () => {
  const html = buildSceneSuccessCardHtml({
    sceneId: "s1", sceneName: "<i>城</i>", data: { grid_width: 10, grid_height: 10, start_pos: [1, 2] },
    sceneWidth: 1000, sceneHeight: 1000, wallCount: 0, noteCount: 0, journalId: null,
  });
  assert.ok(html.includes("&lt;i&gt;城"));
  assert.ok(html.includes("壁は省略されました"));
  assert.ok(html.includes("(1, 2)"));
  assert.ok(html.includes("未設定"));
  assert.ok(!html.includes("ai-gm-journal-open-btn"));
});

test("buildRoomNarrationHtml はフレーバーをエスケープする", () => {
  const html = buildRoomNarrationHtml({ name: "部屋", flavor: "<img src=x onerror=alert(1)>" });
  assert.ok(!html.includes("<img"));
});
