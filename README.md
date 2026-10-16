<!--
SPDX-FileCopyrightText: Simon A. F. Lund <os@safl.dk>

SPDX-License-Identifier: BSD-3-Clause
-->

# Smashbox

A fullscreen keyboard smash toy for toddlers. Any key, click or touch makes
something colourful happen. Animal and instrument keys follow their Danish
first letter, so K is kat, ko and klaver, and F is fugl, fløjte, frø and får.
Two simple games, whack-a-mole and counting 1 to 9, sit alongside free play.

The repository also builds a Raspberry Pi OS image that boots straight into
the app, so the Pi becomes a dedicated toy rather than a computer. Nothing is
set up by hand on the device: the image is flashed and booted, and a parent
reaches the settings or the exit through a hidden supervisor panel.

The hardware in use is a Raspberry Pi 4 with a small HDMI monitor, a USB
keyboard and a powered speaker, all tucked inside a wooden backgammon case and
run from a 10000 mAh USB power bank. It is a portable computer, like a laptop,
except that every part is left in view, so the case also shows what a computer
is made of. Sound goes out the Pi's 3.5 mm jack to the speaker, which takes
its power from one of the Pi's USB ports. The image switches HDMI audio off,
because it never worked with the monitor, and blocks USB audio, because it was
flaky, so the jack is the only sound output. The jack is held at -10 dB, which
leaves the speaker's knob room to adjust and limits how loud a toddler can
turn it.

## Running it on a desktop

```
make venv
make run              # windowed
make run-fullscreen
```

pygame 2.6 has no wheel for Python 3.14 yet. If that is your `python3`, point
the venv at an older interpreter, for example `make venv PY=python3.13`.

To leave the app, hold the top-left corner for about two seconds, or type
`iddqd`. The supervisor panel that opens switches theme (`t`), mute (`m`),
fullscreen (`f`) and game mode (`g`), resets the counter (`r`), and quits
(`q`). `Esc` goes back to the toy.

## Building the Raspberry Pi image

The build runs on Linux and needs `qemu-user-static binfmt-support sudo
parted losetup gzip xz-utils curl openssl e2fsprogs`, plus cijoe. On Ubuntu
26.04 and later, install `qemu-user-binfmt` instead of `qemu-user-static`:

```
make media-deps
make image
```

The image lands in `~/system_imaging/disk/smashbox-rpi-arm64.img.gz`. The
rescue user `pi` gets the password set in `configs/rpi.toml`; change it
before building an image you hand to anyone. GitHub also builds the image on
every push that changes what goes into it, keeping it as a workflow artifact
for a week, and tagged releases carry a prebuilt image.

## Development

```
make lint
make test
```

`CLAUDE.md` describes the app's structure and the kiosk boot sequence in more
detail.

## License

The code is BSD-3-Clause, see `LICENSE`. The recorded sounds under
`assets/sounds/` carry their own licenses: public domain, CC0, CC-BY and
CC-BY-SA. `assets/sounds/ATTRIBUTIONS.md` lists each one, with the credit
lines the CC-BY ones require.
