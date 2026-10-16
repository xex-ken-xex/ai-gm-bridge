import { test } from "node:test";
import assert from "node:assert/strict";
import { AI_COMMAND_RGX, cleanCommandPrompt, parseAiCommand } from "../../scripts/lib/commands.js";

test("parseAiCommand は /ai と /agy の引数を返す", () => {
  assert.equal(parseAiCommand("/ai ダンジョンを作って"), "ダンジョンを作って");
  assert.equal(parseAiCommand("/AGY 相談"), "相談");
  assert.equal(parseAiCommand("/ai"), "");
});

test("parseAiCommand は HTML・全角スペース・&nbsp;・引用符を吸収する", () => {
  assert.equal(parseAiCommand("<p>/ai&nbsp;氷の洞窟</p>"), "氷の洞窟");
  assert.equal(parseAiCommand("「/ai　塔を生成」"), "塔を生成");
  assert.equal(parseAiCommand("/ai 一行目<br>二行目"), "一行目\n二行目");
});

test("parseAiCommand はコマンドでなければ null", () => {
  assert.equal(parseAiCommand("こんにちは"), null);
  assert.equal(parseAiCommand("/aim 狙う"), null);
  assert.equal(parseAiCommand("/roll 1d20"), null);
  assert.equal(parseAiCommand(""), null);
  assert.equal(parseAiCommand(null), null);
});

test("AI_COMMAND_RGX と cleanCommandPrompt", () => {
  const m = "/ai&nbsp;<b>相談</b>」".match(AI_COMMAND_RGX);
  assert.ok(m);
  assert.equal(cleanCommandPrompt(m[1]), "相談");
  assert.equal("/aim x".match(AI_COMMAND_RGX), null);
  // 末尾の $ を誤って文字として扱わない (reference 版の不具合)
  assert.equal(cleanCommandPrompt("価格は100$"), "価格は100$");
});
