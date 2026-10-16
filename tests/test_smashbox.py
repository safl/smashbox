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


# Device names as SDL's ALSA backend reports them on a Pi 4. The jack
# entry is modelled on the card's ALSA description and has not been
# read off real hardware.
HDMI = "vc4-hdmi-0, MAI PCM i2s-hifi-0"
JACK = "bcm2835 Headphones, bcm2835 Headphones"
USB = "USB Audio Device, USB Audio"


@pytest.mark.parametrize("names, expected", [
    ([HDMI, JACK], JACK),
    ([USB, JACK], JACK),
    ([HDMI, USB], None),
    ([HDMI], None),
])
def test_pick_audio_device(monkeypatch, names, expected):
    monkeypatch.delenv("SMASHBOX_AUDIO_DEVICE", raising=False)
    monkeypatch.setattr(smashbox, "get_audio_device_names", lambda _capture: names)
    assert smashbox.Smashbox._pick_audio_device() == expected


def test_pick_audio_device_honours_override(monkeypatch):
    monkeypatch.setenv("SMASHBOX_AUDIO_DEVICE", USB)
    monkeypatch.setattr(smashbox, "get_audio_device_names", lambda _capture: [JACK])
    assert smashbox.Smashbox._pick_audio_device() == USB


def test_usb_audio_driver_is_blocked():
    conf = (Path(__file__).resolve().parent.parent
            / "rootfs/etc/modprobe.d/smashbox-no-usb-audio.conf")
    lines = conf.read_text(encoding="utf-8").splitlines()
    assert "blacklist snd_usb_audio" in lines
    assert "install snd_usb_audio /bin/false" in lines


def test_boot_config_drops_hdmi_audio():
    fragment = Path(__file__).resolve().parent.parent / "rootfs/boot/firmware/smashbox.txt"
    lines = fragment.read_text(encoding="utf-8").splitlines()
    kms = [i for i, ln in enumerate(lines) if ln.startswith("dtoverlay=vc4-kms-v3d")]
    assert [lines[i] for i in kms] == ["dtoverlay=vc4-kms-v3d,noaudio"]
    # Anything after the overlay is in its parameter scope; its audio
    # parameter would re-enable HDMI audio.
    assert not [ln for ln in lines[kms[0]:] if ln.startswith("dtparam=")]
