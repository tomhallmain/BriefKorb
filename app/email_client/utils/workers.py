"""
Worker threads for background email operations
"""

from typing import Optional, List
from PySide6.QtCore import QThread, Signal

# Add parent directories to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from email_server import UnifiedEmailServer, EmailMessage


class EmailWorkerThread(QThread):
    """Worker thread for fetching emails without blocking UI"""
    messages_loaded = Signal(list)
    error_occurred = Signal(str)
    
    def __init__(self, server: UnifiedEmailServer, provider_name: Optional[str] = None):
        super().__init__()
        self.server = server
        self.provider_name = provider_name  # None means load from all providers
        self.folder = 'inbox'
        self.unread_only = False
        self.max_messages = 50
    
    def run(self):
        """Fetch messages in background thread"""
        try:
            # Get authenticated providers (filter by provider_name if specified)
            auth_providers = self.server.get_authenticated_providers(self.provider_name)
            messages = self.server.get_user_messages(
                providers=auth_providers,
                folder=self.folder,
                max_messages=self.max_messages,
                unread_only=self.unread_only
            )
            self.messages_loaded.emit(messages)
        except Exception as e:
            self.error_occurred.emit(str(e))
