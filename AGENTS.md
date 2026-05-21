# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd prime` for full workflow context.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work atomically
bd close <id>         # Complete work
bd dolt push          # Push beads data to remote
```

## Git Commit Rules

**NEVER add `Co-authored-by` trailers** to any commit message. Do not add
`Co-authored-by: Cursor <cursoragent@cursor.com>` or any other co-author
attribution. All commits are authored solely by the repository owner.

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until every local change is committed. Pushing to remote is OPTIONAL and only happens when the user explicitly requests it.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **COMMIT EVERYTHING LOCALLY** - This is MANDATORY:
   ```bash
   git add -A
   git commit -m "<message>"   # Use a HEREDOC for multi-line messages
   git status                  # MUST show "nothing to commit, working tree clean"
   ```
   Beads data is tracked locally via the local routing mode in `.beads/config.yaml`, so no `bd dolt push` is needed during a normal session.
5. **Clean up** - Clear stashes; do not delete local branches without user approval
6. **Verify** - All changes committed locally (working tree clean)
7. **Hand off** - Provide context for next session

**OPTIONAL: PUSH TO REMOTE**

Only push when the user explicitly asks you to. If they do:

```bash
git pull --rebase
bd dolt push          # Only if beads data needs to be shared with remote
git push
git status            # Should show "up to date with origin"
```

**CRITICAL RULES:**
- Work is NOT complete until every change is committed locally
- NEVER leave uncommitted changes or stranded edits in the working tree
- NEVER push to remote (git or `bd dolt push`) unless the user explicitly requests it
- If a commit fails (hook, conflict, etc.), resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
