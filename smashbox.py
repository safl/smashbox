# SPDX-FileCopyrightText: Simon A. F. Lund <os@safl.dk>
#
# SPDX-License-Identifier: BSD-3-Clause

"""Smashbox — a fullscreen, toddler-safe keyboard smash toy.

Inspired by the toy at tinyfingers.net. Press any key, click
anywhere, or touch the screen and something colorful happens. Designed
to run on a Raspberry Pi in kiosk mode (X11 on tty1).

Supervisor gate to exit:
    * hold the top-left corner for ~2 seconds, OR
    * type the cheat code "iddqd".
Inside the supervisor panel:
    [t] cycle theme, [m] mute/unmute, [f] toggle fullscreen,
    [g] cycle mode (smash → whack-a-mole → counting),
    [r] reset counter, [q] quit, [esc] resume.
"""

from __future__ import annotations

# Bumped manually before tagging a release. The GitHub Actions
# release workflow also sed-overrides this line with the pushed tag
# at build time, so tagged images always report the tag — but for
# `pip install`-ish use the literal here is the source of truth.
__version__ = "0.2.0"

import sys

# `--version` is the only CLI arg we handle without pygame — answered
# here, before the SDL imports below, so ops scripts (and a quick
# `smashbox.py --version` over ssh from the kiosk) work even when
# pygame isn't installed in the active interpreter.
if any(a in ("--version", "-V") for a in sys.argv[1:]):
    print(f"smashbox {__version__}")
    sys.exit(0)

import math
import os
import random
import time

# Prefer the KMS/DRM video driver when no display server is present (the
# Raspberry Pi kiosk case). Fall back to whatever pygame picks otherwise.
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

# On the Raspberry Pi (Bookworm Lite has no Pulse/Pipewire installed),
# tell SDL to go straight to ALSA. Otherwise it probes pulse → pipewire
# → alsa, eating ~30-150ms at init on each failed probe AND occasionally
# falling back to the dummy driver if a half-installed pulse confuses it.
# Detected via /proc/device-tree/model so this is a no-op on dev hosts
# where pulse/pipewire actually exist and should keep handling routing.
try:
    with open("/proc/device-tree/model", "rb") as _f:
        if b"Raspberry Pi" in _f.read():
            os.environ.setdefault("SDL_AUDIODRIVER", "alsa")
except OSError:
    pass

import numpy as np  # noqa: E402  -- used for synthesized audio
import pygame  # noqa: E402
from pygame._sdl2.video import Window, Renderer, Texture  # noqa: E402
try:
    from pygame._sdl2.audio import get_audio_device_names  # noqa: E402
except ImportError:
    get_audio_device_names = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

THEMES = {
    "space": {
        "bg_top": (8, 6, 32),
        "bg_bottom": (32, 12, 72),
        "palette": [
            (255, 245, 200), (255, 200, 120), (180, 200, 255),
            (255, 120, 200), (160, 255, 240), (255, 255, 255),
        ],
        "shape": "star",
        "ambient": "stars",
    },
    "underwater": {
        "bg_top": (4, 40, 80),
        "bg_bottom": (8, 90, 140),
        "palette": [
            (255, 200, 80), (255, 130, 100), (140, 230, 255),
            (180, 255, 200), (255, 180, 220), (90, 200, 230),
        ],
        "shape": "fish",
        "ambient": "wavy",
    },
    "bubbles": {
        "bg_top": (255, 220, 240),
        "bg_bottom": (180, 220, 255),
        "palette": [
            (255, 120, 180), (130, 200, 255), (180, 255, 200),
            (255, 220, 120), (200, 160, 255), (255, 180, 200),
        ],
        "shape": "bubble",
        "ambient": "bubbles",
    },
    "confetti": {
        "bg_top": (20, 20, 30),
        "bg_bottom": (60, 30, 90),
        "palette": [
            (255, 80, 110), (255, 200, 60), (90, 220, 130),
            (90, 180, 255), (220, 120, 255), (255, 255, 255),
        ],
        "shape": "rect",
        "ambient": "none",
    },
}
THEME_ORDER = ["confetti", "space", "underwater", "bubbles"]


# ---------------------------------------------------------------------------
# Clip-art: small drawn-from-primitives sprites that float alongside the
# pressed letter. Pygame primitives only, so this scales to 4K, ships zero
# assets, and has no font/emoji dependency.
# ---------------------------------------------------------------------------

def _clip_sun(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = cy = size / 2
    r = size * 0.28
    rw = max(2, size // 25)
    for i in range(8):
        a = i * math.pi / 4
        p1 = (cx + math.cos(a) * r * 1.35, cy + math.sin(a) * r * 1.35)
        p2 = (cx + math.cos(a) * r * 1.80, cy + math.sin(a) * r * 1.80)
        pygame.draw.line(s, (255, 140, 30), p1, p2, rw)
    pygame.draw.circle(s, (255, 220, 50), (int(cx), int(cy)), int(r))
    er = max(2, size // 50)
    pygame.draw.circle(s, (60, 40, 0),
                       (int(cx - r * 0.3), int(cy - r * 0.15)), er)
    pygame.draw.circle(s, (60, 40, 0),
                       (int(cx + r * 0.3), int(cy - r * 0.15)), er)
    pygame.draw.arc(s, (60, 40, 0),
                    pygame.Rect(int(cx - r * 0.4), int(cy - r * 0.1),
                                int(r * 0.8), int(r * 0.6)),
                    math.pi + 0.3, 2 * math.pi - 0.3, max(2, size // 50))
    return s


def _clip_heart(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    sc = size * 0.4 / 16
    pts = []
    for i in range(48):
        t = (i / 48) * math.tau
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t)
              - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * sc, cy + y * sc - size * 0.05))
    pygame.draw.polygon(s, (240, 70, 100), pts)
    hl = [(p[0] - size * 0.04, p[1] - size * 0.09) for p in pts[2:9]]
    pygame.draw.polygon(s, (255, 180, 200), hl)
    return s


def _clip_star(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = cy = size / 2
    R, r = size * 0.45, size * 0.18
    pts = []
    for i in range(10):
        a = i * math.pi / 5 - math.pi / 2
        rr = R if i % 2 == 0 else r
        pts.append((cx + math.cos(a) * rr, cy + math.sin(a) * rr))
    pygame.draw.polygon(s, (255, 215, 60), pts)
    pygame.draw.polygon(s, (200, 150, 30), pts, max(2, size // 40))
    return s


def _clip_cloud(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    by = size * 0.62
    pygame.draw.ellipse(s, (200, 210, 235),
                        pygame.Rect(size * 0.1, by - size * 0.04,
                                    size * 0.8, size * 0.24))
    for cx, dy, rr in [(0.30, 0.0, 0.20), (0.50, -0.10, 0.25), (0.70, 0.0, 0.18)]:
        pygame.draw.circle(s, (245, 245, 255),
                           (int(size * cx), int(by + size * dy)),
                           int(size * rr))
    pygame.draw.rect(s, (245, 245, 255),
                     pygame.Rect(int(size * 0.25), int(by),
                                 int(size * 0.5), int(size * 0.22)))
    return s


def _clip_flower(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = cy = size / 2
    for i in range(6):
        a = i * math.tau / 6
        pygame.draw.circle(s, (255, 130, 200),
                           (int(cx + math.cos(a) * size * 0.22),
                            int(cy + math.sin(a) * size * 0.22)),
                           int(size * 0.18))
    pygame.draw.circle(s, (255, 220, 80), (int(cx), int(cy)), int(size * 0.12))
    return s


def _clip_lightning(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    pts = [
        (size * 0.55, size * 0.05), (size * 0.25, size * 0.50),
        (size * 0.45, size * 0.50), (size * 0.30, size * 0.95),
        (size * 0.75, size * 0.40), (size * 0.55, size * 0.40),
        (size * 0.70, size * 0.05),
    ]
    pygame.draw.polygon(s, (255, 220, 30), pts)
    pygame.draw.polygon(s, (200, 140, 0), pts, max(2, size // 40))
    return s


def _clip_balloon(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    color = random.choice([(255, 100, 100), (100, 200, 255),
                           (200, 120, 255), (100, 220, 130),
                           (255, 180, 80)])
    pygame.draw.ellipse(s, color,
                        pygame.Rect(size * 0.2, size * 0.05,
                                    size * 0.6, size * 0.7))
    hl = pygame.Surface((int(size * 0.18), int(size * 0.20)),
                        pygame.SRCALPHA)
    pygame.draw.ellipse(hl, (255, 255, 255, 160), hl.get_rect())
    s.blit(hl, (int(size * 0.30), int(size * 0.15)))
    pygame.draw.polygon(s, color, [
        (size * 0.50, size * 0.72),
        (size * 0.42, size * 0.80),
        (size * 0.58, size * 0.80),
    ])
    pygame.draw.line(s, (90, 90, 90),
                     (size * 0.50, size * 0.80),
                     (size * 0.42, size * 0.97),
                     max(1, size // 80))
    return s


def _clip_snowflake(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = cy = size / 2
    arm = size * 0.42
    aw = max(2, size // 30)
    color = (200, 230, 255)
    for i in range(6):
        a = i * math.tau / 6
        ex = cx + math.cos(a) * arm
        ey = cy + math.sin(a) * arm
        pygame.draw.line(s, color, (cx, cy), (ex, ey), aw)
        for bp in (0.55, 0.80):
            bx = cx + math.cos(a) * arm * bp
            by = cy + math.sin(a) * arm * bp
            bl = arm * 0.18
            for j in (-1, 1):
                ba = a + j * math.pi / 3
                pygame.draw.line(s, color, (bx, by),
                                 (bx + math.cos(ba) * bl,
                                  by + math.sin(ba) * bl), aw)
    pygame.draw.circle(s, (240, 250, 255),
                       (int(cx), int(cy)), int(size * 0.08))
    return s


def _clip_music_note(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    c = (50, 60, 90)
    pygame.draw.ellipse(s, c,
                        pygame.Rect(size * 0.2, size * 0.60,
                                    size * 0.30, size * 0.25))
    pygame.draw.rect(s, c,
                     pygame.Rect(size * 0.48, size * 0.15,
                                 size * 0.06, size * 0.55))
    pygame.draw.polygon(s, c, [
        (size * 0.54, size * 0.15),
        (size * 0.85, size * 0.30),
        (size * 0.75, size * 0.55),
        (size * 0.54, size * 0.40),
    ])
    return s


def _clip_apple(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.ellipse(s, (220, 50, 50),
                        pygame.Rect(size * 0.15, size * 0.25,
                                    size * 0.7, size * 0.7))
    hl = pygame.Surface((int(size * 0.17), int(size * 0.20)),
                        pygame.SRCALPHA)
    pygame.draw.ellipse(hl, (255, 200, 200, 200), hl.get_rect())
    s.blit(hl, (int(size * 0.27), int(size * 0.32)))
    pygame.draw.rect(s, (90, 60, 30),
                     pygame.Rect(size * 0.48, size * 0.12,
                                 size * 0.06, size * 0.18))
    pygame.draw.polygon(s, (90, 180, 60), [
        (size * 0.52, size * 0.20),
        (size * 0.72, size * 0.15),
        (size * 0.62, size * 0.30),
    ])
    return s


def _clip_bell(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = size / 2
    body = (255, 200, 50)
    edge = (180, 130, 20)
    # Flared trapezoid body.
    trap = [
        (cx - size * 0.22, size * 0.30),
        (cx + size * 0.22, size * 0.30),
        (cx + size * 0.38, size * 0.74),
        (cx - size * 0.38, size * 0.74),
    ]
    pygame.draw.polygon(s, body, trap)
    pygame.draw.polygon(s, edge, trap, max(2, size // 50))
    # Rounded dome on top of the trapezoid.
    pygame.draw.circle(s, body, (int(cx), int(size * 0.30)), int(size * 0.22))
    pygame.draw.circle(s, edge, (int(cx), int(size * 0.30)),
                       int(size * 0.22), max(2, size // 50))
    # Rim along the bottom (slightly darker).
    pygame.draw.ellipse(s, edge,
                        pygame.Rect(int(cx - size * 0.40), int(size * 0.70),
                                    int(size * 0.80), int(size * 0.10)),
                        width=max(2, size // 40))
    # Clapper.
    pygame.draw.circle(s, edge,
                       (int(cx), int(size * 0.82)), max(3, int(size * 0.05)))
    # Handle.
    pygame.draw.rect(s, body,
                     pygame.Rect(int(cx - size * 0.025), int(size * 0.07),
                                 int(size * 0.05), int(size * 0.07)))
    return s


def _clip_arrow(size):
    # Up-arrow by default; rotated at draw time for other directions.
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    fill = (255, 255, 255)
    edge = (60, 60, 70)
    pts = [
        (size * 0.50, size * 0.10),
        (size * 0.85, size * 0.45),
        (size * 0.65, size * 0.45),
        (size * 0.65, size * 0.90),
        (size * 0.35, size * 0.90),
        (size * 0.35, size * 0.45),
        (size * 0.15, size * 0.45),
    ]
    pygame.draw.polygon(s, fill, pts)
    pygame.draw.polygon(s, edge, pts, max(2, size // 40))
    return s


def _clip_dog(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    body = (180, 130, 80)
    dark = (110, 70, 30)
    # Floppy ears (behind head).
    pygame.draw.ellipse(s, dark, pygame.Rect(
        int(cx - size * 0.40), int(cy - size * 0.25),
        int(size * 0.22), int(size * 0.34)))
    pygame.draw.ellipse(s, dark, pygame.Rect(
        int(cx + size * 0.18), int(cy - size * 0.25),
        int(size * 0.22), int(size * 0.34)))
    # Head.
    pygame.draw.ellipse(s, body, pygame.Rect(
        int(cx - size * 0.32), int(cy - size * 0.25),
        int(size * 0.64), int(size * 0.55)))
    # Lighter snout.
    pygame.draw.ellipse(s, (220, 180, 120), pygame.Rect(
        int(cx - size * 0.18), int(cy + size * 0.05),
        int(size * 0.36), int(size * 0.25)))
    # Eyes.
    eye_r = max(2, int(size * 0.04))
    pygame.draw.circle(s, (30, 20, 10),
                       (int(cx - size * 0.13), int(cy - size * 0.05)), eye_r)
    pygame.draw.circle(s, (30, 20, 10),
                       (int(cx + size * 0.13), int(cy - size * 0.05)), eye_r)
    # Nose + tongue.
    pygame.draw.ellipse(s, (30, 20, 10), pygame.Rect(
        int(cx - size * 0.06), int(cy + size * 0.08),
        int(size * 0.12), int(size * 0.08)))
    pygame.draw.ellipse(s, (240, 100, 130), pygame.Rect(
        int(cx - size * 0.05), int(cy + size * 0.22),
        int(size * 0.10), int(size * 0.08)))
    return s


def _clip_cat(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    body = (140, 140, 145)
    inner = (240, 180, 190)
    # Pointed ears (outer + inner triangles).
    pygame.draw.polygon(s, body, [
        (cx - size * 0.32, cy - size * 0.05),
        (cx - size * 0.10, cy - size * 0.40),
        (cx - size * 0.05, cy - size * 0.10),
    ])
    pygame.draw.polygon(s, body, [
        (cx + size * 0.32, cy - size * 0.05),
        (cx + size * 0.10, cy - size * 0.40),
        (cx + size * 0.05, cy - size * 0.10),
    ])
    pygame.draw.polygon(s, inner, [
        (cx - size * 0.24, cy - size * 0.08),
        (cx - size * 0.13, cy - size * 0.30),
        (cx - size * 0.08, cy - size * 0.13),
    ])
    pygame.draw.polygon(s, inner, [
        (cx + size * 0.24, cy - size * 0.08),
        (cx + size * 0.13, cy - size * 0.30),
        (cx + size * 0.08, cy - size * 0.13),
    ])
    # Head.
    pygame.draw.ellipse(s, body, pygame.Rect(
        int(cx - size * 0.32), int(cy - size * 0.20),
        int(size * 0.64), int(size * 0.55)))
    # Green eyes with vertical pupils.
    pygame.draw.ellipse(s, (90, 200, 90), pygame.Rect(
        int(cx - size * 0.18), int(cy - size * 0.05),
        int(size * 0.10), int(size * 0.14)))
    pygame.draw.ellipse(s, (90, 200, 90), pygame.Rect(
        int(cx + size * 0.08), int(cy - size * 0.05),
        int(size * 0.10), int(size * 0.14)))
    slit = max(2, size // 50)
    pygame.draw.line(s, (20, 20, 20),
                     (cx - size * 0.13, cy - size * 0.04),
                     (cx - size * 0.13, cy + size * 0.08), slit)
    pygame.draw.line(s, (20, 20, 20),
                     (cx + size * 0.13, cy - size * 0.04),
                     (cx + size * 0.13, cy + size * 0.08), slit)
    # Nose + whiskers.
    pygame.draw.polygon(s, (220, 120, 140), [
        (cx, cy + size * 0.10),
        (cx - size * 0.05, cy + size * 0.16),
        (cx + size * 0.05, cy + size * 0.16),
    ])
    ww = max(1, size // 80)
    for dy in (-0.01, 0.01):
        for side in (-1, 1):
            pygame.draw.line(s, (40, 40, 40),
                             (cx + side * size * 0.06,
                              cy + size * 0.18 + size * dy),
                             (cx + side * size * 0.30,
                              cy + size * 0.18 + size * dy), ww)
    return s


def _clip_cow(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    white = (250, 250, 250)
    black = (40, 40, 50)
    pink = (245, 190, 200)
    edge = max(2, size // 50)
    # Horns (small white ovals with outlines).
    for sx in (-0.42, 0.28):
        r = pygame.Rect(int(cx + size * sx), int(cy - size * 0.35),
                        int(size * 0.14), int(size * 0.10))
        pygame.draw.ellipse(s, white, r)
        pygame.draw.ellipse(s, black, r, edge)
    # Ears (white outer + pink inner).
    for sx in (-0.45, 0.27):
        pygame.draw.ellipse(s, white, pygame.Rect(
            int(cx + size * sx), int(cy - size * 0.20),
            int(size * 0.18), int(size * 0.20)))
        pygame.draw.ellipse(s, pink, pygame.Rect(
            int(cx + size * (sx + 0.04)), int(cy - size * 0.16),
            int(size * 0.10), int(size * 0.12)))
    # Head.
    pygame.draw.ellipse(s, white, pygame.Rect(
        int(cx - size * 0.30), int(cy - size * 0.25),
        int(size * 0.60), int(size * 0.65)))
    # Black patches on head — the iconic Friesian cow look.
    pygame.draw.ellipse(s, black, pygame.Rect(
        int(cx - size * 0.26), int(cy - size * 0.22),
        int(size * 0.16), int(size * 0.14)))
    pygame.draw.ellipse(s, black, pygame.Rect(
        int(cx + size * 0.10), int(cy - size * 0.18),
        int(size * 0.18), int(size * 0.12)))
    # Snout (large pink oval) + nostrils.
    pygame.draw.ellipse(s, pink, pygame.Rect(
        int(cx - size * 0.20), int(cy + size * 0.12),
        int(size * 0.40), int(size * 0.25)))
    for sx in (-0.10, 0.05):
        pygame.draw.ellipse(s, black, pygame.Rect(
            int(cx + size * sx), int(cy + size * 0.20),
            int(size * 0.05), int(size * 0.06)))
    # Eyes.
    eye_r = max(2, int(size * 0.04))
    pygame.draw.circle(s, black,
                       (int(cx - size * 0.13), int(cy - size * 0.05)), eye_r)
    pygame.draw.circle(s, black,
                       (int(cx + size * 0.13), int(cy - size * 0.05)), eye_r)
    return s


def _clip_owl(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    body = (140, 100, 70)
    pale = (230, 200, 160)
    edge = max(2, size // 50)
    # Ear tufts.
    pygame.draw.polygon(s, body, [
        (cx - size * 0.25, cy - size * 0.15),
        (cx - size * 0.32, cy - size * 0.40),
        (cx - size * 0.15, cy - size * 0.25),
    ])
    pygame.draw.polygon(s, body, [
        (cx + size * 0.25, cy - size * 0.15),
        (cx + size * 0.32, cy - size * 0.40),
        (cx + size * 0.15, cy - size * 0.25),
    ])
    # Round body + lighter belly.
    pygame.draw.ellipse(s, body, pygame.Rect(
        int(cx - size * 0.35), int(cy - size * 0.25),
        int(size * 0.70), int(size * 0.75)))
    pygame.draw.ellipse(s, pale, pygame.Rect(
        int(cx - size * 0.22), int(cy + size * 0.00),
        int(size * 0.44), int(size * 0.42)))
    # Huge eye discs — the iconic owl feature.
    eye_r = int(size * 0.16)
    for sx in (-0.17, 0.17):
        cx_e = int(cx + size * sx)
        cy_e = int(cy - size * 0.05)
        pygame.draw.circle(s, (245, 245, 245), (cx_e, cy_e), eye_r)
        pygame.draw.circle(s, (20, 20, 20), (cx_e, cy_e), int(size * 0.08))
        pygame.draw.circle(s, body, (cx_e, cy_e), eye_r, edge)
    # Beak (yellow triangle).
    pygame.draw.polygon(s, (240, 180, 60), [
        (cx, cy + size * 0.10),
        (cx - size * 0.05, cy + size * 0.18),
        (cx + size * 0.05, cy + size * 0.18),
    ])
    return s


def _clip_bird(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    body = (90, 170, 230)
    wing = (60, 130, 200)
    belly = (240, 240, 255)
    # Body + belly (side view, beak on the left).
    pygame.draw.ellipse(s, body, pygame.Rect(
        int(cx - size * 0.30), int(cy - size * 0.10),
        int(size * 0.55), int(size * 0.40)))
    pygame.draw.ellipse(s, belly, pygame.Rect(
        int(cx - size * 0.22), int(cy + size * 0.02),
        int(size * 0.30), int(size * 0.25)))
    # Head.
    pygame.draw.circle(s, body,
                       (int(cx - size * 0.18), int(cy - size * 0.12)),
                       int(size * 0.15))
    # Wing.
    pygame.draw.ellipse(s, wing, pygame.Rect(
        int(cx - size * 0.10), int(cy - size * 0.05),
        int(size * 0.30), int(size * 0.20)))
    # Orange beak (triangle pointing left).
    pygame.draw.polygon(s, (240, 160, 50), [
        (cx - size * 0.36, cy - size * 0.13),
        (cx - size * 0.18, cy - size * 0.18),
        (cx - size * 0.18, cy - size * 0.08),
    ])
    # Eye.
    pygame.draw.circle(s, (20, 20, 20),
                       (int(cx - size * 0.20), int(cy - size * 0.16)),
                       max(2, int(size * 0.025)))
    # Tail (triangle pointing right).
    pygame.draw.polygon(s, wing, [
        (cx + size * 0.25, cy + size * 0.05),
        (cx + size * 0.42, cy - size * 0.05),
        (cx + size * 0.42, cy + size * 0.18),
    ])
    # Leg (single line down).
    pygame.draw.line(s, (240, 160, 50),
                     (cx + size * 0.05, cy + size * 0.28),
                     (cx + size * 0.05, cy + size * 0.42),
                     max(2, size // 60))
    return s


def _clip_sheep(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    wool = (245, 240, 235)
    face = (60, 50, 50)
    # Wool body — overlapping puffs.
    for fx, fy, fr in [
        (-0.18, 0.05, 0.18), (0.18, 0.05, 0.18),
        (-0.10, -0.10, 0.16), (0.10, -0.10, 0.16),
        (0.0, 0.18, 0.18), (-0.25, 0.20, 0.14), (0.25, 0.20, 0.14),
    ]:
        pygame.draw.circle(s, wool,
                           (int(cx + size * fx), int(cy + size * fy)),
                           int(size * fr))
    # Dark face (oval).
    pygame.draw.ellipse(s, face, pygame.Rect(
        int(cx - size * 0.14), int(cy - size * 0.32),
        int(size * 0.28), int(size * 0.28)))
    # Tuft of wool on forehead.
    pygame.draw.circle(s, wool,
                       (int(cx), int(cy - size * 0.32)),
                       int(size * 0.08))
    # Ears (small dark ovals at the sides of the face).
    for sx in (-0.18, 0.18):
        pygame.draw.ellipse(s, face, pygame.Rect(
            int(cx + size * sx - size * 0.05),
            int(cy - size * 0.22),
            int(size * 0.10), int(size * 0.08)))
    # Eyes.
    eye_r = max(2, int(size * 0.025))
    pygame.draw.circle(s, (245, 245, 245),
                       (int(cx - size * 0.05), int(cy - size * 0.22)), eye_r)
    pygame.draw.circle(s, (245, 245, 245),
                       (int(cx + size * 0.05), int(cy - size * 0.22)), eye_r)
    # Legs (two short dark rectangles).
    for sx in (-0.12, 0.12):
        pygame.draw.rect(s, face, pygame.Rect(
            int(cx + size * sx - size * 0.03), int(cy + size * 0.30),
            int(size * 0.06), int(size * 0.14)))
    return s


def _clip_pig(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    body = (245, 170, 190)
    dark = (200, 130, 150)
    # Ears (triangles at top).
    pygame.draw.polygon(s, body, [
        (cx - size * 0.32, cy - size * 0.10),
        (cx - size * 0.22, cy - size * 0.32),
        (cx - size * 0.10, cy - size * 0.15),
    ])
    pygame.draw.polygon(s, body, [
        (cx + size * 0.32, cy - size * 0.10),
        (cx + size * 0.22, cy - size * 0.32),
        (cx + size * 0.10, cy - size * 0.15),
    ])
    # Head (round).
    pygame.draw.ellipse(s, body, pygame.Rect(
        int(cx - size * 0.30), int(cy - size * 0.20),
        int(size * 0.60), int(size * 0.60)))
    # Big snout disc.
    pygame.draw.ellipse(s, dark, pygame.Rect(
        int(cx - size * 0.18), int(cy + size * 0.10),
        int(size * 0.36), int(size * 0.22)))
    # Nostrils.
    for sx in (-0.07, 0.04):
        pygame.draw.ellipse(s, (60, 30, 40), pygame.Rect(
            int(cx + size * sx), int(cy + size * 0.18),
            int(size * 0.04), int(size * 0.07)))
    # Eyes.
    eye_r = max(2, int(size * 0.035))
    pygame.draw.circle(s, (30, 20, 20),
                       (int(cx - size * 0.13), int(cy - size * 0.03)), eye_r)
    pygame.draw.circle(s, (30, 20, 20),
                       (int(cx + size * 0.13), int(cy - size * 0.03)), eye_r)
    return s


def _clip_frog(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    green = (110, 190, 90)
    dark = (70, 140, 60)
    # Body (wide ellipse).
    pygame.draw.ellipse(s, green, pygame.Rect(
        int(cx - size * 0.40), int(cy - size * 0.05),
        int(size * 0.80), int(size * 0.55)))
    # Lighter underbelly.
    pygame.draw.ellipse(s, (200, 230, 170), pygame.Rect(
        int(cx - size * 0.25), int(cy + size * 0.20),
        int(size * 0.50), int(size * 0.25)))
    # Eye bulges on top — the iconic frog look.
    eye_r = size * 0.13
    for sx in (-0.18, 0.18):
        cx_e = int(cx + size * sx)
        cy_e = int(cy - size * 0.15)
        pygame.draw.circle(s, green, (cx_e, cy_e), int(eye_r))
        pygame.draw.circle(s, (250, 250, 250),
                           (cx_e, int(cy - size * 0.17)),
                           int(size * 0.09))
        pygame.draw.circle(s, (20, 20, 20),
                           (int(cx + size * sx * 0.95),
                            int(cy - size * 0.16)),
                           max(2, int(size * 0.045)))
    # Wide smile.
    pygame.draw.arc(s, dark,
                    pygame.Rect(int(cx - size * 0.25), int(cy + size * 0.02),
                                int(size * 0.50), int(size * 0.25)),
                    math.pi + 0.2, 2 * math.pi - 0.2,
                    max(2, size // 35))
    return s


def _clip_guitar(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    body = (180, 110, 50)
    dark = (90, 60, 30)
    # Hourglass body — two overlapping circles, bigger on the bottom.
    pygame.draw.circle(s, body,
                       (int(cx), int(cy + size * 0.22)),
                       int(size * 0.22))
    pygame.draw.circle(s, body,
                       (int(cx), int(cy + size * 0.03)),
                       int(size * 0.18))
    # Sound hole.
    pygame.draw.circle(s, (40, 30, 20),
                       (int(cx), int(cy + size * 0.10)),
                       int(size * 0.06))
    # Neck (long rectangle going up).
    pygame.draw.rect(s, dark, pygame.Rect(
        int(cx - size * 0.03), int(cy - size * 0.40),
        int(size * 0.06), int(size * 0.40)))
    # Tuning head.
    pygame.draw.rect(s, body, pygame.Rect(
        int(cx - size * 0.09), int(cy - size * 0.45),
        int(size * 0.18), int(size * 0.10)))
    # Strings on the neck.
    for sx in (-0.015, 0.0, 0.015):
        pygame.draw.line(s, (220, 220, 220),
                         (cx + size * sx, cy - size * 0.38),
                         (cx + size * sx, cy + size * 0.15),
                         max(1, size // 100))
    return s


def _clip_piano(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    n_keys = 7
    key_w = size * 0.85 / n_keys
    key_h = size * 0.45
    top = cy - size * 0.10
    left = cx - size * 0.425
    # Top bar (case).
    pygame.draw.rect(s, (90, 60, 40), pygame.Rect(
        int(left), int(top - size * 0.08),
        int(n_keys * key_w), int(size * 0.08)))
    # White keys row.
    for i in range(n_keys):
        kx = left + i * key_w
        pygame.draw.rect(s, (250, 250, 245), pygame.Rect(
            int(kx), int(top), int(key_w - 1), int(key_h)))
        pygame.draw.rect(s, (60, 60, 70), pygame.Rect(
            int(kx), int(top), int(key_w - 1), int(key_h)), 2)
    # Black keys — between specific white keys (after 1, 2, 4, 5, 6).
    black_h = key_h * 0.55
    black_w = key_w * 0.55
    for i in (0, 1, 3, 4, 5):
        bx = left + (i + 1) * key_w - black_w / 2
        pygame.draw.rect(s, (30, 30, 35), pygame.Rect(
            int(bx), int(top), int(black_w), int(black_h)))
    return s


def _clip_saxophone(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    brass = (230, 180, 60)
    dark = (140, 100, 30)
    # Tube going down on the right then curling left into the bell.
    pygame.draw.line(s, brass,
                     (cx + size * 0.15, cy - size * 0.35),
                     (cx + size * 0.15, cy + size * 0.10),
                     int(size * 0.10))
    pygame.draw.line(s, brass,
                     (cx + size * 0.15, cy + size * 0.10),
                     (cx - size * 0.20, cy + size * 0.30),
                     int(size * 0.10))
    # Flared bell.
    pygame.draw.polygon(s, brass, [
        (cx - size * 0.30, cy + size * 0.42),
        (cx - size * 0.05, cy + size * 0.20),
        (cx - size * 0.20, cy + size * 0.10),
        (cx - size * 0.42, cy + size * 0.35),
    ])
    pygame.draw.polygon(s, dark, [
        (cx - size * 0.30, cy + size * 0.42),
        (cx - size * 0.05, cy + size * 0.20),
        (cx - size * 0.20, cy + size * 0.10),
        (cx - size * 0.42, cy + size * 0.35),
    ], max(2, size // 50))
    # Mouthpiece at the top.
    pygame.draw.rect(s, dark, pygame.Rect(
        int(cx + size * 0.12), int(cy - size * 0.42),
        int(size * 0.06), int(size * 0.10)))
    # Key dots along the tube.
    for fy in (-0.20, -0.10, 0.0):
        pygame.draw.circle(s, dark,
                           (int(cx + size * 0.12), int(cy + size * fy)),
                           max(2, int(size * 0.025)))
    return s


def _clip_flute(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    silver = (200, 200, 210)
    dark = (90, 90, 100)
    # Horizontal tube body.
    pygame.draw.rect(s, silver, pygame.Rect(
        int(cx - size * 0.42), int(cy - size * 0.05),
        int(size * 0.84), int(size * 0.10)))
    pygame.draw.rect(s, dark, pygame.Rect(
        int(cx - size * 0.42), int(cy - size * 0.05),
        int(size * 0.84), int(size * 0.10)), max(2, size // 80))
    # Mouthpiece flange (left).
    pygame.draw.rect(s, silver, pygame.Rect(
        int(cx - size * 0.45), int(cy - size * 0.09),
        int(size * 0.06), int(size * 0.18)))
    # Bell flange (right).
    pygame.draw.rect(s, silver, pygame.Rect(
        int(cx + size * 0.34), int(cy - size * 0.09),
        int(size * 0.10), int(size * 0.18)))
    # Key holes along the tube.
    for fx in (-0.25, -0.10, 0.05, 0.20):
        pygame.draw.circle(s, dark,
                           (int(cx + size * fx), int(cy)),
                           max(2, int(size * 0.025)))
    return s


def _clip_violin(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    body = (160, 80, 30)
    dark = (80, 40, 15)
    # Hourglass body via two overlapping ovals.
    pygame.draw.ellipse(s, body, pygame.Rect(
        int(cx - size * 0.22), int(cy - size * 0.10),
        int(size * 0.44), int(size * 0.30)))
    pygame.draw.ellipse(s, body, pygame.Rect(
        int(cx - size * 0.20), int(cy + size * 0.08),
        int(size * 0.40), int(size * 0.30)))
    # F-holes — short vertical slits on either side.
    for sx in (-0.07, 0.07):
        pygame.draw.line(s, dark,
                         (cx + size * sx, cy + size * 0.05),
                         (cx + size * sx, cy + size * 0.20),
                         max(2, size // 60))
    # Neck and scroll.
    pygame.draw.rect(s, dark, pygame.Rect(
        int(cx - size * 0.025), int(cy - size * 0.40),
        int(size * 0.05), int(size * 0.30)))
    pygame.draw.circle(s, dark,
                       (int(cx), int(cy - size * 0.42)),
                       int(size * 0.04))
    # Strings.
    for sx in (-0.012, -0.004, 0.004, 0.012):
        pygame.draw.line(s, (240, 240, 240),
                         (cx + size * sx, cy - size * 0.38),
                         (cx + size * sx, cy + size * 0.18),
                         max(1, size // 120))
    return s


def _clip_drums(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    body = (200, 50, 60)
    head = (245, 245, 240)
    rim = (60, 60, 70)
    # Top head (drum skin seen at an angle).
    pygame.draw.ellipse(s, head, pygame.Rect(
        int(cx - size * 0.35), int(cy - size * 0.30),
        int(size * 0.70), int(size * 0.18)))
    pygame.draw.ellipse(s, rim, pygame.Rect(
        int(cx - size * 0.35), int(cy - size * 0.30),
        int(size * 0.70), int(size * 0.18)),
                        max(2, size // 50))
    # Cylindrical body below the head.
    pygame.draw.rect(s, body, pygame.Rect(
        int(cx - size * 0.35), int(cy - size * 0.22),
        int(size * 0.70), int(size * 0.42)))
    # Bottom rim (ellipse on top of the body).
    pygame.draw.ellipse(s, rim, pygame.Rect(
        int(cx - size * 0.35), int(cy + size * 0.10),
        int(size * 0.70), int(size * 0.18)))
    # Tension lugs along the body.
    for fx in (-0.25, -0.13, 0.0, 0.13, 0.25):
        pygame.draw.line(s, rim,
                         (cx + size * fx, cy - size * 0.20),
                         (cx + size * fx, cy + size * 0.18),
                         max(2, size // 60))
    # Crossed drumsticks above the drum.
    stick = (200, 160, 100)
    pygame.draw.line(s, stick,
                     (cx - size * 0.10, cy - size * 0.45),
                     (cx + size * 0.10, cy - size * 0.22),
                     max(2, size // 40))
    pygame.draw.line(s, stick,
                     (cx + size * 0.10, cy - size * 0.45),
                     (cx - size * 0.10, cy - size * 0.22),
                     max(2, size // 40))
    return s


def _clip_percussion(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    metal = (210, 180, 70)
    dark = (130, 100, 30)
    # Two cymbals (ellipses seen edge-on) with a stand between.
    for cy_offset in (-0.10, 0.15):
        rect = pygame.Rect(
            int(cx - size * 0.38), int(cy + size * cy_offset - size * 0.05),
            int(size * 0.76), int(size * 0.10))
        pygame.draw.ellipse(s, metal, rect)
        pygame.draw.ellipse(s, dark, rect, max(2, size // 60))
        pygame.draw.circle(s, metal,
                           (int(cx), int(cy + size * cy_offset)),
                           int(size * 0.05))
    # Stand (vertical line).
    pygame.draw.line(s, dark,
                     (cx, cy - size * 0.05),
                     (cx, cy + size * 0.35),
                     max(2, size // 40))
    # Sparkle rays at the cymbal edges to suggest sound.
    for ax in (-0.40, -0.30, 0.30, 0.40):
        pygame.draw.line(s, metal,
                         (cx + size * ax, cy - size * 0.05),
                         (cx + size * ax * 1.15, cy - size * 0.20),
                         max(1, size // 100))
    return s


def _clip_xylophone(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx, cy = size / 2, size / 2
    rail = (120, 75, 35)
    # Two rails converging to the right, as the bars get shorter.
    for y0, y1 in ((-0.20, -0.10), (0.20, 0.10)):
        pygame.draw.line(s, rail,
                         (cx - size * 0.42, cy + size * y0),
                         (cx + size * 0.42, cy + size * y1),
                         max(3, size // 30))
    # Rainbow bars, longest (lowest note) on the left.
    colors = ((230, 60, 60), (245, 150, 40), (245, 215, 50),
              (80, 190, 80), (60, 150, 230), (140, 90, 210))
    bar_w = size * 0.10
    for i, color in enumerate(colors):
        x = cx - size * 0.38 + i * size * 0.13
        half = size * (0.30 - i * 0.025)
        rect = pygame.Rect(int(x), int(cy - half), int(bar_w), int(2 * half))
        pygame.draw.rect(s, color, rect, border_radius=max(2, size // 40))
        pygame.draw.circle(s, (240, 240, 240),
                           (int(x + bar_w / 2), int(cy - half * 0.7)),
                           max(1, int(size * 0.012)))
    # Mallet resting across the top-right bars.
    pygame.draw.line(s, (200, 160, 100),
                     (cx + size * 0.05, cy - size * 0.42),
                     (cx + size * 0.40, cy - size * 0.18),
                     max(2, size // 50))
    pygame.draw.circle(s, (230, 60, 60),
                       (int(cx + size * 0.05), int(cy - size * 0.42)),
                       max(3, int(size * 0.05)))
    return s


CLIPART_BUILDERS = [
    _clip_sun, _clip_heart, _clip_star, _clip_cloud, _clip_flower,
    _clip_lightning, _clip_balloon, _clip_snowflake, _clip_music_note,
    _clip_apple, _clip_bell, _clip_arrow,
    _clip_dog, _clip_cat, _clip_cow, _clip_owl, _clip_bird, _clip_frog,
    _clip_sheep, _clip_pig,
    _clip_guitar, _clip_piano, _clip_saxophone, _clip_flute,
    _clip_violin, _clip_drums, _clip_percussion, _clip_xylophone,
]
# Named indices so the keyed-clip tables read as something other than
# a sea of magic numbers. Indices match the order in CLIPART_BUILDERS.
CLIP_SUN = 0
CLIP_HEART = 1
CLIP_STAR = 2
CLIP_CLOUD = 3
CLIP_FLOWER = 4
CLIP_LIGHTNING = 5
CLIP_BALLOON = 6
CLIP_SNOWFLAKE = 7
CLIP_MUSIC = 8
CLIP_APPLE = 9
CLIP_BELL = 10
CLIP_ARROW = 11
CLIP_DOG = 12
CLIP_CAT = 13
CLIP_COW = 14
CLIP_OWL = 15
CLIP_BIRD = 16
CLIP_FROG = 17
CLIP_SHEEP = 18
CLIP_PIG = 19
CLIP_GUITAR = 20
CLIP_PIANO = 21
CLIP_SAXOPHONE = 22
CLIP_FLUTE = 23
CLIP_VIOLIN = 24
CLIP_DRUMS = 25
CLIP_PERCUSSION = 26
CLIP_XYLOPHONE = 27


# ---------------------------------------------------------------------------
# Audio: a tiny synthesizer that produces short, friendly notes
# ---------------------------------------------------------------------------

SAMPLE_RATE = 44100  # pygame's default — keeps numpy synthesis and mixer in sync
# Diatonic C major across 2 octaves. Indexed so digits 0..9 map to the
# first 10 notes (C D E F G A B C D E), and stays consonant with
# toddler letter-mashing.
DIATONIC = [
    261.63, 293.66, 329.63, 349.23, 392.00, 440.00, 493.88,  # C4..B4
    523.25, 587.33, 659.25, 698.46, 783.99, 880.00, 987.77,  # C5..B5
]


def _to_sound(samples):
    """Take a float array in [-1, 1] and turn it into a pygame Sound.

    Applies a 1.5ms fade-in and a 10ms fade-out so the first/last samples
    reach zero — even if the caller's envelope isn't there yet. A non-
    zero first sample produces a DC click; a still-loud last sample
    produces a softer but audible "cut off" when the Sound ends mid-
    decay (several of our synths only decay to 10–50% by `duration`).
    10ms is well below perception as latency but long enough that
    truncated tails sound natural on small Pi speakers."""
    s = np.clip(samples, -1.0, 1.0).astype(np.float32, copy=True)
    fade_in = max(1, int(SAMPLE_RATE * 0.0015))
    fade_out = max(1, int(SAMPLE_RATE * 0.010))
    if len(s) > fade_in:
        s[:fade_in] *= np.linspace(0.0, 1.0, fade_in)
    if len(s) > fade_out:
        s[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)
    s16 = (s * 32767).astype(np.int16)
    stereo = np.column_stack([s16, s16])
    return pygame.sndarray.make_sound(stereo)


def _make_piano(freq, duration=0.13):
    """Tight handpan-flavoured ping. Same inharmonic partial structure
    as a handpan, but decays are aggressive enough that the note is
    essentially silent by 0.13s — feels like a tap on a small steel
    drum, not a struck bowl. No tail to mask the next key press."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    wave = np.zeros(n)

    partials = [
        (1.000, 1.00, 20.0),
        (2.012, 0.55, 28.0),
        (3.020, 0.32, 38.0),
        (4.030, 0.17, 55.0),
    ]
    if freq <= 700:
        partials.append((5.500, 0.10, 80.0))

    for ratio, amp, decay in partials:
        f = freq * ratio
        wave += amp * np.exp(-decay * t) * np.sin(2 * np.pi * f * t)

    # 1.5ms fade-in — just enough to suppress the click of starting a
    # sine at non-zero amplitude. Anything longer feels delayed.
    attack_n = max(1, int(SAMPLE_RATE * 0.0015))
    env = np.ones(n)
    env[:attack_n] = np.linspace(0.0, 1.0, attack_n)

    wave /= max(np.max(np.abs(wave)), 1e-6)
    return _to_sound(wave * env * 0.240)


def _make_swoosh(start_freq, end_freq, duration=0.22):
    """Frequency sweep with filtered noise — light air movement."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    freq = start_freq * (end_freq / start_freq) ** (t / duration)
    phase = np.cumsum(2 * np.pi * freq / SAMPLE_RATE)
    wave = np.sin(phase) * 0.6
    noise = np.random.normal(0, 0.3, n)
    k = np.ones(16) / 16
    wave += np.convolve(noise, k, mode='same') * 0.4
    env = np.exp(-3 * t) * (1 - np.exp(-50 * t))
    return _to_sound(wave * env * 0.160)


def _make_boom(duration=0.32):
    """Deep thump for space bar / heavy actions."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    freq = 100 * np.exp(-4 * t) + 50
    phase = np.cumsum(2 * np.pi * freq / SAMPLE_RATE)
    wave = np.sin(phase) * 0.85
    # Softer + shorter noise transient. The old 0.35-std × 8ms burst was
    # itself audible as crackle, especially through small speakers.
    click_n = max(1, int(SAMPLE_RATE * 0.004))
    wave[:click_n] += np.random.normal(0, 0.15, click_n)
    env = np.exp(-4.5 * t)
    return _to_sound(wave * env * 0.275)


def _make_chime(freq=880, duration=0.7):
    """Bell-like inharmonic chime for Enter."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    wave = np.zeros(n)
    for ratio, amp, decay in [(1.0, 1.0, 3.0), (2.756, 0.55, 5.0),
                              (5.404, 0.35, 7.0), (8.933, 0.22, 10.0)]:
        wave += amp * np.sin(2 * np.pi * freq * ratio * t) * np.exp(-decay * t)
    wave /= max(np.max(np.abs(wave)), 1e-6)
    return _to_sound(wave * 0.200)


def _make_pop(duration=0.10):
    """Quick high pop for backspace."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    freq = 800 * np.exp(-18 * t) + 200
    phase = np.cumsum(2 * np.pi * freq / SAMPLE_RATE)
    wave = np.sin(phase)
    env = np.exp(-18 * t)
    return _to_sound(wave * env * 0.200)


def _make_boing(duration=0.35):
    """Cartoon spring 'boing' for surprises."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    # Pitch wobbles down with damped oscillation
    pitch = 600 * np.exp(-3 * t) + 150 + 200 * np.exp(-5 * t) * np.sin(2 * np.pi * 9 * t)
    phase = np.cumsum(2 * np.pi * pitch / SAMPLE_RATE)
    wave = np.sin(phase) * 0.5 + np.sin(2 * phase) * 0.25
    env = np.exp(-3 * t)
    return _to_sound(wave * env * 0.175)


def _make_nope(duration=0.28):
    """Soft 'uh-uh' for wrong inputs in game modes: two short descending
    sine pulses (A4 → E4) with half-sine bell envelopes. Quiet on purpose
    — meant to nudge, not scold."""
    n = int(SAMPLE_RATE * duration)
    wave = np.zeros(n)
    pulse_n = int(SAMPLE_RATE * 0.08)
    for start_s, freq in ((0.00, 440.0), (0.14, 330.0)):
        s = int(SAMPLE_RATE * start_s)
        e = min(n, s + pulse_n)
        if e <= s:
            continue
        seg_n = e - s
        tt = np.linspace(0.0, seg_n / SAMPLE_RATE, seg_n, endpoint=False)
        env = np.sin(np.pi * np.linspace(0.0, 1.0, seg_n))
        wave[s:e] += np.sin(2 * np.pi * freq * tt) * env
    return _to_sound(wave * 0.110)


# ---------- Animal sounds (synthesized, no sample files) ----------

def _make_bird(duration=0.20):
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    # Fast warble around 2.5 kHz
    freq = 2500 + 800 * np.sin(2 * np.pi * 28 * t) + 60 * np.sin(2 * np.pi * 70 * t)
    phase = np.cumsum(2 * np.pi * freq / SAMPLE_RATE)
    wave = np.sin(phase)
    env = np.exp(-9 * t) * (1 - np.exp(-40 * t))
    return _to_sound(wave * env * 0.150)


def _make_frog(duration=0.32):
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    base = 85
    wave = np.zeros(n)
    for h in (1, 2, 3, 4, 5, 6):
        wave += np.sin(2 * np.pi * base * h * t) / h
    wave /= max(np.max(np.abs(wave)), 1e-6)
    # Two smooth Gaussian pulses (no hard gates — those click).
    def pulse(center, width=0.035):
        return np.exp(-((t - center) / width) ** 2)
    env = pulse(0.05) + pulse(0.22)
    return _to_sound(wave * env * 0.275)


def _make_dog(duration=0.24):
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    pitch = 150 + 90 * np.exp(-18 * t)
    phase = np.cumsum(2 * np.pi * pitch / SAMPLE_RATE)
    wave = (np.sin(phase) + 0.5 * np.sin(2 * phase)
            + 0.3 * np.sin(3 * phase) + 0.2 * np.sin(4 * phase))
    wave /= max(np.max(np.abs(wave)), 1e-6)
    wave = wave * 0.7 + np.random.normal(0, 0.3, n) * 0.3
    # Smooth attack + decay (was piecewise np.where with a slope kink).
    env = (1 - np.exp(-35 * t)) * np.exp(-5 * t)
    return _to_sound(wave * env * 0.190)


def _make_cat(duration=0.42):
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    # "Meee-ow" — rise then fall
    pitch = 280 + 220 * np.sin(np.pi * t / duration)
    phase = np.cumsum(2 * np.pi * pitch / SAMPLE_RATE)
    wave = (np.sin(phase) * 0.45 + np.sin(2 * phase) * 0.30
            + np.sin(3 * phase) * 0.18)
    env = np.exp(-3 * t) * (1 - np.exp(-30 * t))
    return _to_sound(wave * env * 0.190)


def _make_owl(duration=0.60):
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    pitch = 220
    wave = np.sin(2 * np.pi * pitch * t) * 0.85 + np.random.normal(0, 0.1, n)
    # Two Gaussian "hoots" — smooth at every t, so the envelope never
    # has a discontinuity. (The previous version used hard-gated tent
    # functions which produced a click each hoot.)
    def hoot(center, width=0.07):
        return np.exp(-((t - center) / width) ** 2)
    env = hoot(0.10) * 0.95 + hoot(0.42) * 0.95
    return _to_sound(wave * env * 0.175)


def _make_cow(duration=0.70):
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    pitch = 115 + 18 * np.sin(2 * np.pi * 1.8 * t)
    phase = np.cumsum(2 * np.pi * pitch / SAMPLE_RATE)
    wave = (np.sin(phase) * 0.50 + np.sin(2 * phase) * 0.30
            + np.sin(3 * phase) * 0.18)
    env = (1 - np.exp(-15 * t)) * np.exp(-1.8 * t)
    return _to_sound(wave * env * 0.210)


def _make_sheep(duration=0.50):
    """Wobbly 'baa' — pitched buzz with vibrato that drops at the end."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    # Pitch slides from ~340 Hz down to ~260 Hz with fast vibrato.
    pitch = 300 + 40 * (1 - t / duration) + 22 * np.sin(2 * np.pi * 14 * t)
    phase = np.cumsum(2 * np.pi * pitch / SAMPLE_RATE)
    wave = np.sin(phase) * 0.55 + np.sin(2 * phase) * 0.35
    env = (1 - np.exp(-30 * t)) * np.exp(-2.6 * t)
    return _to_sound(wave * env * 0.200)


def _make_pig(duration=0.32):
    """Snorty 'oink' — saw-ish buzz with two punchy attacks."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    pitch = 180 + 80 * np.exp(-8 * t)
    phase = np.cumsum(2 * np.pi * pitch / SAMPLE_RATE)
    wave = (np.sin(phase) * 0.45 + np.sin(2 * phase) * 0.30
            + np.sin(3 * phase) * 0.20)
    # Two grunts via Gaussian bursts.
    def grunt(center, width=0.04):
        return np.exp(-((t - center) / width) ** 2)
    env = grunt(0.05) * 0.95 + grunt(0.20) * 0.90
    return _to_sound(wave * env * 0.220)


# ---------- Instrument fallbacks (used when the bundled samples are
# ---------- missing from /opt/smashbox/sounds/instruments/).
# These are simple, recognizable-shape timbres rather than realistic
# instrument models — good enough as a fallback path so the kiosk still
# makes the "right" sound when the user hits the instrument key.

def _make_guitar(duration=0.50):
    """Plucked-string ping at G3 — exponential-decaying harmonic stack."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    freq = 196.0  # G3
    wave = np.zeros(n)
    for ratio, amp, decay in [(1.0, 1.00, 2.5), (2.0, 0.50, 4.0),
                              (3.0, 0.30, 6.0), (4.0, 0.15, 9.0)]:
        wave += amp * np.exp(-decay * t) * np.sin(2 * np.pi * freq * ratio * t)
    wave /= max(np.max(np.abs(wave)), 1e-6)
    return _to_sound(wave * 0.220)


def _make_piano_hit(duration=0.50):
    """Single piano-like hit at C4. Re-uses the handpan-flavoured
    _make_piano synth (the default smash voice) but tuned to a fixed
    pitch and given a longer duration for a natural decay."""
    return _make_piano(DIATONIC[0], duration=duration)


def _make_sax(duration=0.55):
    """Reedy A3 sustain — odd-harmonic stack with 6 Hz vibrato."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    freq = 220.0  # A3
    vib = 5.0 * np.sin(2 * np.pi * 6 * t)
    phase = np.cumsum(2 * np.pi * (freq + vib) / SAMPLE_RATE)
    wave = np.zeros(n)
    for h, amp in [(1, 1.00), (2, 0.50), (3, 0.33), (4, 0.25), (5, 0.20)]:
        wave += amp * np.sin(phase * h)
    wave /= max(np.max(np.abs(wave)), 1e-6)
    env = (1 - np.exp(-20 * t)) * np.exp(-2.5 * t)
    return _to_sound(wave * env * 0.180)


def _make_flute(duration=0.55):
    """Pure C5 sine + filtered breath noise."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    freq = 523.25  # C5
    wave = np.sin(2 * np.pi * freq * t) * 0.7
    noise = np.random.normal(0, 0.15, n)
    k = np.ones(8) / 8
    wave += np.convolve(noise, k, mode='same') * 0.3
    env = (1 - np.exp(-15 * t)) * np.exp(-2.0 * t)
    return _to_sound(wave * env * 0.170)


def _make_violin(duration=0.55):
    """Bowed A4 — saw-like via harmonic stack with vibrato."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    freq = 440.0  # A4
    vib = 3.0 * np.sin(2 * np.pi * 5 * t)
    phase = np.cumsum(2 * np.pi * (freq + vib) / SAMPLE_RATE)
    wave = np.zeros(n)
    for h in range(1, 7):
        wave += (1.0 / h) * np.sin(phase * h)
    wave /= max(np.max(np.abs(wave)), 1e-6)
    env = (1 - np.exp(-12 * t)) * np.exp(-2.5 * t)
    return _to_sound(wave * env * 0.160)


def _make_drum(duration=0.30):
    """Snare-flavoured hit — low pitched thud + noise burst."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    freq = 120 * np.exp(-12 * t) + 80
    phase = np.cumsum(2 * np.pi * freq / SAMPLE_RATE)
    thud = np.sin(phase) * 0.6
    noise = np.random.normal(0, 0.7, n)
    env = np.exp(-22 * t)
    return _to_sound((thud + noise) * env * 0.240)


def _make_perc(duration=0.18):
    """Closed hi-hat — bright high-passed noise with very fast decay.
    Cheap high-pass: subtract a moving-average of the noise to remove
    low-frequency content, leaving the hiss in the upper register."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    noise = np.random.normal(0, 0.8, n)
    k = np.ones(40) / 40
    smoothed = np.convolve(noise, k, mode='same')
    high = noise - smoothed
    env = np.exp(-26 * t)
    return _to_sound(high * env * 0.250)


def _make_xylophone(freq, duration=0.40):
    """Struck wooden bar: fundamental plus the bar's inharmonic
    ~3.9x partial, which decays much faster than the body."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    wave = (np.sin(2 * np.pi * freq * t) * np.exp(-9 * t)
            + 0.4 * np.sin(2 * np.pi * freq * 3.93 * t) * np.exp(-30 * t))
    wave /= max(np.max(np.abs(wave)), 1e-6)
    return _to_sound(wave * 0.220)


def _make_keyed_fallbacks():
    """Synth stand-ins for the recorded samples, keyed by the basenames
    used in Smashbox._KEY_CYCLES_TABLE."""
    return {
        "bird":  _make_bird(),
        "cat":   _make_cat(),
        "cow":   _make_cow(),
        "dog":   _make_dog(),
        "frog":  _make_frog(),
        "owl":   _make_owl(),
        "pig":   _make_pig(),
        "sheep": _make_sheep(),
        "guitar":     _make_guitar(),
        "piano":      _make_piano_hit(),
        "saxophone":  _make_sax(),
        "flute":      _make_flute(),
        "violin":     _make_violin(),
        "drums":      _make_drum(),
        "percussion": _make_perc(),
        "xylophone-1": _make_xylophone(DIATONIC[7]),
        "xylophone-2": _make_xylophone(DIATONIC[9]),
    }




# ---------------------------------------------------------------------------
# Particles and floating letters
# ---------------------------------------------------------------------------

_SIZE_BUCKETS = (12, 18, 26)


def _shape_bucket(size: float) -> int:
    if size <= 14:
        return _SIZE_BUCKETS[0]
    if size <= 22:
        return _SIZE_BUCKETS[1]
    return _SIZE_BUCKETS[2]


def _make_shape_sprite(shape: str, color: tuple, size: int) -> "pygame.Surface":
    pad = int(size * 2.6)
    s = pygame.Surface((pad, pad), pygame.SRCALPHA)
    cx = cy = pad / 2
    if shape == "rect":
        pygame.draw.rect(
            s, color,
            pygame.Rect(int(cx - size), int(cy - size / 2), size * 2, size))
    elif shape == "bubble":
        pygame.draw.circle(s, color, (int(cx), int(cy)), size,
                           width=max(2, size // 6))
        pygame.draw.circle(s, (255, 255, 255, 180),
                           (int(cx - size / 3), int(cy - size / 3)),
                           max(1, size // 5))
    elif shape == "star":
        pts = []
        for i in range(10):
            r = size if i % 2 == 0 else size * 0.45
            a = i * math.pi / 5 - math.pi / 2
            pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
        pygame.draw.polygon(s, color, pts)
    elif shape == "fish":
        w, h = size * 2, size
        pygame.draw.ellipse(s, color,
                            (int(cx - w / 2), int(cy - h / 2), w, h))
        pygame.draw.polygon(s, color, [
            (cx + w / 2, cy),
            (cx + w, cy - h / 2),
            (cx + w, cy + h / 2),
        ])
        pygame.draw.circle(s, (20, 20, 30),
                           (int(cx - w / 4), int(cy - h / 8)),
                           max(2, h // 8))
    else:
        pygame.draw.circle(s, color, (int(cx), int(cy)), size)
    # No convert_alpha — Texture.from_surface handles GPU-format conversion
    # itself, and convert_alpha requires a legacy display Surface which
    # the Window+Renderer pipeline doesn't create.
    return s


class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "max_life", "color",
                 "size", "shape", "_grav")

    def __init__(self, x, y, vx, vy, life, color, size, shape):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.life = life
        self.max_life = life
        self.color = color
        self.size = size
        self.shape = shape
        # Pre-baked gravity term so update() avoids the branch.
        self._grav = 220.0 if shape == "rect" else -60.0 if shape == "bubble" else 30.0

    def update(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vy += self._grav * dt
        self.vx *= (1 - 0.6 * dt)
        self.life -= dt

    @property
    def alive(self):
        return self.life > 0

    def draw(self, renderer, tex_cache):
        # GPU draw: texture lookup, alpha mod, hardware-accelerated copy.
        # Roughly an order of magnitude faster than a CPU SRCALPHA blit.
        # dstrect is a tuple, not a pygame.Rect — saves the per-frame
        # Rect allocation across 60+ particles.
        tex, w, h = tex_cache[(self.shape, self.color,
                               _shape_bucket(self.size))]
        frac = self.life / self.max_life
        tex.alpha = 255 if frac >= 1.0 else int(255 * max(0.0, frac))
        tex.draw(dstrect=(int(self.x - w / 2),
                          int(self.y - h / 2), w, h))


class FloatingSprite:
    """A pre-built texture (a letter, clip-art, etc.) that floats and
    fades. Holds size + angle baked in at spawn — `draw()` is one GPU
    copy with alpha modulation and hardware rotation."""
    __slots__ = ("tex", "w", "h", "x", "y", "vx", "vy",
                 "life", "max_life", "angle")

    def __init__(self, tex, w, h, x, y, life=2.0, vy_bias=-80, angle=0.0):
        self.tex = tex
        self.w = w
        self.h = h
        self.x = x
        self.y = y
        a = random.uniform(0, math.tau)
        speed = random.uniform(40, 110)
        self.vx = math.cos(a) * speed
        self.vy = math.sin(a) * speed + vy_bias
        self.life = self.max_life = life
        self.angle = angle

    def update(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vy += 35 * dt
        self.vx *= (1 - 0.4 * dt)
        self.life -= dt

    @property
    def alive(self):
        return self.life > 0

    def draw(self, renderer):
        frac = self.life / self.max_life
        self.tex.alpha = 255 if frac >= 1.0 else int(255 * max(0.0, frac))
        self.tex.draw(dstrect=(int(self.x - self.w / 2),
                               int(self.y - self.h / 2),
                               self.w, self.h),
                      angle=self.angle)


# Rotation is applied by the GPU at draw time (via FloatingSprite's
# `angle` attribute stored at spawn), so we never pre-rotate.


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

class Smashbox:
    SUPERVISOR_WORD = "iddqd"
    CORNER_HOLD_SECONDS = 2.0
    CORNER_SIZE = 80  # pixels from the top-left corner

    # Substring matches for the Pi's 3.5 mm jack, which ALSA calls
    # "bcm2835 Headphones".
    JACK_HINTS = ("headphones",)

    @classmethod
    def _pick_audio_device(cls):
        """Enumerate playback devices and pick the 3.5 mm jack.
        SMASHBOX_AUDIO_DEVICE overrides the choice by name, for debugging
        or to force a specific device."""
        forced = os.environ.get("SMASHBOX_AUDIO_DEVICE")
        if forced:
            return forced
        if get_audio_device_names is None:
            return None
        try:
            names = get_audio_device_names(False)
        except pygame.error:
            return None
        if not names:
            return None
        print(f"[init] audio devices: {names}", flush=True)
        # None leaves SDL on its default device.
        for name in names:
            low = name.lower()
            if any(h in low for h in cls.JACK_HINTS):
                return name
        return None

    # Key → ordered list of (category, basename, clip-art index) for
    # round-robin cycling. Each press of a mapped key advances to the
    # next item and plays its sound + shows its sprite.
    #
    # Mnemonics are the Danish first letter of each item:
    #   fugl (bird), fløjte (flute), frø (frog), får (sheep)       → K_f
    #   guitar, gris (pig)                                          → K_g
    #   hund (dog)                                                  → K_h
    #   kat (cat), ko (cow), klaver (piano)                         → K_k
    #   perkussion                                                  → K_p
    #   saxofon                                                     → K_s
    #   tromme (drums)                                              → K_t
    #   ugle (owl)                                                  → K_u
    #   violin                                                      → K_v
    #   xylofon                                                     → K_x
    #
    # Items within a cycle are alphabetical by English name so the
    # rotation order is deterministic and easy to scan/edit.
    _KEY_CYCLES_TABLE = {
        "K_f": [
            ("animals",     "bird",  CLIP_BIRD),    # fugl
            ("instruments", "flute", CLIP_FLUTE),   # fløjte
            ("animals",     "frog",  CLIP_FROG),    # frø
            ("animals",     "sheep", CLIP_SHEEP),   # får
        ],
        "K_g": [
            ("instruments", "guitar", CLIP_GUITAR),
            ("animals",     "pig",    CLIP_PIG),    # gris
        ],
        "K_h": [
            ("animals", "dog", CLIP_DOG),           # hund
        ],
        "K_k": [
            ("animals",     "cat",   CLIP_CAT),     # kat
            ("animals",     "cow",   CLIP_COW),     # ko
            ("instruments", "piano", CLIP_PIANO),   # klaver
        ],
        "K_p": [
            ("instruments", "percussion", CLIP_PERCUSSION),
        ],
        "K_s": [
            ("instruments", "saxophone", CLIP_SAXOPHONE),
        ],
        "K_t": [
            ("instruments", "drums", CLIP_DRUMS),   # tromme
        ],
        "K_u": [
            ("animals", "owl", CLIP_OWL),           # ugle
        ],
        "K_v": [
            ("instruments", "violin", CLIP_VIOLIN),
        ],
        "K_x": [
            ("instruments", "xylophone-1", CLIP_XYLOPHONE),  # xylofon
            ("instruments", "xylophone-2", CLIP_XYLOPHONE),
        ],
    }

    # Digit-key → spoken Danish number basename. Files live under
    # ``sounds/counts/<basename>.{ogg,wav}``. The set is optional — if
    # files are missing the loader returns an empty dict and the kiosk
    # falls back to the piano-note default (and the existing
    # celebration chime in counting mode). Drop user-recorded voice
    # samples here and they're picked up automatically on next build.
    _COUNT_SAMPLES = (
        ("K_1", "et"),
        ("K_2", "to"),
        ("K_3", "tre"),
        ("K_4", "fire"),
        ("K_5", "fem"),
        ("K_6", "seks"),
        ("K_7", "syv"),
        ("K_8", "otte"),
        ("K_9", "ni"),
    )

    # Special non-printable keys → clip-art. These keys have no ASCII
    # letter to show, so the burst displays the clip-art alone. Pairs
    # the existing action sound (boom/chime/pop/slide/swoosh) with the
    # visual it already implies. The seven previously-unused primitives
    # (sun/heart/star/cloud/flower/snowflake/apple) are paired with
    # modifier keys so every keyboard key produces a distinct visual.
    _SPECIAL_CLIP_BY_KEY_NAME = {
        # Action keys with a paired sound.
        "K_SPACE":     CLIP_LIGHTNING,
        "K_RETURN":    CLIP_BELL,
        "K_KP_ENTER":  CLIP_BELL,
        "K_BACKSPACE": CLIP_BALLOON,
        "K_TAB":       CLIP_MUSIC,
        "K_UP":        CLIP_ARROW,
        "K_DOWN":      CLIP_ARROW,
        "K_LEFT":      CLIP_ARROW,
        "K_RIGHT":     CLIP_ARROW,
        # Modifier / nav keys — visual only (the sound stays the default
        # piano-by-smash-counter fallback). Mapping is arbitrary but
        # consistent so the toddler learns "shift → star always".
        "K_LSHIFT":    CLIP_STAR,
        "K_RSHIFT":    CLIP_STAR,
        "K_LCTRL":     CLIP_HEART,
        "K_RCTRL":     CLIP_HEART,
        "K_LALT":      CLIP_FLOWER,
        "K_RALT":      CLIP_FLOWER,
        "K_CAPSLOCK":  CLIP_SUN,
        "K_INSERT":    CLIP_SNOWFLAKE,
        "K_DELETE":    CLIP_CLOUD,
        "K_HOME":      CLIP_APPLE,
    }

    # Per-key base rotation for the spawned clipart sprite. Only the
    # arrow keys care; everything else picks up the burst()'s default
    # of 0° (plus the existing small random tilt jitter).
    _KEYED_ANGLE_BY_KEY_NAME = {
        "K_UP":    0.0,
        "K_RIGHT": 90.0,
        "K_DOWN":  180.0,
        "K_LEFT":  270.0,
    }

    @classmethod
    def _sounds_root(cls):
        """Where do the bundled audio assets live?

        - Kiosk image: ``/opt/smashbox/sounds`` (placed there by
          ``scripts/rpi_image_customize.py``).
        - Dev checkout: ``<repo>/assets/sounds`` relative to this
          script.
        - Env override: ``SMASHBOX_SOUNDS_DIR``.

        Returns the first path that exists, or None — caller decides
        whether the missing-assets case is fatal."""
        candidates = []
        override = os.environ.get("SMASHBOX_SOUNDS_DIR")
        if override:
            candidates.append(override)
        candidates.append("/opt/smashbox/sounds")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(script_dir, "assets", "sounds"))
        for c in candidates:
            if os.path.isdir(c):
                return c
        return None

    def _load_samples_by_basename(self, category, basenames):
        """Look for ``<sounds_root>/<category>/<basename>.{ogg,wav}`` for
        each basename and load it as a ``Sound``. Returns a dict of
        ``{basename: Sound}`` for the ones that loaded. Missing files
        (or no sounds_root at all) produce a smaller dict — the caller
        leaves the synth fallback in place for those entries."""
        if not self.audio_ok:
            return {}
        root = self._sounds_root()
        if root is None:
            return {}
        loaded = {}
        for base in basenames:
            for ext in (".ogg", ".wav"):
                path = os.path.join(root, category, base + ext)
                if not os.path.isfile(path):
                    continue
                try:
                    loaded[base] = pygame.mixer.Sound(path)
                except pygame.error as e:
                    print(f"[init] sample load failed {path}: {e!r}",
                          flush=True)
                break  # first matching extension wins
        if loaded:
            print(f"[init] {category} samples loaded from {root}: "
                  f"{sorted(loaded.keys())}", flush=True)
        return loaded

    def __init__(self):
        # We may overwrite SAMPLE_RATE post-mixer-init to match whatever
        # the device negotiated (see "sync synth rate" below). Declared
        # at the top because Python forbids `global` after a read.
        global SAMPLE_RATE

        # ---- audio must be configured BEFORE pygame.init() -------------
        os.environ.setdefault("PULSE_LATENCY_MSEC", "20")
        os.environ.setdefault("SDL_AUDIO_DEVICE_APP_NAME", "smashbox")
        # 256-sample buffer ≈ 5.8ms floor at 44.1kHz, 5.3ms at 48kHz.
        # Earlier defaults of 64 / 128 gave lower latency on paper but
        # underran on Pi 4 + USB speakers under mixer load (24 channels),
        # producing periodic crackle. 256 is well below toddler-perceptible
        # latency and leaves the mixer thread real headroom.
        audio_buf = int(os.environ.get("SMASHBOX_AUDIO_BUFFER", "256"))

        # pre_init has to come before pygame.init() so the mixer doesn't
        # come up with pygame's defaults. We init pygame WITHOUT mixer
        # first to enumerate the audio devices, then mixer.init with a
        # chosen device. SDL_INIT_AUDIO is needed for enumeration.
        pygame.mixer.pre_init(SAMPLE_RATE, -16, 2, audio_buf)
        pygame.init()

        # Block events we never consume so pygame.event.get() doesn't pay
        # Python translation cost for them during a flooding KEYDOWN
        # burst. We DO consume MOUSEMOTION (corner-hold tracking), so
        # leave that allowed. We also leave TEXTINPUT and TEXTEDITING
        # allowed even though we never read them: on Bookworm's
        # pygame 2.1.x, KEYDOWN.unicode population can be coupled to
        # SDL's text-input state, and blocking those events has been
        # observed to leave .unicode empty — which would silently break
        # the "iddqd" supervisor-gate backdoor and the per-letter piano-note
        # mapping. Their queue cost is tiny (only fire when keys produce
        # text — same cadence as KEYDOWN), so it's not worth the risk.
        # Constants are looked up by name because not all exist across
        # pygame versions.
        _block = [getattr(pygame, _n, None) for _n in (
            "AUDIODEVICEADDED", "AUDIODEVICEREMOVED", "WINDOWEVENT",
            "JOYAXISMOTION", "JOYBALLMOTION", "JOYHATMOTION",
            "JOYBUTTONDOWN", "JOYBUTTONUP",
            "JOYDEVICEADDED", "JOYDEVICEREMOVED",
            "MULTIGESTURE", "FINGERMOTION", "FINGERUP",
            "DROPBEGIN", "DROPCOMPLETE", "DROPFILE", "DROPTEXT",
            "USEREVENT",
        )]
        _block = [_e for _e in _block if _e is not None]
        if _block:
            pygame.event.set_blocked(_block)

        device = self._pick_audio_device()
        try:
            # Force-reinit with our exact params + chosen device.
            pygame.mixer.quit()
            kwargs = dict(frequency=SAMPLE_RATE, size=-16, channels=2,
                          buffer=audio_buf)
            if device:
                kwargs["devicename"] = device
            try:
                pygame.mixer.init(**kwargs)
            except (pygame.error, TypeError) as e:
                # Either the named device failed to open (e.g. it got
                # unplugged between enumeration and now), or this pygame
                # build doesn't accept devicename. Fall back to default.
                if device:
                    print(f"[init] mixer.init(device={device!r}) failed "
                          f"({e!r}); falling back to default", flush=True)
                pygame.mixer.init(SAMPLE_RATE, -16, 2, audio_buf)
                device = None
            # 16 channels: at toddler mash rates (~3-5 keys/sec) with
            # ~0.3s sounds, max concurrent is ~5. 16 keeps 3× headroom
            # while halving per-callback mixer work vs. the old 24.
            pygame.mixer.set_num_channels(16)
            self.audio_ok = True
            # Sync the synth rate to whatever the mixer actually negotiated.
            # pygame.mixer.init's default allowedchanges permits frequency
            # change, so a device that does not take 44.1kHz gets its
            # native rate instead of our request. If we kept synthesizing at
            # 44.1kHz the mixer would either resample (CPU + softness) or
            # play the buffer faster than intended. Querying back and
            # rebuilding sounds at the actual rate avoids both.
            actual = pygame.mixer.get_init()
            if actual is not None and actual[0] != SAMPLE_RATE:
                print(f"[init] adjusting synth rate "
                      f"{SAMPLE_RATE} -> {actual[0]} (device native)",
                      flush=True)
                SAMPLE_RATE = actual[0]
            print(f"[init] audio: {pygame.mixer.get_init()} "
                  f"buf={audio_buf} samples "
                  f"(~{audio_buf*1000/SAMPLE_RATE:.1f}ms floor) "
                  f"device={device or 'default'}", flush=True)
        except pygame.error as e:
            print(f"[init] audio init failed: {e!r}", flush=True)
            self.audio_ok = False

        info = pygame.display.Info()
        phys = (info.current_w or 1280, info.current_h or 720)
        want_fullscreen = os.environ.get("SMASHBOX_WINDOWED", "0") == "0"
        # Cap the logical render resolution. At 4K (or anything above
        # the cap) the renderer uses 1080p internal coords and GPU-scales
        # to the actual panel — replaces the old SCALED flag.
        logical_h_cap = int(os.environ.get("SMASHBOX_LOGICAL_HEIGHT", "1080"))
        if want_fullscreen and phys[1] > logical_h_cap:
            aspect = phys[0] / phys[1]
            self.size = (int(logical_h_cap * aspect), logical_h_cap)
            scaled_used = True
        else:
            self.size = phys
            scaled_used = False

        print(f"[init] smashbox v{__version__}", flush=True)
        print(f"[init] pygame {pygame.version.ver}, "
              f"SDL {'.'.join(map(str, pygame.get_sdl_version()))}, "
              f"driver={pygame.display.get_driver()}", flush=True)
        print(f"[init] physical={phys} logical={self.size} "
              f"scaled={scaled_used} fullscreen={want_fullscreen}",
              flush=True)

        # GPU pipeline: Window + Renderer. Surfaces become Textures and
        # draws happen on the GPU rather than as CPU alpha blits.
        self.window = Window(
            f"Smashbox v{__version__}",
            size=phys,
            fullscreen_desktop=want_fullscreen,
        )
        try:
            self.renderer = Renderer(self.window, accelerated=1, vsync=False)
        except pygame.error:
            print("[init] hardware renderer unavailable, falling back",
                  flush=True)
            self.renderer = Renderer(self.window, accelerated=-1, vsync=False)
        self.renderer.logical_size = self.size
        self.fullscreen = want_fullscreen
        pygame.mouse.set_visible(False)

        self.font_big = pygame.font.SysFont(
            "freesansbold,dejavusans,sans", int(self.size[1] * 0.4), bold=True)
        self.font_hud = pygame.font.SysFont(
            "freesansbold,dejavusans,sans", int(self.size[1] * 0.045), bold=True)
        self.font_panel = pygame.font.SysFont(
            "freesansbold,dejavusans,sans", int(self.size[1] * 0.06), bold=True)
        # Counting mode's bottom-row digit strip — sized so 9 digits
        # read clearly at arm's length on the kiosk panel.
        self.font_count_row = pygame.font.SysFont(
            "freesansbold,dejavusans,sans", int(self.size[1] * 0.10), bold=True)

        self.theme_idx = 0
        # Plain attribute (not a property) so hot-path reads in burst()
        # and spawn_ambient() skip the descriptor + dict lookup. Kept in
        # sync by cycle_theme().
        self.theme = THEMES[THEME_ORDER[self.theme_idx]]
        self.bg_tex_cache: dict = {}      # (theme_name) -> Texture

        self.particles: list[Particle] = []
        self.sprites: list[FloatingSprite] = []
        self.ambient: list[Particle] = []

        # Pre-render all clip-art to GPU textures at a size proportional
        # to the screen.
        clip_size = max(96, int(self.size[1] * 0.22))
        self.clip_textures: list[tuple] = []
        for b in CLIPART_BUILDERS:
            surf = b(clip_size)
            tex = Texture.from_surface(self.renderer, surf)
            self.clip_textures.append((tex, surf.get_width(),
                                       surf.get_height()))

        # Shape texture cache: each (shape, color, bucket) maps to a
        # (texture, w, h) tuple. Pre-warmed for every theme so the first
        # frame after a theme switch is free of any lazy upload cost.
        self.shape_textures: dict = {}
        prewarm_t0 = time.perf_counter()
        for theme in THEMES.values():
            shape = theme["shape"]
            for color in theme["palette"]:
                for bucket in _SIZE_BUCKETS:
                    surf = _make_shape_sprite(shape, color, bucket)
                    tex = Texture.from_surface(self.renderer, surf)
                    self.shape_textures[(shape, color, bucket)] = (
                        tex, surf.get_width(), surf.get_height())
        print(f"[init] pre-warmed shape cache "
              f"({len(self.shape_textures)} textures in "
              f"{(time.perf_counter()-prewarm_t0)*1000:.0f}ms)", flush=True)

        # Letter texture cache: (char, color) -> (texture, w, h). A
        # toddler typing AAAAA used to upload five identical textures —
        # this dedupes them. FIFO-evicted at LETTER_CACHE_CAP so the GPU
        # memory footprint stays bounded.
        self._letter_tex_cache: "dict[tuple, tuple]" = {}

        # Pre-warm the cache for A-Z + 0-9 at one palette colour. On the
        # Pi 4 Cortex-A72, font.render at font_big size (~432px @ 1080p)
        # is ~20ms cold per glyph — without pre-warm, the first ~36
        # keypresses each show as a one-frame-late letter. ~700ms once at
        # init removes the first-mash stutter entirely. Only one colour
        # is pre-warmed because burst() picks a random palette colour;
        # subsequent (char, other_colour) pairs still miss, but the FONT
        # internals (glyph atlas) are warm by then, so those misses are
        # much cheaper than the first one.
        prewarm_t0 = time.perf_counter()
        warm_color = self.theme["palette"][0]
        for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
            self._letter_texture(_ch, warm_color)
        print(f"[init] pre-warmed letter cache "
              f"({len(self._letter_tex_cache)} glyphs in "
              f"{(time.perf_counter()-prewarm_t0)*1000:.0f}ms)", flush=True)

        # Corner-hold indicator cache. We snap `frac` to _CORNER_BUCKETS
        # discrete buckets so the texture only rebuilds when the visible
        # progress arc actually advances by one bucket (~15 Hz at 60 fps
        # for a 2-second hold). Replaces a per-frame Surface + upload.
        self._corner_tex = None
        self._corner_bucket = -1

        # Supervisor-panel text cache. The panel can be on-screen for many
        # seconds; without this, _draw_supervisor_panel does 9 font.render +
        # Texture.from_surface calls per frame = ~540/sec on a Pi 4.
        # Cache key is the tuple of lines, so any state change (mute,
        # mode, theme, smashed-reset, …) invalidates automatically.
        self._panel_cache_key: tuple | None = None
        self._panel_entries: list = []

        # ---- game mode --------------------------------------------------
        # Three modes cycled via [g] in the supervisor panel:
        #   "smash"        — free smash (default), no target.
        #   "whack-a-mole" — a single A–Z or 0–9 target appears; hit it
        #                    to clear and reveal the next one.
        #   "counting"     — digits 1–9 (Danish convention: no 0)
        #                    appear in order; hit each one in sequence.
        #                    Wraps from 9 back to 1 with extra fanfare.
        self.game_state = "smash"
        self.target = None                  # tuple describing the target
        self._target_text_cache_key = None
        self._target_text_tex = None
        self._target_text_size = (0, 0)
        self._target_celebrate_until = 0.0
        self._target_next_at = 0.0
        self._target_shake_until = 0.0     # gentle "no" shake on wrong input
        self.game_hits = 0
        # Counting mode state — position in the 1..9 sequence. When
        # the player completes the row (hits 9) we leave this at 10 as
        # a "row complete, celebrating" sentinel; _tick_target resets
        # it to 1 once the wrap fanfare window elapses.
        self._count_pos = 1
        # Lazy-built textures for the bottom row digit strip: keys are
        # (digit, hit) → (Texture, w, h). 18 entries total once built.
        self._count_row_textures: dict = {}
        # Big-celebration fanfare schedule (counting wrap from 9→1):
        # bursts and sounds queued at absolute timestamps and drained
        # by _tick_target so the fanfare unfolds over ~2.5 s instead
        # of detonating all at once.
        self._count_fanfare_bursts: list = []
        self._count_fanfare_sounds: list = []
        # Mode banner cache (visible-mode label at top of screen).
        self._banner_text: str | None = None
        self._banner_tex = None
        self._banner_size = (0, 0)

        self.smashed = 0

        # Whack-a-mole target pool: built once after pygame.init() so the
        # K_a/K_0 constants are resolved. _pick_target used to rebuild
        # this 36-entry list every call — micro, but free to fix.
        self._wam_pool = (
            [("letter", c, pygame.K_a + i)
             for i, c in enumerate(self.GAME_LETTERS)]
            + [("digit", str(d), pygame.K_0 + d) for d in range(10)]
        )

        # Special-key clip mapping (modifier + action keys → sprite).
        # Animal/instrument clips live in the per-key cycles built
        # below; this dict is consulted only when a key has no cycle of
        # its own.
        self._special_clip = {
            getattr(pygame, name): idx
            for name, idx in self._SPECIAL_CLIP_BY_KEY_NAME.items()
            if hasattr(pygame, name)
        }
        # Per-key base rotation (currently just the four arrow keys).
        # Defaults to 0° via .get(key, 0.0) at the call site.
        self._keyed_angle = {
            getattr(pygame, name): angle
            for name, angle in self._KEYED_ANGLE_BY_KEY_NAME.items()
            if hasattr(pygame, name)
        }

        self.muted = False
        self.snd_piano: list = []
        self.snd_special: dict = {}
        self.snd_counts: dict = {}
        # Per-key round-robin cycle: pygame_key → [(Sound, clip_idx), …].
        # Built below from _KEY_CYCLES_TABLE + the basename → Sound dict.
        self._key_cycles: "dict[int, list[tuple]]" = {}
        # Cycle counter, advances on each press of a cycled key.
        self._key_cycle_pos: "dict[int, int]" = {}
        if self.audio_ok:
            print("[init] building sound bank…", flush=True)
            self.snd_piano = [_make_piano(f) for f in DIATONIC]
            self.snd_special = {
                "boom":     _make_boom(),
                "chime":    _make_chime(),
                "pop":      _make_pop(),
                "boing":    _make_boing(),
                "nope":     _make_nope(),
                "swoosh_u": _make_swoosh(200, 1100),
                "swoosh_d": _make_swoosh(1100, 200),
                "swoosh_h": _make_swoosh(450, 800, 0.18),
                "slide":    _make_swoosh(300, 1500, 0.30),
            }
            # On-disk samples override these on a per-basename basis below.
            sounds_by_base = _make_keyed_fallbacks()
            # Sample overrides: if the kiosk image (or dev checkout)
            # ships real recordings under sounds/{animals,instruments}/
            # <name>.{ogg,wav}, replace the synthesised version with the
            # real thing. Falls back silently when the file isn't there,
            # so dev runs without assets/ keep working via the synth.
            animal_basenames = sorted({
                base for items in self._KEY_CYCLES_TABLE.values()
                for cat, base, _clip in items if cat == "animals"
            })
            instrument_basenames = sorted({
                base for items in self._KEY_CYCLES_TABLE.values()
                for cat, base, _clip in items if cat == "instruments"
            })
            sounds_by_base.update(self._load_samples_by_basename(
                "animals", animal_basenames))
            sounds_by_base.update(self._load_samples_by_basename(
                "instruments", instrument_basenames))

            # Compose the per-key cycle list. Each item is the (Sound,
            # clip_idx) pair to pick up on a given cycle step.
            for key_name, items in self._KEY_CYCLES_TABLE.items():
                key = getattr(pygame, key_name, None)
                if key is None:
                    continue
                cycle = []
                for _cat, base, clip in items:
                    snd = sounds_by_base.get(base)
                    if snd is not None:
                        cycle.append((snd, clip))
                if cycle:
                    self._key_cycles[key] = cycle

            # Count words — keyed by digit, single sound, no cycle.
            count_basenames = [base for _, base in self._COUNT_SAMPLES]
            count_by_base = self._load_samples_by_basename(
                "counts", count_basenames)
            self.snd_counts = {
                getattr(pygame, name): count_by_base[base]
                for name, base in self._COUNT_SAMPLES
                if hasattr(pygame, name) and base in count_by_base
            }

            print("[init] sound bank ready "
                  f"(piano={len(self.snd_piano)}, "
                  f"special={len(self.snd_special)}, "
                  f"key-cycles={len(self._key_cycles)} "
                  f"covering {sum(len(c) for c in self._key_cycles.values())} items, "
                  f"counts={len(self.snd_counts)})", flush=True)

        self.supervisor_buffer = ""
        self.corner_held_since: float | None = None
        self.in_supervisor_panel = False
        self.running = True
        # Ignore supervisor-gate sequences in the first few seconds — protects
        # against phantom KEYDOWN events emitted at X startup or a wedged
        # key on a USB keyboard plugged into the Pi during boot.
        self.gate_grace_until = time.monotonic() + 5.0

    # ------------------------------------------------------------------ theme

    def cycle_theme(self):
        self.theme_idx = (self.theme_idx + 1) % len(THEME_ORDER)
        self.theme = THEMES[THEME_ORDER[self.theme_idx]]
        self.ambient.clear()

    def background_texture(self):
        key = THEME_ORDER[self.theme_idx]
        cached = self.bg_tex_cache.get(key)
        if cached is not None:
            return cached
        # Build the gradient on a CPU surface once, then upload to GPU.
        surf = pygame.Surface(self.size)
        top = self.theme["bg_top"]
        bot = self.theme["bg_bottom"]
        h = self.size[1]
        for y in range(h):
            t = y / max(1, h - 1)
            color = (int(top[0] + (bot[0] - top[0]) * t),
                     int(top[1] + (bot[1] - top[1]) * t),
                     int(top[2] + (bot[2] - top[2]) * t))
            pygame.draw.line(surf, color, (0, y), (self.size[0], y))
        tex = Texture.from_surface(self.renderer, surf)
        self.bg_tex_cache[key] = tex
        return tex

    # ---------------------------------------------------------------- effects

    MAX_PARTICLES = 60
    MAX_SPRITES = 20
    LETTER_CACHE_CAP = 96   # ~A-Z + 0-9 across 6 theme colours, comfortably

    def _letter_texture(self, char: str, color: tuple) -> tuple:
        """Return a cached (texture, w, h) for char rendered in color.
        Renders + uploads on cache miss; FIFO-evicts when over cap.
        Multiple live FloatingSprites can share one Texture — alpha is
        set per-draw, so cross-sprite races are impossible."""
        key = (char, color)
        entry = self._letter_tex_cache.get(key)
        if entry is not None:
            return entry
        if len(self._letter_tex_cache) >= self.LETTER_CACHE_CAP:
            # Python 3.7+ dicts preserve insertion order — pop the oldest.
            oldest = next(iter(self._letter_tex_cache))
            del self._letter_tex_cache[oldest]
        surf = self.font_big.render(char, True, color)
        tex = Texture.from_surface(self.renderer, surf)
        entry = (tex, surf.get_width(), surf.get_height())
        self._letter_tex_cache[key] = entry
        return entry

    def burst(self, x: float, y: float, char: str | None,
              *, clip_idx: int | None = None, angle: float = 0.0):
        """Spawn the burst of particles, the floating letter sprite (if
        char is set), and a clipart sprite (if clip_idx is set).

        Pairing rules (per the smash-mode design):
          * char and clip_idx are independent — pass either, both, or
            neither.
          * Letter/digit keys → caller passes char + (optional) clip_idx
            when the key has an animal/instrument association.
          * Special keys (Space, Enter, …) → caller passes only clip_idx;
            no letter sprite.
          * Mouse / touch / generic-pulse → caller passes neither; just
            the particle burst.
        No random clip-art is ever drawn — every visual is intentional."""
        theme = self.theme
        palette = theme["palette"]
        shape = theme["shape"]
        n = random.randint(4, 8)
        for _ in range(n):
            # Use a distinct name so we don't shadow the `angle`
            # parameter (which is the clipart base rotation — bug
            # observed before this rename: all arrow sprites read as
            # "upward" because this loop trashed the passed-in angle
            # with a random radians value that was later fed to
            # Texture.draw expecting degrees).
            theta = random.uniform(0, math.tau)
            speed = random.uniform(150, 520)
            color = random.choice(palette)
            size = random.randint(10, 20)
            life = random.uniform(0.6, 1.2)
            self.particles.append(Particle(
                x, y, math.cos(theta) * speed, math.sin(theta) * speed,
                life, color, size, shape,
            ))
        if char:
            color = random.choice(palette)
            tex, lw, lh = self._letter_texture(char, color)
            self.sprites.append(FloatingSprite(
                tex, lw, lh, x, y,
                life=2.0, angle=random.uniform(-18, 18)))
        # Clipart sprite: only when the caller forces a specific index.
        # No random fallback — every visual in smash mode is meaningful.
        # `angle` is the base rotation (used for the arrow keys so each
        # direction points the right way); a small random jitter rides
        # on top so consecutive presses don't look identical.
        if (self.clip_textures and clip_idx is not None
                and 0 <= clip_idx < len(self.clip_textures)):
            offset = self.size[1] * 0.10
            gx = x + random.uniform(-offset, offset)
            gy = y + random.uniform(-offset, offset)
            tex, w, h = self.clip_textures[clip_idx]
            self.sprites.append(FloatingSprite(
                tex, w, h, gx, gy, life=2.2,
                angle=angle + random.uniform(-15, 15)))

        # Bound total counts — older items get dropped if we go over.
        # In-place truncation (no list copy), matching the run-loop
        # compaction pattern.
        if len(self.particles) > self.MAX_PARTICLES:
            del self.particles[:-self.MAX_PARTICLES]
        if len(self.sprites) > self.MAX_SPRITES:
            del self.sprites[:-self.MAX_SPRITES]

        # Audio is dispatched separately by _play_for_event so we can pick
        # different timbres per key (piano / animal / bang / pop).

    def spawn_ambient(self, _dt):
        kind = self.theme.get("ambient", "none")
        if kind == "none":
            return
        # Maintain roughly a population proportional to screen size.
        # Ambient is always painted, every frame, so it directly costs frame
        # budget. Halving the populations frees ~10ms per frame on the Pi.
        target = 15 if kind == "stars" else 10
        while len(self.ambient) < target:
            x = random.uniform(0, self.size[0])
            if kind == "bubbles":
                y = self.size[1] + 10
                vy = random.uniform(-60, -30)
                shape = "bubble"
            elif kind == "wavy":
                y = random.uniform(0, self.size[1])
                vy = random.uniform(-15, 15)
                shape = "fish"
            else:  # stars
                y = random.uniform(0, self.size[1])
                vy = random.uniform(-5, 5)
                shape = "star"
            color = random.choice(self.theme["palette"])
            life = random.uniform(6, 14)
            size = random.randint(4, 12)
            vx = random.uniform(-20, 20)
            self.ambient.append(Particle(x, y, vx, vy, life, color, size, shape))

    # ----------------------------------------------------------------- sound

    def _play(self, sound):
        if sound is None or not self.audio_ok or self.muted:
            return
        # Sound.play() finds a free channel and plays, returning None if
        # all 16 channels are busy (drop-on-overflow). Equivalent to the
        # old find_channel(False) + ch.play() two-step, minus a Python
        # call and a temporary.
        sound.play()

    def _play_for_keydown(self, event):
        """Play the sound for this key and return the clip_idx the
        caller should pair with it (or None for "no specific clip").

        Resolution order:
          1. Action keys (Space/Enter/Backspace/Tab/arrows) — hard-coded
             sound + matching clip from _special_clip.
          2. Cycled keys (animals + instruments, grouped by Danish first
             letter — see _KEY_CYCLES_TABLE) — advance the per-key cycle
             and play that item's sound + clip.
          3. Count words (digit keys 1..9) — single sound per digit; no
             clip (the digit char is drawn by burst as the letter sprite).
          4. Modifier keys (Shift/Ctrl/Alt/CapsLock/Insert/Delete/Home)
             — piano-note default sound + clip from _special_clip.
          5. Everything else — piano-note default, no clip."""
        k = event.key
        # 1. Action keys.
        if k == pygame.K_SPACE:
            self._play(self.snd_special.get("boom"))
            return self._special_clip.get(k)
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._play(self.snd_special.get("chime"))
            return self._special_clip.get(k)
        if k == pygame.K_BACKSPACE:
            self._play(self.snd_special.get("pop"))
            return self._special_clip.get(k)
        if k == pygame.K_TAB:
            self._play(self.snd_special.get("slide"))
            return self._special_clip.get(k)
        if k == pygame.K_UP:
            self._play(self.snd_special.get("swoosh_u"))
            return self._special_clip.get(k)
        if k == pygame.K_DOWN:
            self._play(self.snd_special.get("swoosh_d"))
            return self._special_clip.get(k)
        if k in (pygame.K_LEFT, pygame.K_RIGHT):
            self._play(self.snd_special.get("swoosh_h"))
            return self._special_clip.get(k)

        # 2. Round-robin cycled keys (animals + instruments, grouped by
        # Danish first letter). Each press advances by one item.
        cycle = self._key_cycles.get(k)
        if cycle:
            pos = self._key_cycle_pos.get(k, 0)
            snd, clip_idx = cycle[pos % len(cycle)]
            self._key_cycle_pos[k] = pos + 1
            self._play(snd)
            return clip_idx

        # 3. Count words.
        count_snd = self.snd_counts.get(k)
        if count_snd is not None:
            self._play(count_snd)
            return None

        # 4. Modifier keys (sprite-only, no sound override).
        mod_clip = self._special_clip.get(k)
        if mod_clip is not None:
            self._play_piano_default(event)
            return mod_clip

        # 5. Default piano voice.
        self._play_piano_default(event)
        return None

    def _play_piano_default(self, event):
        """Play the default piano-by-key fallback note. Same pitch for
        the same letter every time so the kid hears "I'm typing music"
        rather than random pitches."""
        if not self.snd_piano:
            return
        ch = (event.unicode or "").lower()
        if ch and ch.isalnum():
            idx = ord(ch) % len(self.snd_piano)
        else:
            idx = self.smashed % len(self.snd_piano)
        self._play(self.snd_piano[idx])

    # ------------------------------------------------------------------ input

    def handle_event(self, event):
        now = time.monotonic()

        if self.in_supervisor_panel:
            self._handle_supervisor_event(event)
            return

        if event.type == pygame.KEYDOWN:
            # Block ESC/alt-F4 escapes — toddlers shouldn't be able to quit.
            if event.key in (pygame.K_ESCAPE, pygame.K_F4):
                self._feed_supervisor_buffer("")
                self._maybe_pulse()
                return

            # Track the secret "supervisor" word (works in any mode).
            self._feed_supervisor_buffer(event.unicode)

            if self.game_state != "smash":
                # Game mode: only the correct next key gets a reaction.
                # During the post-hit celebration window, swallow input
                # silently so the celebration isn't interrupted.
                if now < self._target_next_at:
                    return
                if self._target_matches(event):
                    self._handle_game_keypress(event)
                    self.smashed += 1
                else:
                    self._trigger_nope()
                return

            # Letter / digit keys → show the character. Special non-
            # printable keys (Space, Enter, Backspace, Tab, arrows,
            # Shift, modifiers) → no letter — they get only the clip-art
            # returned by _play_for_keydown (or nothing if unmapped).
            uc = event.unicode
            if uc and uc.isprintable() and not uc.isspace():
                char = uc.upper()
            else:
                char = None
            x = random.uniform(self.size[0] * 0.1, self.size[0] * 0.9)
            y = random.uniform(self.size[1] * 0.2, self.size[1] * 0.85)
            # Play the sound first (so the cycle counter for keys with
            # multiple items advances exactly once per press); the
            # returned clip_idx is the visual that pairs with the sound
            # we just played. Arrow keys also pick up a base rotation.
            clip_idx = self._play_for_keydown(event)
            angle = self._keyed_angle.get(event.key, 0.0)
            self.burst(x, y, char, clip_idx=clip_idx, angle=angle)
            self.smashed += 1

        elif event.type == pygame.MOUSEBUTTONDOWN:
            x, y = event.pos
            in_corner = x < self.CORNER_SIZE and y < self.CORNER_SIZE
            if in_corner:
                self.corner_held_since = now
            if self.game_state != "smash":
                # Skip the smash reaction in game modes. Corner-hold
                # still arms the supervisor gate silently; everything else
                # gets the gentle nope.
                if not in_corner:
                    self._trigger_nope()
                return
            self.burst(x, y, None)
            # Mouse / touch: 1-in-8 chance of a "boing" surprise,
            # otherwise a random piano note.
            if random.random() < 0.12 and self.snd_special:
                self._play(self.snd_special.get("boing"))
            elif self.snd_piano:
                self._play(random.choice(self.snd_piano))
            self.smashed += 1

        elif event.type == pygame.MOUSEBUTTONUP:
            self.corner_held_since = None

        elif event.type == pygame.MOUSEMOTION:
            if (self.corner_held_since is not None and
                    (event.pos[0] >= self.CORNER_SIZE or
                     event.pos[1] >= self.CORNER_SIZE)):
                self.corner_held_since = None

        elif event.type == pygame.FINGERDOWN:
            x = event.x * self.size[0]
            y = event.y * self.size[1]
            in_corner = x < self.CORNER_SIZE and y < self.CORNER_SIZE
            if in_corner:
                self.corner_held_since = now
            if self.game_state != "smash":
                if not in_corner:
                    self._trigger_nope()
                return
            self.burst(x, y, None)
            if self.snd_piano:
                self._play(random.choice(self.snd_piano))
            self.smashed += 1

    def _feed_supervisor_buffer(self, ch: str):
        if time.monotonic() < self.gate_grace_until:
            return
        self.supervisor_buffer = (self.supervisor_buffer + ch.lower())[-len(self.SUPERVISOR_WORD):]
        if self.supervisor_buffer == self.SUPERVISOR_WORD:
            self.supervisor_buffer = ""
            self.open_supervisor_panel()

    def _maybe_pulse(self):
        # Visual hint that the key was received even though the action was
        # suppressed — a tiny burst at the center of the screen.
        self.burst(self.size[0] / 2, self.size[1] / 2, None)

    # ------------------------------------------------------------ supervisor gate

    def open_supervisor_panel(self):
        self.in_supervisor_panel = True
        self.corner_held_since = None
        pygame.mouse.set_visible(True)

    def close_supervisor_panel(self):
        self.in_supervisor_panel = False
        pygame.mouse.set_visible(False)

    def _handle_supervisor_event(self, event):
        if event.type != pygame.KEYDOWN:
            return
        if event.key == pygame.K_ESCAPE:
            self.close_supervisor_panel()
        elif event.key == pygame.K_q:
            self.running = False
        elif event.key == pygame.K_t:
            self.cycle_theme()
        elif event.key == pygame.K_m:
            self.muted = not self.muted
        elif event.key == pygame.K_f:
            self.toggle_fullscreen()
        elif event.key == pygame.K_r:
            self.smashed = 0
        elif event.key == pygame.K_g:
            self.toggle_game_mode()

    # Cycle order: smash (free play) → counting (gentle, ordered)
    # → whack-a-mole (harder, random). Counting comes first because
    # it's the easier game-mode step up from smash — a fixed
    # sequence the toddler can master before facing random targets.
    GAME_STATES = ("smash", "counting", "whack-a-mole")

    def toggle_game_mode(self):
        # Cycle smash → counting → whack-a-mole → smash.
        idx = self.GAME_STATES.index(self.game_state)
        self.game_state = self.GAME_STATES[(idx + 1) % len(self.GAME_STATES)]
        self.game_hits = 0
        self.target = None
        self._target_celebrate_until = 0.0
        self._target_next_at = 0.0
        # Invalidate the banner cache so the next render rebuilds it.
        self._banner_text = None
        if self.game_state == "counting":
            self._count_pos = 1
        if self.game_state != "smash":
            self._pick_target()

    GAME_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    def _pick_target(self):
        if self.game_state == "counting":
            # Fixed sequence 1..9 (Danish convention: no zero in the
            # count). Once the row is complete (_count_pos == 10), the
            # target becomes "0" — the dedicated reset key. 0 is not
            # part of the count sequence, so pressing it earlier in
            # the row gets the same nope as any other wrong key.
            if self._count_pos > 9:
                self.target = ("digit", "0", pygame.K_0)
            else:
                digit = str(self._count_pos)
                self.target = ("digit", digit, pygame.K_0 + self._count_pos)
        else:
            # "whack-a-mole" pool = A–Z and 0–9 only. Clipart targets for
            # special keys were removed so the toddler always has a
            # printable character to match. Pool itself is built once in
            # __init__ (see _wam_pool).
            prev = self.target
            cand = random.choice(self._wam_pool)
            for _ in range(8):
                if cand != prev:
                    break
                cand = random.choice(self._wam_pool)
            self.target = cand
        self._target_celebrate_until = 0.0
        self._target_next_at = 0.0

    def _target_matches(self, event) -> bool:
        if self.target is None:
            return False
        kind = self.target[0]
        if kind == "letter":
            return (event.unicode or "").upper() == self.target[1]
        if kind == "digit":
            return (event.unicode or "") == self.target[1]
        return False

    def _handle_game_keypress(self, event):
        """Run the celebration for a matched target press: triple burst
        around the centre, chime, hit counter, the matching keyed sound
        (animal / instrument / count word — if any), and (for counting
        mode) advance the 1..9 counter.

        Caller (handle_event) is responsible for filtering — this is
        only invoked once it has confirmed the press matches the target
        and we're not still inside the previous celebration window."""
        # _target_matches() returned True for this event, so target is
        # not None. Asserted to document the invariant for type-checkers
        # (cheap; never trips in practice).
        assert self.target is not None
        now = time.monotonic()
        cx, cy = self.size[0] / 2, self.size[1] / 2
        # Triple-burst celebration around the target.
        char_for_burst = self.target[1]
        for dx, dy in ((-1, 0), (1, 0), (0, -1)):
            self.burst(cx + dx * self.size[0] * 0.08,
                       cy + dy * self.size[1] * 0.08,
                       char_for_burst)
        self._play(self.snd_special.get("chime"))
        # Layer the matched key's own sound on top of the chime — hitting
        # the "D" target in whack-a-mole also plays a dog bark, hitting
        # "1" in counting also plays "et" (once voice samples are
        # recorded), etc. Falls through to the piano fallback otherwise.
        self._play_for_keydown(event)
        self.game_hits += 1

        if self.game_state == "counting":
            if self._count_pos > 9:
                # The press that brought us here was on "0" — the reset
                # key shown after a completed row. Start over from 1.
                self._count_pos = 1
                self._pick_target()
                self._target_celebrate_until = now + 0.40
                self._target_next_at = now + 0.40
                return
            self._count_pos += 1
            if self._count_pos > 9:
                # Row complete on 9. Big fanfare; _pick_target() with
                # count_pos == 10 sets target = "0" so once the
                # celebration window ends the toddler sees "0" as the
                # next thing to hit (instead of an auto-reset).
                self._trigger_count_fanfare(now)
                self._pick_target()
                self._target_celebrate_until = now + self.COUNT_FANFARE_SECS
                self._target_next_at = now + self.COUNT_FANFARE_SECS
                return
        # Pick the next target NOW (not lazily on the next draw frame).
        # Otherwise: between _target_next_at elapsing and the next
        # _tick_target call, the OLD target is still matchable —
        # toddler mashes of a single digit have been observed jumping
        # 1→4 and 6→8 because consecutive KEYDOWNs landed in that gap
        # and re-matched the same target. _pick_target also clears the
        # celebration windows, so set those *after*.
        self._pick_target()
        self._target_celebrate_until = now + 0.40
        self._target_next_at = now + 0.40

    # Total length of the wrap-celebration window (seconds). Used by
    # both the fanfare scheduler and the post-9 input gate so they
    # stay in lockstep.
    COUNT_FANFARE_SECS = 4.0

    def _trigger_count_fanfare(self, now: float):
        """Queue the row-complete celebration: an initial radial salvo
        around the centre, ~24 scattered bursts spread across the full
        COUNT_FANFARE_SECS window, a layered chime+boom+pop opening
        chord, and a full ascending-then-descending piano run.
        Drained by _tick_target each frame so the fanfare unfolds in
        time rather than detonating all at once."""
        w, h = self.size
        self._count_fanfare_bursts.clear()
        self._count_fanfare_sounds.clear()
        cx, cy = w / 2.0, h / 2.0

        # 8 bursts in a ring around the centre at t=0 — instant "you
        # did it!" punctuation before the scattered confetti starts.
        ring_r = min(w, h) * 0.20
        for i in range(8):
            ang = (i / 8) * math.tau
            self._count_fanfare_bursts.append((
                now,
                cx + math.cos(ang) * ring_r,
                cy + math.sin(ang) * ring_r,
            ))
        # 24 scattered random bursts spread across the full window.
        for i in range(24):
            t = now + (i / 23) * self.COUNT_FANFARE_SECS
            bx = random.uniform(w * 0.08, w * 0.92)
            by = random.uniform(h * 0.15, h * 0.80)
            self._count_fanfare_bursts.append((t, bx, by))

        # Opening chord — chime + boom + pop layered now.
        self._play(self.snd_special.get("chime"))
        boom = self.snd_special.get("boom")
        pop = self.snd_special.get("pop")
        if boom is not None:
            self._count_fanfare_sounds.append((now + 0.02, boom))
        if pop is not None:
            self._count_fanfare_sounds.append((now + 0.18, pop))
        # Chime layers along the arc of the celebration.
        chime = self.snd_special.get("chime")
        if chime is not None:
            for t_off in (0.90, 1.80, 2.70, 3.50):
                self._count_fanfare_sounds.append((now + t_off, chime))
        # Diatonic scale up then back down for the rising-then-settling
        # cheer, spread across ~3.6 s.
        if self.snd_piano:
            n = len(self.snd_piano)
            seq = list(range(n)) + list(range(n - 2, -1, -1))
            for i, idx in enumerate(seq):
                t_off = 0.10 + i * (3.6 / max(1, len(seq) - 1))
                self._count_fanfare_sounds.append(
                    (now + t_off, self.snd_piano[idx]))

    def _trigger_nope(self):
        """Gentle 'no' feedback for wrong inputs in game modes — a short
        target shake plus a soft descending two-tone. Deliberately small
        so it nudges rather than scolds. Self-debounced so rapid wrong
        keys (e.g. a supervisor typing 'iddqd') don't stack."""
        now = time.monotonic()
        if now < self._target_shake_until:
            return
        self._target_shake_until = now + 0.30
        self._play(self.snd_special.get("nope"))

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        try:
            self.window.set_fullscreen(self.fullscreen)
        except (AttributeError, pygame.error):
            # Older pygame's Window API may not have set_fullscreen;
            # ignore — the user can quit + relaunch in the other mode.
            pass

    # ---------------------------------------------------------------- chaos

    def chaos_label(self) -> str:
        n = self.smashed
        if n < 10:
            return "Tiny chaos"
        if n < 50:
            return "Wiggly chaos"
        if n < 150:
            return "Bouncy chaos"
        if n < 400:
            return "Big chaos"
        return "LEGENDARY chaos"

    # ------------------------------------------------------------------ loop

    def run(self):
        clock = pygame.time.Clock()
        while self.running:
            dt = min(clock.tick(60) / 1000.0, 0.05)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    # Ignore — only the supervisor gate can actually quit.
                    continue
                self.handle_event(event)

            self._tick_corner_hold()
            self.spawn_ambient(dt)

            # In-place compaction: update + filter in one walk, then
            # truncate. Avoids the per-frame list-comprehension allocation
            # when nothing died (the common case).
            xmax = self.size[0] + 50
            ymax = self.size[1] + 50
            w = 0
            for p in self.ambient:
                p.update(dt)
                if p.alive and -50 < p.x < xmax and -50 < p.y < ymax:
                    self.ambient[w] = p
                    w += 1
            del self.ambient[w:]

            w = 0
            for p in self.particles:
                p.update(dt)
                if p.alive:
                    self.particles[w] = p
                    w += 1
            del self.particles[w:]

            w = 0
            for s in self.sprites:
                s.update(dt)
                if s.alive:
                    self.sprites[w] = s
                    w += 1
            del self.sprites[w:]

            self.draw()
            self.renderer.present()

        pygame.quit()

    def _tick_corner_hold(self):
        if self.corner_held_since is None:
            return
        held = time.monotonic() - self.corner_held_since
        if held >= self.CORNER_HOLD_SECONDS:
            self.open_supervisor_panel()

    # ----------------------------------------------------------------- draw

    def draw(self):
        r = self.renderer
        # Clear is technically unnecessary because the background texture
        # covers the whole frame, but it's free and a defence against
        # texture corruption on driver hiccups.
        r.draw_color = (0, 0, 0, 255)
        r.clear()
        bg = self.background_texture()
        bg.draw(dstrect=(0, 0, self.size[0], self.size[1]))

        w, h = self.size
        margin = 60
        cache = self.shape_textures
        for p in self.ambient:
            if -margin < p.x < w + margin and -margin < p.y < h + margin:
                p.draw(r, cache)
        for p in self.particles:
            if -margin < p.x < w + margin and -margin < p.y < h + margin:
                p.draw(r, cache)
        for s in self.sprites:
            if -200 < s.x < w + 200 and -200 < s.y < h + 200:
                s.draw(r)

        if self.game_state != "smash":
            self._tick_target()
            self._draw_target()
            if self.game_state == "counting":
                self._draw_count_row()

        # The keys-smashed counter doubles as the smash-mode banner (see
        # _draw_mode_banner); counting / whack-a-mole intentionally hide
        # it so the game-mode label has the top-center spot to itself.
        self._draw_mode_banner()
        self._draw_corner_indicator()

        if self.in_supervisor_panel:
            self._draw_supervisor_panel()

    def _tick_target(self):
        # Per-frame game-mode hook. Normal post-hit transitions pick
        # eagerly in _handle_game_keypress; this method only drains
        # the scheduled fanfare bursts and sounds during the
        # row-complete window. There's no auto-reset — once the
        # fanfare ends, target stays at "0" and the toddler resets the
        # row by pressing 0 (handled in _handle_game_keypress).
        if self.game_state != "counting" or self._count_pos <= 9:
            return
        now = time.monotonic()
        while (self._count_fanfare_bursts
               and now >= self._count_fanfare_bursts[0][0]):
            _, bx, by = self._count_fanfare_bursts.pop(0)
            self.burst(bx, by, None)
        while (self._count_fanfare_sounds
               and now >= self._count_fanfare_sounds[0][0]):
            _, snd = self._count_fanfare_sounds.pop(0)
            self._play(snd)

    def _ensure_target_text(self, text: str):
        """Render text-based targets (letters / digits) lazily into a
        cached Texture. The cache key includes the displayed text so a
        new target re-renders, but a still-active target doesn't."""
        if (self._target_text_cache_key == text
                and self._target_text_tex is not None):
            return
        self._target_text_cache_key = text
        surf = self.font_big.render(text, True, (255, 255, 255))
        self._target_text_tex = Texture.from_surface(self.renderer, surf)
        self._target_text_size = surf.get_size()

    def _draw_target(self):
        if self.target is None:
            return
        # Hide the letter briefly so the celebration burst owns the
        # screen; the next target lands a few hundred ms later.
        now = time.monotonic()
        if now < self._target_celebrate_until:
            return
        pulse = 1.0 + 0.05 * math.sin(now * 2.8)
        cx, cy = self.size[0] // 2, self.size[1] // 2
        # Wrong-input "no" shake: ~9.5 Hz horizontal wobble (sin(now·60),
        # i.e. 60 rad/s), amplitude decays linearly to zero over the
        # 0.30s shake window. Capped at 2% of screen width — a head-
        # shake "nope", not an alarm.
        if now < self._target_shake_until:
            remaining = self._target_shake_until - now
            amp = (remaining / 0.30) * (self.size[0] * 0.02)
            cx += int(amp * math.sin(now * 60.0))
        self._ensure_target_text(self.target[1])
        tex = self._target_text_tex
        w, h = self._target_text_size
        if tex is None:
            return
        dw, dh = int(w * pulse), int(h * pulse)
        # Drop shadow, then bright character on top.
        tex.alpha = 110
        tex.draw(dstrect=(
            cx - dw // 2 + max(3, dw // 60),
            cy - dh // 2 + max(3, dh // 60), dw, dh))
        tex.alpha = 255
        tex.draw(dstrect=(cx - dw // 2, cy - dh // 2, dw, dh))

    # Counting-row palette: dim gray for "not yet hit", bright lime
    # for "hit". Picked for clear contrast at toddler-eye distance.
    _COUNT_ROW_GRAY  = (110, 110, 120)
    _COUNT_ROW_GREEN = (90, 220, 110)

    def _draw_count_row(self):
        """Bottom-of-screen 1..9 strip. Each digit is gray until it
        has been hit, green afterwards. During the wrap fanfare
        (_count_pos == 10) all nine read green with a gentle pulse so
        the toddler sees the full row 'light up' before the reset."""
        if not self._count_row_textures:
            # 18 textures total. Built once, reused forever (until the
            # renderer is recreated, which doesn't happen in practice).
            for d in range(1, 10):
                txt = str(d)
                for hit, color in ((False, self._COUNT_ROW_GRAY),
                                   (True,  self._COUNT_ROW_GREEN)):
                    surf = self.font_count_row.render(txt, True, color)
                    tex = Texture.from_surface(self.renderer, surf)
                    self._count_row_textures[(d, hit)] = (
                        tex, surf.get_width(), surf.get_height())

        w, h = self.size
        all_hit = (self._count_pos > 9)
        # Centre the 9 cells across the middle 80% of the screen.
        band_left = w * 0.10
        band_w = w * 0.80
        cell_w = band_w / 9
        row_y = int(h * 0.86)
        now = time.monotonic()
        for d in range(1, 10):
            hit = all_hit or d < self._count_pos
            tex, tw, th = self._count_row_textures[(d, hit)]
            cx = int(band_left + cell_w * (d - 0.5))
            if all_hit:
                # Per-digit phase offset so the row "ripples" rather
                # than all pulsing in unison.
                pulse = 1.0 + 0.10 * math.sin(now * 6.0 + d * 0.7)
                dw, dh = int(tw * pulse), int(th * pulse)
            else:
                dw, dh = tw, th
            tex.draw(dstrect=(cx - dw // 2, row_y - dh // 2, dw, dh))

    # Per-mode colours used by the frame border (and the text pill
    # in the two non-smash modes). Calm, desaturated mid-tones — all
    # in the same value range so no mode shouts louder than another;
    # the hue alone tells them apart.
    MODE_COLORS = {
        "smash":        (110, 170, 130),    # soft sage green
        "counting":     (210, 165, 110),    # soft amber
        "whack-a-mole": (120, 150, 195),    # soft slate blue
    }

    def _draw_mode_banner(self):
        bg = self.MODE_COLORS[self.game_state]
        # 6-px frame border around the entire viewport in every mode
        # so a glance at the screen identifies what game is running.
        border = 6
        w, h = self.size
        self.renderer.draw_color = (*bg, 255)
        self.renderer.fill_rect((0, 0, w, border))
        self.renderer.fill_rect((0, h - border, w, border))
        self.renderer.fill_rect((0, 0, border, h))
        self.renderer.fill_rect((w - border, 0, border, h))

        # Build the displayed text (cached against itself to avoid a
        # font.render on every frame). Smash uses the keys-smashed
        # counter as its banner — there's no game progression to show,
        # so we surface the chaos-meter instead.
        if self.game_state == "smash":
            text = f"SMASH  ·  {self.smashed}   {self.chaos_label()}"
        elif self.game_state == "whack-a-mole":
            text = f"WHACK-A-MOLE  ·  hits: {self.game_hits}"
        else:  # counting
            shown = min(self._count_pos, 9)
            text = f"COUNTING  ·  {shown}/9"
        if text != self._banner_text:
            self._banner_text = text
            surf = self.font_hud.render(text, True, (255, 255, 255))
            self._banner_tex = Texture.from_surface(self.renderer, surf)
            self._banner_size = surf.get_size()

        bw, bh = self._banner_size
        pad_x, pad_y = int(bh * 0.6), int(bh * 0.35)
        box_w, box_h = bw + pad_x * 2, bh + pad_y * 2
        box_x = self.size[0] // 2 - box_w // 2
        box_y = int(self.size[1] * 0.025)

        # Solid coloured pill behind the text. Drawn with alpha 230 so
        # busy backgrounds still tint through subtly.
        self.renderer.draw_color = (*bg, 230)
        self.renderer.fill_rect((box_x, box_y, box_w, box_h))
        # Thin lighter outline so the pill reads as "a thing", not just
        # background discolouration.
        outline = tuple(min(255, c + 60) for c in bg)
        self.renderer.draw_color = (*outline, 255)
        self.renderer.draw_rect((box_x, box_y, box_w, box_h))
        # Centered text.
        if self._banner_tex is not None:
            self._banner_tex.draw(
                dstrect=(box_x + pad_x, box_y + pad_y, bw, bh))

    _CORNER_BUCKETS = 30   # ≈15 Hz visual update at 60 fps for a 2s hold

    def _draw_corner_indicator(self):
        if self.corner_held_since is None:
            return
        held = time.monotonic() - self.corner_held_since
        frac = min(1.0, held / self.CORNER_HOLD_SECONDS)
        bucket = int(frac * self._CORNER_BUCKETS)
        radius = int(self.CORNER_SIZE * 0.7)
        # Snap to discrete buckets so we only rebuild when the arc has
        # advanced enough to be visible. Cuts texture uploads ~4×.
        if bucket != self._corner_bucket or self._corner_tex is None:
            self._corner_bucket = bucket
            snapped = bucket / self._CORNER_BUCKETS
            surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
            pygame.draw.circle(surf, (255, 255, 255, 60),
                               (radius, radius), radius)
            if snapped > 0.02:
                end_angle = -math.pi / 2 + snapped * math.tau
                pygame.draw.arc(surf, (255, 255, 255, 230),
                                surf.get_rect(),
                                -math.pi / 2, end_angle, width=6)
            self._corner_tex = Texture.from_surface(self.renderer, surf)
        self._corner_tex.draw(dstrect=(10, 10, radius * 2, radius * 2))

    def _draw_supervisor_panel(self):
        # Translucent backdrop via the renderer's primitive fill.
        self.renderer.draw_color = (0, 0, 0, 180)
        self.renderer.fill_rect((0, 0, self.size[0], self.size[1]))

        if self.game_state == "smash":
            game_line = "Mode:    smash (free play)   [g]"
        elif self.game_state == "whack-a-mole":
            game_line = f"Mode:    whack-a-mole  ({self.game_hits} hits)   [g]"
        else:  # counting
            shown = min(self._count_pos, 9)
            game_line = f"Mode:    counting ({shown}/9)   [g]"
        lines = (
            f"Supervisor panel   ·   v{__version__}",
            "",
            f"Theme:   {THEME_ORDER[self.theme_idx]}   [t]",
            f"Sound:   {'muted' if self.muted else 'on'}   [m]",
            f"Window:  {'fullscreen' if self.fullscreen else 'windowed'}   [f]",
            game_line,
            f"Smashed: {self.smashed}   [r] reset",
            "",
            "[esc] resume      [q] quit",
        )
        # Cache miss only when the displayed content actually changed.
        if lines != self._panel_cache_key:
            self._panel_cache_key = lines
            entries = []
            for i, line in enumerate(lines):
                font = self.font_panel if i == 0 else self.font_hud
                row_h = font.get_height()
                if not line:
                    entries.append((None, 0, 0, row_h))
                    continue
                color = (255, 255, 255) if i == 0 else (230, 230, 240)
                surf = font.render(line, True, color)
                tex = Texture.from_surface(self.renderer, surf)
                w, h = surf.get_size()
                entries.append((tex, w, h, row_h))
            self._panel_entries = entries

        y = int(self.size[1] * 0.18)
        x_center = self.size[0] // 2
        for tex, w, h, row_h in self._panel_entries:
            if tex is not None:
                tex.draw(dstrect=(x_center - w // 2, y - h // 2, w, h))
            y += int(row_h * 1.3)


def main():
    import time
    import traceback
    # `--version` is handled at import time (above the pygame imports)
    # so it works without a working SDL stack.
    try:
        Smashbox().run()
        return 0
    except KeyboardInterrupt:
        return 0
    except BaseException:
        # On a kiosk, X is hiding tty1 until xinit tears it down — anything
        # we print here only shows up on screen *after* xinit exits, via
        # the .bash_profile log-tail-on-exit path. Still write the full
        # traceback so the operator has something to work with.
        traceback.print_exc()
        # Keep the process alive briefly so a tail -f on the log catches it
        # before xinit decides to respawn (the .bash_profile sleep covers
        # this too, but belt and suspenders).
        try:
            time.sleep(5)
        except Exception:
            pass
        return 1
    finally:
        try:
            pygame.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
