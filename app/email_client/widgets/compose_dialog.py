"""
Compose email dialog
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QPushButton, QComboBox, QMessageBox
)
from PySide6.QtCore import Qt
from typing import Optional

from email_server import UnifiedEmailServer


class ComposeDialog(QDialog):
    """Dialog for composing and sending emails"""
    
    def __init__(self, server: UnifiedEmailServer, user_id: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.server = server
        self.default_user_id = user_id  # Default user_id, but may be overridden by provider selection
        self._init_ui()
    
    def _init_ui(self):
        """Initialize the dialog UI"""
        self.setWindowTitle("Compose Email")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        
        layout = QVBoxLayout(self)
        
        # Provider selection
        provider_layout = QHBoxLayout()
        provider_layout.addWidget(QLabel("Send via:"))
        self.provider_combo = QComboBox()
        # Get available authenticated providers from server
        if self.server:
            authenticated = self.server.get_authenticated_users()
            for provider_name in authenticated.keys():
                self.provider_combo.addItem(provider_name.capitalize(), provider_name)
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        provider_layout.addWidget(self.provider_combo)
        provider_layout.addStretch()
        layout.addLayout(provider_layout)
        
        # User selection (if multiple users for a provider)
        self.user_combo = QComboBox()
        self.user_combo.setVisible(False)  # Hide by default, show if multiple users
        user_layout = QHBoxLayout()
        user_layout.addWidget(QLabel("Account:"))
        user_layout.addWidget(self.user_combo)
        user_layout.addStretch()
        layout.addLayout(user_layout)
        self._update_user_list()
        
        # To field
        to_layout = QHBoxLayout()
        to_layout.addWidget(QLabel("To:"))
        self.to_input = QLineEdit()
        self.to_input.setPlaceholderText("recipient@example.com")
        to_layout.addWidget(self.to_input)
        layout.addLayout(to_layout)
        
        # CC field
        cc_layout = QHBoxLayout()
        cc_layout.addWidget(QLabel("CC:"))
        self.cc_input = QLineEdit()
        self.cc_input.setPlaceholderText("cc@example.com (optional)")
        cc_layout.addWidget(self.cc_input)
        layout.addLayout(cc_layout)
        
        # Subject field
        subject_layout = QHBoxLayout()
        subject_layout.addWidget(QLabel("Subject:"))
        self.subject_input = QLineEdit()
        self.subject_input.setPlaceholderText("Email subject")
        subject_layout.addWidget(self.subject_input)
        layout.addLayout(subject_layout)
        
        # Body field
        body_layout = QVBoxLayout()
        body_layout.addWidget(QLabel("Message:"))
        self.body_input = QTextEdit()
        self.body_input.setPlaceholderText("Enter your message here...")
        body_layout.addWidget(self.body_input)
        layout.addLayout(body_layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self._send_email)
        self.send_btn.setDefault(True)
        button_layout.addWidget(self.send_btn)
        
        layout.addLayout(button_layout)
    
    def _on_provider_changed(self, provider_text: str):
        """Handle provider selection change"""
        self._update_user_list()
    
    def _update_user_list(self):
        """Update the user combo box based on selected provider"""
        if not self.server:
            return
        
        provider_name = self.provider_combo.currentData()
        if not provider_name:
            self.user_combo.setVisible(False)
            return
        
        authenticated = self.server.get_authenticated_users(provider=provider_name)
        if provider_name in authenticated:
            user_ids = authenticated[provider_name]
            self.user_combo.clear()
            for user_id in user_ids:
                self.user_combo.addItem(user_id, user_id)
            
            # Show user selection if multiple users, hide if single user
            self.user_combo.setVisible(len(user_ids) > 1)
            
            # Select default user_id if it exists for this provider
            if self.default_user_id and self.default_user_id in user_ids:
                index = self.user_combo.findData(self.default_user_id)
                if index >= 0:
                    self.user_combo.setCurrentIndex(index)
        else:
            self.user_combo.setVisible(False)
    
    def _get_user_id_for_provider(self, provider_name: str) -> Optional[str]:
        """Get the user_id to use for the selected provider"""
        if self.user_combo.isVisible() and self.user_combo.count() > 0:
            return self.user_combo.currentData()
        
        # If user combo is hidden, get first authenticated user for this provider
        authenticated = self.server.get_authenticated_users(provider=provider_name)
        if provider_name in authenticated and authenticated[provider_name]:
            return authenticated[provider_name][0]
        
        return self.default_user_id
    
    def _send_email(self):
        """Send the email"""
        # Validate inputs
        to_text = self.to_input.text().strip()
        if not to_text:
            QMessageBox.warning(self, "Validation Error", "Please enter a recipient email address.")
            return
        
        subject = self.subject_input.text().strip()
        body = self.body_input.toPlainText().strip()
        
        if not body:
            QMessageBox.warning(self, "Validation Error", "Please enter a message body.")
            return
        
        # Get selected provider
        provider_name = self.provider_combo.currentData()
        if not provider_name:
            QMessageBox.warning(self, "Validation Error", "Please select an email provider.")
            return
        
        # Get user_id for selected provider
        user_id = self._get_user_id_for_provider(provider_name)
        if not user_id:
            QMessageBox.warning(self, "Validation Error", f"No authenticated user found for {provider_name}.")
            return
        
        # Parse recipients
        to_recipients = [email.strip() for email in to_text.split(',')]
        cc_recipients = None
        if self.cc_input.text().strip():
            cc_recipients = [email.strip() for email in self.cc_input.text().split(',')]
        
        # Send email
        try:
            success = self.server.send_message(
                user_id=user_id,
                provider_name=provider_name,
                to=to_recipients,
                subject=subject,
                body=body,
                cc=cc_recipients
            )
            
            if success:
                QMessageBox.information(self, "Success", "Email sent successfully!")
                self.accept()
            else:
                QMessageBox.warning(self, "Error", "Failed to send email. Please check your configuration and authentication.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error sending email:\n{str(e)}")
