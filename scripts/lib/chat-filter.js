// チャット発言をサーバーへ転送するかどうかの判定 (純粋関数・Foundry 非依存)

export const MODULE_ID = "ai-gm-bridge";

/** AI が投稿したメッセージに付けるフラグ (alias に頼らずループを防ぐ) */
export const GENERATED_FLAG = "generated";

/** ループ検知用: AI 由来の発言者名 */
export const AI_ALIAS_PREFIXES = ["AI GM", "サポートGM"];

export function isAiAlias(alias) {
  const a = alias || "";
  return AI_ALIAS_PREFIXES.some(prefix => a.startsWith(prefix));
}

/** ChatMessage.create に渡す flags (AI 生成マーク) */
export function generatedFlags() {
  return { [MODULE_ID]: { [GENERATED_FLAG]: true } };
}

export function isGeneratedMessage(flags) {
  return Boolean(flags?.[MODULE_ID]?.[GENERATED_FLAG]);
}

/**
 * 通常のプレイヤー発言として転送する本文を返す。転送しない場合は null。
 * - AI 自身の発言 (フラグまたは alias) は除外 (無限ループ防止)
 * - `/` や `[` で始まるコマンド、ダイスロールは除外
 */
export function playerMessageToForward({ alias, content, isRoll, flags }) {
  if (isGeneratedMessage(flags) || isAiAlias(alias)) return null;
  if (isRoll) return null;
  const clean = (content || "").replace(/<[^>]*>/g, "").trim();
  if (!clean) return null;
  if (clean.startsWith("/") || clean.startsWith("[")) return null;
  return clean;
}
