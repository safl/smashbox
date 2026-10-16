# SPDX-FileCopyrightText: Simon A. F. Lund <os@safl.dk>
#
# SPDX-License-Identifier: BSD-3-Clause

"""Host-side helpers in scripts/rpi_image_customize.py.

The script imports cijoe at module level; only the helpers are tested
here, so a stub stands in for it.
"""

import importlib.util
import struct
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rpi_image_customize.py"


@pytest.fixture(scope="module")
def script():
    misc = types.ModuleType("cijoe.core.misc")
    misc.download = None
    stubs = {
        "cijoe": types.ModuleType("cijoe"),
        "cijoe.core": types.ModuleType("cijoe.core"),
        "cijoe.core.misc": misc,
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("rpi_image_customize", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def _elf(path, program_header_types):
    """Write a minimal 64-bit little-endian ELF with the given program
    header types and return its path."""
    phoff, phentsize = 64, 56
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", header, 0x20, phoff)
    struct.pack_into("<HH", header, 0x36, phentsize, len(program_header_types))
    body = b"".join(struct.pack("<I", t) + bytes(phentsize - 4)
                    for t in program_header_types)
    path.write_bytes(bytes(header) + body)
    path.chmod(0o755)
    return path


PT_LOAD, PT_INTERP = 1, 3


def test_static_elf_detection(script, tmp_path):
    assert script._is_static_elf(_elf(tmp_path / "static", [PT_LOAD]))
    assert not script._is_static_elf(_elf(tmp_path / "dynamic", [PT_LOAD, PT_INTERP]))
    (tmp_path / "text").write_text("#!/bin/sh\n")
    assert not script._is_static_elf(tmp_path / "text")


def _which(mapping):
    return lambda name: mapping.get(name)


def test_prefers_qemu_aarch64_static(script, monkeypatch, tmp_path):
    monkeypatch.setattr(script.shutil, "which", _which({
        "qemu-aarch64-static": "/usr/bin/qemu-aarch64-static",
        "qemu-aarch64": str(_elf(tmp_path / "qemu-aarch64", [PT_LOAD])),
    }))
    assert script._find_qemu_aarch64() == "/usr/bin/qemu-aarch64-static"


def test_accepts_static_qemu_aarch64(script, monkeypatch, tmp_path):
    static = str(_elf(tmp_path / "qemu-aarch64", [PT_LOAD]))
    monkeypatch.setattr(script.shutil, "which", _which({"qemu-aarch64": static}))
    assert script._find_qemu_aarch64() == static


def test_rejects_dynamic_qemu_aarch64(script, monkeypatch, tmp_path):
    dynamic = str(_elf(tmp_path / "qemu-aarch64", [PT_LOAD, PT_INTERP]))
    monkeypatch.setattr(script.shutil, "which", _which({"qemu-aarch64": dynamic}))
    assert script._find_qemu_aarch64() is None
