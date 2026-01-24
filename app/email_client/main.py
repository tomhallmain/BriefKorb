"""
Main entry point for the Email Client GUI application
"""

import sys
import os
from pathlib import Path
import threading
import subprocess

# Add parent directory to path to import email_server
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from ui.main_window import MainWindow
from ui.theme import get_dark_theme_stylesheet


class DjangoServerThread(threading.Thread):
    """Thread to run Django development server for OAuth callbacks"""
    
    def __init__(self):
        super().__init__(daemon=True)
        self.app_dir = Path(__file__).parent.parent
        self.process = None
    
    def run(self):
        """Run Django server in background"""
        try:
            # Change to app directory
            original_cwd = os.getcwd()
            os.chdir(str(self.app_dir))
            
            # Run Django server using subprocess
            # Use --noreload to avoid file watching issues
            self.process = subprocess.Popen(
                [sys.executable, 'manage.py', 'runserver', '127.0.0.1:8000', '--noreload'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Wait for process to complete (or until interrupted)
            self.process.wait()
        except Exception as e:
            print(f"Error starting Django server: {e}")
        finally:
            if 'original_cwd' in locals():
                os.chdir(original_cwd)
    
    def stop(self):
        """Stop the Django server"""
        if self.process:
            try:
                # Try graceful termination first
                self.process.terminate()
                try:
                    # Wait up to 3 seconds for graceful shutdown
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't terminate gracefully
                    print("Django server did not terminate gracefully, forcing shutdown...")
                    self.process.kill()
                    self.process.wait()
            except Exception as e:
                print(f"Error stopping Django server: {e}")
                # Try to kill it anyway
                try:
                    if self.process.poll() is None:  # Process still running
                        self.process.kill()
                        self.process.wait()
                except:
                    pass


def start_django_server():
    """Start Django server in background thread for OAuth callbacks"""
    try:
        server_thread = DjangoServerThread()
        server_thread.start()
        # Give it a moment to start
        import time
        time.sleep(0.5)
        print("Django server started on http://127.0.0.1:8000 for OAuth callbacks")
        return server_thread
    except Exception as e:
        print(f"Warning: Could not start Django server: {e}")
        print("OAuth callbacks may not work properly")
        return None


def main():
    """Launch the email client application"""
    app = QApplication(sys.argv)
    app.setApplicationName("BriefKorb")
    app.setOrganizationName("BriefKorb")
    
    # Enable high DPI scaling
    app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    # Apply dark theme
    app.setStyleSheet(get_dark_theme_stylesheet())
    
    # Start Django server in background for OAuth callbacks
    django_thread = start_django_server()
    
    # Set up cleanup on application exit
    def cleanup():
        """Clean up resources on application exit"""
        if django_thread and django_thread.process:
            print("Stopping Django server...")
            django_thread.stop()
            print("Django server stopped.")
    
    app.aboutToQuit.connect(cleanup)
    
    window = MainWindow()
    window.show()
    
    try:
        sys.exit(app.exec())
    finally:
        # Ensure cleanup happens even if exec() doesn't return normally
        cleanup()

if __name__ == "__main__":
    main()
