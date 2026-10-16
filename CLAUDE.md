# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A fullscreen pygame "keyboard smash" toy for toddlers, plus a [cijoe](https://github.com/safl/cijoe) pipeline that bakes it into a flashable Raspberry Pi OS image that boots straight into the app. Two artifacts, one repo:

- `smashbox.py` — the pygame app (single file, ~1700 lines).
- `tasks/` + `configs/` + `scripts/` + `rootfs/` — cijoe workflow that turns upstream RPi OS Lite arm64 into a kiosk image.

## Commands

```
make venv             # create .venv with pygame + numpy
make run              # windowed (SMASHBOX_WINDOWED=1) — preferred under WSLg
make run-fullscreen   # fullscreen
make media-deps       # pipx install cijoe (one-time)
make image            # build via cijoe → ~/system_imaging/disk/smashbox-rpi-arm64.img.gz
make clean / clean-image / distclean
```

`make image` is `cijoe tasks/build.yaml --monitor -c configs/rpi.toml`. The variant is parametric (`VARIANT=rpi` by default) so a second target could be added later by dropping a new `configs/<name>.toml`.

There is no test suite, linter, or formatter wired up. The app is exercised by running it.

Runtime env vars the app honors: `SMASHBOX_WINDOWED`, `SMASHBOX_LOGICAL_HEIGHT` (defaults to 1080 — caps render res and GPU-scales above that), `SMASHBOX_AUDIO_DEVICE` (force a device name), `SMASHBOX_AUDIO_BUFFER` (default 256 samples). When no `DISPLAY`/`WAYLAND_DISPLAY` is present the app sets `SDL_VIDEODRIVER=kmsdrm`. On a Pi (detected via `/proc/device-tree/model`) it also sets `SDL_AUDIODRIVER=alsa` so SDL skips the pulse/pipewire probes.

Image build knobs (in `configs/rpi.toml`): `[smashbox] pi_password`, `[smashbox] wifi_country`, `[system-imaging.images.smashbox-rpi-arm64]` block (URL, paths, target size, gzip level).

## App architecture (`smashbox.py`)

Single file, one class `Smashbox` that owns everything. Section banners (`# --- Themes ---`, `# --- Clip-art ---`, `# --- Sound synthesis ---`, etc.) carve it into:

- **Themes** (`THEMES`, `THEME_ORDER`) — pure data: bg gradient, palette, sprite shape, ambient style.
- **Clip-art** (`_clip_*`) — sprites drawn from pygame primitives at any size. Intentional: no asset files, scales to 4K, no font/emoji dependency.
- **Sound synth** (`_make_piano`, `_make_swoosh`, `_make_bird`, ...) — every fallback sound is generated from numpy arrays at startup via `_to_sound` (1.5 ms fade-in, 10 ms fade-out to mask truncated synth tails). Recorded samples in `assets/sounds/{animals,instruments,counts}/<basename>.{ogg,wav}` override the synth fallbacks at load time (see `_load_samples_by_basename`).
- **Key cycles** (`_KEY_CYCLES_TABLE`) — animal/instrument keys are bound by their **Danish first letter** (B → ?, F → fugl/fløjte/frø/får, G → guitar/gris, K → kat/ko/klaver, etc.). Keys with multiple items round-robin through them on each press. `_play_for_keydown` advances the per-key counter and returns the matching `clip_idx` so `burst` shows the paired sprite.
- **Rendering primitives** — `Particle`, `FloatingSprite`.
- **`Smashbox`** (~1000 lines) — init, main loop, input, three game modes.

Three game modes cycled via the supervisor panel's `g` key, tracked in `self.game_state`:

```
GAME_STATES = ("smash", "whack-a-mole", "counting")
```

In `whack-a-mole` / `counting`, only the correct next key is reactive — wrong keys / mouse / touch trigger a gentle shake + soft two-tone "nope" (`_trigger_nope`, debounced 0.30 s). `whack-a-mole` picks a random A-Z or 0-9 target each round; `counting` walks the Danish 1..9 sequence (no zero in the count) and shows a bottom-row 1..9 strip that fills gray→green as each digit is hit (`_draw_count_row`). On hitting 9 the row reads all-green and a bigger fanfare (`_trigger_count_fanfare`: ring of 8 bursts around centre + 24 scattered bursts + opening chime/boom/pop chord + full pentatonic up-and-down piano run, spread across `COUNT_FANFARE_SECS` ≈ 4 s) plays. After the fanfare the target becomes **"0"** — the dedicated reset key — and the toddler has to press 0 to start a new row. `_count_pos = 10` is the "row complete, awaiting 0" sentinel; there's no auto-reset. 0 is not part of the count sequence, so pressing it during 1..9 progress gets the same nope as any other wrong key. Post-hit target picking is **eager** in `_handle_game_keypress` (not lazy in `_tick_target`) — toddler mashes used to advance 1→4 because the OLD target stayed matchable in the ~16 ms gap between `_target_next_at` expiring and the next frame; eager pick closes that race. Mode-specific branching lives inline in the update/draw/input methods (search `self.game_state == "..."`) rather than in subclasses.

### GPU pipeline

Uses `pygame._sdl2.video.Window` / `Renderer` / `Texture` rather than the classic `pygame.display.set_mode` surface. Surfaces are uploaded as Textures and drawn on the GPU. `renderer.logical_size` sets an internal render resolution which the GPU scales to the physical panel — this is how 4K is handled cheaply (cap logical height at 1080 by default). All hot-path `Texture.draw(dstrect=...)` and renderer `fill_rect/draw_rect` calls pass plain 4-tuples, not `pygame.Rect` instances, to avoid per-frame allocations.

### Audio

`pygame.mixer.pre_init` runs **before** `pygame.init()` (mandatory ordering). The app then enumerates devices with `pygame._sdl2.audio.get_audio_device_names`, picks the first USB-ish device (`USB_AUDIO_HINTS` substring match), and `mixer.quit()` + `mixer.init(devicename=...)` to bind to it. Falls back to default device if the named device fails to open or this pygame build doesn't accept `devicename`. After init, the actual mixer sample rate is queried back via `pygame.mixer.get_init()` and the module-level `SAMPLE_RATE` is mutated to match — synth functions read it lazily, so the sound bank is built at the device-native rate (typically 48 kHz on HDMI/USB). 16 mixer channels, 256-sample buffer.

### Supervisor gate

Exit is gated behind either holding the top-left `CORNER_SIZE` corner for `CORNER_HOLD_SECONDS`, or typing `SUPERVISOR_WORD` ("iddqd"). Inside the panel: `t` theme, `m` mute, `f` fullscreen, `g` mode, `r` reset, `q` quit, `Esc` resume. **Do not** add `TEXTINPUT` / `TEXTEDITING` to the `pygame.event.set_blocked` list — on Bookworm's pygame 2.1.x, that has been observed to leave `KEYDOWN.unicode` empty and silently break the iddqd backdoor.

## Image build pipeline

Linux-only build host. Needs: `qemu-user-static binfmt-support sudo parted losetup gzip xz-utils curl openssl e2fsprogs`, plus `cijoe` via `make media-deps`. Run as a normal user; the cijoe script `sudo`s for the loop-mount + chroot parts.

Layout:

- `configs/rpi.toml` — TOML config: image URL, paths, `pi_password`, `wifi_country`, gzip level.
- `tasks/build.yaml` — single-step cijoe workflow that calls `rpi_image_customize`.
- `scripts/rpi_image_customize.py` — the script that does the work. Mirrors `add_args(parser)` / `main(args, cijoe)` convention from the sibling `bty` / `tellybox` repos.
- `rootfs/` — overlay tree bulk-copied (`cp -a`) over the mounted Pi rootfs. Ownership for `/home/smashbox` is fixed inside the chroot once the user exists.
- Output: `~/system_imaging/disk/smashbox-rpi-arm64.img{,.gz,.gz.sha256}`.

Pipeline steps (all in `rpi_image_customize.py`):

1. Download (cached at `~/system_imaging/cloud/`) and `xz -d` the pinned RPi OS Lite arm64 image.
2. `truncate` to `target_size_gib`, `losetup --partscan`, `parted resizepart 2 100%`, `e2fsck -fy`, `resize2fs`.
3. Mount root + `boot/firmware`; bind-mount `/proc /sys /dev /dev/pts`.
4. `cp -a rootfs/.` over the mounted root; `install` `smashbox.py` from the repo root into `/opt/smashbox/`.
5. Boot-partition: append HDMI tweaks fragment to `config.txt` with begin/end markers (idempotent); strip `systemd.run=…` from `cmdline.txt` and append quiet/plymouth flags; `touch ssh`; delete `userconf.txt` + `firstrun.sh`.
6. Host-side `openssl passwd -6` to hash `pi_password` → handed to chroot via `/tmp/pi-creds`.
7. Drop `qemu-aarch64-static` into the chroot. `chroot` and run the install script: `apt-get install` X + pygame stack + NetworkManager + plymouth, create `smashbox` + `pi` users, enable services, slim caches/docs/logs.
8. Restore the original resolv.conf, remove qemu-static, zero free space (helps gzip), unmount everything, detach loop, `gzip -9`, write sha256 manifest.

### Kiosk boot story (load-bearing — don't break)

- `tty1`: `getty@tty1` autologin drop-in (`rootfs/etc/systemd/system/getty@tty1.service.d/autologin.conf`) logs in user `smashbox`. Their `.bash_profile` (only on `tty1` with no `$DISPLAY`) runs `startx /opt/smashbox/start.sh -- vt1 -nocursor`. **Single owner of tty1.** The chroot install masks `userconfig.service` and the host-side boot-partition step deletes `firstrun.sh` + strips the `systemd.run=…` cmdline — both would steal tty1.
- `tty2`: stock `getty@tty2`. `Ctrl+Alt+F2` → log in as `pi` (NOPASSWD sudo) for rescue.
- `sshd` enabled out of the box; `pi` user password baked in from `[smashbox] pi_password`. We **do not** use `/boot/firmware/userconf.txt` because that triggers `userconfig.service`, which would also fight for tty1.
- `.bash_profile` runs `startx` rather than `exec startx` so when the app exits it can `tail` `~/smashbox.log` and `Xorg.0.log` errors to tty1 before getty respawns — debuggability without ssh.
- `smashbox-wifi-unblock.service` runs `iw reg set $COUNTRY` + `rfkill unblock all` at boot. Without this, the radio is soft-blocked on first boot and wifi is invisible in `nmtui`.
- `smashbox-cpu-perf.service` pins `cpufreq/scaling_governor` to `performance` so the ondemand-governor ramp-up doesn't show as a one-frame-late letter on a toddler's first press.

If you touch tty1 ownership, plymouth, the cmdline, or `userconfig.service`/`firstrun.sh`, re-verify the single-owner-of-tty1 invariant — getting it wrong looks like a black screen on boot.

### Releases (GitHub Actions)

`.github/workflows/release.yml` runs the same `make image` pipeline on `ubuntu-latest`. Two triggers:

- **Tag push** (`git push origin v1.2.3`) — builds and publishes `.img.gz` + `.sha256` as assets on a GitHub Release. `contents: write` permission required.
- **`workflow_dispatch`** — same build, but uploads to the workflow artifact store (7-day retention) instead of cutting a release. Use it to smoke-test the pipeline before tagging.

The runner needs to free ~6 GB before the build: ~700 MB compressed image + 5 GB raw + downloaded RPi OS Lite source. The workflow prunes preinstalled toolcache (dotnet, android, ghc, codeql) up front. `qemu-user-static` + `binfmt-support` are installed inline; the chroot step then runs aarch64 binaries on the x86_64 runner.
