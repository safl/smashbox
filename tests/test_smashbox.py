# SPDX-FileCopyrightText: Simon A. F. Lund <os@safl.dk>
#
# SPDX-License-Identifier: BSD-3-Clause

"""Consistency checks on smashbox's data tables and bundled assets.

The Smashbox class needs a GPU renderer, so these tests stay at module
level, where a missing sample, an orphaned asset or a clip index that
points at the wrong builder would otherwise only show up on the kiosk.
"""

import os
from pathlib import Path

import pygame
import pytest

import smashbox

SOUNDS = Path(__file__).resolve().parent.parent / "assets" / "sounds"
SAMPLE_EXTS = (".ogg", ".wav")


def _table_entries():
    for key_name, items in smashbox.Smashbox._KEY_CYCLES_TABLE.items():
        for category, base, clip in items:
            yield key_name, category, base, clip


@pytest.fixture(scope="module")
def mixer():
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pygame.mixer.init(frequency=smashbox.SAMPLE_RATE, size=-16, channels=2)
    yield
    pygame.mixer.quit()


def test_clip_indices_cover_builders():
    indices = [v for k, v in vars(smashbox).items() if k.startswith("CLIP_")]
    assert sorted(indices) == list(range(len(smashbox.CLIPART_BUILDERS)))


@pytest.mark.parametrize("builder", smashbox.CLIPART_BUILDERS,
                         ids=lambda b: b.__name__)
def test_clip_builders_draw_something(builder):
    surf = builder(64)
    assert surf.get_size() == (64, 64)
    assert surf.get_bounding_rect().width > 0


def test_key_cycle_keys_exist():
    for key_name, _, _, _ in _table_entries():
        assert hasattr(pygame, key_name), key_name


def test_every_cycled_sample_is_shipped():
    for _, category, base, _ in _table_entries():
        found = [SOUNDS / category / (base + ext) for ext in SAMPLE_EXTS
                 if (SOUNDS / category / (base + ext)).is_file()]
        assert found, f"{category}/{base} has no sample under {SOUNDS}"


def test_every_shipped_sample_is_cycled():
    used = {(category, base) for _, category, base, _ in _table_entries()}
    for category in ("animals", "instruments"):
        for path in (SOUNDS / category).iterdir():
            assert (category, path.stem) in used, f"{path} is never played"


def test_every_shipped_sample_is_attributed():
    attributions = (SOUNDS / "ATTRIBUTIONS.md").read_text(encoding="utf-8")
    for path in SOUNDS.rglob("*"):
        if path.suffix in SAMPLE_EXTS:
            rel = path.relative_to(SOUNDS).as_posix()
            assert rel in attributions, rel


def test_every_cycled_sample_has_synth_fallback(mixer):
    fallbacks = smashbox._make_keyed_fallbacks()
    for _, _, base, _ in _table_entries():
        assert isinstance(fallbacks.get(base), pygame.mixer.Sound), base
