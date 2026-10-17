"""環境変数と定数"""

import os

# FVTT の Data フォルダ (音声・マップ画像の出力先)
DATA_DIR = os.path.expanduser(os.environ.get("AI_GM_DATA_DIR", "~/foundrydata/Data"))

# Google Gemini
API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("AI_GM_GEMINI_MODEL", "gemini-3.8-flash")

# agy CLI の応答待ち上限 (秒)。超えたら打ち切り、呼び出し側のフォールバックに任せる
AGY_TIMEOUT = float(os.environ.get("AI_GM_AGY_TIMEOUT", "300"))
# agy に渡すモデル (`agy models` で一覧。既定 gemini-3.8-flash-medium、空文字なら agy 自身の既定)・推論の深さ (low|medium|high|xhigh|max)
AGY_MODEL = os.environ.get("AI_GM_AGY_MODEL", "gemini-3.8-flash-medium").strip()
AGY_EFFORT = os.environ.get("AI_GM_AGY_EFFORT", "").strip()
# agy を同時に何本まで動かすか。agy は同時に複数起動すると応答が返らなくなることがあるため既定は 1 (順番に実行)
AGY_CONCURRENCY = max(1, int(os.environ.get("AI_GM_AGY_CONCURRENCY", "1")))
# agy 自身の動作ログ (--log-file) を詳細ログのフォルダに残すか (止まったときの調査用)
AGY_LOG = os.environ.get("AI_GM_AGY_LOG", "1") not in ("0", "false", "no", "")
# Gemini API 1 回あたりの応答待ち上限 (秒)。超えたら agy へフォールバック
GEMINI_TIMEOUT = float(os.environ.get("AI_GM_GEMINI_TIMEOUT", "120"))
# ダンジョンの幾何生成と PNG 書き出しの待ち上限 (秒)
MAPGEN_TIMEOUT = float(os.environ.get("AI_GM_MAPGEN_TIMEOUT", "300"))
# 部屋の情景を分割して問い合わせるときの同時実行数 (Gemini のとき。agy は AGY_CONCURRENCY)
LLM_CONCURRENCY = max(1, int(os.environ.get("AI_GM_LLM_CONCURRENCY", "3")))

# WebSocket サーバー
HOST = os.environ.get("AI_GM_HOST", "0.0.0.0")
PORT = int(os.environ.get("AI_GM_PORT", "8765"))
# シーン構築ペイロード (壁・部屋情報) が大きくなるため受信上限を拡張
MAX_MESSAGE_SIZE = 15 * 1024 * 1024

# 詳細ログ (<日付>/server.log)。FVTT はモジュールフォルダを Web で配信するため、既定は Data フォルダの外
LOG_DIR = os.path.expanduser(os.environ.get("AI_GM_LOG_DIR", "~/ai-gm-logs"))
# 残す日数 (今日を含む)。これより古い日付フォルダは削除する
LOG_RETENTION_DAYS = max(1, int(os.environ.get("AI_GM_LOG_DAYS", "10")))
# agy を動かすフォルダ。agy はエージェントなので、起動したフォルダ (このモジュールのソース) を
# 読み回って時間を使うことがある。中身の無い専用フォルダで動かす
AGY_WORKDIR = os.path.expanduser(os.environ.get("AI_GM_AGY_WORKDIR", os.path.join(LOG_DIR, "agy-workspace")))
# 1 日分のログファイルの上限。超えたら server.log.1 … に回す
LOG_MAX_BYTES = int(os.environ.get("AI_GM_LOG_MAX_BYTES", str(20 * 1024 * 1024)))

# 音声
DEFAULT_VOICE = "ja-JP-NanamiNeural"

# 発言者名 (bridge.js のループ検知 `AI GM` / `サポートGM` 前方一致と揃えること)
SPEAKER_GM = "AI GM"
SPEAKER_GM_ROLL = "AI GM (判定)"
SPEAKER_SUPPORT = "サポートGM アギィ"
