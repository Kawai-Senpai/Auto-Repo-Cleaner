# Repo Cleaner

`Repo Cleaner` is a standalone desktop utility for removing sensitive `.env`-style files from Git history with backups, audit logs, verification, and guarded force-push support.

It is designed to work against any Git repository you choose at runtime.

## Features

- Pick any target Git repository from the UI
- Auto-detect the actual repository root
- Create safety backups before rewriting history
- Restore a separate clone from the saved backup mirror or bundle
- Discover historical `.env`, `.env.*`, `prod.env`, and similar paths
- Add custom files or directories such as `.claude` and `.codex`
- Skip common safe example/template files such as `.env.example`
- Auto-install and detect `git-filter-repo` using multiple strategies
- Rewrite history with explicit confirmation
- Verify what remains after cleanup
- Offer guarded force-push with remote selection
- Write timestamped logs and a manifest into the target repo's cleanup artifact folder

## Project Layout

```text
repo-cleaner/
  app.py
  README.md
  .gitignore
  repo_cleaner/
    __init__.py
    engine.py
    gui.py
  requirements.txt
```

## Requirements

- Python 3.11+
- Git installed and on `PATH`
- Internet access if `git-filter-repo` needs to be installed automatically

Tkinter is included with standard Python on most Windows and many Linux/macOS installations.

## Run

From this folder:

```bash
python app.py
```

## Workflow

1. Choose the repository folder you want to clean.
2. Refresh remotes.
3. Check prerequisites.
4. Review built-in and custom targets in the Targets tab.
5. Create a safety backup.
6. Discover env-style and custom paths from Git history.
7. Rewrite history with `git-filter-repo`.
8. Verify the cleanup.
9. Force-push only when you are ready.

## Target Types

The app always knows how to match env-style files such as:

- `.env`
- `.env.development`
- `.env.local`
- `.env.production`
- `prod.env`

You can also add custom targets in the `Targets` tab, for example:

- `.claude`
- `.codex`
- `secrets`
- `backend/private-config`

If a custom target is a directory, the tool removes all historical paths under that directory too.

## Branch And Ref Coverage

The cleanup logic is designed to operate across the whole local repository history, not just the currently checked out branch.

- discovery uses `git log --all`
- backups use `git bundle create --all`
- history rewrite runs `git-filter-repo` against the repository's refs in the local clone
- force-push uses `git push --force --mirror <remote>` so branches and tags on that remote are updated together

Important caveat:

- this covers the refs that exist in your local clone
- if a hosting platform has extra hidden refs that are not present locally, those are outside what a normal local rewrite can mirror

## Artifacts

For each run, the tool writes artifacts under the target repository:

```text
<repo>/.git-history-cleanup/<session-id>/
```

That folder contains:

- `cleanup-log.txt`
- `manifest.json`
- `env-paths-to-remove.txt`
- `backup/repo.bundle`
- `backup/mirror.git`
- worktree and index diffs
- snapshots of untracked files

## Restore Flow

After a backup exists, use `Step 3b: Restore Clone From Backup` to create a separate restored repository in an empty folder you choose.

This is intentionally safer than trying to overwrite the working repository in place.

The app will prefer:

1. the mirror backup
2. the bundle backup

## Important Safety Note

History cleanup does not un-leak credentials. Rotate any exposed tokens, passwords, and API keys before or during cleanup. Treat exposed secrets as compromised.
