import os
import re
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
import platform

LOG_RETENTION_DAYS = 3  # previous days' files kept alongside today's

def get_log_directory():
    """Get the appropriate log directory based on the operating system.

    Honors BRIEFKORB_LOG_DIR ahead of the OS-default location -- tests set
    this so the eager `setup_logger(...)` calls that run at import time in
    several email_server modules never create/write real user log files.
    """
    override = os.getenv('BRIEFKORB_LOG_DIR')
    if override:
        log_dir = Path(override)
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    system = platform.system().lower()

    if system == 'windows':
        # Use AppData\Local for Windows
        appdata = os.getenv('LOCALAPPDATA')
        if not appdata:
            appdata = os.path.expanduser('~\\AppData\\Local')
        log_dir = Path(appdata) / 'email_server' / 'logs'
    else:
        # Use ~/.local/share for Linux/Mac
        log_dir = Path.home() / '.local' / 'share' / 'email_server' / 'logs'

    # Create directory if it doesn't exist
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir

class DailyFileHandler(logging.FileHandler):
    """Appends to ``<stem>.<YYYY-MM-DD><suffix>`` and opens a new file when a
    record's date passes the current file's.

    Files are never renamed: on Windows a rename fails while any other handle
    on the file is open, and the desktop app and its Django subprocess both
    log to the same directory. Both processes can append to the same day's file.
    """

    def __init__(self, log_dir, log_file, retention_days=LOG_RETENTION_DAYS):
        self._log_dir = Path(log_dir)
        self._stem, self._suffix = Path(log_file).stem, Path(log_file).suffix
        self._retention_days = retention_days
        self._date = date.today()
        self._dated_name = re.compile(
            rf"^{re.escape(self._stem)}\.(\d{{4}}-\d{{2}}-\d{{2}}){re.escape(self._suffix)}$"
        )
        super().__init__(self._path_for(self._date), encoding='utf-8', delay=True)
        self._prune()

    def _path_for(self, day: date) -> Path:
        return self._log_dir / f"{self._stem}.{day.isoformat()}{self._suffix}"

    def emit(self, record):
        record_date = datetime.fromtimestamp(record.created).date()
        if record_date > self._date:
            if self.stream:
                self.stream.close()
                self.stream = None
            self._date = record_date
            self.baseFilename = os.path.abspath(self._path_for(record_date))
            self._prune()
        super().emit(record)  # reopens self.stream on baseFilename when None

    def _prune(self):
        """Delete dated files older than the retention window. Failures are
        ignored: another process may be pruning or holding the same file, and
        pruning runs again at the next date change or startup."""
        cutoff = self._date - timedelta(days=self._retention_days)
        try:
            names = os.listdir(self._log_dir)
        except OSError:
            return
        for name in names:
            m = self._dated_name.match(name)
            if not m:
                continue
            try:
                if date.fromisoformat(m.group(1)) < cutoff:
                    os.remove(self._log_dir / name)
            except (ValueError, OSError):
                pass

# One handler set per (log dir, log file), shared by every logger that
# setup_logger configures -- separate handlers would each hold their own
# handle on the same file.
_shared_handlers: dict[tuple[str, str], list[logging.Handler]] = {}

def _get_shared_handlers(log_file):
    log_dir = get_log_directory()
    key = (str(log_dir), log_file)
    handlers = _shared_handlers.get(key)
    if handlers is None:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler = DailyFileHandler(log_dir, log_file)
        console_handler = logging.StreamHandler()
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        handlers = _shared_handlers[key] = [file_handler, console_handler]
    return handlers

def setup_logger(name, log_file='email_server.log'):
    """Set up a logger writing to a daily log file and the console.

    Args:
        name: Name of the logger
        log_file: Base name of the log file; the date is inserted before the
            extension (email_server.log -> email_server.2026-01-31.log)

    Returns:
        logging.Logger: Configured logger instance
    """
    logger = logging.getLogger(name)

    # Don't add handlers if they already exist (prevent duplicate handlers)
    if logger.handlers:
        return logger

    # Prevent propagation to parent loggers to avoid duplicate logs
    logger.propagate = False

    logger.setLevel(logging.INFO)

    for handler in _get_shared_handlers(log_file):
        logger.addHandler(handler)

    return logger
