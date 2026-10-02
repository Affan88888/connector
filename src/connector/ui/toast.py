"""A short glass note saying someone sent you a link, shown beside the panel."""

from urllib.parse import urlsplit

from PySide6.QtCore import QPoint, QPropertyAnimation, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter

from connector.core.links import Link
from connector.ui.glass import GlassWindow
from connector.ui.style import text_width, ui_font
from connector.ui.widget import avatar_color


class Toast(GlassWindow):
    MOVABLE = False
    SHOW_MS = 4500  # then it fades away by itself; clicking it closes it sooner
    FADE_MS = 180
    GAP = 10  # between the toast and the panel (or tab) it appears beside
    PAD = 10
    AVATAR = 28
    HEIGHT = 52
    MAX_TEXT_WIDTH = 230  # longer titles are cut off with "…"
    HEADLINE_PX = 13
    DETAIL_PX = 12

    def __init__(self) -> None:
        super().__init__()
        self._sender = ""
        self._headline = ""
        self._detail = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Dismiss")

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.SHOW_MS)
        self._timer.timeout.connect(self.dismiss)

        # windowOpacity fades the whole window, glass and all.
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(self.FADE_MS)
        self._fade.finished.connect(self._after_fade)

    def show_link(self, sender: str, link: Link, beside: QRect) -> None:
        """Show who sent what, next to `beside` (the panel, or the tab)."""
        self._sender = sender
        self._headline = f"{sender} sent you a link"
        # The page title if we have it, otherwise just the site, e.g. "github.com".
        self._detail = link.title or urlsplit(link.url).netloc or link.url

        text = max(
            text_width(self._headline, ui_font(self.HEADLINE_PX, QFont.Weight.DemiBold), self),
            text_width(self._detail, ui_font(self.DETAIL_PX), self),
        )
        width = self.PAD + self.AVATAR + self.PAD + min(text, self.MAX_TEXT_WIDTH) + self.PAD + 4
        self.setFixedSize(width, self.HEIGHT)
        self.move(self._position_beside(beside))

        # A second link while it's showing just replaces the text and
        # restarts the countdown.
        self._timer.start()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self._fade_to(1.0)
        self.update()

    def dismiss(self) -> None:
        self._timer.stop()
        self._fade_to(0.0)

    def _fade_to(self, opacity: float) -> None:
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(opacity)
        self._fade.start()

    def _after_fade(self) -> None:
        if self.windowOpacity() < 0.01:
            self.hide()

    def _position_beside(self, beside: QRect) -> QPoint:
        screen = QGuiApplication.screenAt(beside.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        # On the side facing the middle of the screen, so it has room.
        if beside.center().x() > area.center().x():
            x = beside.x() - self.GAP - self.width()
        else:
            x = beside.x() + beside.width() + self.GAP
        y = beside.center().y() - self.height() // 2
        return self.clamped(QPoint(x, y), area)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.dismiss()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # the glass
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        size = self.AVATAR
        avatar = QRectF(self.PAD, (self.height() - size) / 2, size, size)
        base = avatar_color(self._sender)
        gradient = QLinearGradient(avatar.topLeft(), avatar.bottomLeft())
        gradient.setColorAt(0, base.lighter(125))
        gradient.setColorAt(1, base)
        p.setBrush(gradient)
        p.drawEllipse(avatar)
        p.setPen(QColor(255, 255, 255))
        p.setFont(ui_font(12, QFont.Weight.DemiBold))
        p.drawText(avatar, Qt.AlignmentFlag.AlignCenter, self._sender[:1].upper())

        left = avatar.right() + self.PAD
        text_w = self.width() - left - self.PAD
        middle = self.height() / 2

        p.setPen(self.theme.text)
        p.setFont(ui_font(self.HEADLINE_PX, QFont.Weight.DemiBold))
        headline = p.fontMetrics().elidedText(self._headline, Qt.TextElideMode.ElideRight, int(text_w))
        p.drawText(QRectF(left, middle - 18, text_w, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, headline)

        detail_color = QColor(self.theme.text)
        detail_color.setAlphaF(0.7)
        p.setPen(detail_color)
        p.setFont(ui_font(self.DETAIL_PX))
        detail = p.fontMetrics().elidedText(self._detail, Qt.TextElideMode.ElideRight, int(text_w))
        p.drawText(QRectF(left, middle + 1, text_w, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, detail)
