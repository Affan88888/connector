"""The pairing card: the six-digit code both people compare, and its buttons.

The same card serves both sides of a pairing:
- "Pairing with Bob" + code + [Cancel], while you wait for Bob to decide
- "Alice wants to pair" + code + [Decline] [Accept], when you're asked
- a short message afterwards ("Paired with Bob"), which fades away
"""

from collections.abc import Callable

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLayout, QVBoxLayout, QWidget

from connector.ui.glass import GlassWindow
from connector.ui.style import HoverFade, Theme, luminance, text_width, ui_font

CONTENT_WIDTH = 244
DONE_MS = 1800  # how long a "Paired with Bob" message stays


class GlassButton(QWidget):
    """A pill-shaped button drawn on the glass. `primary` fills it with the
    accent colour, for the button most people will want (Accept)."""

    clicked = Signal()
    HEIGHT = 30
    PAD = 16
    FONT_PX = 13

    def __init__(self, text: str, theme: Theme, primary: bool = False) -> None:
        super().__init__()
        self._text = text
        self._theme = theme
        self._primary = primary
        self._hover = HoverFade(self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        width = text_width(self._text, ui_font(self.FONT_PX, QFont.Weight.DemiBold), self)
        return QSize(width + 2 * self.PAD, self.HEIGHT)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def enterEvent(self, event) -> None:
        self._hover.enter()

    def leaveEvent(self, event) -> None:
        self._hover.leave()

    def mousePressEvent(self, event) -> None:
        event.accept()  # ours, so the release comes back to us

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._hover.reset()
            self.clicked.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._primary:
            fill = QColor(self._theme.drop)
            fill.setAlphaF(0.6 + 0.3 * self._hover.value)
            # Dark text on a light accent, white on a dark one.
            text = QColor(12, 22, 44) if luminance(self._theme.drop) > 0.5 else QColor(255, 255, 255)
        else:
            fill = QColor(self._theme.hover)
            fill.setAlphaF(min(1.0, fill.alphaF() * (1.4 + 1.4 * self._hover.value)))
            text = self._theme.text
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        p.setPen(text)
        p.setFont(ui_font(self.FONT_PX, QFont.Weight.DemiBold))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)


class CardText(QWidget):
    """The card's words: a headline, the code (if there is one) and a hint."""

    HEADLINE_PX = 13
    CODE_PX = 28
    HINT_PX = 12

    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self._theme = theme
        self._headline = ""
        self._code = ""
        self._hint = ""

    def set_content(self, headline: str, code: str, hint: str) -> None:
        self._headline, self._code, self._hint = headline, code, hint
        self.updateGeometry()  # the height depends on the content
        self.update()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def _hint_height(self) -> int:
        if not self._hint:
            return 0
        metrics = QFontMetrics(ui_font(self.HINT_PX), self)
        return metrics.boundingRect(QRect(0, 0, CONTENT_WIDTH, 1000), Qt.TextFlag.TextWordWrap, self._hint).height()

    def sizeHint(self) -> QSize:
        height = 20 + (44 if self._code else 0) + (4 + self._hint_height() if self._hint else 0)
        if self._code or self._hint:
            return QSize(CONTENT_WIDTH, height)
        # Just a message: only as wide as it needs.
        width = text_width(self._headline, ui_font(self.HEADLINE_PX, QFont.Weight.DemiBold), self)
        return QSize(min(width, CONTENT_WIDTH), height)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        y = 0
        p.setPen(self._theme.text)
        p.setFont(ui_font(self.HEADLINE_PX, QFont.Weight.DemiBold))
        headline = p.fontMetrics().elidedText(self._headline, Qt.TextElideMode.ElideRight, width)
        p.drawText(QRectF(0, y, width, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, headline)
        y += 20

        if self._code:
            font = ui_font(self.CODE_PX, QFont.Weight.DemiBold)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)  # easier to read out loud
            p.setFont(font)
            p.drawText(QRectF(0, y, width, 44), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._code)
            y += 44

        if self._hint:
            hint_color = QColor(self._theme.text)
            hint_color.setAlphaF(0.72)
            p.setPen(hint_color)
            p.setFont(ui_font(self.HINT_PX))
            p.drawText(
                QRectF(0, y + 4, width, self._hint_height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                self._hint,
            )


class PairingCard(GlassWindow):
    MOVABLE = False
    MARGIN = 14

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(self.MARGIN, self.MARGIN - 2, self.MARGIN, self.MARGIN)
        layout.setSpacing(12)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        self._text = CardText(self.theme)
        layout.addWidget(self._text)
        self._button_row = QHBoxLayout()
        self._button_row.setSpacing(8)
        self._button_row.addStretch(1)  # pushes the buttons to the right
        layout.addLayout(self._button_row)
        self._buttons: list[GlassButton] = []

        self._done_timer = QTimer(self)
        self._done_timer.setSingleShot(True)
        self._done_timer.setInterval(DONE_MS)
        self._done_timer.timeout.connect(self.fade_out)

    @property
    def waiting(self) -> bool:
        """True while it shows a question with buttons, rather than a message."""
        return self.isVisible() and bool(self._buttons)

    def show_question(
        self,
        headline: str,
        code: str,
        hint: str,
        buttons: list[tuple[str, bool, Callable[[], None]]],
        beside: QRect,
    ) -> None:
        """Show a code and buttons. Each button is (label, primary, action)."""
        self._done_timer.stop()
        self._text.set_content(headline, code, hint)
        self._set_buttons(buttons)
        self._show_beside(beside)

    def show_message(self, message: str, beside: QRect) -> None:
        """Show a one-line message that fades away by itself."""
        self._text.set_content(message, "", "")
        self._set_buttons([])
        self._show_beside(beside)
        self._done_timer.start()

    def close_card(self) -> None:
        self._done_timer.stop()
        self.fade_out()

    def _show_beside(self, beside: QRect) -> None:
        self.adjustSize()  # the size depends on the content
        self.place_beside(beside)
        self.fade_in()

    def _set_buttons(self, buttons: list[tuple[str, bool, Callable[[], None]]]) -> None:
        for button in self._buttons:
            self._button_row.removeWidget(button)
            button.deleteLater()
        self._buttons = []
        for label, primary, action in buttons:
            button = GlassButton(label, self.theme, primary)
            button.clicked.connect(action)
            self._button_row.addWidget(button)
            self._buttons.append(button)

    def on_theme_changed(self) -> None:
        self._text.set_theme(self.theme)
        for button in self._buttons:
            button.set_theme(self.theme)
