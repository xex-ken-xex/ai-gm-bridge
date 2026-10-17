"""環境変数と定数"""

import os

# FVTT の Data フォルダ (音声・マップ画像の出力先)
DATA_DIR = os.path.expanduser(os.environ.get("AI_GM_DATA_DIR", "~/foundrydata/Data"))

# Google Gemini
API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("AI_GM_GEMINI_MODEL", "gemini-3.8-flash")

# agy CLI の応答待ち上限 (秒)。超えたら打ち切り、呼び出し側のフォールバックに任せる
AGY_TIMEOUT = float(os.environ.get("AI_GM_AGY_TIMEOUT", "300"))
# Gemini API 1 回あたりの応答待ち上限 (秒)。超えたら agy へフォールバック
GEMINI_TIMEOUT = float(os.environ.get("AI_GM_GEMINI_TIMEOUT", "120"))
# ダンジョンの幾何生成と PNG 書き出しの待ち上限 (秒)
MAPGEN_TIMEOUT = float(os.environ.get("AI_GM_MAPGEN_TIMEOUT", "300"))
# 部屋の情景を分割して問い合わせるときの同時実行数
LLM_CONCURRENCY = max(1, int(os.environ.get("AI_GM_LLM_CONCURRENCY", "3")))

# WebSocket サーバー
HOST = os.environ.get("AI_GM_HOST", "0.0.0.0")
PORT = int(os.environ.get("AI_GM_PORT", "8765"))
# シーン構築ペイロード (壁・部屋情報) が大きくなるため受信上限を拡張
MAX_MESSAGE_SIZE = 15 * 1024 * 1024

# 音声
DEFAULT_VOICE = "ja-JP-NanamiNeural"

# 発言者名 (bridge.js のループ検知 `AI GM` / `サポートGM` 前方一致と揃えること)
SPEAKER_GM = "AI GM"
SPEAKER_GM_ROLL = "AI GM (判定)"
SPEAKER_SUPPORT = "サポートGM (agy)"
