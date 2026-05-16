#!/bin/sh
# X session entrypoint. Plain POSIX redirection (no process substitution
# — dash, which is /bin/sh on Debian, doesn't support `> >(tee ...)`).
# The .bash_profile reads the log and dumps it to tty1 after the app
# exits, so kiosk-side debugging is possible without SSH.
set -u

LOG="$HOME/smashbox.log"
: > "$LOG"
exec >>"$LOG" 2>&1
set -x

echo "==== $(date --iso-8601=seconds) smashbox starting ===="
echo "DISPLAY=${DISPLAY:-unset} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-unset}"
echo "uname:   $(uname -a)"
echo "python:  $(python3 --version 2>&1)"
echo "pygame:  $(python3 -c 'import pygame; print(pygame.version.ver, "SDL", ".".join(map(str, pygame.get_sdl_version())))' 2>&1)"
echo "user:    $(id)"
echo "tty:     $(tty 2>&1)"
echo "kbd:     $(ls -la /dev/input/by-path/*kbd* 2>&1)"
echo

xset s off       || true
xset s noblank   || true
xset -dpms       || true

if command -v unclutter >/dev/null 2>&1; then
    unclutter -idle 0 -root >/dev/null 2>&1 &
fi

export PYTHONUNBUFFERED=1
python3 /opt/smashbox/smashbox.py
rc=$?

echo
echo "==== $(date --iso-8601=seconds) smashbox exited rc=$rc ===="
exit "$rc"
