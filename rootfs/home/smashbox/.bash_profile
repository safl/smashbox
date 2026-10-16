# SPDX-FileCopyrightText: Simon A. F. Lund <os@safl.dk>
#
# SPDX-License-Identifier: BSD-3-Clause

# Auto-launch smashbox when this user lands on tty1 via getty
# autologin. Other ttys / SSH drop straight to a shell so debugging is
# easy.

if [ -z "${DISPLAY:-}" ] && [ "$(tty)" = "/dev/tty1" ]; then
    # Run startx (NOT exec) so when the app exits we can show the tail
    # of the log on screen before getty respawns us. Otherwise the loop
    # is invisible and looks like a boot failure.
    startx /opt/smashbox/start.sh -- vt1 -nocursor
    rc=$?

    clear
    echo "==== smashbox exited (rc=$rc) at $(date) ===="
    echo
    if [ -f "$HOME/smashbox.log" ]; then
        echo "----- ~/smashbox.log (last 40 lines) -----"
        tail -n 40 "$HOME/smashbox.log"
    else
        echo "(no ~/smashbox.log found)"
    fi
    echo
    if [ -f "$HOME/.local/share/xorg/Xorg.0.log" ]; then
        echo "----- Xorg.0.log (errors only) -----"
        grep -E '^\(EE\)|\(WW\)' "$HOME/.local/share/xorg/Xorg.0.log" | tail -n 20
    fi
    echo
    echo "==== Sleeping 60s before getty respawns (Ctrl+Alt+F2 for rescue tty). ===="
    sleep 60
fi
