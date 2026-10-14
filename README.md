# claude-workspaces (`cw`)

Workspace manager for Claude Code sessions. Creates, launches, resets, rotates,
archives and compacts per-project workspaces under `~/workspaces/`, each in its own
tmux session.

Standard library Python 3 and tmux; nothing else to install.

## Install

```sh
git clone https://github.com/mikeengleai/claude-workspaces.git ~/tools/claude-workspaces
cd ~/tools/claude-workspaces
mkdir -p ~/.local/share/claude-workspaces
HARNESS_INSTALL_DIR=~/.local/share/claude-workspaces sh deploy/install.sh
```

`deploy/install.sh` copies the tree into `HARNESS_INSTALL_DIR`, runs the tests
against the installed copy, and points `~/bin/claude-workspaces` and
`~/bin/compaction-prompt.md` at it (`~/bin/cw` is a link to
`~/bin/claude-workspaces`). Put `~/bin` on your `PATH`. To upgrade, `git pull` and
run the same command again. Never edit an installed copy.

On the author's own hosts a reconciler runs that installer with the version pinned
for each host (`harness.yaml`); changes are committed to a private source repo,
gated by `scripts/validate`, promoted, then exported here.

### Headless launch with Remote Control

`cw launch NAME --rc --detach` starts the workspace in tmux with Remote Control on,
named NAME, without attaching: it appears on claude.ai/code and in the Claude app.
A session already running is left alone, so a supervisor can call it in a loop.

## The compaction prompt

`cw reset` and `cw compact` curate the memory directory using the prompt at
`~/bin/compaction-prompt.md` (`COMPACTION_PROMPT_PATH` in the script). It ships in
this repo as `compaction-prompt.md`; the installer links it into `~/bin`.

**If that file is missing, compaction is skipped, silently.** The reset still runs
its other two steps — it writes the handoff and archives session history — but it
leaves memory uncompacted instead of curating it, so a `reset` stops doing the one
job named "reset". The prompt is deliberately conservative (it errs on KEEP, never
removes channel IDs / paths / commands / agent-cron names / user-and-feedback
memories, and every MERGE must preserve every fact), so the risk it guards against
is *losing* history, not accumulating it.

## The commands, and which ones destroy things

| Command | Effect |
|---|---|
| `cw` | interactive menu |
| `cw launch <name>` | start or attach a session |
| `cw reset <name>` | **safe default.** Handoff, compact memory, archive session history. All workspace files stay in place |
| `cw reset <name> --purge` | as above but deletes session history instead of archiving |
| `cw reset <name> --full` | delegates to `rotate` |
| `cw rotate <name>` | **destructive.** Archives the entire workspace directory and recreates it |
| `cw compact <name>` | curate the memory directory |
| `cw archive list` / `restore` | inspect and restore archived workspaces |

## `.rotate-keep`

A rotation carries forward only `CLAUDE.md`, `HANDOFF.md`, `LINEAGE.md`,
`handoffs/` and the compacted memory directory. To preserve anything else, drop a
`.rotate-keep` at the workspace root listing one workspace-relative path per line:

```
# ~/workspaces/example/.rotate-keep
.git
docs
marketing/content-index
pyproject.toml
```

Nested paths work. Comments and blank lines are ignored. Absolute paths and
entries containing `..` are rejected. Listed paths that do not exist are
reported rather than failing silently. The file itself carries forward.

**Include `.git` if the workspace is a repository**, or the rotation archives the
history. As of 2026-09-02, eight workspaces on one server had that gap.

This file was documentation only until 2026-09-02; nothing read it. Any note in a
workspace `CLAUDE.md` claiming it does not work predates that and is stale.

## Rotation safety checks

Before the confirm prompt, `cw rotate` now:

- lists every top-level entry it is about to remove;
- warns if the workspace is a git repository whose `.git` is not in
  `.rotate-keep`;
- warns about systemd user units whose `WorkingDirectory=` or `ExecStart=` points
  into the workspace.

Either warning upgrades the prompt from `y` to typing `rotate` in full. That last
check exists because a dashboard's service was rotated out from under it
and restarted 675,294 times over 46 days, consuming 61% of the journal, before
anyone noticed.

## Ordering guarantee

The handoff is generated before anything is compacted, archived or deleted, and a
failure there aborts with history untouched. A reset must never consume the
transcripts it failed to summarise.
