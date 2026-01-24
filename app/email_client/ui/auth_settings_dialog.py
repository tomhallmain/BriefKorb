"""
Authentication and settings configuration dialog
"""

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QMessageBox, QTabWidget, QWidget,
    QCheckBox, QListWidget, QListWidgetItem, QFileDialog,
    QTextEdit, QGroupBox, QFormLayout
)
from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QDesktopServices

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from email_server.config import EmailServerConfig, ProviderConfig
from email_client.utils.scope_checker import ScopeChecker


class AuthSettingsDialog(QDialog):
    """Dialog for configuring authentication and email server settings"""
    
    def __init__(self, config: EmailServerConfig, config_path: str, parent=None):
        super().__init__(parent)
        self.config = config
        self.config_path = config_path
        self.original_config = self._copy_config(config)
        self.auth_status_timer = None
        self._init_ui()
        self._load_current_config()
        self._update_auth_status()
    
    def _copy_config(self, config: EmailServerConfig) -> EmailServerConfig:
        """Create a deep copy of the config"""
        import copy
        return copy.deepcopy(config)
    
    def _init_ui(self):
        """Initialize the dialog UI"""
        self.setWindowTitle("Authentication Settings")
        self.setMinimumWidth(700)
        self.setMinimumHeight(600)
        
        layout = QVBoxLayout(self)
        
        # Create tab widget
        self.tabs = QTabWidget()
        
        # Microsoft tab
        microsoft_tab = self._create_microsoft_tab()
        self.tabs.addTab(microsoft_tab, "Microsoft")
        
        # Gmail tab
        gmail_tab = self._create_gmail_tab()
        self.tabs.addTab(gmail_tab, "Gmail")
        
        # General settings tab
        general_tab = self._create_general_tab()
        self.tabs.addTab(general_tab, "General")
        
        layout.addWidget(self.tabs)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._save_config)
        self.save_btn.setDefault(True)
        button_layout.addWidget(self.save_btn)
        
        self.auth_btn = QPushButton("Authenticate")
        self.auth_btn.clicked.connect(self._start_auth_flow)
        button_layout.addWidget(self.auth_btn)
        
        layout.addLayout(button_layout)
    
    def _create_microsoft_tab(self) -> QWidget:
        """Create Microsoft configuration tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Enable checkbox
        self.ms_enabled = QCheckBox("Enable Microsoft Graph API")
        layout.addWidget(self.ms_enabled)
        
        # Authentication status
        self.ms_auth_status = QLabel("Not authenticated")
        self.ms_auth_status.setStyleSheet("color: #888; font-style: italic; padding: 5px;")
        layout.addWidget(self.ms_auth_status)
        
        # Microsoft settings form
        form = QFormLayout()
        
        self.ms_client_id = QLineEdit()
        self.ms_client_id.setPlaceholderText("Application (client) ID")
        form.addRow("Client ID:", self.ms_client_id)
        
        self.ms_client_secret = QLineEdit()
        self.ms_client_secret.setEchoMode(QLineEdit.Password)
        self.ms_client_secret.setPlaceholderText("Client secret value")
        form.addRow("Client Secret:", self.ms_client_secret)
        
        self.ms_tenant_id = QLineEdit()
        self.ms_tenant_id.setPlaceholderText("common or your-tenant-id")
        form.addRow("Tenant ID:", self.ms_tenant_id)
        
        self.ms_redirect_uri = QLineEdit()
        self.ms_redirect_uri.setPlaceholderText("http://localhost:8000/auth/microsoft/callback")
        form.addRow("Redirect URI:", self.ms_redirect_uri)
        
        layout.addLayout(form)
        
        # Scopes section
        scope_group = QGroupBox("Permissions (Scopes)")
        scope_layout = QVBoxLayout(scope_group)
        
        scope_info = QLabel(
            "Select the permissions your application needs. "
            "Changing scopes will require re-authentication."
        )
        scope_info.setWordWrap(True)
        scope_layout.addWidget(scope_info)
        
        self.ms_scopes_list = QListWidget()
        available_scopes = ScopeChecker.get_available_scopes('microsoft')
        for scope_info in available_scopes:
            item = QListWidgetItem(scope_info['label'])
            item.setData(Qt.UserRole, scope_info['value'])
            item.setCheckState(Qt.Unchecked)
            self.ms_scopes_list.addItem(item)
        
        scope_layout.addWidget(self.ms_scopes_list)
        layout.addWidget(scope_group)
        
        layout.addStretch()
        
        return widget
    
    def _create_gmail_tab(self) -> QWidget:
        """Create Gmail configuration tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Enable checkbox
        self.gmail_enabled = QCheckBox("Enable Gmail API")
        layout.addWidget(self.gmail_enabled)
        
        # Authentication status
        self.gmail_auth_status = QLabel("Not authenticated")
        self.gmail_auth_status.setStyleSheet("color: #888; font-style: italic; padding: 5px;")
        layout.addWidget(self.gmail_auth_status)
        
        # Gmail settings form
        form = QFormLayout()
        
        credentials_layout = QHBoxLayout()
        self.gmail_credentials_path = QLineEdit()
        self.gmail_credentials_path.setPlaceholderText("Path to credentials.json")
        credentials_layout.addWidget(self.gmail_credentials_path)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(lambda: self._browse_credentials_file())
        credentials_layout.addWidget(browse_btn)
        form.addRow("Credentials File:", credentials_layout)
        
        self.gmail_redirect_uri = QLineEdit()
        self.gmail_redirect_uri.setPlaceholderText("http://localhost:8000/auth/gmail/callback")
        form.addRow("Redirect URI:", self.gmail_redirect_uri)
        
        layout.addLayout(form)
        
        # Scopes section
        scope_group = QGroupBox("Permissions (Scopes)")
        scope_layout = QVBoxLayout(scope_group)
        
        scope_info = QLabel(
            "Select the permissions your application needs. "
            "Changing scopes will require re-authentication."
        )
        scope_info.setWordWrap(True)
        scope_layout.addWidget(scope_info)
        
        self.gmail_scopes_list = QListWidget()
        available_scopes = ScopeChecker.get_available_scopes('gmail')
        for scope_info in available_scopes:
            item = QListWidgetItem(scope_info['label'])
            item.setData(Qt.UserRole, scope_info['value'])
            item.setCheckState(Qt.Unchecked)
            self.gmail_scopes_list.addItem(item)
        
        scope_layout.addWidget(self.gmail_scopes_list)
        layout.addWidget(scope_group)
        
        layout.addStretch()
        
        return widget
    
    def _create_general_tab(self) -> QWidget:
        """Create general settings tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        form = QFormLayout()
        
        self.token_storage_path = QLineEdit()
        self.token_storage_path.setPlaceholderText("tokens")
        form.addRow("Token Storage Path:", self.token_storage_path)
        
        self.log_level = QComboBox()
        self.log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        form.addRow("Log Level:", self.log_level)
        
        layout.addLayout(form)
        layout.addStretch()
        
        return widget
    
    def _browse_credentials_file(self):
        """Browse for Gmail credentials file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Gmail Credentials File",
            "",
            "JSON Files (*.json);;All Files (*)"
        )
        if file_path:
            # Make path relative to app directory if possible
            app_dir = Path(__file__).parent.parent.parent
            try:
                rel_path = Path(file_path).relative_to(app_dir)
                self.gmail_credentials_path.setText(str(rel_path))
            except ValueError:
                self.gmail_credentials_path.setText(file_path)
    
    def _update_auth_status(self):
        """Update authentication status labels for each provider"""
        try:
            from email_server import UnifiedEmailServer
            from email_server.config import EmailServerConfig
            
            # Try to load server to check auth status
            config_path = Path(self.config_path)
            if config_path.exists():
                config = EmailServerConfig.from_file(str(config_path))
                server = UnifiedEmailServer(config=config)
                
                # Update Microsoft status
                ms_auth = server.get_authenticated_providers('microsoft')
                if ms_auth:
                    emails = [ap.get_user_email() for ap in ms_auth]
                    self.ms_auth_status.setText(f"Authenticated: {', '.join(emails)}")
                    self.ms_auth_status.setStyleSheet("color: #008a8f; font-style: normal; padding: 5px;")
                else:
                    self.ms_auth_status.setText("Not authenticated")
                    self.ms_auth_status.setStyleSheet("color: #888; font-style: italic; padding: 5px;")
                
                # Update Gmail status
                gmail_auth = server.get_authenticated_providers('gmail')
                if gmail_auth:
                    emails = [ap.get_user_email() for ap in gmail_auth]
                    self.gmail_auth_status.setText(f"Authenticated: {', '.join(emails)}")
                    self.gmail_auth_status.setStyleSheet("color: #008a8f; font-style: normal; padding: 5px;")
                else:
                    self.gmail_auth_status.setText("Not authenticated")
                    self.gmail_auth_status.setStyleSheet("color: #888; font-style: italic; padding: 5px;")
        except Exception as e:
            # If we can't load server, just show not authenticated
            self.ms_auth_status.setText("Not authenticated")
            self.gmail_auth_status.setText("Not authenticated")
    
    def _load_current_config(self):
        """Load current configuration into UI"""
        # Microsoft settings
        self.ms_enabled.setChecked(self.config.microsoft.enabled)
        self.ms_client_id.setText(self.config.microsoft.client_id or "")
        self.ms_client_secret.setText(self.config.microsoft.client_secret or "")
        self.ms_tenant_id.setText(self.config.microsoft.tenant_id or "common")
        self.ms_redirect_uri.setText(self.config.microsoft.redirect_uri or "")
        
        # Set Microsoft scopes
        current_ms_scopes = set(self.config.microsoft.scopes or [])
        for i in range(self.ms_scopes_list.count()):
            item = self.ms_scopes_list.item(i)
            scope_value = item.data(Qt.UserRole)
            if scope_value in current_ms_scopes:
                item.setCheckState(Qt.Checked)
        
        # Gmail settings
        self.gmail_enabled.setChecked(self.config.gmail.enabled)
        if self.config.gmail.credentials_path:
            self.gmail_credentials_path.setText(self.config.gmail.credentials_path)
        self.gmail_redirect_uri.setText(self.config.gmail.redirect_uri or "")
        
        # Set Gmail scopes
        current_gmail_scopes = set(self.config.gmail.scopes or [])
        for i in range(self.gmail_scopes_list.count()):
            item = self.gmail_scopes_list.item(i)
            scope_value = item.data(Qt.UserRole)
            if scope_value in current_gmail_scopes:
                item.setCheckState(Qt.Checked)
        
        # General settings
        self.token_storage_path.setText(self.config.token_storage_path)
        index = self.log_level.findText(self.config.log_level.upper())
        if index >= 0:
            self.log_level.setCurrentIndex(index)
    
    def _get_selected_scopes(self, scope_list: QListWidget) -> list[str]:
        """Get selected scopes from list widget"""
        scopes = []
        for i in range(scope_list.count()):
            item = scope_list.item(i)
            if item.checkState() == Qt.Checked:
                scopes.append(item.data(Qt.UserRole))
        return scopes
    
    def _save_config(self):
        """Save configuration to file"""
        try:
            # Update Microsoft config
            self.config.microsoft.enabled = self.ms_enabled.isChecked()
            self.config.microsoft.client_id = self.ms_client_id.text().strip() or None
            self.config.microsoft.client_secret = self.ms_client_secret.text().strip() or None
            self.config.microsoft.tenant_id = self.ms_tenant_id.text().strip() or None
            self.config.microsoft.redirect_uri = self.ms_redirect_uri.text().strip() or None
            self.config.microsoft.scopes = self._get_selected_scopes(self.ms_scopes_list)
            
            # Update Gmail config
            self.config.gmail.enabled = self.gmail_enabled.isChecked()
            credentials_path = self.gmail_credentials_path.text().strip()
            if credentials_path:
                # Convert to absolute path if relative, then make relative to app dir if possible
                if not Path(credentials_path).is_absolute():
                    app_dir = Path(__file__).parent.parent.parent
                    abs_path = app_dir / credentials_path
                    # Try to make it relative to app_dir for cleaner config
                    try:
                        credentials_path = str(abs_path.relative_to(app_dir))
                    except ValueError:
                        credentials_path = str(abs_path)
                else:
                    # If absolute, try to make relative to app_dir
                    app_dir = Path(__file__).parent.parent.parent
                    try:
                        credentials_path = str(Path(credentials_path).relative_to(app_dir))
                    except ValueError:
                        pass  # Keep absolute if can't make relative
            self.config.gmail.credentials_path = credentials_path or None
            self.config.gmail.redirect_uri = self.gmail_redirect_uri.text().strip() or None
            self.config.gmail.scopes = self._get_selected_scopes(self.gmail_scopes_list)
            
            # Update general settings
            self.config.token_storage_path = self.token_storage_path.text().strip() or "tokens"
            self.config.log_level = self.log_level.currentText().lower()
            
            # Validate
            try:
                self.config.validate()
            except ValueError as e:
                QMessageBox.warning(self, "Validation Error", f"Configuration error:\n{str(e)}")
                return
            
            # Check if scopes changed (requires re-auth)
            scopes_changed = self._scopes_changed()
            
            # Save to file
            self.config.save(self.config_path)
            
            if scopes_changed:
                QMessageBox.information(
                    self,
                    "Scopes Changed",
                    "Scopes have been updated. You will need to re-authenticate "
                    "for the affected providers. Click 'Authenticate' to start the OAuth flow."
                )
            
            QMessageBox.information(self, "Success", "Configuration saved successfully!")
            self.accept()
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save configuration:\n{str(e)}")
    
    def _scopes_changed(self) -> bool:
        """Check if scopes have changed"""
        original_ms_scopes = set(self.original_config.microsoft.scopes or [])
        new_ms_scopes = set(self.config.microsoft.scopes or [])
        
        original_gmail_scopes = set(self.original_config.gmail.scopes or [])
        new_gmail_scopes = set(self.config.gmail.scopes or [])
        
        return (original_ms_scopes != new_ms_scopes or 
                original_gmail_scopes != new_gmail_scopes)
    
    def _start_auth_flow(self):
        """Start OAuth authentication flow based on the currently active tab"""
        # Determine which provider to authenticate based on the active tab
        current_tab_index = self.tabs.currentIndex()
        current_tab_text = self.tabs.tabText(current_tab_index)
        
        if current_tab_text == "Microsoft":
            self._start_microsoft_auth()
        elif current_tab_text == "Gmail":
            self._start_gmail_auth()
        else:
            QMessageBox.information(
                self,
                "Select Provider",
                "Please select either the Microsoft or Gmail tab to authenticate."
            )
    
    def _start_microsoft_auth(self):
        """Start Microsoft OAuth authentication flow"""
        if not self.config.microsoft.enabled:
            QMessageBox.warning(
                self,
                "Provider Disabled",
                "Microsoft Graph API is not enabled. Please enable it first."
            )
            return
        
        if not self.config.microsoft.client_id:
            QMessageBox.warning(
                self,
                "Configuration Missing",
                "Please configure Microsoft client_id and client_secret first."
            )
            return
        
        # Generate auth URL using MSAL (simplified - would need proper OAuth flow)
        tenant = self.config.microsoft.tenant_id or "common"
        scopes = " ".join(self.config.microsoft.scopes or [])
        auth_url = (
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
            f"?client_id={self.config.microsoft.client_id}"
            f"&response_type=code"
            f"&redirect_uri={self.config.microsoft.redirect_uri}"
            f"&scope={scopes}"
            f"&response_mode=query"
        )
        # Clear any existing status file
        app_dir = Path(__file__).parent.parent.parent
        status_file = app_dir / 'email_server' / '.microsoft_auth_status.json'
        if status_file.exists():
            status_file.unlink()
        
        QDesktopServices.openUrl(QUrl(auth_url))
        
        # Start polling for authentication completion
        self._start_auth_status_polling('microsoft')
    
    def _start_gmail_auth(self):
        """Start Gmail OAuth authentication flow"""
        if not self.config.gmail.enabled:
            QMessageBox.warning(
                self,
                "Provider Disabled",
                "Gmail API is not enabled. Please enable it first."
            )
            return
        
        if not self.config.gmail.credentials_path:
            QMessageBox.warning(
                self,
                "Configuration Missing",
                "Please configure Gmail credentials file path first."
            )
            return
        
        # Check if credentials file exists
        credentials_path = Path(self.config.gmail.credentials_path)
        if not credentials_path.is_absolute():
            app_dir = Path(__file__).parent.parent.parent
            credentials_path = app_dir / credentials_path
        
        if not credentials_path.exists():
            QMessageBox.warning(
                self,
                "Credentials File Not Found",
                f"Gmail credentials file not found at:\n{credentials_path}\n\n"
                "Please check the path in the configuration."
            )
            return
        
        try:
            # Use Gmail OAuth to generate auth URL
            from email_server.auth import GmailOAuth
            from email_server.auth import TokenManager
            
            # Get scopes from config
            scopes = self.config.gmail.scopes or [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send"
            ]
            
            # Initialize Gmail OAuth
            token_manager = TokenManager()
            gmail_oauth = GmailOAuth(
                credentials_path=str(credentials_path),
                redirect_uri=self.config.gmail.redirect_uri or "http://localhost:8000/auth/gmail/callback",
                token_manager=token_manager
            )
            
            # Generate auth URL
            auth_url = gmail_oauth.get_auth_url()
            
            # Clear any existing status file
            app_dir = Path(__file__).parent.parent.parent
            status_file = app_dir / 'email_server' / '.gmail_auth_status.json'
            if status_file.exists():
                status_file.unlink()
            
            QDesktopServices.openUrl(QUrl(auth_url))
            
            # Start polling for authentication completion
            self._start_auth_status_polling('gmail')
        except Exception as e:
            QMessageBox.critical(
                self,
                "Authentication Error",
                f"Failed to start Gmail authentication:\n{str(e)}"
            )
    
    def _start_auth_status_polling(self, provider: str):
        """Start polling for authentication status file"""
        import json
        
        # Stop any existing timer
        if self.auth_status_timer:
            self.auth_status_timer.stop()
        
        app_dir = Path(__file__).parent.parent.parent
        status_file = app_dir / 'email_server' / f'.{provider}_auth_status.json'
        poll_count = [0]  # Use list to allow modification in nested function
        max_polls = 120  # Poll for up to 2 minutes (120 * 1 second)
        
        def check_auth_status():
            poll_count[0] += 1
            
            if status_file.exists():
                try:
                    with open(status_file, 'r') as f:
                        status_data = json.load(f)
                    
                    # Stop polling
                    if self.auth_status_timer:
                        self.auth_status_timer.stop()
                        self.auth_status_timer = None
                    
                    # Remove status file
                    status_file.unlink()
                    
                    # Show result
                    if status_data.get('status') == 'success':
                        user_email = status_data.get('user_email', 'user')
                        QMessageBox.information(
                            self,
                            "Authentication Successful",
                            f"{provider.capitalize()} authentication completed successfully!\n\n"
                            f"User: {user_email}\n\n"
                            "You can now use this provider in BriefKorb."
                        )
                        # Update auth status display
                        self._update_auth_status()
                    else:
                        error_msg = status_data.get('error', 'Unknown error')
                        QMessageBox.warning(
                            self,
                            "Authentication Failed",
                            f"{provider.capitalize()} authentication failed:\n\n{error_msg}"
                        )
                except Exception as e:
                    print(f"Error reading auth status: {e}")
            elif poll_count[0] >= max_polls:
                # Timeout
                if self.auth_status_timer:
                    self.auth_status_timer.stop()
                    self.auth_status_timer = None
                QMessageBox.warning(
                    self,
                    "Authentication Timeout",
                    f"Authentication did not complete within the expected time.\n\n"
                    "Please check if the browser window is still open and try again."
                )
        
        # Start timer to poll every second
        self.auth_status_timer = QTimer()
        self.auth_status_timer.timeout.connect(check_auth_status)
        self.auth_status_timer.start(1000)  # Check every second
        
        # Show initial message
        QMessageBox.information(
            self,
            f"{provider.capitalize()} Authentication",
            "A browser window has been opened. Please complete the authentication in your browser.\n\n"
            "This dialog will automatically detect when authentication is complete."
        )
