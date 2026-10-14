# Memory Compaction Curator

You are curating a Claude Code memory directory for the `{{workspace_name}}` workspace. Produce a cleaner, tighter version without losing load-bearing knowledge.

## Paths

- **Source (read-only):** `{{source_dir}}`
- **Staged output (write here):** `{{staged_dir}}` — already created, empty

Do not modify anything in `{{source_dir}}`. Write every curated file into `{{staged_dir}}`.

## Procedure

1. List every file in `{{source_dir}}` and read each one (`MEMORY.md` plus all `*.md` memories).
2. Classify each memory as one of:
   - **KEEP** — accurate and load-bearing; copy content as-is into `{{staged_dir}}`
   - **MERGE** — overlaps another memory; combine into a single file
   - **STALE** — safely removable (criteria below)
   - **REWRITE** — relevant but verbose, unclear, or with rotted references
3. Write the curated directory to `{{staged_dir}}`:
   - One `.md` file per kept / merged / rewritten memory
   - Reuse original filenames when possible; pick a clear name when merging
   - `MEMORY.md` index, grouped under `## Reference`, `## Project`, `## Feedback`, `## User`
   - `.compaction-summary.md` describing every change

## STALE criteria (err on KEEP)

Mark a memory STALE only if **all** hold:

- It describes a one-off bug whose fix is stable and not likely to recur
- The fix left no recurring gotcha, allowlist pattern, API quirk, or subtle invariant
- It references no IDs, paths, commands, or skill/agent/cron names that might still matter
- No other memory cross-references it

If any doubt remains, KEEP. Removing knowledge is more costly than leaving a slightly verbose memory.

**Never remove:**
- Slack channel IDs, QMD/SQLite paths, specific CLI commands or flags
- Names of skills/agents under `~/.openclaw/agents/` or `~/.openclaw/skills/`
- Cron job names from `~/.openclaw/cron/jobs.json`
- User preferences or feedback memories (these compound value over time)

## MERGE criteria

Merge two memories when:
- They describe the same subsystem and one strictly extends or supersedes the other
- They are fragments of a single story (e.g., two "fixed X on date Y" notes for the same component)
- They reference the same IDs and cover adjacent facts

Merged output must preserve **every** fact from each source. Do not summarize away specifics (dates, IDs, version numbers).

## REWRITE criteria

Rewrite when:
- Verbose prose can become a tight bullet list
- Past-tense narrative can become present-tense facts
- Dates embedded in prose can move to a trailing `_(YYYY-MM-DD)_` tag
- References to files that no longer exist can be trimmed

Keep the frontmatter block (`name`, `description`, `type`).

## File format

Every memory file looks like:

```
---
name: ...
description: ...   (one-line, used as relevance hook in future conversations)
type: user|feedback|project|reference
---

(body — for feedback/project, structure as: rule/fact, then **Why:** and **How to apply:** lines)
```

`MEMORY.md` entries follow exactly:

```
- [Title](filename.md) — one-line description under 150 chars
```

## Required summary file

Write `{{staged_dir}}/.compaction-summary.md` in this shape:

```
# Compaction Summary — {{workspace_name}}

- Before: N files
- After:  M files

## Removed (stale)
- filename.md — one-line reason

## Merged
- a.md + b.md → c.md — one-line reason

## Rewritten
- filename.md — one-line change

## Kept as-is (N files)
- filename.md
- ...
```

## Do NOT

- Touch `{{source_dir}}` in any way
- Invent memories or add knowledge that is not in the source
- Remove a memory because you don't recognize its subject — KEEP it
- Rename files casually (other memories or external code may reference them)
- Expand memories with speculation; shorter-same-facts is the goal

Begin now. Read `{{source_dir}}`, think, then write into `{{staged_dir}}`.
