"""
Custom list item for displaying email messages
"""

from PySide6.QtWidgets import QListWidgetItem
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from email_server import EmailMessage


class MessageListItem(QListWidgetItem):
    """Custom list item for email messages"""
    
    def __init__(self, message: EmailMessage):
        super().__init__()
        self.message = message
        self._update_display()
    
    def _update_display(self):
        """Update the display text and formatting"""
        # Format the display text
        subject = self.message.subject or "(No Subject)"
        sender = self.message.sender or "Unknown"
        date_str = self.message.received_date.strftime("%Y-%m-%d %H:%M")
        
        # Create display text
        if not self.message.is_read:
            # Bold for unread messages
            self.setText(f"● {subject} - {sender} ({date_str})")
            font = QFont()
            font.setBold(True)
            self.setFont(font)
        else:
            self.setText(f"  {subject} - {sender} ({date_str})")
        
        # Add provider indicator
        provider_text = f"[{self.message.provider.upper()}]"
        full_text = f"{self.text()} {provider_text}"
        self.setText(full_text)
        
        # Set tooltip with more details
        tooltip = f"Subject: {subject}\n"
        tooltip += f"From: {sender}\n"
        tooltip += f"Date: {self.message.received_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
        tooltip += f"Provider: {self.message.provider}\n"
        tooltip += f"Status: {'Read' if self.message.is_read else 'Unread'}"
        self.setToolTip(tooltip)
