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
