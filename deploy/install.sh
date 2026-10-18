#!/bin/sh
# Install cw into the artifact root the reconciler hands us, prove the installed
# copy works, then point the stable names at it.
#
# The install dir is never replaced, only its contents, so a read-only bind mount
# of it (a container launching its workspace with cw, say) follows every
# promotion without being recreated.
set -eu

: "${HARNESS_INSTALL_DIR:?not set}"
SRC="$(pwd)"
DEST="$HARNESS_INSTALL_DIR"

# Deny-list copy: adding a file to this repo ships it.
( cd "$SRC" && tar --exclude=./.git --exclude=./.githooks -cf - . ) | ( cd "$DEST" && tar -xf - )
chmod 755 "$DEST/claude-workspaces"

# Prune what this version no longer ships. Without it a removed file lives on in
# the install dir forever (project-manager's installer had exactly that defect).
( cd "$SRC" && find . -path ./.git -prune -o -path ./.githooks -prune -o -type f -print | sort ) > "$DEST/.manifest.new"
( cd "$DEST" && find . -type f ! -name .installed ! -name '.manifest*' -print | sort ) \
  | comm -23 - "$DEST/.manifest.new" | while IFS= read -r stale; do rm -f "$DEST/$stale"; done
mv "$DEST/.manifest.new" "$DEST/.manifest"
find "$DEST" -depth -type d -empty ! -path "$DEST" -exec rmdir {} \; 2>/dev/null || true

# Function, not existence: the gate's tests, run against the INSTALLED file.
if ! out="$(cd "$DEST" && CW_UNDER_TEST="$DEST/claude-workspaces" PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests 2>&1)"; then
  echo "FAIL: installed cw failed its self-test:"; echo "$out" | tail -20; exit 1
fi

# Stable names. ~/bin/cw and ~/.local/bin/cw already point at ~/bin/claude-workspaces
# on the hosts that have them; cw reads its compaction prompt from ~/bin.
link() {
  if [ -e "$2" ] && [ ! -L "$2" ]; then mv "$2" "$2.pre-harness-$(date +%Y%m%d-%H%M%S)"; fi
  ln -sfn "$1" "$2"
}
mkdir -p "$HOME/bin"
link "$DEST/claude-workspaces" "$HOME/bin/claude-workspaces"
link "$DEST/compaction-prompt.md" "$HOME/bin/compaction-prompt.md"
[ -e "$HOME/bin/cw" ] || [ -L "$HOME/bin/cw" ] || ln -s "$HOME/bin/claude-workspaces" "$HOME/bin/cw"
# Boot hook: run `cw boot` so workspaces flagged with `cw autostart NAME on` come back
# after a reboot. Written, never started here: starting it now would launch every
# flagged workspace at install time. It takes effect at the next login (macOS) or
# boot (Linux). With nothing flagged, `cw boot` does nothing.
# Revert note: an older cw left behind by a revert prints its usage for `boot` and
# exits 0, so a stale hook is harmless.
put() {  # put FILE: stdin becomes FILE, rewritten only when it changed
  put_tmp="$1.new.$$"; cat > "$put_tmp"
  if [ -f "$1" ] && cmp -s "$put_tmp" "$1"; then rm -f "$put_tmp"; else mv "$put_tmp" "$1"; echo "boot hook: wrote $1"; fi
}
# The hook runs `cw boot` through the account's login shell, so the profile's PATH
# and environment reach the tmux server, which every later session on the host
# inherits. launchd passes no SHELL, so it is looked up here. The PATH below is only
# the system baseline the shell starts from; cw adds the user's tool dirs itself.
BOOT_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
# CW_BOOT_PLATFORM exists for the gate, which installs both hooks on one machine.
PLATFORM="${CW_BOOT_PLATFORM:-$(uname -s)}"
case "$PLATFORM" in
  Darwin) LOGIN_SHELL="$(dscl . -read "/Users/$(id -un)" UserShell 2>/dev/null | awk '{print $2}')" ;;
  *)      LOGIN_SHELL="$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f7)" ;;
esac
[ -x "${LOGIN_SHELL:-}" ] || LOGIN_SHELL=/bin/sh
case "$PLATFORM" in
  Darwin)
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
    put "$HOME/Library/LaunchAgents/ai.claude-workspaces.boot.plist" <<EOF2
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ai.claude-workspaces.boot</string>
  <key>ProgramArguments</key>
  <array><string>$LOGIN_SHELL</string><string>-lc</string><string>exec "\$HOME/bin/cw" boot --wait-network 300</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$BOOT_PATH</string></dict>
  <key>RunAtLoad</key><true/>
  <!-- tmux daemonizes, but launchd would still reap the job's process group on exit. -->
  <key>AbandonProcessGroup</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/claude-workspaces-boot.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/claude-workspaces-boot.log</string>
</dict>
</plist>
EOF2
    ;;
  Linux)
    U="$HOME/.config/systemd/user"
    mkdir -p "$U/default.target.wants"
    put "$U/claude-workspaces-boot.service" <<EOF2
[Unit]
Description=Resume cw workspaces flagged with 'cw autostart NAME on'

[Service]
Type=oneshot
RemainAfterExit=yes
# The tmux server cw starts lives in this unit's cgroup: stopping or restarting the
# unit must never take the sessions with it.
KillMode=process
Environment=PATH=$BOOT_PATH
# %h, not \$HOME: systemd expands \$VARS in ExecStart itself, even inside quotes.
ExecStart=$LOGIN_SHELL -lc 'exec %h/bin/cw boot --wait-network 300'

[Install]
WantedBy=default.target
EOF2
    # What `systemctl --user enable` does, without touching the running user manager.
    ln -sfn ../claude-workspaces-boot.service "$U/default.target.wants/claude-workspaces-boot.service"
    if [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || true)" = "no" ]; then
      echo "boot hook: WARNING lingering is off for $(id -un): the hook runs at first login, not at boot"
    fi
    ;;
  *) echo "boot hook: skipped on $PLATFORM" ;;
esac

echo "claude-workspaces: installed ${HARNESS_ARTIFACT_SHA:-unknown}; self-test passed"
