# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

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
5. **Clean up** - Clear stashes; do not delete local branches without user approval
6. **Verify** - All changes committed locally (working tree clean)
7. **Hand off** - Provide context for next session

**OPTIONAL: PUSH TO REMOTE**

Only push when the user explicitly asks you to. If they do:

```bash
git pull --rebase
git push
git status  # Should show "up to date with origin"
```

**CRITICAL RULES:**

- Work is NOT complete until every change is committed locally
- NEVER leave uncommitted changes or stranded edits in the working tree
- NEVER push to remote unless the user explicitly requests it
- If a commit fails (hook, conflict, etc.), resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->

## Build & Test

_Add your build and test commands here_

```bash
# Example:
# npm install
# npm test
```

## Architecture Overview

_Add a brief overview of your project architecture_

## Conventions & Patterns

_Add your project-specific conventions here_
