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
echo "claude-workspaces: installed ${HARNESS_ARTIFACT_SHA:-unknown}; self-test passed"
