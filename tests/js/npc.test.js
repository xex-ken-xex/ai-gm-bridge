import { test } from "node:test";
import assert from "node:assert/strict";
import {
  brainForPrompt,
  brainFromForm,
  clampImportance,
  clarityLabel,
  decayMemories,
  DEFAULT_NPC_BRAIN,
  FORGET_BELOW,
  normalizeBrain,
  rememberDialogue,
  SHORT_TERM_LIMIT,
} from "../../scripts/lib/npc-brain.js";
import {
  checkFormula,
  checkResultContext,
  chooseRoute,
  isConversableNpc,
  selectListeners,
  skillKeyOf,
  speakerTokenId,
} from "../../scripts/lib/npc-listen.js";
import { buildNpcDialogueHtml, buildNpcGmNoteHtml } from "../../scripts/lib/cards.js";
import { isBrainCommand } from "../../scripts/lib/commands.js";

test("normalizeBrain は保存が無ければ伝記を表向きの知識にし、既定値を壊さない", () => {
  const brain = normalizeBrain(null, "<p>鍛冶屋の<b>親方</b></p>");
  assert.equal(brain.public_known, "鍛冶屋の 親方");
  brain.long_term_memory.push({ summary: "x" });
  assert.equal(DEFAULT_NPC_BRAIN.long_term_memory.length, 0);
});

test("normalizeBrain は範囲外の値を丸め、文の無い記憶を捨てる (L7)", () => {
  const brain = normalizeBrain({
    trust: 99, memory_retention: 5, voice: { name: "ja-JP-NanamiNeural" },
    short_term_memory: [{ summary: "" }, { summary: "挨拶", importance: "9" }],
    long_term_memory: [{ summary: "襲撃", importance: 0, clarity: 3 }],
  });
  assert.equal(brain.trust, 5);
  assert.equal(brain.memory_retention, 1);
  assert.equal(brain.voice.rate, "+0%");
  assert.deepEqual(brain.short_term_memory.map(m => m.importance), [5]);
  assert.deepEqual([brain.long_term_memory[0].importance, brain.long_term_memory[0].clarity], [1, 1]);
  assert.equal(clampImportance(undefined), 3);
});

test("rememberDialogue: 重要な記憶は長期記憶に 1 回だけ入る (M8)", () => {
  let brain = normalizeBrain({});
  brain = rememberDialogue(brain, { trustDelta: 2, memory: { summary: "命を救われた", importance: 5 }, now: 1 });
  assert.equal(brain.trust, 2);
  assert.equal(brain.long_term_memory.length, 1);
  for (let i = 0; i < SHORT_TERM_LIMIT + 2; i++) {
    brain = rememberDialogue(brain, { memory: { summary: `雑談${i}`, importance: 2 }, now: 10 + i });
  }
  assert.equal(brain.short_term_memory.length, SHORT_TERM_LIMIT);
  assert.equal(brain.long_term_memory.filter(m => m.summary === "命を救われた").length, 1);
  assert.deepEqual(brain.long_term_memory.map(m => m.summary), ["命を救われた", "雑談0", "雑談1"]);
  assert.equal(brain.long_term_memory[1].clarity, 0.9);
  assert.equal(rememberDialogue(brain, { trustDelta: -99 }).trust, -5);
});

test("decayMemories は丸めずに減らし、FORGET_BELOW 未満で忘れる。重要度 5 は忘れない (M9)", () => {
  let brain = normalizeBrain({
    memory_retention: 0.99,
    long_term_memory: [
      { summary: "些細", importance: 4, clarity: 0.5 },
      { summary: "一生の恩", importance: 5, clarity: 0.3 },
      { summary: "消えかけ", importance: 1, clarity: 0.16 },
    ],
  });
  brain = decayMemories(brain);
  const small = brain.long_term_memory.find(m => m.summary === "些細");
  assert.ok(small.clarity < 0.5 && small.clarity > 0.49);  // 0.01 単位に丸めると変わらない減り幅
  assert.equal(brain.long_term_memory.find(m => m.summary === "一生の恩").clarity, 1);
  brain = normalizeBrain({ memory_retention: 0.5, long_term_memory: [{ summary: "消えかけ", importance: 1, clarity: FORGET_BELOW }] });
  assert.equal(decayMemories(brain).long_term_memory.length, 0);
});

test("brainForPrompt は記憶に鮮明度の印を付ける", () => {
  const brain = normalizeBrain({ long_term_memory: [{ summary: "盗賊", importance: 4, clarity: 0.5 }], short_term_memory: [{ summary: "挨拶" }] });
  const p = brainForPrompt(brain);
  assert.deepEqual(p.long_term_memory, ["[うろ覚え] (重要度:4) 盗賊"]);
  assert.deepEqual(p.short_term_memory, ["挨拶"]);
  assert.equal(clarityLabel(0.85), "[鮮明]");
  assert.equal(clarityLabel(0.2), "[ほぼ忘却]");
});

test("brainFromForm は文が変わらない記憶の時刻を残し、空の行を消す (L6)", () => {
  const prev = normalizeBrain({ long_term_memory: [{ summary: "盗賊", importance: 4, clarity: 0.5, timestamp: 111 }, { summary: "古い", timestamp: 222 }] });
  const next = brainFromForm(prev, {
    trust: "3", memory_retention: "0.7", voice_name: "悪い名前", voice_pitch: "+5Hz",
    memories: [{ summary: "盗賊", importance: "4", clarity: "0.5" }, { summary: "古い話", importance: "2", clarity: "1" }, { summary: " " }],
  }, 999);
  assert.deepEqual(next.long_term_memory.map(m => m.timestamp), [111, 999]);
  assert.equal(next.trust, 3);
  assert.equal(next.voice.name, "ja-JP-KeitaNeural");
  assert.equal(next.voice.pitch, "+5Hz");
});

test("isConversableNpc はプレイヤー所有と隠れたトークンを除く (L4)", () => {
  assert.equal(isConversableNpc({ actorType: "npc" }), true);
  assert.equal(isConversableNpc({ actorType: "npc", hasPlayerOwner: true }), false);
  assert.equal(isConversableNpc({ actorType: "character" }), false);
  assert.equal(isConversableNpc({ actorType: "npc", hidden: true }), false);
  assert.equal(isConversableNpc({}), false);
});

test("selectListeners は近い順に選び、発言者自身・遠い NPC・遮られた NPC を除く (L8)", () => {
  const speaker = { id: "pc", x: 0, y: 0 };
  const npcs = [
    { id: "far", x: 1000, y: 0 },       // 50ft
    { id: "near", x: 100, y: 0 },       // 5ft
    { id: "mid", x: 300, y: 400 },      // 25ft
    { id: "wall", x: 200, y: 0 },
    { id: "pc", x: 0, y: 0 },
  ];
  const got = selectListeners(npcs, speaker, { blocked: npc => npc.id === "wall" });
  assert.deepEqual(got.map(l => [l.npc.id, l.distanceFt]), [["near", 5], ["mid", 25]]);
  assert.deepEqual(selectListeners(npcs, null), []);
  assert.equal(selectListeners(npcs, speaker, { radiusFt: 100, limit: 2 }).length, 2);
});

test("chooseRoute は NPC がいれば NPC 会話、いなければ AI GM の 1 経路 (H4)", () => {
  assert.equal(chooseRoute([{}]), "npc_dialogue_request");
  assert.equal(chooseRoute([]), "player_message");
});

test("speakerTokenId はメッセージの発言者を優先し、無ければユーザーのキャラクターのトークン (H2)", () => {
  const tokens = [{ id: "t1", actorId: "a1" }, { id: "t2", actorId: "a2" }];
  assert.equal(speakerTokenId({ speakerTokenId: "t2", characterId: "a1" }, tokens), "t2");
  assert.equal(speakerTokenId({ speakerTokenId: "他のシーン", characterId: "a1" }, tokens), "t1");
  assert.equal(speakerTokenId({}, tokens), null);
});

test("skillKeyOf と checkFormula", () => {
  assert.equal(skillKeyOf("説得"), "per");
  assert.equal(skillKeyOf("看破判定"), "ins");
  assert.equal(skillKeyOf("手先の早業"), "slt");
  assert.equal(skillKeyOf("ins"), "ins");
  assert.equal(skillKeyOf("謎の判定"), null);
  assert.equal(checkFormula("per", { skills: { per: { total: 5 } } }), "1d20 + @skills.per.total");
  assert.equal(checkFormula("per", {}), "1d20");
  assert.equal(checkFormula(null, { skills: {} }), "1d20");
});

test("checkResultContext は成功したときだけヒントを添える", () => {
  assert.equal(checkResultContext({ skill: "説得", dc: 13, total: 15, hint: "帳簿は樽の下" }),
    "説得判定 成功 (出目の合計 15 / 目標値 13)。打ち明けてよい内容: 帳簿は樽の下");
  assert.equal(checkResultContext({ skill: "説得", dc: 13, total: 9, hint: "帳簿は樽の下" }), "説得判定 失敗 (出目の合計 9 / 目標値 13)");
});

test("NPC 会話カードは AI の文をエスケープし、ヒントと内心を載せない (H6・H7)", () => {
  const res = {
    narrative_action: "<img src=x onerror=alert(1)>眉をひそめた",
    speech: "知らんな",
    internal_thought: "帳簿のことだ",
    ambient_reactions: [{ npc_name: "<b>ボルグ</b>", silent_emote: "目を逸らす" }],
    check_offer: { skill: "説得", dc: 14, target_secret_hint: "帳簿は樽の下" },
  };
  const html = buildNpcDialogueHtml(res, { offerId: "o1", actorId: "a1" });
  assert.ok(!html.includes("<img") && html.includes("&lt;img"));
  assert.ok(html.includes("&lt;b&gt;ボルグ"));
  assert.ok(!html.includes("樽の下") && !html.includes("帳簿のことだ"));
  assert.ok(html.includes('data-offer-id="o1"') && html.includes('data-skill="説得"') && html.includes("DC14"));
  assert.ok(!buildNpcDialogueHtml(res).includes("ai-npc-check-btn"));
  const note = buildNpcGmNoteHtml({ ...res, trust_delta: -1, physical_action: "turn_hostile" }, { npcName: "エドガー", trust: -2 });
  assert.ok(note.includes("樽の下") && note.includes("帳簿のことだ") && note.includes("敵対する") && note.includes("いま -2"));
  assert.equal(buildNpcGmNoteHtml({}), "");
});

test("isBrainCommand", () => {
  assert.ok(isBrainCommand("NPC"));
  assert.ok(isBrainCommand(" ブレイン "));
  assert.ok(!isBrainCommand("NPC と話す"));
});
