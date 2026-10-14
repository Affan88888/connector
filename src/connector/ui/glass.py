"""See-through glass windows.

A GlassWindow grabs the part of the screen behind it, blurs it lightly and
paints it as its own background. For that grab to show what's behind the
window rather than the window itself, the window is left out of screen
captures. Only Windows supports that for now; elsewhere the glass falls back
to an opaque background.

A GlassWindow can also be dragged anywhere on the screen with the mouse.
"""

import ctypes
import sys
from typing import Literal

from PySide6.QtCore import QPoint, QPointF, QPropertyAnimation, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
)
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene, QWidget

from connector.ui.optics import STRENGTH, extend_edges, refract
from connector.ui.style import ON_DARK, ON_LIGHT, RADIUS, Theme, luminance, theme_for_system

Edge = Literal["left", "right"]

CAPTURE_INTERVAL_MS = 125  # refresh the view of what's behind, 8 times a second
FADE_MS = 180  # fade_in() and fade_out()
REFRACTION = STRENGTH + 4  # px of the surroundings grabbed for the edges to bend in
BLUR_RADIUS = 6  # blur strength, applied at half size
BLUR_PAD = 12  # extra px grabbed so the blur has real pixels to sample at the edges

_WDA_EXCLUDEFROMCAPTURE = 0x11
# Older Windows 10 builds accept the call but black the window out in captures
# instead of leaving it out, which would turn the glass black.
_MIN_WINDOWS_BUILD = 19041  # Windows 10, version 2004
_DWMWA_TRANSITIONS_FORCEDISABLED = 3


def exclude_from_capture(widget: QWidget) -> bool:
    """Leave a top-level widget out of all screen captures. Returns True if it worked."""
    if sys.platform != "win32" or sys.getwindowsversion().build < _MIN_WINDOWS_BUILD:
        return False

    from ctypes import wintypes

    hwnd = wintypes.HWND(int(widget.winId()))
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE))
    except (AttributeError, OSError):
        return False


def disable_window_animations(widget: QWidget) -> None:
    """Turn off Windows' own fade when a window is shown or hidden.

    We animate the windows ourselves, and a screen grab taken while Windows
    is still fading a window waits for the fade to finish (~300 ms).
    """
    if sys.platform != "win32":
        return

    from ctypes import wintypes

    hwnd = wintypes.HWND(int(widget.winId()))
    disabled = ctypes.c_int(1)
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, _DWMWA_TRANSITIONS_FORCEDISABLED, ctypes.byref(disabled), ctypes.sizeof(disabled)
        )
    except (AttributeError, OSError):
        pass


def blur(image: QImage, radius: float) -> QImage:
    """Blur an image with Qt's own blur effect.

    QGraphicsBlurEffect only works on items in a QGraphicsScene, so the image
    goes into a throwaway scene, which is then rendered into a new image.
    """
    item = QGraphicsPixmapItem(QPixmap.fromImage(image))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene = QGraphicsScene()
    scene.addItem(item)

    result = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(Qt.GlobalColor.transparent)
    p = QPainter(result)
    scene.render(p, QRectF(result.rect()), QRectF(image.rect()))
    p.end()
    return result


class GlassWindow(QWidget):
    """A frameless, always-on-top window with see-through glass behind its
    contents, which you can drag anywhere on the screen."""

    MOVABLE = True  # subclasses set this to False to opt out of dragging

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            # Clicking the widget shouldn't take keyboard focus away from
            # the app you're typing in.
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        # Our paintEvent decides every pixel, including see-through ones.
        # Must be set before the native window exists.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.theme = theme_for_system()
        self.sliding = False  # True while SideDock animates us; no captures then
        self._clip: QRect | None = None  # while sliding, only this part of the desktop shows us
        self._backdrop: QImage | None = None  # blurred view of what's behind

        # Dragging: where the mouse was pressed, and whether it became a drag.
        self._press_global: QPoint | None = None
        self._press_pos = QPoint()
        self._dragging = False
        # While dragging, one blurred picture of the whole screen. Each step
        # of the drag cuts the right piece out of it instead of grabbing the
        # screen again, which would be too slow to keep up with the mouse.
        self._snapshot: QImage | None = None
        self._snapshot_screen = ""  # name of the screen it was taken on

        self._capture_timer = QTimer(self)
        self._capture_timer.setInterval(CAPTURE_INTERVAL_MS)
        self._capture_timer.timeout.connect(self._on_capture_timer)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(FADE_MS)
        self._fade.finished.connect(self._after_fade)

        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_scheme_changed)

        # winId() inside creates the native window now, so it's left out of
        # captures before it's ever shown. Creating it already sends a
        # moveEvent, which reads _live, hence the False first.
        self._live = False
        self._live = exclude_from_capture(self)
        disable_window_animations(self)

    # --- position -------------------------------------------------------

    def clamped(self, pos: QPoint, area: QRect | None = None) -> QPoint:
        """`pos`, nudged so the whole window stays inside `area`
        (by default the usable area of the window's screen)."""
        if area is None:
            area = self.screen().availableGeometry()
        x = min(max(pos.x(), area.x()), area.x() + area.width() - self.width())
        y = min(max(pos.y(), area.y()), area.y() + area.height() - self.height())
        return QPoint(x, y)

    def nearest_edge(self) -> Edge:
        """The side edge of the screen, left or right, closest to the window's middle."""
        area = self.screen().availableGeometry()
        return "left" if self.geometry().center().x() < area.center().x() else "right"

    def place_beside(self, other: QRect, gap: int = 10) -> None:
        """Move next to another window's rectangle, on the side facing the
        middle of the screen (where there's room), level with its middle."""
        screen = QGuiApplication.screenAt(other.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        if other.center().x() > area.center().x():
            x = other.x() - gap - self.width()
        else:
            x = other.x() + other.width() + gap
        y = other.center().y() - self.height() // 2
        self.move(self.clamped(QPoint(x, y), area))

    def fade_in(self) -> None:
        """Show the window, fading it in."""
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self._fade_to(1.0)

    def fade_out(self) -> None:
        """Fade the window out, then hide it."""
        if self.isVisible():
            self._fade_to(0.0)

    def _fade_to(self, opacity: float) -> None:
        # windowOpacity fades the whole window, glass and all.
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(opacity)
        self._fade.start()

    def _after_fade(self) -> None:
        if self.windowOpacity() < 0.01:
            self.hide()

    def set_sliding(self, sliding: bool, clip: QRect | None = None) -> None:
        """Called by SideDock around its animations.

        Capturing takes ~15 ms, longer than one animation frame, so it's
        paused while sliding and done once the window has landed. With
        `clip`, any part of the window outside that area isn't drawn, so it
        disappears behind the screen edge instead of showing up on a monitor
        next to it.
        """
        self.sliding = sliding
        self._clip = clip if sliding else None
        if sliding:
            self._apply_clip()
        else:
            self.clearMask()
            if self.isVisible():
                self.refresh_backdrop()

    def _apply_clip(self) -> None:
        if self._clip is None:
            return
        visible = self.geometry().intersected(self._clip).translated(-self.pos())
        if visible.isEmpty():
            # An empty mask means "no mask" to Qt, which would show the whole
            # window. A 1 px rect just outside it shows nothing instead.
            visible = QRect(-1, -1, 1, 1)
        self.setMask(QRegion(visible))

    # --- shape and theme ------------------------------------------------

    def glass_geometry(self, rect: QRectF) -> tuple[QRectF, float]:
        """The glass's rounded rectangle and corner radius, for a window of
        size `rect`. Subclasses can override it to change the shape."""
        return rect, RADIUS

    def glass_shape(self, rect: QRectF) -> QPainterPath:
        """The outline of the glass, as a path to paint with."""
        box, radius = self.glass_geometry(rect)
        path = QPainterPath()
        path.addRoundedRect(box, radius, radius)
        return path

    def set_theme(self, theme: Theme) -> None:
        if theme is self.theme:
            return
        self.theme = theme
        self.on_theme_changed()
        self.update()

    def on_theme_changed(self) -> None:
        """Called after the theme changes. Subclasses pass it on to their children."""

    def _on_scheme_changed(self, scheme: Qt.ColorScheme) -> None:
        # With live glass, the background decides the theme instead.
        if not self._live:
            self.set_theme(theme_for_system())

    # --- dragging -------------------------------------------------------

    # A press on a child widget that doesn't handle the mouse itself (like a
    # PeerRow) is passed up to us, so the whole window works as a handle.

    def mousePressEvent(self, event) -> None:
        if self.MOVABLE and event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_pos = self.pos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_global is None:
            super().mouseMoveEvent(event)
            return
        cursor = event.globalPosition().toPoint()
        delta = cursor - self._press_global
        if not self._dragging:
            # A tiny wobble during a click shouldn't move the window.
            if delta.manhattanLength() < QGuiApplication.styleHints().startDragDistance():
                return
            self._start_drag()
        # Keep it fully on the screen the cursor is on.
        screen = QGuiApplication.screenAt(cursor) or self.screen()
        self.move(self.clamped(self._press_pos + delta, screen.availableGeometry()))

    def mouseReleaseEvent(self, event) -> None:
        if self._press_global is not None and event.button() == Qt.MouseButton.LeftButton:
            self._press_global = None
            if self._dragging:
                self._end_drag()
            else:
                # Pressed and released without moving: a click, not a drag.
                self.on_clicked(self.mapFromGlobal(event.globalPosition().toPoint()))
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def on_clicked(self, pos: QPoint) -> None:
        """Called for a click that didn't become a drag, with its position
        in this window. Subclasses can react."""

    def _start_drag(self) -> None:
        self._dragging = True
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._take_snapshot()

    def _end_drag(self) -> None:
        self._dragging = False
        self.unsetCursor()
        self._snapshot = None
        self.refresh_backdrop()  # one fresh grab now that it has stopped
        self.on_drag_finished()

    def on_drag_finished(self) -> None:
        """Called after the user drops the window somewhere. Subclasses can react."""

    # --- events ---------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._live:
            if not self.sliding:
                self.refresh_backdrop()
            self._capture_timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._capture_timer.stop()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if self.sliding:
            self._apply_clip()
            return
        if self._dragging:
            self._backdrop_from_snapshot()
        else:
            self.refresh_backdrop()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        old = event.oldSize()
        if self.isVisible() and not self.sliding and old.isValid():
            # Grow or shrink around the middle (e.g. when peers join or
            # leave), and stay on screen.
            shift = QPoint((old.width() - self.width()) // 2, (old.height() - self.height()) // 2)
            self.move(self.clamped(self.pos() + shift))
            self.refresh_backdrop()

    def _on_capture_timer(self) -> None:
        if not self.sliding and not self._dragging:
            self.refresh_backdrop()

    # --- capture --------------------------------------------------------

    def refresh_backdrop(self, at: QPoint | None = None) -> None:
        """Capture and blur what's behind the window, at its current position
        or at `at` if given (SideDock uses that before sliding a window in)."""
        if not self._live:
            return
        screen = self.screen()
        dpr = self.devicePixelRatioF()
        pos = (self.pos() if at is None else at) - screen.geometry().topLeft()

        # We're left out of screen captures, so this grabs exactly what's
        # behind us. It grabs more than we cover: REFRACTION px of the
        # surroundings to bend in at the edges, plus BLUR_PAD px so the blur
        # samples real pixels near the edges instead of fading to transparent.
        margin = REFRACTION + BLUR_PAD
        area = QRect(pos.x() - margin, pos.y() - margin, self.width() + 2 * margin, self.height() + 2 * margin)
        # Only grab what's on the screen, and repeat its edge pixels for any
        # part past the screen edge, where a grab would give black.
        on_screen = area.intersected(QRect(QPoint(0, 0), screen.geometry().size()))
        shot = screen.grabWindow(
            0, on_screen.x(), on_screen.y(), on_screen.width(), on_screen.height()
        ).toImage()
        shot.setDevicePixelRatio(1)  # work in physical pixels from here on
        if on_screen != area:
            shot = extend_edges(
                shot,
                round((on_screen.left() - area.left()) * dpr),
                round((on_screen.top() - area.top()) * dpr),
                round((area.right() - on_screen.right()) * dpr),
                round((area.bottom() - on_screen.bottom()) * dpr),
            )

        # Blur at half size: a quarter of the pixels, and it gets smoothly
        # scaled back up anyway.
        half = shot.scaled(
            shot.width() // 2, shot.height() // 2,
            Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation,
        )
        blurred = blur(half, BLUR_RADIUS)

        # Cut off the padding, keeping the refraction margin.
        pad = round(BLUR_PAD * dpr / 2)
        self._set_backdrop(blurred.copy(pad, pad, blurred.width() - 2 * pad, blurred.height() - 2 * pad))

    def _take_snapshot(self) -> None:
        if not self._live:
            return
        screen = self.screen()
        shot = screen.grabWindow(0).toImage()  # the whole screen, minus us
        shot.setDevicePixelRatio(1)
        half = shot.scaled(
            shot.width() // 2, shot.height() // 2,
            Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation,
        )
        self._snapshot = blur(half, BLUR_RADIUS)
        self._snapshot_screen = screen.name()

    def _backdrop_from_snapshot(self) -> None:
        if self._snapshot is None:
            return
        if self.screen().name() != self._snapshot_screen:
            self._take_snapshot()  # dragged onto another monitor
        dpr = self.devicePixelRatioF()
        pos = self.pos() - self.screen().geometry().topLeft()
        scale = dpr / 2  # the snapshot is at half size
        piece = QRect(
            round((pos.x() - REFRACTION) * scale),
            round((pos.y() - REFRACTION) * scale),
            round((self.width() + 2 * REFRACTION) * scale),
            round((self.height() + 2 * REFRACTION) * scale),
        )
        # Right at the screen edge the piece sticks out of the snapshot:
        # take what's there and repeat its edge pixels for the rest.
        inside = piece.intersected(self._snapshot.rect())
        view = self._snapshot.copy(inside)
        if inside != piece:
            view = extend_edges(
                view,
                inside.left() - piece.left(),
                inside.top() - piece.top(),
                piece.right() - inside.right(),
                piece.bottom() - inside.bottom(),
            )
        self._set_backdrop(view)

    def _set_backdrop(self, blurred: QImage) -> None:
        """Turn a blurred, half-size view of what's behind us (REFRACTION px
        bigger than us on every side) into the background we paint."""
        dpr = self.devicePixelRatioF()
        scale = dpr / 2  # the blurred view is at half size

        # Bend the view near the edges (see optics.py). Done at half size,
        # where there are four times fewer pixels to work out.
        rect, radius = self.glass_geometry(QRectF(0, 0, self.width(), self.height()))
        box = (
            rect.x() * scale,
            rect.y() * scale,
            (rect.x() + rect.width()) * scale,
            (rect.y() + rect.height()) * scale,
        )
        view = refract(
            blurred,
            max(1, round(self.width() * scale)),
            max(1, round(self.height() * scale)),
            REFRACTION * scale,
            box,
            radius * scale,
            scale,
        )

        w, h = round(self.width() * dpr), round(self.height() * dpr)
        backdrop = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        backdrop.fill(Qt.GlobalColor.transparent)
        p = QPainter(backdrop)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # First the glass shape, solid, with antialiased (smooth) curves.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0))
        p.save()
        p.scale(dpr, dpr)  # so glass_shape() can work in normal (logical) pixels
        p.drawPath(self.glass_shape(QRectF(0, 0, self.width(), self.height())))
        p.restore()
        # Then the view, scaled up to full size. SourceIn keeps it only where
        # the shape is, so the corners outside it stay see-through. The
        # smooth scaling also softens the steps between the bent pixels.
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.drawImage(QRectF(0, 0, w, h), view)
        p.end()

        backdrop.setDevicePixelRatio(dpr)
        self._backdrop = backdrop
        self._adapt_to_background(blurred)
        self.update()

    def _adapt_to_background(self, image: QImage) -> None:
        average = image.scaled(
            1, 1, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
        ).pixelColor(0, 0)
        brightness = luminance(average)
        # Two thresholds, so a mid-grey background doesn't make it flicker
        # between themes.
        if self.theme is ON_DARK and brightness > 0.62:
            self.set_theme(ON_LIGHT)
        elif self.theme is ON_LIGHT and brightness < 0.48:
            self.set_theme(ON_DARK)

    # --- painting -------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.theme
        # Half-pixel inset so the 1 px rim lands exactly on whole pixels.
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        shape = self.glass_shape(rect)

        # 0. the view of what's behind us, already blurred and cut to shape
        if self._backdrop is not None:
            p.drawImage(QPointF(0, 0), self._backdrop)
            tint = t.tint
        else:
            tint = t.tint_opaque

        # 1. a light tint over it
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tint)
        p.drawPath(shape)

        # 2. sheen: light falling on the top half of the glass
        sheen_end = QColor(t.sheen)
        sheen_end.setAlpha(0)
        sheen = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.5))
        sheen.setColorAt(0, t.sheen)
        sheen.setColorAt(1, sheen_end)
        p.setBrush(sheen)
        p.drawPath(shape)

        # 3. a soft glow just inside the edge, which makes the glass look
        # thick: brightest where the light hits, top left, and again
        # bottom right, fading out in between.
        glow = QLinearGradient(rect.topLeft(), rect.bottomRight())
        glow_none = QColor(t.glow)
        glow_none.setAlpha(0)
        glow.setColorAt(0, t.glow)
        glow.setColorAt(0.5, glow_none)
        glow.setColorAt(1, t.glow)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QBrush(glow), 3))
        p.drawPath(self.glass_shape(rect.adjusted(1.5, 1.5, -1.5, -1.5)))

        # 4. rim: a thin bright edge where the light catches it, top left,
        # and a fainter one bottom right, like the two highlights on a lens
        rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
        rim.setColorAt(0, t.rim_light)
        rim.setColorAt(0.5, t.rim_dim)
        rim.setColorAt(1, t.rim_far)
        p.setPen(QPen(QBrush(rim), 1))
        p.drawPath(shape)
