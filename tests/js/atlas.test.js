import { test } from "node:test";
import assert from "node:assert/strict";
import {
  actorsContext,
  atlasEntryContext,
  atlasIndex,
  buildAtlasPages,
  formatBuilderProgress,
  formatStageDraft,
  htmlToText,
  recentCardIds,
  resolveParentIds,
  splitLines,
  textToParagraphsHtml,
} from "../../scripts/lib/atlas.js";

test("splitLines は空行と行頭の・を取り除く", () => {
  assert.deepEqual(splitLines("・資源: 塩\n\n- 人物: エドガー\n  歴史: 塔 "), ["資源: 塩", "人物: エドガー", "歴史: 塔"]);
  assert.deepEqual(splitLines(undefined), []);
});

test("textToParagraphsHtml は AI テキストをエスケープして段落にする", () => {
  const html = textToParagraphsHtml("【気候】\n乾燥<img src=x onerror=alert(1)>\n\n【資源】\n塩");
  assert.equal(html, "<p>【気候】<br>乾燥&lt;img src=x onerror=alert(1)&gt;</p><p>【資源】<br>塩</p>");
});

test("htmlToText はジャーナルの HTML を改行付きのテキストに戻す", () => {
  assert.equal(htmlToText("<p>一行目<br>二行目</p><ul><li>塩</li><li>A &amp; B &lt;x&gt;</li></ul>"), "一行目\n二行目\n\n・塩\n・A & B <x>");
});

test("textToParagraphsHtml と htmlToText は往復で本文を保つ", () => {
  const text = "【大目標】\n荷を取り返す\n\n【導入】\n鈴の破片 <壊れた>";
  assert.equal(htmlToText(textToParagraphsHtml(text)), text);
});

test("formatStageDraft: 地域の資源・歴史の層・確定事項を入力欄の文字列にする", () => {
  const d = formatStageDraft("region", {
    region_name: "白塩の野",
    naming_style: "柔らかい母音",
    public_lore: {
      terrain_and_climate: "塩の平原",
      natural_resources: [{ name: "岩塩", location: "南の干潟", usage: "保存食" }],
      history_layers: [{ era: "潮の時代", what_happened: "海が退いた", what_remains: "貝の崖" }],
    },
    dm_secrets: { hidden_threat: "墓" },
    canon: ["資源: 岩塩"],
    open_threads: ["墓は誰のものか"],
    design_notes: ["地形カード: 塩の平原"],
    consistency_notes: "GM 指示で時代を上書き",
  });
  assert.equal(d.name, "白塩の野");
  assert.ok(d.publicText.includes("・岩塩 (南の干潟): 保存食"));
  assert.ok(d.publicText.includes("・潮の時代: 海が退いた / 今も残るもの: 貝の崖"));
  assert.ok(d.dmText.includes("【命名の作法】\n柔らかい母音"));
  assert.ok(d.dmText.includes("整合性: GM 指示で時代を上書き"));
  assert.ok(!d.publicText.includes("墓"));
  assert.equal(d.canonText, "・資源: 岩塩");
  assert.equal(d.threadsText, "・墓は誰のものか");
});

test("formatStageDraft: 旅路のランドマークは DM 欄に 1 始まりの番号で並ぶ", () => {
  const d = formatStageDraft("wilderness", {
    wilderness_title: "塩の道",
    public_lore: { route_overview: "道", weather_table: ["雨", "風"] },
    landmarks: [{ id: 0, title: "出発の門", tts_narration: "風が鳴る", safe_rest: true }],
  });
  assert.ok(d.publicText.includes("・雨\n・風"));
  assert.ok(d.dmText.includes("・#1 出発の門 (野営可): 風が鳴る"));
});

test("formatStageDraft: 遺跡の中目標と大目標を分けて置く", () => {
  const d = formatStageDraft("site_lore", {
    site_name: "見張り塔",
    mission_archetype: "奪還",
    public_lore: { clear_main_goal: "荷を取り返す", sensory_ambience: { visual: "苔" } },
    dm_secrets: { sub_objectives: [{ title: "閂", target_topology: "dead_end", connection_to_town_or_pc: "鈴", effect: "裏口" }] },
  });
  assert.ok(d.publicText.startsWith("【大目標】\n荷を取り返す"));
  assert.ok(d.dmText.includes("1. 閂 (dead_end)"));
  assert.ok(!d.publicText.includes("閂"));
});

test("formatStageDraft は壊れた下書きでも落ちない", () => {
  assert.deepEqual(formatStageDraft("settlement", null), { name: "", publicText: "", dmText: "", canonText: "", threadsText: "" });
});

test("buildAtlasPages: DM と確定事項のページはプレイヤーに見せない", () => {
  const pages = buildAtlasPages(
    { publicText: "表", dmText: "裏", canon: ["資源: <塩>"], openThreads: ["墓"] },
    { ownership: { NONE: 0, OBSERVER: 2 }, html: 1 },
    "ai-gm-bridge",
  );
  assert.deepEqual(pages.map(p => p.flags["ai-gm-bridge"].atlas_page), ["public", "dm", "canon"]);
  assert.deepEqual(pages.map(p => p.ownership.default), [2, 0, 0]);
  assert.ok(pages[2].text.content.includes("<li>資源: &lt;塩&gt;</li>"));
});

test("atlasEntryContext は確定事項を flag から読み、ページは公開と DM に分ける", () => {
  const ctx = atlasEntryContext({
    name: "グレン",
    meta: { categoryKey: "settlement", canon: ["手仕事: 鈴"], open_threads: ["密売"] },
    pages: [
      { kind: "public", content: "<p>石の家</p>" },
      { kind: "dm", content: "<p>密売人</p>" },
      { kind: "canon", content: "<ul><li>手仕事: 鈴</li></ul>" },
      { kind: null, name: "古い DM ページ", content: "<p>旧</p>" },
    ],
  });
  assert.deepEqual(ctx, {
    category: "settlement", name: "グレン", public_lore: "石の家", dm_secrets: "密売人\n旧",
    canon: ["手仕事: 鈴"], open_threads: ["密売"],
  });
  assert.equal(atlasEntryContext(null), null);
});

test("atlasIndex は上位段階として渡すエントリを除く", () => {
  const entries = [
    { id: "a", name: "白塩の野", meta: { categoryKey: "region", canon: ["1", "2", "3", "4"] } },
    { id: "b", name: "グレン", meta: { categoryKey: "settlement" } },
  ];
  assert.deepEqual(atlasIndex(entries, ["b", null]), [{ category: "region", name: "白塩の野", canon: ["1", "2", "3"] }]);
});

test("recentCardIds は新しいエントリのカードから重複なく集める", () => {
  const entries = [
    { meta: { updatedAt: 1, cards: ["old"] } },
    { meta: { updatedAt: 3, cards: ["a", "b"] } },
    { meta: { updatedAt: 2, cards: ["b", "c"] } },
  ];
  assert.deepEqual(recentCardIds(entries, ["d"], 2), ["a", "b", "c", "d"]);
});

test("resolveParentIds は子の parent_ids から地域を補う", () => {
  const metas = {
    s1: { meta: { parent_ids: { region: "r1" } } },
    w1: { meta: { parent_ids: { region: "r2", settlement: "s2" } } },
  };
  const lookup = id => metas[id] ?? null;
  assert.deepEqual(resolveParentIds({ settlement: "s1", wilderness: null }, lookup), ["r1", "s1"]);
  assert.deepEqual(resolveParentIds({ wilderness: "w1" }, lookup), ["r2", "s2", "w1"]);
  assert.deepEqual(resolveParentIds({ region: "r9", settlement: "s1" }, lookup), ["r9", "s1"]);
  assert.deepEqual(resolveParentIds({}, lookup), []);
});

test("actorsContext は PC と NPC に分け、経歴の HTML を外す", () => {
  const ctx = actorsContext([
    { name: "アリア", type: "character", race: "エルフ", background: "学者", bio: "<p>塩の研究</p>" },
    { name: "エドガー", type: "npc", role: "職人", bio: "" },
  ]);
  assert.deepEqual(ctx.pcs, [{ name: "アリア", info: "エルフ 学者", bio: "塩の研究" }]);
  assert.deepEqual(ctx.npcs, [{ name: "エドガー", role: "職人", bio: "" }]);
});

test("formatStageDraft: 人柄が届かなかった NPC も undefined を出さない", () => {
  const d = formatStageDraft("settlement", {
    settlement_name: "塩見の里",
    public_lore: { key_npcs: [{ name: "エドガー", role: "職人頭", want: "道を取り戻す" }] },
  });
  assert.ok(d.publicText.includes("・エドガー (職人頭) / 望み: 道を取り戻す"));
  assert.ok(!d.publicText.includes("undefined"));
});

test("formatBuilderProgress: 工程・完了数・経過・残り秒を並べ、受信後の秒数だけ進める", () => {
  const p = { step: 2, steps: 2, step_label: "地点の描写", detail: "完了 1/2", elapsed: 40, remaining: 260 };
  // 1 行目は全体 (工程と全体の経過)、2 行目はいまの作業
  assert.equal(
    formatBuilderProgress(p, 3, "draft", 75),
    "⏳ AI が下書きを作成中です… 工程 2/2「地点の描写」 ・ 全体の経過 75秒\n└ 完了 1/2 ・ この工程の経過 43秒 ・ タイムアウトまで残り 257秒",
  );
  assert.equal(formatBuilderProgress({ step: 1, steps: 1, elapsed: 5, remaining: 295 }), "⏳ AI が下書きを作成中です…\n└ 経過 5秒 ・ タイムアウトまで残り 295秒");
  assert.equal(formatBuilderProgress(null, 0, "draft", 2), "⏳ AI が下書きを作成中です… 全体の経過 2秒");
  assert.ok(formatBuilderProgress({ ...p, remaining: 2 }, 5).endsWith("タイムアウト。延長するかチャットのカードで選んでください"));
  assert.equal(formatBuilderProgress(null), "⏳ AI が下書きを作成中です…");
});

test("formatBuilderProgress: シーン構築中は文言が変わり、自動延長なら確認を求めない", () => {
  assert.equal(formatBuilderProgress(null, 0, "build"), "⏳ シーンを構築中です…");
  const p = { kind: "build", step: 2, steps: 2, step_label: "区画の情景", elapsed: 10, remaining: 0 };
  const text = formatBuilderProgress(p);
  assert.ok(text.startsWith("⏳ シーンを構築中です… 工程 2/2「区画の情景」"));
  assert.ok(text.endsWith("延長するかチャットのカードで選んでください"));
  assert.ok(formatBuilderProgress({ ...p, auto_extend: true }).endsWith("タイムアウト。自動で延長します"));
});
