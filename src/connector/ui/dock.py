"""Shows the peer panel, and slides it off the nearest side edge of the screen
and back, leaving a small arrow tab at that edge while it's hidden."""

from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter

from connector.core.links import Link
from connector.ui.glass import Edge, GlassWindow
from connector.ui.links import link_from_drag
from connector.ui.pairing import PairingCard
from connector.ui.style import HoverFade, draw_chevron
from connector.ui.toast import Toast
from connector.ui.widget import PeerWidget


def springy(kind: QEasingCurve.Type, overshoot: float) -> QEasingCurve:
    """An easing curve that goes past its end (OutBack) or pulls back before
    starting (InBack) by `overshoot`: 0 is none, Qt's default is about 1.7."""
    curve = QEasingCurve(kind)
    curve.setOvershoot(overshoot)
    return curve


class EdgeTab(GlassWindow):
    """The small tab left at the screen edge while the panel is hidden.

    Click it to bring the panel back, or hold a dragged link over it for a
    moment and it opens by itself, so you can drop the link on a name
    without letting go first.
    """

    clicked = Signal()
    link_held = Signal()  # a dragged link has been held over the tab
    MOVABLE = False  # it belongs at the edge; clicking it is all it does
    SIZE = QSize(18, 48)
    CORNER = 12
    HOLD_MS = 250  # long enough not to open when a drag merely passes by

    def __init__(self) -> None:
        super().__init__()
        self._edge: Edge = "right"
        self.setFixedSize(self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Show")
        self._hover = HoverFade(self)

        self.setAcceptDrops(True)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.setInterval(self.HOLD_MS)
        self._hold.timeout.connect(self.link_held)

    def set_edge(self, edge: Edge) -> None:
        self._edge = edge
        self.update()

    def glass_geometry(self, rect: QRectF) -> tuple[QRectF, float]:
        # Round only the two corners facing into the screen. The rectangle
        # runs past the window on the edge side, so the corners there fall
        # outside the window and get cut off.
        if self._edge == "right":
            return rect.adjusted(0, 0, self.CORNER, 0), self.CORNER
        return rect.adjusted(-self.CORNER, 0, 0, 0), self.CORNER

    def enterEvent(self, event) -> None:
        self._hover.enter()

    def leaveEvent(self, event) -> None:
        self._hover.leave()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._hover.reset()
            self.clicked.emit()

    def dragEnterEvent(self, event) -> None:
        if link_from_drag(event.mimeData()) is None:
            event.ignore()
            return
        # Accepting is what makes Qt tell us when the drag leaves again.
        event.acceptProposedAction()
        self._hover.enter()
        self._hold.start()

    def dragLeaveEvent(self, event) -> None:
        self._hold.stop()  # passed by without stopping: stay hidden
        self._hover.leave()

    def dropEvent(self, event) -> None:
        # Let go on the tab itself: there's no one here to send it to, so
        # just open the panel.
        event.ignore()
        self._hold.stop()
        self._hover.reset()
        self.link_held.emit()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # the glass
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._hover.value > 0:
            hover = QColor(self.theme.hover)
            hover.setAlphaF(hover.alphaF() * self._hover.value)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(hover)
            p.drawPath(self.glass_shape(QRectF(self.rect())))

        # Points back into the screen, the way the panel will come.
        color = QColor(self.theme.text)
        color.setAlphaF(0.7 + 0.3 * self._hover.value)
        draw_chevron(p, QRectF(self.rect()).center(), pointing_right=self._edge == "left", color=color)


class SideDock(QObject):
    """Owns the panel and the tab, and animates between them."""

    START_MARGIN = 12  # the panel first appears this far from the right edge

    def __init__(self) -> None:
        super().__init__()
        self.panel = PeerWidget()
        self.tab = EdgeTab()
        self.toast = Toast()
        self.card = PairingCard()
        self.panel.collapse_requested.connect(self.collapse)
        self.tab.clicked.connect(self.expand)
        self.tab.link_held.connect(self.expand)
        self._animation: QPropertyAnimation | None = None

        # Remembered when the panel is hidden, to bring it back to the same place.
        self._home = QPoint()
        self._edge: Edge = "right"
        self._area = QRect()  # usable area of the screen it was hidden on

    def show(self) -> None:
        # Size and place the panel before showing it, so it never flashes
        # up somewhere else first. It starts vertically centred on the
        # right edge of the main screen (Windows would otherwise pick the
        # monitor the mouse is on); after that it's wherever you drag it.
        self.panel.adjustSize()
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.panel.move(
            area.x() + area.width() - self.panel.width() - self.START_MARGIN,
            area.y() + (area.height() - self.panel.height()) // 2,
        )
        self.panel.show()

    def beside(self) -> QRect:
        """Where notes like the toast and the pairing card go next to: the
        panel, or the tab while the panel is hidden."""
        return self.panel.geometry() if self.panel.isVisible() else self.tab.geometry()

    def show_toast(self, sender: str, link: Link) -> None:
        """Say who sent a link, beside the panel (or the tab, if it's hidden)."""
        if self.card.waiting:
            # The card is asking a question: don't cover it, go next to it.
            self.toast.show_link(sender, link, self.card.geometry())
            return
        self.card.close_card()  # a leftover "Paired with…" message makes way
        self.toast.show_link(sender, link, self.beside())

    def collapse(self) -> None:
        if self._animation is not None:  # already moving
            return
        self._home = self.panel.pos()
        self._edge = self.panel.nearest_edge()
        self._area = self.panel.screen().availableGeometry()
        end = self._past_edge(self.panel, self._home.y())
        # InBack: a small pull back first, like a drop gathering itself
        # before it moves, then away.
        self._slide(self.panel, end, springy(QEasingCurve.Type.InBack, 0.9), 260, self._after_collapse)

    def _after_collapse(self) -> None:
        self.panel.hide()
        self.tab.set_edge(self._edge)
        # Level with the middle of where the panel was, kept on screen.
        middle_y = self._home.y() + self.panel.height() // 2
        if self._edge == "right":
            x = self._area.x() + self._area.width() - self.tab.width()
        else:
            x = self._area.x()
        target = self.tab.clamped(QPoint(x, middle_y - self.tab.height() // 2), self._area)
        self._slide_in(self.tab, target, 260)

    def expand(self) -> None:
        if self._animation is not None:
            return
        end = self._past_edge(self.tab, self.tab.y())
        self._slide(self.tab, end, springy(QEasingCurve.Type.InBack, 0.9), 180, self._after_expand)

    def _after_expand(self) -> None:
        self.tab.hide()
        self.panel.adjustSize()  # it may have grown or shrunk while hidden
        self._slide_in(self.panel, self.panel.clamped(self._home, self._area), 340)

    def _past_edge(self, window: GlassWindow, y: int) -> QPoint:
        """Just past the screen edge we're hiding at, at height y."""
        if self._edge == "right":
            return QPoint(self._area.x() + self._area.width(), y)
        return QPoint(self._area.x() - window.width(), y)

    def _slide_in(self, window: GlassWindow, target: QPoint, duration_ms: int) -> None:
        window.set_sliding(True, clip=self._area)
        # Capture the spot it's heading for, so the glass is ready on landing.
        window.refresh_backdrop(at=target)
        window.move(self._past_edge(window, target.y()))
        window.show()
        # OutBack: it shoots slightly past where it's going and settles back,
        # like liquid sloshing to a stop.
        self._slide(window, target, springy(QEasingCurve.Type.OutBack, 1.1), duration_ms, None)

    def _slide(
        self,
        window: GlassWindow,
        end: QPoint,
        curve: QEasingCurve,
        duration_ms: int,
        then: Callable[[], None] | None,
    ) -> None:
        window.set_sliding(True, clip=self._area)
        animation = QPropertyAnimation(window, b"pos", self)
        animation.setDuration(duration_ms)
        animation.setStartValue(window.pos())
        animation.setEndValue(end)
        animation.setEasingCurve(curve)

        def finished() -> None:
            self._animation = None
            if then is not None:
                then()
            window.set_sliding(False)

        animation.finished.connect(finished)
        self._animation = animation  # also keeps it alive while it runs
        animation.start()
