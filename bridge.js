Hooks.once('ready', async function() {
  console.log("AI GM Bridge | === チャット双方向モジュール ロード完了 ===");

  if (!game.user.isGM) return;

  let ws = null;

  function connect() {
    ws = new WebSocket('ws://127.0.0.1:8765');

    ws.onopen = () => {
      console.log("AI GM Bridge | WebSocket接続完了 (8765)");
      ui.notifications.info("AI GM Bridge: 接続成功（双方向チャット有効）");
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

  // FVTTのチャット投稿を検知してPythonへ転送するフック
  Hooks.on("createChatMessage", (message) => {
    // AI自身の発言やシステムダイス通知のループを防ぐ
    const alias = message.speaker?.alias || "";
    if (alias.startsWith("AI GM")) return;

    // コマンド（/r やマクロ等）は無視し、通常のプレイヤー発言のみ送信
    const content = message.content.trim();
    if (content.startsWith("/") || content.startsWith("[")) return;

    if (ws && ws.readyState === WebSocket.OPEN) {
      console.log("AI GM Bridge | プレイヤー発言を検知、送信中:", content);
      ws.send(JSON.stringify({
        event: "player_message",
        user: message.author?.name || "Player",
        content: content
      }));
    }
  });
});
