"""The peer panel: one row per nearby laptop, plus the arrow that hides it."""

from functools import partial

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QLayout, QVBoxLayout, QWidget

from connector.core.peers import Peer
from connector.ui.glass import Edge, GlassWindow
from connector.ui.style import HoverFade, Theme, draw_chevron, text_width, ui_font

AVATAR_COLORS = ["#5E8BFF", "#34C3A0", "#FF8A5C", "#B884FF", "#FF6FA8", "#F2B84B"]

# The hover highlight glides between rows. The edge in front moves quickly
# and the one behind follows more slowly, so it stretches as it moves and
# then settles, like a drop of liquid.
PILL_LEAD_MS = 150
PILL_TRAIL_MS = 300
PILL_RADIUS = 10


def avatar_color(name: str) -> QColor:
    # sum(ord) rather than hash(): Python randomises hash() for strings on
    # every run, and a person's colour should stay the same.
    return QColor(AVATAR_COLORS[sum(map(ord, name)) % len(AVATAR_COLORS)])


class PeerRow(QWidget):
    """One peer: avatar and name. In milestone D this becomes the drop target.

    The hover highlight isn't drawn here but by the panel, so it can glide
    from row to row.
    """

    hovered = Signal()

    HEIGHT = 40
    AVATAR = 26
    PAD_LEFT = 7  # same as the space above and below the avatar, so it sits evenly
    GAP = 10  # between avatar and name
    PAD_RIGHT = 14
    NAME_FONT_PX = 14
    MAX_NAME_WIDTH = 150  # longer names are cut off with "…"

    def __init__(self, peer: Peer, theme: Theme) -> None:
        super().__init__()
        self.peer = peer
        self._theme = theme

    def sizeHint(self) -> QSize:
        # Exactly wide enough for the avatar and this name, so the panel
        # can shrink to its widest row.
        name_width = min(text_width(self.peer.name, ui_font(self.NAME_FONT_PX), self), self.MAX_NAME_WIDTH)
        return QSize(self.PAD_LEFT + self.AVATAR + self.GAP + name_width + self.PAD_RIGHT, self.HEIGHT)

    def set_peer(self, peer: Peer) -> None:
        self.peer = peer
        self.updateGeometry()  # the name, and with it the width, may have changed
        self.update()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def enterEvent(self, event) -> None:
        self.hovered.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        size = self.AVATAR
        avatar = QRectF(self.PAD_LEFT, (self.height() - size) / 2, size, size)
        base = avatar_color(self.peer.name)
        gradient = QLinearGradient(avatar.topLeft(), avatar.bottomLeft())
        gradient.setColorAt(0, base.lighter(125))
        gradient.setColorAt(1, base)
        p.setBrush(gradient)
        p.drawEllipse(avatar)

        p.setPen(QColor(255, 255, 255))
        p.setFont(ui_font(12, QFont.Weight.DemiBold))
        p.drawText(avatar, Qt.AlignmentFlag.AlignCenter, self.peer.name[:1].upper())

        p.setPen(self._theme.text)
        p.setFont(ui_font(self.NAME_FONT_PX))
        text_left = avatar.right() + self.GAP
        text_rect = QRectF(text_left, 0, self.width() - text_left - self.PAD_RIGHT, self.height())
        name = p.fontMetrics().elidedText(
            self.peer.name, Qt.TextElideMode.ElideRight, int(text_rect.width())
        )
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)


class EmptyRow(QWidget):
    """Shown instead of rows while nobody else is around. The text breathes
    slowly, so it's clear the app is still looking."""

    TEXT = "Looking for others…"
    PAD = 12
    FONT_PX = 13

    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self._theme = theme
        self._strength = 0.6  # how strongly the text shows; the breathing changes it

        self._breathing = QVariantAnimation(self)
        self._breathing.setDuration(1600)
        self._breathing.setKeyValueAt(0.0, 0.35)
        self._breathing.setKeyValueAt(0.5, 0.75)
        self._breathing.setKeyValueAt(1.0, 0.35)
        self._breathing.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._breathing.setLoopCount(-1)  # forever, while shown
        self._breathing.valueChanged.connect(self._on_breath)

    def sizeHint(self) -> QSize:
        width = text_width(self.TEXT, ui_font(self.FONT_PX), self)
        return QSize(width + 2 * self.PAD, PeerRow.HEIGHT)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def showEvent(self, event) -> None:
        self._breathing.start()

    def hideEvent(self, event) -> None:
        self._breathing.stop()  # no point animating what nobody sees

    def _on_breath(self, value: float) -> None:
        self._strength = value
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        color = QColor(self._theme.text)
        color.setAlphaF(self._strength)
        p.setPen(color)
        p.setFont(ui_font(self.FONT_PX))
        p.drawText(
            QRectF(self.rect()).adjusted(self.PAD, 0, -self.PAD, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.TEXT,
        )


class CollapseHandle(QWidget):
    """The arrow on the side of the panel nearest the screen edge.
    Clicking it hides the panel off that edge."""

    clicked = Signal()
    hovered = Signal()
    WIDTH = 16

    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self._theme = theme
        self._pointing_right = True
        self._hover = HoverFade(self)
        self.setFixedWidth(self.WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Hide")

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def set_pointing_right(self, pointing_right: bool) -> None:
        self._pointing_right = pointing_right
        self.update()

    def enterEvent(self, event) -> None:
        self._hover.enter()
        self.hovered.emit()

    def leaveEvent(self, event) -> None:
        self._hover.leave()

    def mousePressEvent(self, event) -> None:
        # Take the press ourselves. Otherwise it's passed up to the panel,
        # and pressing the arrow would start dragging the panel instead.
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        # Like a button: it counts only if released over the handle.
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._hover.reset()
            self.clicked.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._hover.value > 0:
            hover = QColor(self._theme.hover)
            hover.setAlphaF(hover.alphaF() * self._hover.value)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(hover)
            p.drawRoundedRect(QRectF(self.rect()), 8, 8)

        # Quieter at rest, full strength on hover.
        color = QColor(self._theme.text)
        color.setAlphaF(0.55 + 0.45 * self._hover.value)
        draw_chevron(p, QRectF(self.rect()).center(), pointing_right=self._pointing_right, color=color)


class PeerWidget(GlassWindow):
    """The glass panel listing nearby peers."""

    collapse_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._edge: Edge = "right"

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(2)
        # The window is always exactly the size its contents ask for, and
        # resizes itself when rows are added or removed.
        self._layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(2)
        self._layout.addLayout(self._rows_layout)

        # One row per peer, by Peer.id. Filled in by add_peer()/remove_peer().
        self._rows: dict[str, PeerRow] = {}
        # Always first in the layout; hidden while there are rows. (A hidden
        # widget takes up no space in a layout.)
        self._empty = EmptyRow(self.theme)
        self._rows_layout.addWidget(self._empty)

        # The hover highlight: where it is now, how visible it is, and one
        # animation each for its top and bottom edge.
        self._pill = QRectF()
        self._pill_fade = HoverFade(self, 160)
        self._pill_edges: dict[str, QVariantAnimation] = {}
        for edge in ("top", "bottom"):
            animation = QVariantAnimation(self)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.valueChanged.connect(partial(self._on_pill_edge, edge))
            self._pill_edges[edge] = animation

        self._handle = CollapseHandle(self.theme)
        # Passing the handle's click straight on as our own signal.
        self._handle.clicked.connect(self.collapse_requested)
        # The arrow has its own highlight, so the glass one steps aside.
        self._handle.hovered.connect(self._pill_fade.leave)
        self._layout.addWidget(self._handle)

        # Text measures differently on screens with different scaling (your
        # laptop at 125% and the monitor at 100%), so re-measure the rows
        # when the panel is dragged from one to the other.
        self.windowHandle().screenChanged.connect(self._remeasure)

    def _remeasure(self) -> None:
        for row in self._rows.values():
            row.updateGeometry()
        self._empty.updateGeometry()

    # --- peers ----------------------------------------------------------

    def add_peer(self, peer: Peer) -> None:
        """Show a peer, or update its row if it's already shown."""
        row = self._rows.get(peer.id)
        if row is None:
            row = PeerRow(peer, self.theme)
            # partial() fixes `row` now. A lambda would look `row` up only
            # when called, and by then it could be a different row.
            row.hovered.connect(partial(self._glide_to, row))
            self._rows[peer.id] = row
        else:
            row.set_peer(peer)
            self._rows_layout.removeWidget(row)  # re-inserted below, in case its name moved it
        self._rows_layout.insertWidget(self._position_for(peer), row)
        self._rows_changed()

    def remove_peer(self, peer: Peer) -> None:
        row = self._rows.pop(peer.id, None)
        if row is None:
            return
        self._rows_layout.removeWidget(row)
        row.deleteLater()  # Qt deletes it once it's safe to, after this event
        self._rows_changed()

    def _position_for(self, peer: Peer) -> int:
        """Layout position that keeps rows in alphabetical order."""
        key = (peer.name.casefold(), peer.id)
        before = sum(
            1 for other in self._rows.values()
            if other.peer.id != peer.id and (other.peer.name.casefold(), other.peer.id) < key
        )
        return 1 + before  # 1: the empty-state row comes first

    def _rows_changed(self) -> None:
        self._empty.setVisible(not self._rows)
        # Rows have moved, so the highlight would now sit in the wrong place.
        # It comes back on the next hover.
        self._pill_fade.leave()

    def on_theme_changed(self) -> None:
        for row in self._rows.values():
            row.set_theme(self.theme)
        self._empty.set_theme(self.theme)
        self._handle.set_theme(self.theme)

    def on_drag_finished(self) -> None:
        self._set_edge(self.nearest_edge())

    def _set_edge(self, edge: Edge) -> None:
        # The arrow sits on the side facing the nearest screen edge and
        # points at it: that's where the panel goes when hidden.
        if edge == self._edge:
            return
        self._edge = edge
        self._layout.setDirection(
            QBoxLayout.Direction.RightToLeft if edge == "left" else QBoxLayout.Direction.LeftToRight
        )
        self._handle.set_pointing_right(edge == "right")

    # --- the gliding hover highlight --------------------------------------

    def _glide_to(self, row: PeerRow) -> None:
        target = QRectF(row.geometry())
        if self._pill_fade.value == 0:
            # Not showing yet: appear right on this row, no gliding.
            for animation in self._pill_edges.values():
                animation.stop()
            self._pill = target
        else:
            self._pill.setLeft(target.left())
            self._pill.setRight(target.right())
            moving_down = target.top() > self._pill.top()
            self._animate_pill_edge("top", target.top(), PILL_TRAIL_MS if moving_down else PILL_LEAD_MS)
            self._animate_pill_edge("bottom", target.bottom(), PILL_LEAD_MS if moving_down else PILL_TRAIL_MS)
        self._pill_fade.enter()

    def _animate_pill_edge(self, edge: str, end: float, duration_ms: int) -> None:
        animation = self._pill_edges[edge]
        animation.stop()
        animation.setStartValue(self._pill.top() if edge == "top" else self._pill.bottom())
        animation.setEndValue(end)
        animation.setDuration(duration_ms)
        animation.start()

    def _on_pill_edge(self, edge: str, value: float) -> None:
        if edge == "top":
            self._pill.setTop(value)
        else:
            self._pill.setBottom(value)
        self.update()

    def leaveEvent(self, event) -> None:
        self._pill_fade.leave()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._pill_fade.reset()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # the glass
        if self._pill_fade.value == 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = QColor(self.theme.hover)
        hover.setAlphaF(hover.alphaF() * self._pill_fade.value)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(hover)
        p.drawRoundedRect(self._pill, PILL_RADIUS, PILL_RADIUS)
