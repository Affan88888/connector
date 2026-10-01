"""Pixel-level effects for the glass: bending the view near the edges like
light through the rounded edge of a thick lens, with a faint colour fringe
where it bends most.

These need a calculation for every pixel. A Python loop over every pixel
would be far too slow, so this uses numpy, which works on whole arrays of
pixels at once in compiled code.

In memory, each pixel of a Format_ARGB32_Premultiplied QImage is 4 bytes in
the order blue, green, red, alpha.
"""

from functools import lru_cache

import numpy as np
from PySide6.QtGui import QImage

BAND = 14  # px in from the edge where the glass bends the view
STRENGTH = 12  # how many px beyond the edge the very edge looks
DISPERSION = 0.15  # blue bends this much more than green, red this much less

_FORMAT = QImage.Format.Format_ARGB32_Premultiplied


def to_array(image: QImage) -> np.ndarray:
    """A (height, width, 4) array of an image's pixels."""
    image = image.convertToFormat(_FORMAT)
    pixels = np.frombuffer(image.constBits(), np.uint8)
    # Rows can be padded at the end, so reshape by bytesPerLine and trim.
    pixels = pixels.reshape(image.height(), image.bytesPerLine() // 4, 4)[:, : image.width()]
    # Copy, because the array above points into memory owned by `image`.
    return pixels.copy()


def to_image(pixels: np.ndarray) -> QImage:
    pixels = np.ascontiguousarray(pixels)
    height, width = pixels.shape[:2]
    # copy(), so the QImage owns its pixels instead of pointing into the array.
    return QImage(pixels.data, width, height, width * 4, _FORMAT).copy()


def extend_edges(image: QImage, left: int, top: int, right: int, bottom: int) -> QImage:
    """Grow an image by repeating its outermost pixels outwards.

    Used where a grab would reach past the edge of the screen: repeating the
    edge looks natural, while the black that's really there would show up as
    a dark rim on the glass.
    """
    padding = ((max(top, 0), max(bottom, 0)), (max(left, 0), max(right, 0)), (0, 0))
    return to_image(np.pad(to_array(image), padding, mode="edge"))


@lru_cache(maxsize=16)
def _offsets(
    width: int,
    height: int,
    box: tuple[float, float, float, float],
    radius: float,
    band: float,
    strength: float,
) -> tuple[np.ndarray, np.ndarray]:
    """For every pixel, how far outwards (dx, dy) to look for what it shows.

    Depends only on the size and shape of the glass, so it's cached and
    worked out again only when those change.
    """
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32) + 0.5
    left, top, right, bottom = box

    # Distance from each pixel to the edge of the rounded rectangle:
    # negative inside it, 0 on the edge. (The standard formula for a
    # rounded rectangle, worked out for every pixel at once.)
    qx = np.abs(xs - (left + right) / 2) - ((right - left) / 2 - radius)
    qy = np.abs(ys - (top + bottom) / 2) - ((bottom - top) / 2 - radius)
    distance = (
        np.hypot(np.maximum(qx, 0), np.maximum(qy, 0))
        + np.minimum(np.maximum(qx, qy), 0)
        - radius
    )

    # "Outwards" is the direction the distance grows fastest.
    gy, gx = np.gradient(distance)
    length = np.hypot(gx, gy) + 1e-6

    # 0 deeper than `band` inside, rising to 1 at the edge. Squaring makes
    # it rise steeply near the edge, like the curved side of a lens.
    t = 1 - np.clip(-distance / band, 0, 1)
    pull = strength * t * t
    return gx / length * pull, gy / length * pull


def refract(
    source: QImage,
    width: int,
    height: int,
    margin: float,
    box: tuple[float, float, float, float],
    radius: float,
    scale: float,
) -> QImage:
    """Bend the view behind the glass into a width x height image.

    `source` is that view with `margin` px of the surroundings on every side,
    which is what the edges pull in. `box` and `radius` describe the glass's
    rounded rectangle and `scale` how many of these pixels make one normal
    (logical) pixel.
    """
    pixels = to_array(source)
    src_h, src_w = pixels.shape[:2]
    dx, dy = _offsets(width, height, box, radius, BAND * scale, STRENGTH * scale)

    base_x = np.arange(width, dtype=np.float32) + margin
    base_y = np.arange(height, dtype=np.float32)[:, None] + margin

    out = np.empty((height, width, 4), np.uint8)
    # Each colour bends by a slightly different amount: a faint rainbow
    # fringe right at the edge, as with real glass.
    for channel, bend in ((0, 1 + DISPERSION), (1, 1.0), (2, 1 - DISPERSION)):
        x = np.clip(np.rint(base_x + dx * bend), 0, src_w - 1).astype(np.intp)
        y = np.clip(np.rint(base_y + dy * bend), 0, src_h - 1).astype(np.intp)
        out[..., channel] = pixels[y, x, channel]
    out[..., 3] = 255  # the view behind is always opaque
    return to_image(out)
