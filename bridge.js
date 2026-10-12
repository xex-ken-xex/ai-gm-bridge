let ws = null;

function sendAiCommand(prompt) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    console.log("AI GM Bridge | GMの/aiコマンドを検知、送信中:", prompt);
    ui.notifications.info("サポートGM (agy) に指示を送信中...");
    ws.send(JSON.stringify({
      event: "ai_command",
      user: game.user.name || "GM",
      prompt: prompt
    }));
  } else {
    ui.notifications.warn("AI GM Bridge: Python サーバー (8765) に接続されていません。");
  }
}

// コマンド解析ヘルパー (HTMLタグ・&nbsp;・全角スペース・引用符対応)
function parseAiCommand(text) {
  if (!text) return null;
  const stripped = text
    .replace(/<br\s*[\/]?>/gi, "\n")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&#160;/gi, " ")
    .replace(/[\u00A0\u3000]/g, " ")
    .trim()
    .replace(/^["'“”「]+|["'“”」]+$/g, "")
    .trim();

  const match = stripped.match(/^\/(?:ai|agy)(?:[\s\u3000]+([\s\S]*))?$/i);
  if (match) {
    return (match[1] || "").trim();
  }
  return null;
}

// 1. FVTT スラッシュコマンド登録 (/ai, /agy)
function registerAiCommands() {
  if (typeof ChatLog !== "undefined" && ChatLog.CHAT_COMMANDS) {
    const aiCmd = {
      rgx: /^\s*["'“”「]?\s*\/(?:ai|agy)(?:[\s\u3000]+|&nbsp;|&#160;|[\u00A0\u3000]|$)([\s\S]*)$/i,
      isRoll: false,
      isMultiline: false,
      fn: function(command, match, chatData, createOptions) {
        if (game.user && !game.user.isGM) return false;
        const rawPrompt = match[1] || "";
        const prompt = rawPrompt
          .replace(/<[^>]*>/g, "")
          .replace(/&nbsp;/gi, " ")
          .replace(/&#160;/gi, " ")
          .replace(/[\u00A0\u3000]/g, " ")
          .trim()
          .replace(/["'“”」]+$/, "")
          .trim();
        sendAiCommand(prompt);
        return false; // メッセージ生成を防止
      }
    };
    ChatLog.CHAT_COMMANDS.ai = aiCmd;
    ChatLog.CHAT_COMMANDS.agy = aiCmd;
    console.log("AI GM Bridge | ChatLog.CHAT_COMMANDS (.ai, .agy) を登録しました");
  }
}

// モジュール評価時および init フックで即座にコマンド登録
registerAiCommands();
Hooks.once('init', registerAiCommands);

// FVTTのチャット入力直接インターセプト (/ai, /agy スラッシュコマンド)
Hooks.on("chatMessage", (chatLog, messageText, chatData) => {
  if (game.user && !game.user.isGM) return;

  const prompt = parseAiCommand(messageText);
  if (prompt !== null) {
    sendAiCommand(prompt);
    return false; // FVTT標準の「有効なコマンドではありません」エラーを防止し入力を消費
  }
});

Hooks.once('ready', async function() {
  console.log("AI GM Bridge | === チャット双方向 & サポートGM (agy) モジュール ロード完了 ===");

  if (!game.user.isGM) return;

  registerAiCommands();

  function connect() {
    ws = new WebSocket('ws://127.0.0.1:8765');

    ws.onopen = () => {
      console.log("AI GM Bridge | WebSocket接続完了 (8765)");
      ui.notifications.info("AI GM Bridge: 接続成功（サポートGM連携有効）");
    };

    ws.onmessage = async (event) => {
      try {
        const data = JSON.parse(event.data);
        console.log("AI GM Bridge | 受信:", data);

        // 1. 通常チャット投稿
        if (data.action === "chat") {
          await ChatMessage.create({
            speaker: { alias: data.speaker || "AI GM" },
            content: data.content
          });
        }

        // 2. ダイスロール
        if (data.action === "roll") {
          const roll = new Roll(data.formula);
          await roll.evaluate();
          await roll.toMessage({
            speaker: { alias: data.speaker || "AI GM (判定)" }
          });
        }

        // 3. 音声再生
        if (data.action === "play_sound") {
          if (game.audio?.context?.state === "suspended") {
            await game.audio.context.resume();
          }

          await foundry.audio.AudioHelper.play({
            src: data.src,
            volume: data.volume ?? 1.0,
            autoplay: true,
            loop: false
          }, false);
        }

        // 4. インタラクティブ選択カード (確認事項・パラメータ選択)
        if (data.action === "interactive_card") {
          const title = data.title || "サポートGM (agy)";
          const message = data.message || "選択してください：";
          const choices = data.choices || [];

          const buttonsHtml = choices.map(c => `
            <button class="ai-gm-choice-btn" data-choice-value="${c.value}" data-choice-label="${c.label}"
                    style="flex: 1 1 45%; min-width: 120px; padding: 6px 10px; margin: 2px; cursor: pointer; border-radius: 4px; border: 1px solid #782e22; background: #2f2f2f; color: #f0f0e0; font-size: 13px; font-family: inherit;">
              ${c.label}
            </button>
          `).join("");

          const cardHtml = `
          <div class="ai-gm-interactive-card" style="border: 2px solid #e67e22; border-radius: 6px; padding: 10px; background: rgba(20, 20, 25, 0.85); color: #f0f0e0; margin-top: 4px;">
            <div style="font-weight: bold; color: #f39c12; font-size: 14px; margin-bottom: 6px; display: flex; align-items: center; gap: 6px;">
              <span>🧙‍♂️</span> <span>${title}</span>
            </div>
            <p style="margin: 0 0 10px 0; font-size: 13px; line-height: 1.4;">${message}</p>
            <div class="ai-gm-choice-container" style="display: flex; flex-wrap: wrap; gap: 4px;">
              ${buttonsHtml}
            </div>
          </div>
          `;

          await ChatMessage.create({
            speaker: { alias: data.speaker || "サポートGM (agy)" },
            content: cardHtml,
            whisper: [game.user.id] // GM本人のみにウィスパー表示
          });
        }

        // 5. ダンジョンシーン自動構築
        if (data.action === "create_dungeon_scene") {
          ui.notifications.info("AI GM Bridge: ダンジョンシーンを構築中...");
          const gridSize = data.grid_size || 100;
          const sceneWidth = data.grid_width * gridSize;
          const sceneHeight = data.grid_height * gridSize;

          const sceneData = {
            name: data.name || "地下迷宮",
            width: sceneWidth,
            height: sceneHeight,
            padding: 0,
            background: {
              src: data.background_src
            },
            initialLevel: "defaultLevel0000",
            levels: [
              {
                _id: "defaultLevel0000",
                name: "地下1階",
                background: {
                  src: data.background_src
                }
              }
            ],
            grid: {
              size: gridSize,
              distance: 5,
              units: "ft"
            },
            darkness: 0.75,
            globalLight: { enabled: false },
            navigation: true
          };

          const newScene = await Scene.create(sceneData);
          console.log("AI GM Bridge | ダンジョンシーン作成完了 ID:", newScene.id);

          // シーン描画原点オフセット (sceneX, sceneY) を取得 (padding や shiftX/Y に完全追従)
          const dims = newScene.dimensions ?? (newScene.getDimensions ? newScene.getDimensions() : {});
          const offsetX = dims.sceneX ?? 0;
          const offsetY = dims.sceneY ?? 0;
          console.log(`AI GM Bridge | シーン描画原点オフセット: (${offsetX}, ${offsetY})`);

          // v14 レベル背景画像の明示的確認・更新 (確実な描画反映)
          const firstLevel = newScene.levels?.get("defaultLevel0000") || newScene.levels?.contents?.[0];
          if (firstLevel && (!firstLevel.background?.src || firstLevel.background.src !== data.background_src)) {
            console.log("AI GM Bridge | レベル背景画像更新中:", data.background_src);
            await newScene.updateEmbeddedDocuments("Level", [{
              _id: firstLevel.id,
              background: { src: data.background_src }
            }]);
          }

          // 壁ドキュメントの配置 (シーンのsceneX, sceneYオフセットを正確に加算して背景画像と完全同期)
          let actualWalls = 0;
          if (data.walls && data.walls.length > 0) {
            console.log(`AI GM Bridge | 壁ドキュメントを配置中 (${data.walls.length} 本, offset: [${offsetX}, ${offsetY}])...`);
            const wallDocs = data.walls.map(w => ({
              c: [w[0] + offsetX, w[1] + offsetY, w[2] + offsetX, w[3] + offsetY],
              move: CONST.WALL_MOVEMENT_TYPES?.NORMAL ?? 20,
              sight: CONST.EDGE_SENSE_TYPES?.NORMAL ?? 20,
              light: CONST.EDGE_SENSE_TYPES?.NORMAL ?? 20,
              sound: CONST.EDGE_SENSE_TYPES?.NORMAL ?? 20
            }));
            await newScene.createEmbeddedDocuments("Wall", wallDocs);
            actualWalls = wallDocs.length;
            console.log("AI GM Bridge | 壁配置完了！");
          }

          // スタート地点とゴール地点に光源を配置 (sceneX, sceneYオフセットを加算)
          const lights = [];
          if (data.start_pos) {
            lights.push({
              x: data.start_pos[0] * gridSize + gridSize / 2 + offsetX,
              y: data.start_pos[1] * gridSize + gridSize / 2 + offsetY,
              config: {
                bright: 20,
                dim: 40,
                color: "#2ecc71",
                alpha: 0.45,
                animation: { type: "pulse", speed: 3, intensity: 3 }
              }
            });
          }
          if (data.goal_pos) {
            lights.push({
              x: data.goal_pos[0] * gridSize + gridSize / 2 + offsetX,
              y: data.goal_pos[1] * gridSize + gridSize / 2 + offsetY,
              config: {
                bright: 20,
                dim: 40,
                color: "#e74c3c",
                alpha: 0.45,
                animation: { type: "torch", speed: 4, intensity: 4 }
              }
            });
          }
          if (lights.length > 0) {
            await newScene.createEmbeddedDocuments("AmbientLight", lights);
          }

          // 完了通知カード
          const wallCount = newScene.walls?.size ?? actualWalls;
          const roomCountStr = data.room_count ? `・部屋数: ${data.room_count} 部屋<br>` : "";
          const successCard = `
          <div style="border: 2px solid #27ae60; border-radius: 6px; padding: 10px; background: rgba(20, 30, 25, 0.9); color: #ecf0f1;">
            <div style="font-weight: bold; color: #2ecc71; font-size: 14px; margin-bottom: 6px;">
              🏰 新シーン『${newScene.name}』を生成しました！
            </div>
            <div style="font-size: 12px; margin-bottom: 8px; line-height: 1.4;">
              ・サイズ: ${data.grid_width}x${data.grid_height} マス (${sceneWidth}x${sceneHeight} px)<br>
              ${roomCountStr}・自動配置した壁: ${wallCount} 本 (視界・衝突ブロック)<br>
              ・スタート地点 (緑光): (${data.start_pos ? data.start_pos.join(", ") : "未設定"})<br>
              ・最深部ゴール (赤光): (${data.goal_pos ? data.goal_pos.join(", ") : "未設定"})
            </div>
            <div style="display: flex; gap: 6px;">
              <button class="ai-gm-scene-jump-btn" data-scene-id="${newScene.id}"
                      style="flex: 1; padding: 6px; background: #27ae60; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-weight: bold;">
                全員で移動
              </button>
              <button class="ai-gm-scene-view-btn" data-scene-id="${newScene.id}"
                      style="flex: 1; padding: 6px; background: #34495e; color: #fff; border: none; border-radius: 4px; cursor: pointer;">
                GMのみ確認
              </button>
            </div>
          </div>
          `;

          await ChatMessage.create({
            speaker: { alias: "サポートGM (agy)" },
            content: successCard,
            whisper: [game.user.id]
          });
        }

      } catch (err) {
        console.error("AI GM Bridge | エラー:", err);
      }
    };

    ws.onclose = () => {
      setTimeout(connect, 5000);
    };

    ws.onerror = (err) => {};
  }

  connect();

  // チャット内の選択ボタンやシーン移動ボタンのクリックイベント監視
  Hooks.on("renderChatMessage", (message, html) => {
    if (!game.user.isGM) return;

    // DOM要素の取得 (jQuery / HTMLElement 両対応)
    const root = (html instanceof HTMLElement) ? html : (html[0] || html);
    if (!root || !root.querySelectorAll) return;

    // 1. 選択肢ボタンのクリック処理
    root.querySelectorAll(".ai-gm-choice-btn").forEach(btn => {
      btn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        const value = btn.getAttribute("data-choice-value");
        const label = btn.getAttribute("data-choice-label") || btn.textContent.trim();

        // 兄弟ボタンの無効化
        const container = btn.closest(".ai-gm-choice-container");
        if (container) {
          container.querySelectorAll(".ai-gm-choice-btn").forEach(b => {
            b.disabled = true;
            b.style.opacity = "0.5";
          });
        }
        btn.style.borderColor = "#2ecc71";
        btn.style.fontWeight = "bold";
        btn.style.opacity = "1.0";

        ui.notifications.info(`サポートGMへ回答: ${label}`);

        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            event: "ai_choice",
            user: game.user.name,
            value: value,
            label: label
          }));
        }
      });
    });

    // 2. シーン移動 (activate) ボタン
    root.querySelectorAll(".ai-gm-scene-jump-btn").forEach(btn => {
      btn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        const sceneId = btn.getAttribute("data-scene-id");
        const targetScene = game.scenes.get(sceneId);
        if (targetScene) {
          await targetScene.activate();
          ui.notifications.info(`シーン「${targetScene.name}」へ移動しました`);
        }
      });
    });

    // 3. シーン確認 (view) ボタン
    root.querySelectorAll(".ai-gm-scene-view-btn").forEach(btn => {
      btn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        const sceneId = btn.getAttribute("data-scene-id");
        const targetScene = game.scenes.get(sceneId);
        if (targetScene) {
          await targetScene.view();
          ui.notifications.info(`シーン「${targetScene.name}」をGM画面に表示しました`);
        }
      });
    });
  });

  // FVTTのチャット投稿を検知してPythonへ転送するフック (通常発言)
  Hooks.on("createChatMessage", (message) => {
    if (!game.user.isGM) return;

    const alias = message.speaker?.alias || "";
    if (alias.startsWith("AI GM") || alias.startsWith("サポートGM")) return;

    const rawContent = message.content || "";
    const prompt = parseAiCommand(rawContent);
    if (prompt !== null) {
      // 万が一チャットメッセージとしてすり抜けて保存された場合のフォールバック
      try { message.delete(); } catch(e) {}
      sendAiCommand(prompt);
      return;
    }

    // HTMLタグを除去したプレーンテキストで判定
    const cleanContent = rawContent.replace(/<[^>]*>/g, "").trim();
    if (!cleanContent) return;

    // コマンド（/ や [ で始まるもの）やダイスロールは無視し、通常のプレイヤー発言のみ送信
    if (cleanContent.startsWith("/") || cleanContent.startsWith("[") || message.isRoll) return;

    if (ws && ws.readyState === WebSocket.OPEN) {
      console.log("AI GM Bridge | プレイヤー発言を検知、送信中:", cleanContent);
      ws.send(JSON.stringify({
        event: "player_message",
        user: message.author?.name || "Player",
        content: cleanContent
      }));
    }
  });
});
