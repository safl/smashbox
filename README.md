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
the app, so a Pi 4 with a keyboard and a screen becomes a dedicated toy.

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
parted losetup gzip xz-utils curl openssl e2fsprogs`, plus cijoe:

```
make media-deps
make image
```

The image lands in `~/system_imaging/disk/smashbox-rpi-arm64.img.gz`. The
rescue user `pi` gets the password set in `configs/rpi.toml`; change it
before building an image you hand to anyone. Tagged releases on GitHub carry
a prebuilt image.

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
