# SPDX-FileCopyrightText: Simon A. F. Lund <os@safl.dk>
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Customise Raspberry Pi OS Lite arm64 into a smashbox kiosk image
====================================================================

Mirrors what ``image/build.sh`` used to do, just driven from cijoe:

1. Download the upstream Pi OS Lite arm64 ``.img.xz`` (cached under
   ``~/system_imaging/cloud/``).
2. Decompress + grow the raw image to ``disk.target_size_gib``.
3. ``losetup --partscan`` the raw image; ``parted resizepart`` p2 to
   fill the new space; ``e2fsck`` + ``resize2fs`` on the rootfs.
4. Mount root + ``boot/firmware``; bind-mount ``/proc /sys /dev``.
5. ``cp -a rootfs/.`` over the mounted rootfs (autologin getty,
   systemd units, ``smashbox-wifi-unblock``, ``smashbox-cpu-perf``,
   ``/home/smashbox/.bash_profile``, ``/opt/smashbox/start.sh``).
6. Drop ``smashbox.py`` from the repo root into
   ``/opt/smashbox/smashbox.py``.
7. Boot-partition housekeeping (idempotent):
     * append the HDMI tweaks fragment to ``config.txt`` with
       begin/end markers.
     * strip any ``systemd.run=…`` from ``cmdline.txt`` (the rpi-os
       first-boot wizard tail) so tty1 stays under our autologin
       getty.
     * append quiet/plymouth flags to ``cmdline.txt``.
     * ``touch ssh`` to enable sshd at first boot.
     * remove ``userconf.txt`` and ``firstrun.sh`` (the wizard).
8. Generate the ``pi`` user password hash on the host via
   ``openssl passwd -6`` and hand it to the chroot via ``/tmp/pi-creds``.
9. Drop a static ``qemu-aarch64`` into the chroot, then ``chroot`` in
   and run the install script: apt packages, ``smashbox`` kiosk
   user, ``pi`` rescue user, service enables, image slimming.
10. Tear down mounts, detach the loop device, gzip the raw image,
    write a sha256 manifest.

Build-host requirements:
    amd64 Linux with ``binfmt_misc`` + a static ``qemu-aarch64``
    (``qemu-user-static``, or ``qemu-user-binfmt`` on Ubuntu 26.04+),
    passwordless sudo, ``losetup parted e2fsprogs xz-utils gzip
    openssl curl``.

Retargetable: False
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import logging as log
import re
import shutil
import subprocess
import textwrap
from argparse import ArgumentParser
from pathlib import Path

from cijoe.core.misc import download

IMAGE_NAME = "smashbox-rpi-arm64"

# Apt packages installed inside the arm64 chroot. Kept here as the
# single source of truth; mirrors the list ``image/build.sh`` used to
# install.
APT_PACKAGES = (
    "xserver-xorg-core",
    "xserver-xorg-input-libinput",
    "xinit",
    "x11-xserver-utils",
    "xserver-xorg-video-fbdev",
    "xserver-xorg-video-modesetting",
    "unclutter",
    "sudo",
    "python3-pygame",
    "python3-numpy",
    "fonts-dejavu-core",
    "ca-certificates",
    "less",
    "network-manager",
    "rfkill",
    "iw",
    "wireless-regdb",
    "plymouth",
    "plymouth-themes",
)

# Quiet-boot cmdline append. The ``smashbox-quiet`` sentinel is what
# we grep for to keep ``_edit_cmdline`` idempotent.
QUIET_CMDLINE = (
    "quiet loglevel=0 vt.global_cursor_default=0 logo.nologo "
    "systemd.show_status=0 splash plymouth.ignore-serial-consoles "
    "smashbox-quiet"
)


def add_args(parser: ArgumentParser):
    del parser  # no flags; signature kept for cijoe consistency


def main(args, cijoe):
    del args

    images = cijoe.getconf("system-imaging.images", {})
    image = images.get(IMAGE_NAME)
    if not image:
        log.error(f"Image {IMAGE_NAME!r} not found in cijoe config")
        return errno.EINVAL

    sib = cijoe.getconf("smashbox", {})
    pi_password = sib.get("pi_password", "smashbox")
    wifi_country = sib.get("wifi_country", "DK")

    repo_root = Path.cwd()
    rootfs_dir = repo_root / "rootfs"
    app_path = repo_root / "smashbox.py"
    # Sound library — optional. App falls back to synthesised animals
    # when /opt/smashbox/sounds/animals/<name>.{ogg,wav} is missing,
    # so we just skip the copy if assets/sounds/ doesn't exist.
    assets_sounds = repo_root / "assets" / "sounds"
    if not rootfs_dir.exists():
        log.error(f"Missing rootfs overlay tree: {rootfs_dir}")
        return errno.ENOENT
    if not app_path.exists():
        log.error(f"Missing smashbox.py at {app_path}")
        return errno.ENOENT

    app_version = _extract_app_version(app_path)
    log.info(f"Building smashbox v{app_version}")

    source_url = image["source"]["url"]
    source_path = Path(image["source"]["path"])
    raw_path = Path(image["disk"]["raw_path"])
    target_size_gib = int(image["disk"]["target_size_gib"])
    gz_path = Path(image["publish"]["gz_path"])
    gzip_level = int(image["publish"].get("gzip_level", 9))

    # 1. Download (cached).
    if not source_path.exists():
        source_path.parent.mkdir(parents=True, exist_ok=True)
        log.info(f"Downloading {source_url} -> {source_path}")
        err, _ = download(source_url, source_path)
        if err:
            log.error(f"Download failed: {source_url}")
            return err

    # 2. Decompress + grow.
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if raw_path.exists():
        raw_path.unlink()
    log.info(f"Decompressing {source_path} -> {raw_path}")
    if (
        _run(["xz", "--decompress", "--keep", "--stdout", str(source_path)],
             stdout_path=raw_path)
        != 0
    ):
        return errno.EIO
    log.info(f"Growing {raw_path} to {target_size_gib} GiB")
    if _run(["truncate", "--size", f"{target_size_gib}G", str(raw_path)]) != 0:
        return errno.EIO

    # 3-9. Loop-mount, customise, tear down.
    rc = _customize(raw_path, rootfs_dir, app_path, assets_sounds,
                    pi_password, wifi_country, app_version)
    if rc != 0:
        return rc

    # 10. Compress + manifest.
    log.info(f"Compressing {raw_path} -> {gz_path} (gzip -{gzip_level})")
    if gz_path.exists():
        gz_path.unlink()
    if _run_shell(f"gzip -{gzip_level} -c {raw_path} > {gz_path}") != 0:
        return errno.EIO

    sha256_path = gz_path.with_suffix(gz_path.suffix + ".sha256")
    digest = _sha256(gz_path)
    sha256_path.write_text(f"{digest}  {gz_path.name}\n")
    log.info(f"sha256 -> {sha256_path}")

    # Final summary, framed so it survives cijoe's verbose monitor
    # output. The Makefile prints a friendlier version after cijoe
    # returns, but operators who run cijoe directly want the artefact
    # paths visible here too.
    gz_size_mb = gz_path.stat().st_size / (1024 * 1024)
    log.info("")
    log.info("=" * 66)
    log.info(f" Image ready to flash  ·  smashbox v{app_version}")
    log.info("=" * 66)
    log.info(f"  raw:        {raw_path}")
    log.info(f"  gz:         {gz_path}  ({gz_size_mb:.0f} MiB)")
    log.info(f"  sha256:     {sha256_path}")
    log.info("")
    log.info("Flash hints:")
    log.info(f"  rpi-imager — point at {gz_path}")
    log.info(f"  sudo dd if={raw_path} of=/dev/sdX bs=4M conv=fsync status=progress")
    log.info("=" * 66)
    return 0


# ---------------------------------------------------------------------
def _customize(
    raw_path: Path,
    rootfs_dir: Path,
    app_path: Path,
    assets_sounds: Path,
    pi_password: str,
    wifi_country: str,
    app_version: str,
) -> int:
    """Loop-mount, resize rootfs, run chroot customisation, tear down."""
    rc, loop = _losetup_attach(raw_path)
    if rc != 0:
        return rc

    try:
        # Pi OS Lite ships a ~512 MiB FAT boot partition (p1) and a
        # small ext4 rootfs (p2). Grow p2 to fill the truncate'd space,
        # then resize the filesystem.
        log.info(f"Growing rootfs partition on {loop}p2")
        if (
            _run_sudo(["parted", "--script", loop, "resizepart", "2", "100%"])
            != 0
        ):
            return errno.EIO
        # e2fsck rc=1 means "fs errors found and corrected", which is
        # fine after a partition resize.
        if _run_sudo(["e2fsck", "-fy", f"{loop}p2"]) not in (0, 1):
            return errno.EIO
        if _run_sudo(["resize2fs", f"{loop}p2"]) != 0:
            return errno.EIO

        mnt = Path("/tmp/smashbox-rpi-mnt")
        boot_mnt = mnt / "boot" / "firmware"
        mnt.mkdir(exist_ok=True)
        if _run_sudo(["mount", f"{loop}p2", str(mnt)]) != 0:
            return errno.EIO
        if _run_sudo(["mkdir", "-p", str(boot_mnt)]) != 0:
            return errno.EIO
        if _run_sudo(["mount", f"{loop}p1", str(boot_mnt)]) != 0:
            return errno.EIO

        try:
            rc = _stage_and_chroot(
                mnt, rootfs_dir, app_path, assets_sounds,
                pi_password, wifi_country, app_version,
            )
        finally:
            _run_sudo(["umount", str(boot_mnt)])
            _run_sudo(["umount", str(mnt)])
            with contextlib.suppress(OSError):
                mnt.rmdir()
        return rc
    finally:
        _run_sudo(["losetup", "--detach", loop])


def _stage_and_chroot(
    mnt: Path,
    rootfs_dir: Path,
    app_path: Path,
    assets_sounds: Path,
    pi_password: str,
    wifi_country: str,
    app_version: str,
) -> int:
    """Drop overlay + app into the mounted rootfs and run chroot install."""

    # Bulk-copy the overlay tree. We preserve mode + timestamps but NOT
    # ownership — the tree spans both ext4 root and the mounted FAT32
    # /boot/firmware partition, and cp -a fails with rc=1 when trying
    # to preserve owner/group on FAT (it doesn't store Unix ownership).
    # Files land root-owned via sudo, which is what we want anyway;
    # /home/smashbox is chowned to its user inside the chroot.
    log.info(f"Copying overlay {rootfs_dir}/. into chroot")
    if _run_sudo(["cp", "-r", "--preserve=mode,timestamps",
                  f"{rootfs_dir}/.", str(mnt)]) != 0:
        return errno.EIO

    # The pygame app lives at the repo root, not in rootfs/.
    if _run_sudo(["install", "-d", "-m", "0755",
                  str(mnt / "opt/smashbox")]) != 0:
        return errno.EIO
    if _run_sudo(["install", "-m", "0755", str(app_path),
                  str(mnt / "opt/smashbox/smashbox.py")]) != 0:
        return errno.EIO

    # Sound library (assets/sounds/{animals,instruments}/). Optional —
    # the app falls back to synthesised animals when this directory is
    # missing, so skip the copy gracefully if it doesn't exist locally
    # (e.g. a thin checkout that doesn't include the assets/ tree).
    if assets_sounds.is_dir():
        log.info(f"Copying {assets_sounds}/. into /opt/smashbox/sounds/")
        if _run_sudo(["install", "-d", "-m", "0755",
                      str(mnt / "opt/smashbox/sounds")]) != 0:
            return errno.EIO
        # Same flags as the rootfs cp — preserve mode + timestamps but
        # not ownership, for consistency. (Target is ext4 here so
        # ownership would technically work, but we don't want the
        # build-host user's UID in the image anyway.)
        if _run_sudo(["cp", "-r", "--preserve=mode,timestamps",
                      f"{assets_sounds}/.",
                      str(mnt / "opt/smashbox/sounds/")]) != 0:
            return errno.EIO
    else:
        log.info(f"No {assets_sounds}; shipping without sample overrides")

    # /boot/firmware/config.txt: append HDMI tweaks with begin/end
    # markers (idempotent).
    boot_fw = mnt / "boot" / "firmware"
    rc = _append_with_markers(
        target=boot_fw / "config.txt",
        fragment_src=rootfs_dir / "boot" / "firmware" / "smashbox.txt",
        begin="# smashbox begin",
        end="# smashbox end",
    )
    if rc != 0:
        return rc

    # Version markers. SMASHBOX-VERSION.txt on the boot partition is
    # readable from any host (mount the SD on a laptop and you can
    # tell which version is on it without booting). /opt/smashbox/VERSION
    # is the same value reachable from inside the running system.
    version_blob = f"smashbox {app_version}\n"
    rc = _write_sudo(boot_fw / "SMASHBOX-VERSION.txt", version_blob)
    if rc != 0:
        return rc
    rc = _write_sudo(mnt / "opt/smashbox/VERSION", version_blob)
    if rc != 0:
        return rc

    # /boot/firmware/cmdline.txt: strip the first-boot wizard's
    # systemd.run=… (which would steal tty1 from our autologin getty)
    # and append quiet/plymouth flags.
    rc = _edit_cmdline(boot_fw / "cmdline.txt")
    if rc != 0:
        return rc

    # ssh marker is in the overlay tree (lands via cp -a); also ensure
    # it's there in case someone deleted it.
    _run_sudo(["touch", str(boot_fw / "ssh")])
    # Kill the rpi-os first-boot wizard outright — its userconfig.service
    # would grab tty1 from our autologin getty.
    _run_sudo(["rm", "-f", str(boot_fw / "userconf.txt")])
    _run_sudo(["rm", "-f", str(boot_fw / "firstrun.sh")])

    # /etc/default/smashbox-wifi: country baked in for the
    # wifi-unblock service to read at every boot.
    rc = _write_sudo(mnt / "etc/default/smashbox-wifi",
                     f"COUNTRY={wifi_country}\n", mode="0644")
    if rc != 0:
        return rc

    # Generate the pi password hash on the host and hand it to the
    # chroot via /tmp/pi-creds. We deliberately do NOT use
    # /boot/firmware/userconf.txt (which triggers userconfig.service,
    # the tty1-stealer).
    log.info(f"preparing pi user (password: {pi_password})")
    proc = subprocess.run(
        ["openssl", "passwd", "-6", pi_password],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        log.error(f"openssl passwd failed: {proc.stderr}")
        return errno.EIO
    pi_hash = proc.stdout.strip()
    rc = _write_sudo(mnt / "tmp/pi-creds",
                     f"pi:{pi_hash}\n", mode="0600")
    if rc != 0:
        return rc

    # A static qemu-aarch64 + binfmt lets execve arm64 binaries on amd64
    # transparently inside the chroot.
    qemu_static = _find_qemu_aarch64()
    if qemu_static is None:
        log.error(
            "static qemu-aarch64 not found. sudo apt install "
            "qemu-user-static binfmt-support (Ubuntu 26.04+: qemu-user-binfmt)."
        )
        return errno.ENOENT
    qemu_in_image = mnt / "usr/bin" / Path(qemu_static).name
    if _run_sudo(["cp", qemu_static, str(qemu_in_image)]) != 0:
        return errno.EIO

    # Save the image's resolv.conf, swap in the host's so apt can fetch.
    resolv = mnt / "etc/resolv.conf"
    resolv_bak = mnt / "etc/resolv.conf.smashbox-bak"
    saved_resolv = False
    if resolv.exists() or resolv.is_symlink():
        if _run_sudo(["mv", str(resolv), str(resolv_bak)]) != 0:
            return errno.EIO
        saved_resolv = True
    if _run_sudo(["cp", "/etc/resolv.conf", str(resolv)]) != 0:
        return errno.EIO

    # Bind-mount /proc /sys /dev /dev/pts.
    binds = ("/proc", "/sys", "/dev", "/dev/pts")
    for src in binds:
        dst = mnt / src.lstrip("/")
        if _run_sudo(["mkdir", "-p", str(dst)]) != 0:
            return errno.EIO
        if _run_sudo(["mount", "--bind", src, str(dst)]) != 0:
            return errno.EIO

    try:
        install_script = _render_install_script()
        script_path = mnt / "tmp/smashbox-setup.sh"
        rc = _write_sudo(script_path, install_script, mode="0755")
        if rc != 0:
            return rc

        log.info("Running chroot smashbox install")
        rc = _run_sudo(
            ["chroot", str(mnt), "/bin/bash", "/tmp/smashbox-setup.sh"]
        )
        _run_sudo(["rm", "-f", str(script_path)])
        if rc != 0:
            return errno.EIO
    finally:
        for src in reversed(binds):
            dst = mnt / src.lstrip("/")
            _run_sudo(["umount", str(dst)])

    # Restore resolv.conf, remove qemu-static.
    _run_sudo(["rm", "-f", str(resolv)])
    if saved_resolv:
        _run_sudo(["mv", str(resolv_bak), str(resolv)])
    _run_sudo(["rm", "-f", str(qemu_in_image)])

    # Zero unused space so the final gzip compresses well.
    log.info("Zeroing free space (helps gzip)")
    _run_shell(
        f"sudo dd if=/dev/zero of={mnt}/zero.fill bs=4M status=none || true"
    )
    _run_sudo(["rm", "-f", str(mnt / "zero.fill")])
    _run_shell("sync")
    return 0


def _render_install_script() -> str:
    """Bash that runs *inside* the arm64 chroot."""
    apt_list = " ".join(APT_PACKAGES)
    return textwrap.dedent(f"""
        #!/bin/bash
        # smashbox kiosk customisation, runs inside the arm64 chroot
        # transparently via a static qemu-aarch64 + binfmt.
        set -euo pipefail
        export DEBIAN_FRONTEND=noninteractive
        export LC_ALL=C

        echo "[chroot] apt-get update"
        apt-get update

        echo "[chroot] installing X11 + pygame + network stack + plymouth"
        apt-get install -y --no-install-recommends {apt_list}

        echo "[chroot] plymouth: spinner theme"
        plymouth-set-default-theme spinner 2>/dev/null || true

        echo "[chroot] creating smashbox kiosk user"
        if ! id smashbox >/dev/null 2>&1; then
            useradd --create-home --shell /bin/bash smashbox
        fi
        for g in audio video render input tty plugdev dialout; do
            if getent group "$g" >/dev/null; then
                usermod -aG "$g" smashbox
            fi
        done
        id smashbox   # fail-loud if useradd silently bailed

        # The overlay tree's /home/smashbox/.bash_profile was cp -a'd
        # in as root. Fix ownership now that the user exists.
        if [ -d /home/smashbox ]; then
            chown -R smashbox:smashbox /home/smashbox
        fi
        test -f /home/smashbox/.bash_profile

        echo "[chroot] creating pi user (ssh + sudo for debug)"
        if ! id pi >/dev/null 2>&1; then
            useradd --create-home --shell /bin/bash pi
        fi
        chpasswd -e < /tmp/pi-creds
        for g in sudo adm audio video render input plugdev dialout users; do
            if getent group "$g" >/dev/null; then
                usermod -aG "$g" pi
            fi
        done
        cat > /etc/sudoers.d/010-pi-nopasswd <<'CFG'
        pi ALL=(ALL) NOPASSWD: ALL
        CFG
        chmod 0440 /etc/sudoers.d/010-pi-nopasswd
        rm -f /tmp/pi-creds
        id pi

        # Bookworm defaults to allowed_users=console which is fine on our
        # tty1 case, but be explicit.
        mkdir -p /etc/X11
        cat > /etc/X11/Xwrapper.config <<'CFG'
        allowed_users=anybody
        needs_root_rights=yes
        CFG

        echo "[chroot] mask userconfig.service (we created users ourselves)"
        systemctl mask userconfig.service 2>/dev/null || true

        echo "[chroot] enabling getty@tty1, getty@tty2 rescue, sshd, "\
"NetworkManager, wifi-unblock, cpu-perf"
        systemctl enable getty@tty1.service
        systemctl enable getty@tty2.service
        systemctl enable ssh.service
        systemctl enable NetworkManager.service 2>/dev/null || true
        systemctl enable smashbox-wifi-unblock.service
        systemctl enable smashbox-cpu-perf.service

        # Sanity check: autologin drop-in must exist with our user.
        test -f /etc/systemd/system/getty@tty1.service.d/autologin.conf
        grep -q -- '--autologin smashbox' \\
             /etc/systemd/system/getty@tty1.service.d/autologin.conf

        systemctl daemon-reload 2>/dev/null || true

        echo "[chroot] slim: empty MOTD + remove kiosk-irrelevant packages"
        : > /etc/issue
        : > /etc/issue.net
        : > /etc/motd
        rm -f /etc/update-motd.d/*

        apt-get purge -y --auto-remove --allow-remove-essential \\
            triggerhappy dphys-swapfile 2>/dev/null || true
        apt-get purge -y man-db manpages info 2>/dev/null || true

        echo "[chroot] trim caches + docs + logs"
        apt-get clean
        rm -rf /var/lib/apt/lists/* \\
               /var/cache/apt/archives/*.deb \\
               /usr/share/man/* /usr/share/doc/* /usr/share/info/*
        find /var/log -type f -exec truncate -s 0 {{}} +
    """).lstrip()


# ---------------------------------------------------------------------
# Boot-partition helpers
# ---------------------------------------------------------------------
def _append_with_markers(
    target: Path, fragment_src: Path, begin: str, end: str
) -> int:
    """Append fragment_src to target wrapped in begin/end marker lines.
    Idempotent: if `begin` is already a line in target, no-op."""
    if not fragment_src.exists():
        log.error(f"missing fragment: {fragment_src}")
        return errno.ENOENT
    # grep -q via sudo (target is root-owned).
    rc = subprocess.call(
        ["sudo", "grep", "-q", "--", begin, str(target)],
    )
    if rc == 0:
        log.info(f"{target}: already has '{begin}' marker, skip append")
        return 0
    fragment = fragment_src.read_text()
    appendage = f"\n{begin}\n{fragment.rstrip()}\n{end}\n"
    return _append_sudo(target, appendage)


def _edit_cmdline(cmdline_path: Path) -> int:
    """Smashbox edits to /boot/firmware/cmdline.txt.

    1. Strip any ``systemd.run=…`` suffix — the first-boot wizard adds
       one that would otherwise grab tty1 before our autologin getty.
    2. Append quiet/plymouth flags (idempotent via the
       ``smashbox-quiet`` sentinel)."""
    if _run_sudo(["sed", "-i", "s| systemd.run.*||", str(cmdline_path)]) != 0:
        return errno.EIO
    rc = subprocess.call(
        ["sudo", "grep", "-q", "smashbox-quiet", str(cmdline_path)],
    )
    if rc == 0:
        return 0
    return _run_sudo(
        ["sed", "-i", "-E",
         f"s|[[:space:]]*$| {QUIET_CMDLINE}|",
         str(cmdline_path)]
    )


# ---------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------
def _run(cmd: list[str], stdout_path: Path | None = None) -> int:
    log.debug(" ".join(cmd))
    if stdout_path is not None:
        with stdout_path.open("wb") as fh:
            return subprocess.call(cmd, stdout=fh)
    return subprocess.call(cmd)


def _run_shell(cmdline: str) -> int:
    """Run via the shell so redirections (``>``, ``|``) work — used for
    the gzip-to-stdout-redirected-to-file pattern."""
    log.debug(f"sh -c '{cmdline}'")
    return subprocess.call(["sh", "-c", cmdline])


def _find_qemu_aarch64() -> str | None:
    """Path of a static qemu-aarch64 on the build host, or None.

    Debian and Ubuntu up to 24.04 ship it as qemu-aarch64-static. From
    Ubuntu 26.04 qemu-user is static itself and the binary is plain
    qemu-aarch64, so that name is accepted only when it is a static
    build: a dynamic one cannot run inside the arm64 chroot."""
    path = shutil.which("qemu-aarch64-static")
    if path:
        return path
    path = shutil.which("qemu-aarch64")
    if path and _is_static_elf(Path(path)):
        return path
    return None


def _is_static_elf(path: Path) -> bool:
    """True when the ELF at `path` has no PT_INTERP program header,
    i.e. it needs no dynamic loader."""
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        return False  # only 64-bit little-endian hosts are supported
    phoff = int.from_bytes(data[0x20:0x28], "little")
    phentsize = int.from_bytes(data[0x36:0x38], "little")
    phnum = int.from_bytes(data[0x38:0x3A], "little")
    pt_interp = 3
    for i in range(phnum):
        off = phoff + i * phentsize
        if int.from_bytes(data[off:off + 4], "little") == pt_interp:
            return False
    return True


def _run_sudo(cmd: list[str]) -> int:
    log.debug("sudo " + " ".join(str(c) for c in cmd))
    return subprocess.call(["sudo", *cmd])


_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


def _extract_app_version(app_path: Path) -> str:
    """Return the ``__version__`` string from smashbox.py without
    importing it (the script runs on the build host, which has no
    pygame). Falls back to ``"unknown"`` so a bad bump doesn't break
    the build — the value is informational only."""
    try:
        m = _VERSION_RE.search(app_path.read_text(encoding="utf-8"))
    except OSError:
        return "unknown"
    return m.group(1) if m else "unknown"


def _write_sudo(path: Path, content: str, mode: str = "0644") -> int:
    """Write `content` to a root-owned `path` via ``sudo tee``, then chmod."""
    log.debug(f"write {path} ({len(content)} bytes)")
    with subprocess.Popen(
        ["sudo", "tee", str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    ) as proc:
        proc.communicate(content.encode())
        if proc.returncode != 0:
            return errno.EIO
    return _run_sudo(["chmod", mode, str(path)])


def _append_sudo(path: Path, content: str) -> int:
    log.debug(f"append {len(content)} bytes to {path}")
    with subprocess.Popen(
        ["sudo", "tee", "-a", str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    ) as proc:
        proc.communicate(content.encode())
        return 0 if proc.returncode == 0 else errno.EIO


def _losetup_attach(raw_path: Path) -> tuple[int, str]:
    """Attach raw_path to a loop device with --partscan; return /dev/loopN."""
    proc = subprocess.run(
        ["sudo", "losetup", "--find", "--show", "--partscan", str(raw_path)],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        log.error(f"losetup attach failed: {proc.stderr.strip()}")
        return errno.EIO, ""
    loop = proc.stdout.strip()
    if not loop.startswith("/dev/"):
        log.error(f"losetup unexpected output: {proc.stdout!r}")
        return errno.EIO, ""
    log.info(f"losetup -> {loop}")
    return 0, loop


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
