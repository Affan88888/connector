"""Colours, fonts and small drawing helpers shared by the widget's windows."""

import math
from dataclasses import dataclass

from PySide6.QtCore import QAbstractAnimation, QPointF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

RADIUS = 16  # corner radius of the glass


@dataclass(frozen=True)
class Theme:
    tint: QColor  # laid over the captured background
    tint_opaque: QColor  # used when we can't see behind the window
    sheen: QColor  # soft highlight across the top of the glass
    rim_light: QColor  # the thin edge where the light catches it, top left
    rim_dim: QColor  # the edge in between
    rim_far: QColor  # the fainter second highlight, bottom right
    glow: QColor  # soft glow just inside the edge, at its brightest
    text: QColor
    hover: QColor


# Light text, for glass over something dark.
ON_DARK = Theme(
    tint=QColor(255, 255, 255, 24),
    tint_opaque=QColor(30, 32, 36, 235),
    sheen=QColor(255, 255, 255, 30),
    rim_light=QColor(255, 255, 255, 95),
    rim_dim=QColor(255, 255, 255, 10),
    rim_far=QColor(255, 255, 255, 45),
    glow=QColor(255, 255, 255, 20),
    text=QColor(255, 255, 255),
    hover=QColor(255, 255, 255, 36),
)

# Dark text, for glass over something bright.
ON_LIGHT = Theme(
    tint=QColor(255, 255, 255, 80),
    tint_opaque=QColor(246, 246, 248, 235),
    sheen=QColor(255, 255, 255, 90),
    rim_light=QColor(255, 255, 255, 190),
    rim_dim=QColor(0, 0, 0, 12),
    rim_far=QColor(255, 255, 255, 120),
    glow=QColor(255, 255, 255, 70),
    text=QColor(18, 20, 24),
    hover=QColor(255, 255, 255, 110),
)


def theme_for_system() -> Theme:
    scheme = QGuiApplication.styleHints().colorScheme()
    return ON_LIGHT if scheme == Qt.ColorScheme.Light else ON_DARK


def ui_font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Helvetica Neue"])
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def text_width(text: str, font: QFont, widget: QWidget) -> int:
    """Whole pixels needed to draw `text` in `font` on `widget`, never cut off.

    Measured for the widget itself, because text can come out a few pixels
    narrower or wider depending on the screen's scaling, and the painter
    measures it the same way. Rounded up, plus 1 px of slack, since the
    painter cuts a name off ("car…") if it's even a fraction too wide.
    """
    return math.ceil(QFontMetricsF(font, widget).horizontalAdvance(text)) + 1


def luminance(color: QColor) -> float:
    """Perceived brightness from 0 (black) to 1 (white)."""
    return 0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()


def draw_chevron(p: QPainter, center: QPointF, pointing_right: bool, color: QColor) -> None:
    """Draw a small › or ‹ arrow centred on `center`."""
    half_width, half_height = 2.5, 5.0
    tip = 1 if pointing_right else -1
    pen = QPen(color, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolyline(
        QPolygonF(
            [
                center + QPointF(-tip * half_width, -half_height),
                center + QPointF(tip * half_width, 0),
                center + QPointF(-tip * half_width, half_height),
            ]
        )
    )


class HoverFade:
    """A hover amount from 0 to 1 that fades in and out, repainting its widget as it goes.

    The widget calls enter() and leave() from its enterEvent and leaveEvent,
    and reads `value` in its paintEvent.
    """

    def __init__(self, widget: QWidget, duration_ms: int = 140) -> None:
        self.value = 0.0
        self._widget = widget
        self._anim = QVariantAnimation(widget)
        self._anim.setDuration(duration_ms)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_value)

    def enter(self) -> None:
        self._run(QAbstractAnimation.Direction.Forward)

    def leave(self) -> None:
        self._run(QAbstractAnimation.Direction.Backward)

    def reset(self) -> None:
        """Drop straight back to 0, e.g. when a click hides the widget under the mouse."""
        self._anim.stop()
        self._on_value(0.0)

    def _run(self, direction: QAbstractAnimation.Direction) -> None:
        # Changing direction mid-animation reverses it from where it is.
        self._anim.setDirection(direction)
        if self._anim.state() != QAbstractAnimation.State.Running:
            self._anim.start()

    def _on_value(self, value: float) -> None:
        self.value = value
        self._widget.update()  # schedules a repaint; Qt calls paintEvent soon after
