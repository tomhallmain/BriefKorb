"""
Main window for the email client application
"""

import sys
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QComboBox, QLabel, QListWidget, QListWidgetItem, QTextEdit,
    QLineEdit, QMessageBox, QStatusBar, QProgressBar, QGroupBox,
    QSizePolicy
)
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QFont, QTextDocument

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from email_server import UnifiedEmailServer, EmailMessage, AuthenticatedProvider
from email_server.config import EmailServerConfig

from widgets.message_list_item import MessageListItem
from widgets.compose_dialog import ComposeDialog
from ui.auth_settings_dialog import AuthSettingsDialog
from email_client.utils.scope_checker import ScopeChecker
from email_client.utils.message_grouping import MessageGroup, group_messages_by_sender
from email_client.utils.content_type import ContentType
from email_client.utils.workers import EmailWorkerThread, MessageBodyWorkerThread
from email_client.utils.html_utils import sanitize_html, convert_plain_text_to_html, is_html_content, strip_images_for_debug
from email_client.utils.blocklist import BlocklistManager


class _BodyTextEdit(QTextEdit):
    """QTextEdit that never lets document content influence the splitter.

    Qt recomputes sizeHint() from document().idealWidth() after every
    setHtml() call.  When that ideal width exceeds the current pane width the
    splitter redistributes space — even with QSizePolicy.Ignored.  Returning a
    fixed zero-size hint from both sizeHint() and minimumSizeHint() severs that
    link entirely; the splitter can only be moved by the user.
    """

    def sizeHint(self):
        return QSize(0, 0)

    def minimumSizeHint(self):
        return QSize(0, 0)


class MainWindow(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()
        self.server: Optional[UnifiedEmailServer] = None
        self.current_messages: List[EmailMessage] = []
        self.current_groups: List[MessageGroup] = []
        self.current_group_index: Optional[int] = None
        self.current_message_index: int = 0
        self.worker_thread: Optional[EmailWorkerThread] = None
        self.body_worker_thread: Optional[MessageBodyWorkerThread] = None
        self.config: Optional[EmailServerConfig] = None
        self.config_path: Optional[str] = None
        self.blocklist: Optional[BlocklistManager] = None
        # Desired splitter widths — updated only when the user drags the handle.
        # Used to restore positions after content-driven layout passes.
        self._splitter_sizes: List[int] = [400, 600]
        # Last HTML written to the message body; used by "Open in Browser".
        self._current_html: Optional[str] = None

        self._init_ui()
        self._load_config()
        self._update_auth_status()
        # Defer post-init until the event loop is running and the window has
        # its real geometry, so setSizes() lands on a fully-laid-out splitter.
        QTimer.singleShot(0, self._post_init)
    
    def _init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("BriefKorb - Unified Email Client")
        self.setGeometry(100, 100, 1500, 800)
        
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(5)  # Reduce spacing between widgets
        
        # Top toolbar (fixed height, doesn't expand)
        toolbar = self._create_toolbar()
        main_layout.addWidget(toolbar)
        
        # Main content area with splitter
        self.splitter = QSplitter(Qt.Horizontal)
        # Prevent panes from collapsing to zero width
        self.splitter.setChildrenCollapsible(False)

        # Left panel: Message list
        left_panel = self._create_message_list_panel()
        self.splitter.addWidget(left_panel)

        # Right panel: Message detail
        right_panel = self._create_message_detail_panel()
        self.splitter.addWidget(right_panel)

        main_layout.addWidget(self.splitter)
        
        # Status bar
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("Ready")
        
        # Progress bar for loading
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.statusBar.addPermanentWidget(self.progress_bar)
    
    def _create_toolbar(self) -> QWidget:
        """Create the top toolbar"""
        toolbar = QGroupBox()
        toolbar.setMaximumHeight(70)  # Limit height
        # Set size policy to prevent vertical expansion
        toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(10, 5, 10, 5)  # Reduce vertical margins
        layout.setSpacing(10)
        
        # Provider selection
        layout.addWidget(QLabel("Provider:"))
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["All", "Microsoft", "Gmail"])
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        layout.addWidget(self.provider_combo)
        
        layout.addSpacing(20)
        
        # Authentication status label
        self.auth_status_label = QLabel("Not authenticated")
        self.auth_status_label.setStyleSheet("color: #888; font-style: italic;")
        layout.addWidget(self.auth_status_label)
        
        layout.addSpacing(10)
        
        # Refresh button
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._load_messages)
        layout.addWidget(self.refresh_btn)
        
        # Compose button
        self.compose_btn = QPushButton("Compose")
        self.compose_btn.clicked.connect(self._compose_email)
        layout.addWidget(self.compose_btn)
        
        # Settings button
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(self.settings_btn)
        
        layout.addStretch()
        
        return toolbar
    
    def _create_message_list_panel(self) -> QWidget:
        """Create the message list panel"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        
        # Filter options
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        self.unread_only_checkbox = QPushButton("Unread Only")
        self.unread_only_checkbox.setCheckable(True)
        self.unread_only_checkbox.clicked.connect(self._load_messages)
        filter_layout.addWidget(self.unread_only_checkbox)
        filter_layout.addStretch()
        layout.addLayout(filter_layout)
        
        # Message list
        self.message_list = QListWidget()
        self.message_list.itemClicked.connect(self._on_message_selected)
        # Content (long sender names / subjects) must not drive the pane width.
        self.message_list.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.message_list)
        
        # Status label
        self.message_count_label = QLabel("No messages")
        layout.addWidget(self.message_count_label)
        
        return panel
    
    def _create_message_detail_panel(self) -> QWidget:
        """Create the message detail panel"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        
        # Message navigation bar (for messages within a group)
        nav_layout = QHBoxLayout()
        self.prev_msg_btn = QPushButton("◀ Previous")
        self.prev_msg_btn.clicked.connect(self._previous_message)
        self.prev_msg_btn.setEnabled(False)
        nav_layout.addWidget(self.prev_msg_btn)
        
        self.message_nav_label = QLabel("No messages")
        self.message_nav_label.setAlignment(Qt.AlignCenter)
        nav_layout.addWidget(self.message_nav_label)
        
        self.next_msg_btn = QPushButton("Next ▶")
        self.next_msg_btn.clicked.connect(self._next_message)
        self.next_msg_btn.setEnabled(False)
        nav_layout.addWidget(self.next_msg_btn)
        
        layout.addLayout(nav_layout)
        
        # Message header
        header_layout = QHBoxLayout()
        self.subject_label = QLabel("Select a message group to view")
        self.subject_label.setFont(QFont("Arial", 12, QFont.Bold))
        header_layout.addWidget(self.subject_label)
        header_layout.addStretch()
        
        # Action buttons — ordered left to right by impact severity
        self.mark_read_btn = QPushButton("Mark as Read")
        self.mark_read_btn.clicked.connect(self._mark_as_read)
        self.mark_read_btn.setEnabled(False)
        header_layout.addWidget(self.mark_read_btn)

        self.mark_all_read_btn = QPushButton("Mark All as Read")
        self.mark_all_read_btn.clicked.connect(self._mark_group_as_read)
        self.mark_all_read_btn.setEnabled(False)
        header_layout.addWidget(self.mark_all_read_btn)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self._delete_message)
        self.delete_btn.setEnabled(False)
        header_layout.addWidget(self.delete_btn)

        self.delete_all_btn = QPushButton("Delete All")
        self.delete_all_btn.clicked.connect(self._delete_group)
        self.delete_all_btn.setEnabled(False)
        header_layout.addWidget(self.delete_all_btn)

        self.block_btn = QPushButton("Block")
        self.block_btn.clicked.connect(self._block_sender)
        self.block_btn.setEnabled(False)
        header_layout.addWidget(self.block_btn)

        self.open_browser_btn = QPushButton("Open in Browser")
        self.open_browser_btn.clicked.connect(self._open_in_browser)
        self.open_browser_btn.setEnabled(False)
        self.open_browser_btn.setToolTip("Write the processed HTML to a temp file and open it in the default browser")
        header_layout.addWidget(self.open_browser_btn)

        self.save_debug_html_btn = QPushButton("Save Debug HTML")
        self.save_debug_html_btn.clicked.connect(self._save_debug_html)
        self.save_debug_html_btn.setEnabled(False)
        self.save_debug_html_btn.setToolTip("Save processed HTML (images stripped) to the project root for inspection")
        header_layout.addWidget(self.save_debug_html_btn)

        layout.addLayout(header_layout)
        
        # Message metadata
        self.metadata_label = QLabel("")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setStyleSheet("color: gray; padding: 5px;")
        layout.addWidget(self.metadata_label)
        
        # Message body container (to overlay loading indicator)
        message_body_container = QWidget()
        body_layout = QVBoxLayout(message_body_container)
        body_layout.setContentsMargins(0, 0, 0, 0)
        
        # Loading indicator for message content
        self.message_loading_label = QLabel("Loading message content...")
        self.message_loading_label.setAlignment(Qt.AlignCenter)
        self.message_loading_label.setStyleSheet(
            "background-color: rgba(30, 32, 31, 200); "
            "color: #008a8f; "
            "padding: 10px; "
            "font-style: italic; "
            "border-radius: 3px;"
        )
        self.message_loading_label.setVisible(False)
        body_layout.addWidget(self.message_loading_label)
        
        # Message body
        self.message_body = _BodyTextEdit()
        self.message_body.setReadOnly(True)
        self.message_body.setPlaceholderText("Message content will appear here")
        self.message_body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.message_body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Enable external resource loading for images
        # Set base URL to allow relative image paths
        self.message_body.document().setMetaInformation(
            QTextDocument.DocumentUrl, "about:blank"
        )
        body_layout.addWidget(self.message_body)
        
        layout.addWidget(message_body_container)
        
        return panel
    
    def _load_config(self):
        """Load email server configuration"""
        try:
            config_path = Path(__file__).parent.parent.parent / "email_server" / "config.yaml"
            self.config_path = str(config_path)
            
            if not config_path.exists():
                # Try example config
                example_path = config_path.parent / "config.example.yaml"
                if example_path.exists():
                    reply = QMessageBox.question(
                        self,
                        "Configuration Missing",
                        f"Configuration file not found. Would you like to open settings to configure it?",
                        QMessageBox.Yes | QMessageBox.No
                    )
                    if reply == QMessageBox.Yes:
                        self._open_settings()
                    return
                else:
                    QMessageBox.warning(
                        self,
                        "Configuration Missing",
                        "Please configure the email server before using the client."
                    )
                    return
            
            self.config = EmailServerConfig.from_file(str(config_path))
            self.server = UnifiedEmailServer(config=self.config)
            self.blocklist = BlocklistManager(self.config.token_storage_path)
            self._update_ui_permissions()
            self._update_auth_status()
            self.statusBar.showMessage("Configuration loaded successfully")
        except Exception as e:
            QMessageBox.critical(
                self,
                "Configuration Error",
                f"Failed to load configuration: {str(e)}"
            )
            self.statusBar.showMessage(f"Error: {str(e)}")
    
    def _update_ui_permissions(self):
        """Update UI elements based on configured scopes"""
        if not self.config:
            return
        
        # Check permissions for currently selected provider
        provider_text = self.provider_combo.currentText()
        
        if provider_text == "All":
            # For "All", check if any provider has the permission
            ms_send = ScopeChecker.has_send_permission(
                self.config.microsoft.scopes, 'microsoft'
            ) if self.config.microsoft.enabled else False
            gmail_send = ScopeChecker.has_send_permission(
                self.config.gmail.scopes, 'gmail'
            ) if self.config.gmail.enabled else False
            can_send = ms_send or gmail_send
            
            ms_delete = ScopeChecker.has_delete_permission(
                self.config.microsoft.scopes, 'microsoft'
            ) if self.config.microsoft.enabled else False
            gmail_delete = ScopeChecker.has_delete_permission(
                self.config.gmail.scopes, 'gmail'
            ) if self.config.gmail.enabled else False
            can_delete = ms_delete or gmail_delete
        elif provider_text == "Microsoft":
            can_send = ScopeChecker.has_send_permission(
                self.config.microsoft.scopes, 'microsoft'
            ) if self.config.microsoft.enabled else False
            can_delete = ScopeChecker.has_delete_permission(
                self.config.microsoft.scopes, 'microsoft'
            ) if self.config.microsoft.enabled else False
        elif provider_text == "Gmail":
            can_send = ScopeChecker.has_send_permission(
                self.config.gmail.scopes, 'gmail'
            ) if self.config.gmail.enabled else False
            can_delete = ScopeChecker.has_delete_permission(
                self.config.gmail.scopes, 'gmail'
            ) if self.config.gmail.enabled else False
        else:
            can_send = False
            can_delete = False
        
        # Update button states
        self.compose_btn.setEnabled(can_send)
        if not can_send:
            self.compose_btn.setToolTip("Send permission not available. Configure scopes in Settings.")
        else:
            self.compose_btn.setToolTip("")
        
        # Delete button is updated when a message is selected
        if hasattr(self, 'delete_btn'):
            self.delete_btn.setEnabled(can_delete and hasattr(self, 'current_selected_message'))
            if not can_delete:
                self.delete_btn.setToolTip("Delete permission not available. Configure scopes in Settings.")
            else:
                self.delete_btn.setToolTip("")
    
    def _update_auth_status(self):
        """Update the authentication status label"""
        if not self.server:
            self.auth_status_label.setText("Not configured")
            self.auth_status_label.setStyleSheet("color: #ff4444; font-style: italic;")
            return
        
        try:
            auth_providers = self.server.get_authenticated_providers()
            if not auth_providers:
                self.auth_status_label.setText("No providers authenticated - Open Settings to authenticate")
                self.auth_status_label.setStyleSheet("color: #ff8800; font-style: italic;")
            else:
                status_parts = []
                # Group by provider
                by_provider = {}
                for auth_prov in auth_providers:
                    if auth_prov.provider_name not in by_provider:
                        by_provider[auth_prov.provider_name] = []
                    by_provider[auth_prov.provider_name].append(auth_prov)
                
                for provider_name, auth_list in by_provider.items():
                    provider_display = provider_name.capitalize()
                    if len(auth_list) == 1:
                        # Show user email
                        user_email = auth_list[0].get_user_email()
                        status_parts.append(f"{provider_display}: {user_email}")
                    else:
                        # Show count and emails
                        emails = [ap.get_user_email() for ap in auth_list]
                        status_parts.append(f"{provider_display}: {', '.join(emails)}")
                
                status_text = " | ".join(status_parts)
                self.auth_status_label.setText(f"Authenticated: {status_text}")
                self.auth_status_label.setStyleSheet("color: #008a8f; font-style: normal;")
        except Exception as e:
            self.auth_status_label.setText(f"Error checking auth status: {str(e)}")
            self.auth_status_label.setStyleSheet("color: #ff4444; font-style: italic;")
    
    def _post_init(self):
        """Run once after the event loop starts and the window has real geometry.

        setSizes() called during __init__ / _init_ui() is applied before Qt
        has computed the window's actual layout, so it gets overridden by the
        first layout pass.  Calling it here — after the window is visible and
        the event loop is running — gives us an authoritative baseline.

        This is also the right place to trigger any startup work that should
        happen after the UI is ready (e.g. auto-loading messages in the future).
        """
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes(self._splitter_sizes)
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

    def _on_splitter_moved(self, _pos: int, _index: int):
        """Track the user's chosen splitter position.

        splitterMoved is only emitted on user interaction, never on programmatic
        setSizes() calls, so this always reflects the user's explicit intent.
        """
        self._splitter_sizes = self.splitter.sizes()

    def _on_provider_changed(self, provider: str):
        """Handle provider selection change"""
        self._update_ui_permissions()
        self._load_messages()
    
    def _load_messages(self):
        """Load messages from the selected provider using authenticated accounts"""
        if not self.server:
            QMessageBox.warning(self, "No Server", "Email server not initialized. Please configure settings first.")
            return
        
        # Check if any providers are authenticated
        auth_providers = self.server.get_authenticated_providers()
        if not auth_providers:
            QMessageBox.warning(
                self,
                "Not Authenticated",
                "No providers are authenticated. Please open Settings and authenticate at least one provider."
            )
            return
        
        provider_text = self.provider_combo.currentText()
        provider_name = None if provider_text == "All" else provider_text.lower()
        
        # Check if selected provider is authenticated
        if provider_name:
            provider_auth = [ap for ap in auth_providers if ap.provider_name == provider_name]
            if not provider_auth:
                QMessageBox.warning(
                    self,
                    "Not Authenticated",
                    f"{provider_text} is not authenticated. Please open Settings and authenticate this provider."
                )
                return
        
        # Show progress
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # Indeterminate
        self.statusBar.showMessage("Loading messages...")
        self.refresh_btn.setEnabled(False)
        
        # Create and start worker thread
        if self.worker_thread and self.worker_thread.isRunning():
            self.worker_thread.terminate()
            self.worker_thread.wait()
        
        self.worker_thread = EmailWorkerThread(self.server, provider_name=provider_name)
        self.worker_thread.messages_loaded.connect(self._on_messages_loaded)
        self.worker_thread.error_occurred.connect(self._on_load_error)
        self.worker_thread.start()
    
    def _on_messages_loaded(self, messages: List[EmailMessage]):
        """Handle messages loaded from worker thread"""
        # Filter out messages from blocked senders
        if self.blocklist:
            from email_client.utils.message_grouping import extract_sender_email
            messages = [m for m in messages if not self.blocklist.is_blocked(extract_sender_email(m.sender))]
        self.current_messages = messages
        # Group messages by sender
        self.current_groups = group_messages_by_sender(messages)
        self.current_group_index = None
        self.current_message_index = 0
        self._update_message_list()
        
        # Hide progress
        self.progress_bar.setVisible(False)
        total_messages = len(messages)
        total_groups = len(self.current_groups)
        self.statusBar.showMessage(f"Loaded {total_messages} messages in {total_groups} groups")
        self.refresh_btn.setEnabled(True)
    
    def _on_load_error(self, error: str):
        """Handle error from worker thread"""
        self.progress_bar.setVisible(False)
        self.statusBar.showMessage(f"Error: {error}")
        self.refresh_btn.setEnabled(True)
        QMessageBox.critical(self, "Error Loading Messages", f"Failed to load messages:\n{error}")
    
    def _update_message_list(self):
        """Update the message list widget to show message groups"""
        self.message_list.clear()
        
        unread_only = self.unread_only_checkbox.isChecked()
        
        # Filter groups based on unread filter
        if unread_only:
            groups_to_show = [g for g in self.current_groups if g.unread_count > 0]
        else:
            groups_to_show = self.current_groups
        
        # Add groups to list
        for group in groups_to_show:
            # Create display text for the group
            unread_indicator = "●" if group.unread_count > 0 else " "
            date_str = group.latest_date.strftime("%Y-%m-%d %H:%M")
            display_text = f"{unread_indicator} {group.display_name} ({group.count} messages) - {date_str}"
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, group)  # Store group reference
            if group.unread_count > 0:
                font = QFont()
                font.setBold(True)
                item.setFont(font)
            
            # Tooltip with group details
            tooltip = f"Sender: {group.display_name}\n"
            tooltip += f"Domain: {group.sender_domain}\n"
            tooltip += f"Messages: {group.count}\n"
            tooltip += f"Unread: {group.unread_count}\n"
            tooltip += f"Latest: {date_str}\n"
            tooltip += f"Content Type: {group.content_type.value}"
            item.setToolTip(tooltip)
            
            self.message_list.addItem(item)
        
        total_messages = sum(g.count for g in groups_to_show)
        self.message_count_label.setText(
            f"Showing {len(groups_to_show)} groups ({total_messages} messages)"
        )
    
    def _on_message_selected(self, item: QListWidgetItem):
        """Handle message group selection"""
        group = item.data(Qt.UserRole)
        if not isinstance(group, MessageGroup):
            return
        
        # Find group index
        self.current_group_index = None
        for i, g in enumerate(self.current_groups):
            if g.sender_email == group.sender_email:
                self.current_group_index = i
                break
        
        if self.current_group_index is None:
            return
        
        # Show first message in the group
        self.current_message_index = 0
        self._display_current_message()
    
    def _display_current_message(self):
        """Display the current message from the current group"""
        if self.current_group_index is None or self.current_group_index >= len(self.current_groups):
            return
        
        group = self.current_groups[self.current_group_index]
        if not group.messages or self.current_message_index >= len(group.messages):
            return
        
        message = group.messages[self.current_message_index]
        
        # Update navigation label
        self.message_nav_label.setText(
            f"Message {self.current_message_index + 1} of {len(group.messages)}"
        )
        
        # Update navigation buttons
        self.prev_msg_btn.setEnabled(self.current_message_index > 0)
        self.next_msg_btn.setEnabled(self.current_message_index < len(group.messages) - 1)
        
        # Update subject
        self.subject_label.setText(message.subject or "(No Subject)")
        
        # Update metadata
        metadata = f"From: {message.sender}\n"
        metadata += f"To: {', '.join(message.recipients)}\n"
        metadata += f"Date: {message.received_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
        metadata += f"Provider: {message.provider}\n"
        metadata += f"Status: {'Read' if message.is_read else 'Unread'}\n"
        metadata += f"Group: {group.count} messages from {group.display_name}"
        self.metadata_label.setText(metadata)
        
        # Enable action buttons immediately — the user can already judge what
        # they want to do from the sender details shown above.
        self.mark_read_btn.setEnabled(True)
        self.mark_all_read_btn.setEnabled(True)
        self.delete_all_btn.setEnabled(True)
        self.block_btn.setEnabled(True)

        # Check delete permission for this message's provider
        if self.config:
            provider_name = message.provider.lower()
            can_delete = ScopeChecker.has_delete_permission(
                self.config.microsoft.scopes if provider_name == 'microsoft' else self.config.gmail.scopes,
                provider_name
            )
            self.delete_btn.setEnabled(can_delete)
            if not can_delete:
                self.delete_btn.setToolTip("Delete permission not available for this provider. Configure scopes in Settings.")
            else:
                self.delete_btn.setToolTip("")
        else:
            self.delete_btn.setEnabled(True)

        self.current_selected_message = message

        # Load body content in background — sanitising HTML can involve network
        # I/O (image downloads) and must not block the main thread.
        self.message_loading_label.setVisible(True)
        self.message_body.clear()
        self._current_html = None
        self.open_browser_btn.setEnabled(False)
        self.save_debug_html_btn.setEnabled(False)

        # Cancel any in-flight body load for a previously selected message
        if self.body_worker_thread and self.body_worker_thread.isRunning():
            self.body_worker_thread.content_ready.disconnect()
            self.body_worker_thread.error_occurred.disconnect()
            self.body_worker_thread.quit()
            self.body_worker_thread.wait()

        self.body_worker_thread = MessageBodyWorkerThread(message)
        self.body_worker_thread.content_ready.connect(self._on_body_content_ready)
        self.body_worker_thread.error_occurred.connect(self._on_body_load_error)
        self.body_worker_thread.start()

    def _on_body_content_ready(self, html: str):
        """Slot called from MessageBodyWorkerThread when HTML is ready."""
        self.message_loading_label.setVisible(False)
        self._current_html = html or None
        self.open_browser_btn.setEnabled(bool(html))
        self.save_debug_html_btn.setEnabled(bool(html))
        if html:
            self.message_body.setHtml(html)
        else:
            self.message_body.clear()
        # setHtml() posts a QEvent::LayoutRequest that is processed
        # asynchronously.  A singleShot(0) can fire before that event is
        # drained, so use a short but non-zero delay to ensure the layout pass
        # completes before we restore the splitter to the user's desired sizes.
        QTimer.singleShot(50, lambda: self.splitter.setSizes(self._splitter_sizes))

    def _on_body_load_error(self, error: str):
        """Slot called when body processing fails in the worker thread."""
        self.message_loading_label.setVisible(False)
        self.message_body.setPlainText(f"(Failed to load message content: {error})")

    def _open_in_browser(self):
        """Write the current message's processed HTML to a temp file and open it."""
        import tempfile
        import webbrowser

        if not self._current_html:
            return

        try:
            # delete=False so the file survives after we close the handle;
            # the OS will clean up the temp directory on its own schedule.
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".html",
                prefix="briefkorb_msg_",
                delete=False,
                encoding="utf-8",
            ) as f:
                f.write(self._current_html)
                path = f.name

            webbrowser.open(f"file://{path}")
        except Exception as e:
            QMessageBox.warning(self, "Open in Browser", f"Could not open message in browser:\n{e}")

    def _save_debug_html(self):
        """Save the current message's processed HTML (images stripped) to the project root."""
        if not self._current_html:
            return

        try:
            stripped = strip_images_for_debug(self._current_html)
            project_root = Path(__file__).parent.parent.parent.parent
            out_path = project_root / "debug_email.html"
            out_path.write_text(stripped, encoding="utf-8")
            self.statusBar.showMessage(f"Debug HTML saved to {out_path}")
        except Exception as e:
            QMessageBox.warning(self, "Save Debug HTML", f"Could not save debug HTML:\n{e}")

    def _previous_message(self):
        """Navigate to previous message in current group"""
        if self.current_group_index is None:
            return
        
        group = self.current_groups[self.current_group_index]
        if self.current_message_index > 0:
            self.current_message_index -= 1
            self._display_current_message()
    
    def _next_message(self):
        """Navigate to next message in current group"""
        if self.current_group_index is None:
            return
        
        group = self.current_groups[self.current_group_index]
        if self.current_message_index < len(group.messages) - 1:
            self.current_message_index += 1
            self._display_current_message()
    
    def _get_auth_provider_for_message(self, message):
        """Return the AuthenticatedProvider that owns the given message"""
        if not self.server:
            return None
        providers = self.server.get_authenticated_providers(message.provider)
        return providers[0] if providers else None

    def _mark_as_read(self):
        """Mark selected message as read"""
        if not hasattr(self, 'current_selected_message'):
            return
        
        message = self.current_selected_message
        if not self.server:
            return
        
        auth_prov = self._get_auth_provider_for_message(message)
        if not auth_prov:
            QMessageBox.warning(self, "Error", "Could not determine authenticated provider for this message.")
            return
        
        try:
            success = self.server.mark_messages_as_read(
                user_id=auth_prov.user_id,
                provider_name=message.provider,
                message_ids=[message.id]
            )
            if success:
                message.is_read = True
                # Update the group's unread count
                if self.current_group_index is not None:
                    group = self.current_groups[self.current_group_index]
                    # Refresh the display
                    self._update_message_list()
                    self._display_current_message()
                self.statusBar.showMessage("Message marked as read")
            else:
                QMessageBox.warning(self, "Error", "Failed to mark message as read")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to mark message as read: {str(e)}")
    
    def _mark_group_as_read(self):
        """Mark all messages in the current group as read"""
        if self.current_group_index is None or not self.server:
            return

        group = self.current_groups[self.current_group_index]
        unread = [m for m in group.messages if not m.is_read]
        if not unread:
            self.statusBar.showMessage("All messages in this group are already read")
            return

        # Group unread messages by provider so we make one API call per provider
        by_provider: dict = {}
        for message in unread:
            key = message.provider
            by_provider.setdefault(key, []).append(message)

        failed = 0
        for provider_name, messages in by_provider.items():
            auth_prov = self._get_auth_provider_for_message(messages[0])
            if not auth_prov:
                failed += len(messages)
                continue
            try:
                success = self.server.mark_messages_as_read(
                    user_id=auth_prov.user_id,
                    provider_name=provider_name,
                    message_ids=[m.id for m in messages]
                )
                if success:
                    for m in messages:
                        m.is_read = True
                else:
                    failed += len(messages)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to mark messages as read: {str(e)}")
                return

        self._update_message_list()
        self._display_current_message()
        if failed:
            self.statusBar.showMessage(f"Marked {len(unread) - failed}/{len(unread)} messages as read")
        else:
            self.statusBar.showMessage(f"Marked {len(unread)} message(s) as read")

    def _do_delete_group(self, group: MessageGroup) -> bool:
        """Delete all messages in a group without prompting. Returns True on full success."""
        if not self.server:
            return False

        by_provider: dict = {}
        for message in group.messages:
            by_provider.setdefault(message.provider, []).append(message)

        all_succeeded = True
        for provider_name, messages in by_provider.items():
            auth_prov = self._get_auth_provider_for_message(messages[0])
            if not auth_prov:
                all_succeeded = False
                continue
            try:
                success = self.server.delete_user_messages(
                    user_id=auth_prov.user_id,
                    provider_name=provider_name,
                    message_ids=[m.id for m in messages]
                )
                if success:
                    deleted_ids = {m.id for m in messages}
                    self.current_messages = [m for m in self.current_messages if m.id not in deleted_ids]
                else:
                    all_succeeded = False
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete messages: {str(e)}")
                return False

        if self.current_group_index is not None and self.current_groups[self.current_group_index] is group:
            self.current_groups.pop(self.current_group_index)
            self.current_group_index = None
            self.current_message_index = 0

        self._update_message_list()
        self.message_body.clear()
        self.subject_label.setText("Select a message group to view")
        self.metadata_label.clear()
        self.message_nav_label.setText("No messages")
        self.prev_msg_btn.setEnabled(False)
        self.next_msg_btn.setEnabled(False)
        self.mark_read_btn.setEnabled(False)
        self.mark_all_read_btn.setEnabled(False)
        self.delete_all_btn.setEnabled(False)
        self.block_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        return all_succeeded

    def _delete_group(self):
        """Delete all messages in the current group"""
        if self.current_group_index is None or not self.server:
            return

        group = self.current_groups[self.current_group_index]
        reply = QMessageBox.question(
            self,
            "Delete All",
            f"Delete all {group.count} message(s) from {group.sender_email}?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        if self._do_delete_group(group):
            self.statusBar.showMessage(f"Deleted all messages from {group.sender_email}")
        else:
            self.statusBar.showMessage(f"Some messages could not be deleted")

    def _block_sender(self):
        """Block the current group's sender and delete all their messages"""
        if self.current_group_index is None:
            return

        group = self.current_groups[self.current_group_index]
        sender = group.sender_email

        if not self.blocklist:
            QMessageBox.warning(self, "Error", "Blocklist is not available.")
            return

        reply = QMessageBox.question(
            self,
            "Block Sender",
            f"Block {sender} and delete all their messages?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.blocklist.block(sender)
        self._do_delete_group(group)
        self.statusBar.showMessage(f"Blocked {sender} and deleted their messages")

    def _delete_message(self):
        """Delete selected message"""
        if not hasattr(self, 'current_selected_message'):
            return
        
        message = self.current_selected_message
        reply = QMessageBox.question(
            self,
            "Delete Message",
            f"Are you sure you want to delete this message?\n\nSubject: {message.subject}",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        if not self.server:
            return
        
        auth_prov = self._get_auth_provider_for_message(message)
        if not auth_prov:
            QMessageBox.warning(self, "Error", "Could not determine authenticated provider for this message.")
            return
        
        try:
            success = self.server.delete_user_messages(
                user_id=auth_prov.user_id,
                provider_name=message.provider,
                message_ids=[message.id]
            )
            if success:
                # Remove message from current group
                if self.current_group_index is not None:
                    group = self.current_groups[self.current_group_index]
                    group.messages = [m for m in group.messages if m.id != message.id]
                    # Remove group if empty
                    if not group.messages:
                        self.current_groups.pop(self.current_group_index)
                        self.current_group_index = None
                        self.current_message_index = 0
                    else:
                        # Adjust message index if needed
                        if self.current_message_index >= len(group.messages):
                            self.current_message_index = len(group.messages) - 1
                        if self.current_message_index < 0:
                            self.current_message_index = 0
                
                # Remove from all messages list
                self.current_messages = [m for m in self.current_messages if m.id != message.id]
                
                self._update_message_list()
                
                # Update display
                if self.current_group_index is not None and self.current_group_index < len(self.current_groups):
                    self._display_current_message()
                else:
                    self.message_body.clear()
                    self.subject_label.setText("Select a message group to view")
                    self.metadata_label.clear()
                    self.message_nav_label.setText("No messages")
                    self.prev_msg_btn.setEnabled(False)
                    self.next_msg_btn.setEnabled(False)
                    self.mark_read_btn.setEnabled(False)
                    self.mark_all_read_btn.setEnabled(False)
                    self.delete_all_btn.setEnabled(False)
                    self.block_btn.setEnabled(False)
                    self.delete_btn.setEnabled(False)
                
                self.statusBar.showMessage("Message deleted")
            else:
                QMessageBox.warning(self, "Error", "Failed to delete message")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error deleting message: {str(e)}")
    
    def _compose_email(self):
        """Open compose email dialog"""
        if not self.server:
            QMessageBox.warning(
                self,
                "Not Ready",
                "Email server is not initialized. Please configure settings first."
            )
            return
        
        # Check if any providers are authenticated
        authenticated = self.server.get_authenticated_users()
        if not authenticated:
            QMessageBox.warning(
                self,
                "Not Authenticated",
                "No providers are authenticated. Please open Settings and authenticate at least one provider."
            )
            return
        
        # Check if send permission is available for at least one provider
        provider_text = self.provider_combo.currentText()
        if provider_text == "All":
            # Check if any authenticated provider has send permission
            has_send = False
            for provider_name in authenticated.keys():
                scopes = self.config.microsoft.scopes if provider_name == 'microsoft' else self.config.gmail.scopes
                if ScopeChecker.has_send_permission(scopes, provider_name):
                    has_send = True
                    break
            if not has_send:
                QMessageBox.warning(
                    self,
                    "Permission Denied",
                    "Send permission is not available for any authenticated provider. "
                    "Please configure appropriate scopes in Settings."
                )
                return
        else:
            provider_name = provider_text.lower()
            if provider_name not in authenticated:
                QMessageBox.warning(
                    self,
                    "Not Authenticated",
                    f"{provider_text} is not authenticated. Please authenticate this provider in Settings."
                )
                return
            scopes = self.config.microsoft.scopes if provider_name == 'microsoft' else self.config.gmail.scopes
            if not ScopeChecker.has_send_permission(scopes, provider_name):
                QMessageBox.warning(
                    self,
                    "Permission Denied",
                    f"Send permission is not available for {provider_text}. "
                    "Please configure appropriate scopes in Settings."
                )
                return
        
        # Get AuthenticatedProvider for the selected provider (or first available if "All")
        auth_providers = self.server.get_authenticated_providers()
        selected_auth_prov = None
        
        if provider_text == "All":
            # Use first authenticated provider with send permission
            for auth_prov in auth_providers:
                provider_name = auth_prov.provider_name
                scopes = self.config.microsoft.scopes if provider_name == 'microsoft' else self.config.gmail.scopes
                if ScopeChecker.has_send_permission(scopes, provider_name):
                    selected_auth_prov = auth_prov
                    break
            if not selected_auth_prov:
                QMessageBox.warning(self, "Error", "No authenticated provider with send permission available.")
                return
        else:
            provider_name = provider_text.lower()
            matching = [ap for ap in auth_providers if ap.provider_name == provider_name]
            if not matching:
                QMessageBox.warning(self, "Error", f"No authenticated user found for {provider_text}.")
                return
            selected_auth_prov = matching[0]
        
        dialog = ComposeDialog(self.server, selected_auth_prov.user_id, self)
        if dialog.exec():
            # Refresh messages after sending
            QTimer.singleShot(1000, self._load_messages)
    
    def _open_settings(self):
        """Open authentication settings dialog"""
        if not self.config_path:
            config_path = Path(__file__).parent.parent.parent / "email_server" / "config.yaml"
            self.config_path = str(config_path)
        
        # Load current config or create default
        if not self.config:
            if Path(self.config_path).exists():
                self.config = EmailServerConfig.from_file(self.config_path)
            else:
                # Create default config
                from email_server.config import create_default_config
                self.config = create_default_config(self.config_path)
        
        dialog = AuthSettingsDialog(self.config, self.config_path, self)
        if dialog.exec():
            # Reload configuration and server
            self._load_config()
            self._update_auth_status()  # Update auth status after settings change
            QMessageBox.information(
                self,
                "Settings Updated",
                "Configuration has been updated. The email server has been reloaded."
            )
