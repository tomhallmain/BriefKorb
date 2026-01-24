"""
Theme and styling for the Email Client application
"""


def get_dark_theme_stylesheet() -> str:
    """Get dark theme stylesheet for the application with subtle teal coloration"""
    return """
    /* Main application colors - subtle teal tint */
    QMainWindow {
        background-color: #2b2d2c;
        color: #ffffff;
    }
    
    /* Widgets */
    QWidget {
        background-color: #2b2d2c;
        color: #ffffff;
    }
    
    /* Group boxes */
    QGroupBox {
        background-color: #353736;
        border: 1px solid #555857;
        border-radius: 5px;
        margin-top: 10px;
        padding-top: 10px;
        font-weight: bold;
    }
    
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        padding: 0 5px;
        background-color: #353736;
    }
    
    /* Buttons */
    QPushButton {
        background-color: #404342;
        border: 1px solid #555857;
        border-radius: 4px;
        padding: 6px 12px;
        color: #ffffff;
        font-weight: 500;
    }
    
    QPushButton:hover {
        background-color: #4a4d4c;
        border: 1px solid #666968;
    }
    
    QPushButton:pressed {
        background-color: #2d302f;
        border: 1px solid #444746;
    }
    
    QPushButton:disabled {
        background-color: #2d302f;
        border: 1px solid #3a3d3c;
        color: #666968;
    }
    
    /* Line edits */
    QLineEdit {
        background-color: #1e201f;
        border: 1px solid #555857;
        border-radius: 3px;
        padding: 5px;
        color: #ffffff;
        selection-background-color: #008a8f;
    }
    
    QLineEdit:focus {
        border: 1px solid #008a8f;
    }
    
    /* Combo boxes */
    QComboBox {
        background-color: #1e201f;
        border: 1px solid #555857;
        border-radius: 3px;
        padding: 5px;
        color: #ffffff;
    }
    
    QComboBox:hover {
        border: 1px solid #666968;
    }
    
    QComboBox:focus {
        border: 1px solid #008a8f;
    }
    
    QComboBox::drop-down {
        border: none;
        background-color: #404342;
        width: 20px;
    }
    
    QComboBox::down-arrow {
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid #ffffff;
        margin-right: 5px;
    }
    
    QComboBox QAbstractItemView {
        background-color: #2b2d2c;
        border: 1px solid #555857;
        selection-background-color: #008a8f;
        selection-color: #ffffff;
        color: #ffffff;
    }
    
    /* List widgets */
    QListWidget {
        background-color: #1e201f;
        border: 1px solid #555857;
        border-radius: 3px;
        color: #ffffff;
        outline: none;
    }
    
    QListWidget::item {
        padding: 5px;
        border-bottom: 1px solid #353736;
    }
    
    QListWidget::item:selected {
        background-color: #008a8f;
        color: #ffffff;
    }
    
    QListWidget::item:hover {
        background-color: #3a3d3c;
    }
    
    /* Text edits */
    QTextEdit {
        background-color: #1e201f;
        border: 1px solid #555857;
        border-radius: 3px;
        color: #ffffff;
        selection-background-color: #008a8f;
    }
    
    QTextEdit:focus {
        border: 1px solid #008a8f;
    }
    
    /* Labels */
    QLabel {
        color: #ffffff;
    }
    
    /* Status bar */
    QStatusBar {
        background-color: #1e201f;
        color: #ffffff;
        border-top: 1px solid #555857;
    }
    
    /* Progress bar */
    QProgressBar {
        background-color: #1e201f;
        border: 1px solid #555857;
        border-radius: 3px;
        text-align: center;
        color: #ffffff;
        height: 20px;
    }
    
    QProgressBar::chunk {
        background-color: #008a8f;
        border-radius: 2px;
    }
    
    /* Splitter */
    QSplitter::handle {
        background-color: #353736;
    }
    
    QSplitter::handle:horizontal {
        width: 3px;
    }
    
    QSplitter::handle:vertical {
        height: 3px;
    }
    
    QSplitter::handle:hover {
        background-color: #4a4d4c;
    }
    
    /* Scroll bars */
    QScrollBar:vertical {
        background-color: #1e201f;
        width: 12px;
        border: none;
    }
    
    QScrollBar::handle:vertical {
        background-color: #555857;
        border-radius: 6px;
        min-height: 20px;
        margin: 2px;
    }
    
    QScrollBar::handle:vertical:hover {
        background-color: #666968;
    }
    
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {
        height: 0px;
    }
    
    QScrollBar:horizontal {
        background-color: #1e201f;
        height: 12px;
        border: none;
    }
    
    QScrollBar::handle:horizontal {
        background-color: #555857;
        border-radius: 6px;
        min-width: 20px;
        margin: 2px;
    }
    
    QScrollBar::handle:horizontal:hover {
        background-color: #666968;
    }
    
    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {
        width: 0px;
    }
    
    /* Message boxes */
    QMessageBox {
        background-color: #2b2d2c;
        color: #ffffff;
    }
    
    QMessageBox QLabel {
        color: #ffffff;
    }
    
    QMessageBox QPushButton {
        background-color: #404342;
        border: 1px solid #555857;
        border-radius: 4px;
        padding: 6px 12px;
        color: #ffffff;
        min-width: 80px;
    }
    
    QMessageBox QPushButton:hover {
        background-color: #4a4d4c;
    }
    
    /* Dialogs */
    QDialog {
        background-color: #2b2d2c;
        color: #ffffff;
    }
    
    /* Check boxes */
    QCheckBox {
        color: #ffffff;
    }
    
    QCheckBox::indicator {
        width: 16px;
        height: 16px;
        border: 1px solid #555857;
        border-radius: 3px;
        background-color: #1e201f;
    }
    
    QCheckBox::indicator:checked {
        background-color: #008a8f;
        border: 1px solid #008a8f;
    }
    
    QCheckBox::indicator:hover {
        border: 1px solid #666968;
    }
    """
