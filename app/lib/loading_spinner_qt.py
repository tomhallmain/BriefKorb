"""
Tiny indeterminate spinner badge next to the mode label (sidebar).

Used for long-running work such as prevalidation passes and base-directory
scans (including incremental directory loads). Draws a rotating arc segment
with QPainter; no GIF asset required. Hidden by default — call start() / stop().
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from email_client.ui.theme import ACCENT_COLOR


class LoadingSpinnerBadge(QWidget):
    """
    14×14 indeterminate spinner that paints a rotating arc.

    Sits inline with the sidebar mode label. The animation runs only while the
    widget is visible; start() / stop() drive both visibility and the timer.
    """

    _ARC_SPAN_DEG = 120   # length of the visible arc segment in degrees
    _STEP_DEG = 24         # rotation per tick
    _INTERVAL_MS = 50      # ~20 fps — smooth without burning CPU
    _SIZE = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(self._INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Show the spinner and begin animating."""
        self._angle = 0
        self.show()
        self._timer.start()

    def stop(self) -> None:
        """Stop animating and hide the spinner."""
        self._timer.stop()
        self.hide()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        self._angle = (self._angle + self._STEP_DEG) % 360
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(2, 2, -2, -2)
        pen = QPen(QColor(ACCENT_COLOR), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        # Qt arc angles: 1/16th degree units, counter-clockwise from 3 o'clock
        painter.drawArc(rect, self._angle * 16, self._ARC_SPAN_DEG * 16)
