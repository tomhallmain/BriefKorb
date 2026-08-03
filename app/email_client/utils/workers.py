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
from email_client.utils.html_utils import sanitize_html, convert_plain_text_to_html, is_html_content

# The cap every normal load fetches up to. Exposed here (rather than left as
# a bare literal on EmailWorkerThread) so callers that need to reason about
# "is the loaded set below the fetch limit" -- e.g. MainWindow backfilling
# the list after a delete/block shrinks it -- have one place to reference.
DEFAULT_MAX_MESSAGES = 200


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
        self.max_messages = DEFAULT_MAX_MESSAGES
    
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


class MessageBodyWorkerThread(QThread):
    """Worker thread for processing message body content off the main thread.

    Sanitizing HTML (which may download remote images) can be slow; doing it
    here keeps the UI responsive and lets action buttons become available as
    soon as the message header is shown.
    """
    content_ready = Signal(str)   # emits processed HTML ready to pass to setHtml
    error_occurred = Signal(str)

    def __init__(self, message: EmailMessage):
        super().__init__()
        self.message = message

    def run(self):
        try:
            body = self.message.body
            if not body:
                self.content_ready.emit("")
                return
            if is_html_content(body):
                html = sanitize_html(body)
            else:
                html = convert_plain_text_to_html(body)
            self.content_ready.emit(html)
        except Exception as e:
            self.error_occurred.emit(str(e))


class EntityExtractionWorkerThread(QThread):
    """Worker thread for entity extraction over a batch of messages.

    Delegates to ``UnifiedEmailServer.extract_entities()`` so the graph
    manager stays server-owned and the UI thread is never blocked.
    """

    extraction_complete = Signal(int)   # number of job postings found
    error_occurred = Signal(str)

    def __init__(self, server: UnifiedEmailServer, messages: List[EmailMessage]) -> None:
        super().__init__()
        self.server = server
        self.messages = messages

    def run(self) -> None:
        try:
            count = self.server.extract_entities(self.messages)
            self.extraction_complete.emit(count)
        except Exception as e:
            self.error_occurred.emit(str(e))
