// チャットカードの HTML 組み立て (純粋関数)。差し込む値はすべてエスケープする

import { escapeHtml, textToHtml } from "./html.js";

/** interactive_card: 選択肢ボタン付きカード */
export function buildChoiceCardHtml({ title, message, choices }) {
  const buttonsHtml = (choices || []).map(c => `
    <button class="ai-gm-choice-btn" data-choice-value="${escapeHtml(c.value)}" data-choice-label="${escapeHtml(c.label)}"
            style="flex: 1 1 45%; min-width: 120px; padding: 6px 10px; margin: 2px; cursor: pointer; border-radius: 4px; border: 1px solid #782e22; background: #2f2f2f; color: #f0f0e0; font-size: 13px; font-family: inherit;">
      ${escapeHtml(c.label)}
    </button>
  `).join("");

  return `
  <div class="ai-gm-interactive-card" style="border: 2px solid #e67e22; border-radius: 6px; padding: 10px; background: rgba(20, 20, 25, 0.85); color: #f0f0e0; margin-top: 4px;">
    <div style="font-weight: bold; color: #f39c12; font-size: 14px; margin-bottom: 6px; display: flex; align-items: center; gap: 6px;">
      <span>🧙‍♂️</span> <span>${escapeHtml(title || "サポートGM (agy)")}</span>
    </div>
    <p style="margin: 0 0 10px 0; font-size: 13px; line-height: 1.4;">${escapeHtml(message || "選択してください：")}</p>
    <div class="ai-gm-choice-container" style="display: flex; flex-wrap: wrap; gap: 4px;">
      ${buttonsHtml}
    </div>
  </div>
  `;
}

/** シーン構築完了カード */
const SCENE_MODE_LABELS = { underground: "地下迷宮", ruins: "地上の廃墟", wilderness: "野外" };

export function buildSceneSuccessCardHtml({ sceneId, sceneName, data, sceneWidth, sceneHeight, wallCount, noteCount, lightCount = 0, soundCount = 0, journalId }) {
  const modeStr = `・舞台: ${SCENE_MODE_LABELS[data.scene_mode] ?? SCENE_MODE_LABELS.underground}<br>`;
  const ambienceStr = lightCount > 0 || soundCount > 0
    ? `・光源: ${Number(lightCount)} 箇所 / 環境音: ${Number(soundCount)} 箇所<br>`
    : "";
  const roomCountStr = data.room_count ? `・部屋数: ${Number(data.room_count)} 部屋<br>` : "";
  const noteCountStr = noteCount > 0 ? `・フレーバーノート: ${noteCount} 箇所 (全室にマップピン📌配置済)<br>` : "";
  const wallNote = wallCount === 0
    ? (data.scene_mode === "wilderness" ? " (野外のため壁なし)" : " ⚠️ マップが大きいため壁は省略されました")
    : " (視界・衝突ブロック)";
  const journalBtnHtml = journalId ? `
    <button class="ai-gm-journal-open-btn" data-journal-id="${escapeHtml(journalId)}"
            style="flex: 1; padding: 6px; background: #8e44ad; color: #fff; border: none; border-radius: 4px; cursor: pointer;">
      設定録を開く
    </button>
  ` : "";
  const pos = p => (Array.isArray(p) ? p.map(Number).join(", ") : "未設定");

  return `
  <div style="border: 2px solid #27ae60; border-radius: 6px; padding: 10px; background: rgba(20, 30, 25, 0.9); color: #ecf0f1;">
    <div style="font-weight: bold; color: #2ecc71; font-size: 14px; margin-bottom: 6px;">
      🏰 新シーン『${escapeHtml(sceneName)}』を生成しました！
    </div>
    <div style="font-size: 12px; margin-bottom: 8px; line-height: 1.4;">
      ${modeStr}・サイズ: ${Number(data.grid_width)}x${Number(data.grid_height)} マス (${sceneWidth}x${sceneHeight} px)<br>
      ${roomCountStr}・自動配置した壁: ${wallCount} 本${wallNote}<br>
      ${noteCountStr}${ambienceStr}・スタート地点 (緑光): (${pos(data.start_pos)})<br>
      ・最深部ゴール (赤光): (${pos(data.goal_pos)})
    </div>
    <div style="display: flex; gap: 6px;">
      <button class="ai-gm-scene-jump-btn" data-scene-id="${escapeHtml(sceneId)}"
              style="flex: 1; padding: 6px; background: #27ae60; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-weight: bold;">
        全員で移動
      </button>
      <button class="ai-gm-scene-view-btn" data-scene-id="${escapeHtml(sceneId)}"
              style="flex: 1; padding: 6px; background: #34495e; color: #fff; border: none; border-radius: 4px; cursor: pointer;">
        GMのみ確認
      </button>
      ${journalBtnHtml}
    </div>
  </div>
  `;
}

/** 部屋進入時の情景描写カード */
export function buildRoomNarrationHtml(room) {
  return `
  <div style="border-left: 4px solid #e67e22; padding: 6px 10px; background: rgba(30, 25, 20, 0.85); color: #f5f5dc; border-radius: 4px;">
    <div style="font-weight: bold; color: #f39c12; font-size: 14px; margin-bottom: 4px;">
      📍 ${escapeHtml(room.name)}
    </div>
    <p style="margin: 0; font-size: 13px; line-height: 1.5;">
      ${textToHtml(room.flavor)}
    </p>
  </div>
  `;
}

/**
 * NPC 会話の小説風カード (npc_dialogue_response)。全員に見えるため、内心と秘密のヒントは入れない (H7)。
 * 判定ボタンには、GM のクライアントだけが持つ提案の id (offerId) と、判定するキャラクターの actor id だけを載せる
 */
export function buildNpcDialogueHtml(res, { offerId = null, actorId = null } = {}) {
  const ambient = (res.ambient_reactions || []).filter(a => a?.silent_emote);
  const ambientHtml = ambient.length ? `
    <div style="margin-top: 8px; padding-top: 6px; border-top: 1px dashed #c9b99a; font-size: 12px; color: #555;">
      ${ambient.map(a => `<p style="margin: 2px 0;"><em>👀 <strong>${escapeHtml(a.npc_name)}:</strong> ${escapeHtml(a.silent_emote)}</em></p>`).join("")}
    </div>` : "";
  const offer = res.check_offer;
  const checkHtml = offer?.skill && offerId ? `
    <div style="margin-top: 10px; background: #2c2c34; padding: 6px 8px; border-radius: 4px; border-left: 3px solid #f39c12;">
      <div style="font-size: 11px; color: #f39c12; margin-bottom: 4px;">💡 態度の裏に何かがありそうだ…</div>
      <button type="button" class="ai-npc-check-btn" data-offer-id="${escapeHtml(offerId)}" data-actor-id="${escapeHtml(actorId ?? "")}" data-skill="${escapeHtml(offer.skill)}"
              style="width: 100%; background: #e67e22; color: #fff; border: none; padding: 5px; border-radius: 3px; cursor: pointer; font-weight: bold; font-size: 12px;">
        🎲 【${escapeHtml(offer.skill)} DC${Number(offer.dc) || 13}】に挑戦して本音を引き出す
      </button>
    </div>` : "";
  return `
  <div class="ai-novel-dialogue" style="font-family: 'Yu Mincho', 'Hiragino Mincho ProN', serif; line-height: 1.75; font-size: 13.5px; color: #1a1a1a; background: #fdfaf2; padding: 10px 12px; border-radius: 4px; border-left: 4px solid #782e22;">
    ${res.narrative_action ? `<p style="margin: 0 0 8px 0; text-indent: 1em; color: #333;">${textToHtml(res.narrative_action)}</p>` : ""}
    ${res.speech ? `<p style="margin: 0 0 6px 0; font-weight: bold; color: #5a1810; font-size: 14px;">「${escapeHtml(res.speech)}」</p>` : ""}
    ${ambientHtml}
    ${checkHtml}
  </div>`;
}

/** NPC 会話の GM 専用メモ (内心・信頼度の変化・判定の成功時に打ち明ける内容)。GM にだけウィスパーする */
export function buildNpcGmNoteHtml(res, { npcName = "NPC", trust = null } = {}) {
  const rows = [];
  if (res.internal_thought) rows.push(`<p style="margin: 2px 0;">💭 <strong>内心:</strong> ${escapeHtml(res.internal_thought)}</p>`);
  const delta = Number(res.trust_delta) || 0;
  if (delta || trust !== null) {
    rows.push(`<p style="margin: 2px 0;">🤝 <strong>信頼度:</strong> ${delta > 0 ? "+" : ""}${delta}${trust !== null ? ` (いま ${Number(trust)})` : ""}</p>`);
  }
  if (res.physical_action && res.physical_action !== "stay") {
    rows.push(`<p style="margin: 2px 0;">🚶 <strong>動きたがっている:</strong> ${escapeHtml(PHYSICAL_ACTION_LABELS[res.physical_action] ?? res.physical_action)}</p>`);
  }
  if (res.check_offer?.target_secret_hint) {
    rows.push(`<p style="margin: 2px 0;">🔑 <strong>判定に成功したら:</strong> ${escapeHtml(res.check_offer.target_secret_hint)}</p>`);
  }
  if (!rows.length) return "";
  return `
  <div class="ai-npc-gm-note" style="font-size: 12px; background: #fff8e7; border-left: 4px solid #f39c12; padding: 6px 10px; color: #7f5200; border-radius: 2px;">
    <div style="font-weight: bold; margin-bottom: 2px;">🔒 GM 専用: ${escapeHtml(npcName)}</div>
    ${rows.join("")}
  </div>`;
}

export const PHYSICAL_ACTION_LABELS = Object.freeze({
  approach_speaker: "話し手に近づく",
  step_back: "一歩下がる",
  leave_scene: "この場を立ち去る",
  turn_hostile: "敵対する",
});
