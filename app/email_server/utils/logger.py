import os
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import platform
import glob
import time

def get_log_directory():
    """Get the appropriate log directory based on the operating system."""
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

class WindowsCompatibleTimedRotatingFileHandler(TimedRotatingFileHandler):
    """A TimedRotatingFileHandler that works on Windows by closing the file before rotation."""
    
    def emit(self, record):
        """Override emit to handle rotation errors gracefully."""
        try:
            super().emit(record)
        except (PermissionError, OSError) as e:
            # If rotation fails due to file locking, try to handle it gracefully
            # This can happen if another process has the file open
            self.handleError(record)
            # Try to reopen the stream if it was closed
            if self.stream is None or self.stream.closed:
                try:
                    self.stream = self._open()
                except Exception:
                    pass
    
    def doRollover(self):
        """Override doRollover to close the file before rotating on Windows."""
        if self.stream:
            self.stream.close()
            self.stream = None
        
        # Try to rotate with retries for Windows file locking issues
        max_retries = 3
        retry_delay = 0.1
        
        for attempt in range(max_retries):
            try:
                super().doRollover()
                break
            except (PermissionError, OSError) as e:
                if attempt < max_retries - 1:
                    # Wait a bit and try again
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    # Last attempt failed, log the error but don't crash
                    # We'll try to reopen the stream for continued logging
                    try:
                        self.stream = self._open()
                    except Exception:
                        pass
                    # Don't raise - just log to stderr as a fallback
                    import sys
                    print(f"Warning: Could not rotate log file {self.baseFilename}: {e}", file=sys.stderr)

def cleanup_old_logs(log_dir, log_file):
    """Clean up old log files, keeping only the 3 most recent ones.
    
    Args:
        log_dir: Path to the log directory
        log_file: Base name of the log file
    """
    # Get all log files matching the pattern
    log_pattern = str(log_dir / f"{log_file}*")
    log_files = sorted(glob.glob(log_pattern))
    
    # If we have more than 3 files, delete the oldest ones
    if len(log_files) > 3:
        # Sort by modification time (oldest first)
        log_files.sort(key=lambda x: os.path.getmtime(x))
        # Delete oldest files, keeping only the 3 most recent
        for old_file in log_files[:-3]:
            try:
                os.remove(old_file)
            except OSError as e:
                print(f"Warning: Could not delete old log file {old_file}: {e}")

def setup_logger(name, log_file='email_server.log'):
    """Set up a logger with timed rotating file handler and console output.
    
    Args:
        name: Name of the logger
        log_file: Name of the log file
    
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
    
    # Create rotating file handler
    log_dir = get_log_directory()
    log_path = log_dir / log_file
    
    # Clean up any old log files before setting up the handler
    cleanup_old_logs(log_dir, log_file)
    
    # Rotate logs daily and keep 3 days of history
    # Use Windows-compatible handler to avoid file locking issues
    file_handler = WindowsCompatibleTimedRotatingFileHandler(
        log_path,
        when='midnight',  # Rotate at midnight
        interval=1,       # Every day
        backupCount=3,    # Keep 3 days of logs
        encoding='utf-8'
    )
    
    # Create console handler
    console_handler = logging.StreamHandler()
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger 