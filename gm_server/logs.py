"""
詳細ログ (ログファイル) とコンソール表示の日時・レベル付け

- ログは ~/ai-gm-logs/<YYYY-MM-DD>/server.log に書く (AI_GM_LOG_DIR で変更可)。日付が変わると新しいフォルダに切り替え、
  LOG_RETENTION_DAYS 日より古い日付フォルダは削除する。1 日分が LOG_MAX_BYTES を超えたら server.log.1 … に回す
- AI への問い合わせと応答 (プロンプト全文・応答全文・所要時間) は ai_request / ai_response でログファイルにだけ書く
- コンソールの出力 (print / traceback) は 1 行ずつ「日時 レベル」を付けて表示し、ログファイルにも同じ行を書く
- API キーなどの秘密は、ログファイルにもコンソールにも出さない (mask_secrets)
"""

import datetime
import itertools
import logging
import logging.handlers
import os
import re
import shutil
import sys
import threading
from typing import Callable, Iterable, List, Optional, TextIO

from gm_server import config

LOGGER_NAME = "ai_gm"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_FILE_NAME = "server.log"
_DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")

logger = logging.getLogger(LOGGER_NAME)
logger.setLevel(logging.DEBUG)
logger.propagate = False
# setup_logging を呼ぶまで (テストなど) はどこにも書かない
logger.addHandler(logging.NullHandler())
ai_logger = logger.getChild("ai")
console_logger = logger.getChild("console")


# -------------------------------------------------------------------------
# 秘密の伏せ字
# -------------------------------------------------------------------------
_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),  # Google API キー
    re.compile(r"(?i)\b(key|api_key|apikey|access_token|token)=[^&\s\"']+"),
    re.compile(r"(?i)\b(x-goog-api-key|authorization)(\"?\s*[:=]\s*\"?)(bearer\s+)?[^\s\",]+"),
]
MASK = "***"


def secret_values(environ=None) -> List[str]:
    """伏せ字にする値 (GEMINI_API_KEY と、名前に KEY / TOKEN / SECRET / PASSWORD を含む環境変数)"""
    environ = os.environ if environ is None else environ
    values = {config.API_KEY} if config.API_KEY else set()
    values |= {v for k, v in environ.items() if _SECRET_ENV.search(k) and v}
    # 短すぎる値は普通の文字列まで伏せてしまうので対象外
    return sorted((v for v in values if len(v) >= 8), key=len, reverse=True)


def mask_secrets(text: str, secrets: Optional[Iterable[str]] = None) -> str:
    for value in secret_values() if secrets is None else secrets:
        text = text.replace(value, MASK)
    text = _SECRET_PATTERNS[0].sub(MASK, text)
    text = _SECRET_PATTERNS[1].sub(lambda m: f"{m.group(1)}={MASK}", text)
    text = _SECRET_PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    return text


class MaskingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return mask_secrets(super().format(record))


def make_formatter() -> logging.Formatter:
    return MaskingFormatter("%(asctime)s %(levelname)-7s %(message)s", datefmt=DATE_FORMAT)


# -------------------------------------------------------------------------
# 日付フォルダごとのログファイル
# -------------------------------------------------------------------------
def prune_old_days(log_dir: str, today: datetime.date, keep_days: int) -> List[str]:
    """today を含めて keep_days 日より古い日付フォルダを消す。消したフォルダ名を返す"""
    oldest = today - datetime.timedelta(days=max(1, keep_days) - 1)
    removed = []
    if not os.path.isdir(log_dir):
        return removed
    for name in sorted(os.listdir(log_dir)):
        path = os.path.join(log_dir, name)
        if not _DAY_DIR.match(name) or not os.path.isdir(path):
            continue
        try:
            day = datetime.date.fromisoformat(name)
        except ValueError:
            continue
        if day < oldest:
            shutil.rmtree(path, ignore_errors=True)
            removed.append(name)
    return removed


class DailyFolderHandler(logging.Handler):
    """log_dir/<日付>/server.log に書く。日付が変わったらフォルダを切り替え、古い日付フォルダを消す"""

    def __init__(
        self,
        log_dir: str,
        keep_days: int = 10,
        max_bytes: int = 20 * 1024 * 1024,
        backup_count: int = 20,
        today: Callable[[], datetime.date] = datetime.date.today,
    ):
        super().__init__()
        self.log_dir = log_dir
        self.keep_days = keep_days
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.today = today
        self.day: Optional[datetime.date] = None
        self.inner: Optional[logging.Handler] = None

    def current_path(self) -> str:
        return os.path.join(self.log_dir, self.day.isoformat(), LOG_FILE_NAME)

    def _switch(self, day: datetime.date) -> None:
        if self.inner is not None:
            self.inner.close()
        self.day = day
        os.makedirs(os.path.dirname(self.current_path()), exist_ok=True)
        self.inner = logging.handlers.RotatingFileHandler(
            self.current_path(), maxBytes=self.max_bytes, backupCount=self.backup_count, encoding="utf-8",
        )
        self.inner.setFormatter(self.formatter)
        prune_old_days(self.log_dir, day, self.keep_days)

    def setFormatter(self, fmt) -> None:
        super().setFormatter(fmt)
        if self.inner is not None:
            self.inner.setFormatter(fmt)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = self.today()
            if day != self.day:
                self._switch(day)
            self.inner.emit(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self.inner is not None:
            self.inner.close()
        super().close()


# -------------------------------------------------------------------------
# コンソールの日時・レベル付け
# -------------------------------------------------------------------------
def line_level(line: str, default: int = logging.INFO) -> int:
    """print された 1 行のレベル (文面から判断する)"""
    if default >= logging.ERROR or re.search(r"エラー|Traceback|Error|Exception", line):
        return logging.ERROR
    if re.search(r"⚠|警告|失敗|打ち切|見つかりません|無視", line):
        return logging.WARNING
    return default


def console_prefix(level: int, now: Optional[datetime.datetime] = None) -> str:
    now = now or datetime.datetime.now()
    return f"{now.strftime(DATE_FORMAT)} {logging.getLevelName(level):<7} "


class ConsoleStream:
    """
    sys.stdout / sys.stderr の代わり。1 行ずつ先頭に「日時 レベル」を付けて元のストリームへ出し、ログファイルにも書く。
    progress の上書き行 (\\r で始まり改行しない行) は flush のたびに日時を付け直して表示し、
    改行で確定したときだけログファイルに書く。
    """

    def __init__(self, raw: TextIO, default_level: int = logging.INFO,
                 now: Callable[[], datetime.datetime] = datetime.datetime.now):
        self.raw = raw
        self.default_level = default_level
        self.now = now
        self.buf = ""
        self.live = False
        self.lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def isatty(self) -> bool:
        return hasattr(self.raw, "isatty") and self.raw.isatty()

    def _emit_line(self, line: str) -> None:
        text = mask_secrets(line.rstrip())
        if not text:
            self.raw.write("\n")
            return
        level = line_level(text, self.default_level)
        self.raw.write(("\r" if self.live else "") + console_prefix(level, self.now()) + mask_secrets(line) + "\n")
        console_logger.log(level, text)

    def write(self, s: str) -> int:
        with self.lock:
            for part in re.split(r"(\r|\n)", s):
                if part == "\n":
                    self._emit_line(self.buf)
                    self.buf, self.live = "", False
                elif part == "\r":
                    self.buf, self.live = "", True
                else:
                    self.buf += part
        return len(s)

    def flush(self) -> None:
        with self.lock:
            if self.live and self.buf:
                self.raw.write("\r" + console_prefix(logging.INFO, self.now()) + mask_secrets(self.buf))
        self.raw.flush()


# 送受信メッセージをログに書くときの上限 (シーン構築の壁データなどは長いため)
MESSAGE_LOG_LIMIT = 4000


def clip_for_log(text, limit: int = MESSAGE_LOG_LIMIT) -> str:
    text = text if isinstance(text, str) else str(text)
    return text if len(text) <= limit else f"{text[:limit]}…(全 {len(text)} 文字)"


# -------------------------------------------------------------------------
# AI への問い合わせと応答
# -------------------------------------------------------------------------
_request_ids = itertools.count(1)


def ai_request(engine: str, prompt: str, **meta) -> int:
    """問い合わせをログファイルに書き、応答と突き合わせる番号を返す"""
    rid = next(_request_ids)
    info = " ".join(f"{k}={v}" for k, v in meta.items() if v not in (None, ""))
    ai_logger.info(f"[AI #{rid}] → {engine} 問い合わせ ({len(prompt)}文字) {info}\n----- プロンプト -----\n{prompt}\n----------")
    return rid


def ai_response(rid: int, engine: str, text: str, elapsed: float, **meta) -> None:
    info = " ".join(f"{k}={v}" for k, v in meta.items() if v not in (None, ""))
    ai_logger.info(
        f"[AI #{rid}] ← {engine} 応答 ({elapsed:.1f}秒, {len(text or '')}文字) {info}\n----- 応答 -----\n{text}\n----------"
    )


def ai_error(rid: int, engine: str, error, elapsed: float) -> None:
    ai_logger.warning(f"[AI #{rid}] × {engine} 失敗 ({elapsed:.1f}秒): {error}")


def ai_note(rid: int, message: str, level: int = logging.INFO) -> None:
    ai_logger.log(level, f"[AI #{rid}] {message}")


# -------------------------------------------------------------------------
# 起動時の設定
# -------------------------------------------------------------------------
def setup_logging(log_dir: Optional[str] = None, keep_days: Optional[int] = None,
                  wrap_console: bool = True) -> DailyFolderHandler:
    """ログファイルを開き、コンソールに日時・レベルを付ける。server 起動時に 1 回呼ぶ"""
    handler = DailyFolderHandler(log_dir or config.LOG_DIR, keep_days=keep_days or config.LOG_RETENTION_DAYS,
                                 max_bytes=config.LOG_MAX_BYTES)
    handler.setFormatter(make_formatter())
    for old in [h for h in logger.handlers if isinstance(h, DailyFolderHandler)]:
        logger.removeHandler(old)
        old.close()
    logger.addHandler(handler)
    if wrap_console and not isinstance(sys.stdout, ConsoleStream):
        sys.stdout = ConsoleStream(sys.stdout)
        sys.stderr = ConsoleStream(sys.stderr, default_level=logging.ERROR)
    logger.info(f"ログ出力先: {os.path.abspath(handler.log_dir)} ({handler.keep_days} 日分を保持)")
    return handler
