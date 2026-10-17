// /ai, /agy スラッシュコマンドの解析 (純粋関数・Foundry 非依存)

/** ChatLog.CHAT_COMMANDS 登録用の正規表現 (match[1] が引数部分) */
export const AI_COMMAND_RGX = /^\s*["'“”「]?\s*\/(?:ai|agy)(?:[\s　]+|&nbsp;|&#160;|[ 　]|$)([\s\S]*)$/i;

/** コマンド引数から HTML・特殊空白・末尾の引用符を除去 */
export function cleanCommandPrompt(rawPrompt) {
  return (rawPrompt || "")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&#160;/gi, " ")
    .replace(/[ 　]/g, " ")
    .trim()
    .replace(/["'“”」]+$/, "")
    .trim();
}

/**
 * コマンド解析ヘルパー (HTMLタグ・&nbsp;・全角スペース・引用符対応)
 * `/ai 指示` なら "指示"、`/ai` 単体なら ""、コマンドでなければ null を返す
 */
export function parseAiCommand(text) {
  if (!text) return null;
  const stripped = String(text)
    .replace(/<br\s*[\/]?>/gi, "\n")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&#160;/gi, " ")
    .replace(/[ 　]/g, " ")
    .trim()
    .replace(/^["'“”「]+|["'“”」]+$/g, "")
    .trim();

  const match = stripped.match(/^\/(?:ai|agy)(?:[\s　]+([\s\S]*))?$/i);
  if (match) {
    return (match[1] || "").trim();
  }
  return null;
}

/** `/ai NPC` など、NPC のブレイン管理画面を開く指示か (サーバーには送らない) */
export function isBrainCommand(prompt) {
  return /^(npc|brain|ブレイン|npcブレイン|npc管理|NPC ?ブレイン)$/i.test(String(prompt ?? "").trim());
}

/** `/ai ビルダー` など、シナリオビルダー画面を開く指示か (サーバーには送らない) */
export function isBuilderCommand(prompt) {
  return /^(gui|builder|ビルダー|シナリオ(ビルダー)?|シナリオビルド)$/i.test(String(prompt ?? "").trim());
}
