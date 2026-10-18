"""環境変数と定数"""

import os

# FVTT の Data フォルダ (音声・マップ画像の出力先)
DATA_DIR = os.path.expanduser(os.environ.get("AI_GM_DATA_DIR", "~/foundrydata/Data"))

# Google Gemini
API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("AI_GM_GEMINI_MODEL", "gemini-3.8-flash")

# agy CLI の応答待ち上限 (秒)。超えたら打ち切り、呼び出し側のフォールバックに任せる
AGY_TIMEOUT = float(os.environ.get("AI_GM_AGY_TIMEOUT", "300"))
# /ai のスキル判定 (agy) を待つ秒数。過ぎたらキーワード判定に切り替える (GM を長く待たせないため短め)
ROUTER_TIMEOUT = float(os.environ.get("AI_GM_ROUTER_TIMEOUT", "45"))
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
# 音声合成エンジン: edge (edge-tts) / irodori (Irodori-TTS-Server。繋がらないときは edge-tts に戻す)
TTS_ENGINE = os.environ.get("AI_GM_TTS_ENGINE", "edge").strip().lower()
# Irodori-TTS-Server (OpenAI 互換 API) の URL と設定
IRODORI_URL = os.environ.get("AI_GM_IRODORI_URL", "http://127.0.0.1:8088").strip().rstrip("/")
IRODORI_MODEL = os.environ.get("AI_GM_IRODORI_MODEL", "irodori-tts").strip()
# 参照音声の ID (サーバーの voices フォルダのファイル名)。none なら参照音声なしで、声の説明文 (VoiceDesign) だけで作る
IRODORI_VOICE = os.environ.get("AI_GM_IRODORI_VOICE", "none").strip() or "none"
# Irodori-TTS-Server を IRODORI_API_KEY 付きで起動したときだけ設定する
IRODORI_API_KEY = os.environ.get("AI_GM_IRODORI_API_KEY", "").strip()
# 1 回の音声生成の待ち上限 (秒)。描写のチャットは音声ができてから送るため、固まったサーバーで長く止まらないよう短めにする
IRODORI_TIMEOUT = float(os.environ.get("AI_GM_IRODORI_TIMEOUT", "20"))
# 最初の 1 回の待ち上限 (秒)。Irodori-TTS-Server は最初の音声生成でモデルを読み込むため時間がかかる
IRODORI_FIRST_TIMEOUT = float(os.environ.get("AI_GM_IRODORI_FIRST_TIMEOUT", "180"))
# 起動時に /health を確かめるときの待ち上限 (秒)
IRODORI_HEALTH_TIMEOUT = float(os.environ.get("AI_GM_IRODORI_HEALTH_TIMEOUT", "5"))
# Irodori に失敗したあと、edge-tts だけを使う時間 (秒)。毎回待たされないようにする
IRODORI_RETRY_AFTER = float(os.environ.get("AI_GM_IRODORI_RETRY_AFTER", "60"))
# 声の説明文 (VoiceDesign) の既定。edge-tts のボイス名ごとに使い分ける (ナレーションは Nanami)
IRODORI_CAPTION_FEMALE = os.environ.get(
    "AI_GM_IRODORI_CAPTION_FEMALE", "落ち着いた大人の女性の声。物語を語り聞かせるように、穏やかで聞き取りやすい話し方。"
).strip()
# ナレーターの声の説明文。空なら AI_GM_IRODORI_CAPTION_FEMALE と同じ (女性の NPC と分けたいときに設定する)
IRODORI_CAPTION_NARRATOR = os.environ.get("AI_GM_IRODORI_CAPTION_NARRATOR", "").strip() or IRODORI_CAPTION_FEMALE
# サポート GM (Geminiちゃん) の声の説明文。ナレーターの声を変えても Geminiちゃんの声は変わらないようにする
IRODORI_CAPTION_SUPPORT = os.environ.get(
    "AI_GM_IRODORI_CAPTION_SUPPORT", "明るく元気な若い女性の声。親しみやすく、はきはきとした話し方。"
).strip()
IRODORI_CAPTION_MALE = os.environ.get(
    "AI_GM_IRODORI_CAPTION_MALE", "落ち着いた大人の男性の声。低めで、はっきりと聞き取りやすい話し方。"
).strip()
# 声を作るときの乱数の種。同じ説明文でも作るたびに声が揺れないよう固定する (空にすると毎回変わる)。
# ナレーターはこの値、NPC はこの値とボイス名・声の高さ・説明文から決めた値を使う
_seed = os.environ.get("AI_GM_IRODORI_SEED", "1234").strip()
IRODORI_SEED = int(_seed) if _seed.lstrip("-").isdigit() else None
# ナレーターの参照音声の ID (Irodori-TTS-Server の voices フォルダに置いたファイル名)。空なら AI_GM_IRODORI_VOICE と説明文で作る
IRODORI_NARRATOR_VOICE = os.environ.get("AI_GM_IRODORI_NARRATOR_VOICE", "").strip()

# 発言者名 (bridge.js のループ検知 `AI GM` / `サポートGM` 前方一致と揃えること)
SPEAKER_GM = "AI GM"
SPEAKER_GM_ROLL = "AI GM (判定)"
SPEAKER_SUPPORT = "サポートGM Geminiちゃん"
