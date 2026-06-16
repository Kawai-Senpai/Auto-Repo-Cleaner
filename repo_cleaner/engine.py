from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable


LogFn = Callable[[str], None]

DEFAULT_SESSION = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
EXAMPLE_MARKERS = {"example", "sample", "template", "dist", "default", "defaults"}
PATHS_FILE_NAME = "env-paths-to-remove.txt"
LOG_FILE_NAME = "cleanup-log.txt"
MANIFEST_FILE_NAME = "manifest.json"
DEFAULT_CUSTOM_TARGETS = [".claude", ".codex"]


@dataclass(slots=True)
class CleanupSession:
    session_id: str
    repo_root: Path
    root: Path
    backup_dir: Path
    paths_file: Path
    log_file: Path
    manifest_file: Path


@dataclass(slots=True)
class RestoreResult:
    source_type: str
    destination: Path


@dataclass(slots=True)
class DiscoveryResult:
    matches: list[str]
    custom_targets: list[str]
    details: list[dict[str, object]]


def candidate_git_filter_repo_commands() -> list[list[str]]:
    commands: list[list[str]] = []
    git_filter_repo_exe = shutil.which("git-filter-repo")
    py_launcher = shutil.which("py")

    commands.append(["git", "filter-repo"])
    if git_filter_repo_exe:
        commands.append([git_filter_repo_exe])
    commands.append([sys.executable, "-m", "git_filter_repo"])
    if py_launcher:
        commands.append([py_launcher, "-m", "git_filter_repo"])

    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for command in commands:
        key = tuple(command)
        if key not in seen:
            seen.add(key)
            unique.append(command)
    return unique


class RepoCleanerEngine:
    def __init__(self, logger: LogFn | None = None) -> None:
        self.logger = logger or (lambda message: None)

    def git_available(self) -> bool:
        return shutil.which("git") is not None

    def resolve_repo_root(self, start_path: Path) -> Path:
        candidate = start_path.resolve()
        if candidate.is_file():
            candidate = candidate.parent

        if (candidate / ".git").exists():
            return candidate

        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=candidate,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
        raise RuntimeError(f"No git repository found from: {start_path}")

    def create_session(self, repo_path: Path, session_id: str | None = None) -> CleanupSession:
        repo_root = self.resolve_repo_root(repo_path)
        token = session_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        root = repo_root / ".git-history-cleanup" / token
        backup_dir = root / "backup"
        session = CleanupSession(
            session_id=token,
            repo_root=repo_root,
            root=root,
            backup_dir=backup_dir,
            paths_file=root / PATHS_FILE_NAME,
            log_file=root / LOG_FILE_NAME,
            manifest_file=root / MANIFEST_FILE_NAME,
        )
        self.ensure_session_dirs(session)
        return session

    def ensure_session_dirs(self, session: CleanupSession) -> None:
        session.backup_dir.mkdir(parents=True, exist_ok=True)

    def log_line(self, session: CleanupSession, message: str) -> None:
        self.ensure_session_dirs(session)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        line = f"[{timestamp}] {message}"
        self.logger(line)
        with session.log_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def run(
        self,
        session: CleanupSession,
        args: list[str],
        *,
        capture_output: bool = True,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.log_line(session, f"RUN {' '.join(args)}")
        result = subprocess.run(
            args,
            cwd=session.repo_root,
            text=True,
            capture_output=capture_output,
            check=False,
            env=env,
        )
        self._append_process_output(session, result)
        if check and result.returncode != 0:
            raise RuntimeError(
                f"Command failed ({result.returncode}): {' '.join(args)}\n"
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    def _append_process_output(self, session: CleanupSession, result: subprocess.CompletedProcess[str]) -> None:
        with session.log_file.open("a", encoding="utf-8") as handle:
            if result.stdout:
                handle.write(result.stdout)
                if not result.stdout.endswith("\n"):
                    handle.write("\n")
            if result.stderr:
                handle.write(result.stderr)
                if not result.stderr.endswith("\n"):
                    handle.write("\n")

    def write_manifest(self, session: CleanupSession, payload: dict) -> None:
        data = dict(payload)
        data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        with session.manifest_file.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")

    def load_manifest(self, session: CleanupSession) -> dict:
        if not session.manifest_file.exists():
            return {}
        with session.manifest_file.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def git_filter_repo_available(self, session: CleanupSession) -> bool:
        for base_command in candidate_git_filter_repo_commands():
            result = self.run(session, [*base_command, "--version"], check=False)
            if result.returncode == 0:
                return True
        return False

    def resolve_git_filter_repo_command(self, session: CleanupSession) -> list[str]:
        for base_command in candidate_git_filter_repo_commands():
            result = self.run(session, [*base_command, "--version"], check=False)
            if result.returncode == 0:
                return base_command
        raise RuntimeError("git-filter-repo is not available.")

    def install_git_filter_repo(self, session: CleanupSession) -> bool:
        if self.git_filter_repo_available(session):
            self.log_line(session, "git-filter-repo is already available.")
            return True

        install_commands: list[list[str]] = []
        py_launcher = shutil.which("py")
        brew = shutil.which("brew")

        install_commands.append([sys.executable, "-m", "pip", "install", "--user", "git-filter-repo"])
        if py_launcher:
            install_commands.append([py_launcher, "-m", "pip", "install", "--user", "git-filter-repo"])
        if brew:
            install_commands.append([brew, "install", "git-filter-repo"])

        env = os.environ.copy()
        for command in install_commands:
            self.log_line(session, f"Attempting install via: {' '.join(command)}")
            result = subprocess.run(
                command,
                cwd=session.repo_root,
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self._append_process_output(session, result)
            if result.returncode == 0 and self.git_filter_repo_available(session):
                self.log_line(session, "git-filter-repo installed successfully.")
                return True

        self.log_line(session, "Automatic installation did not succeed.")
        return False

    def current_branch(self, session: CleanupSession) -> str:
        result = self.run(session, ["git", "branch", "--show-current"])
        return result.stdout.strip() or "HEAD"

    def fetch_all(self, session: CleanupSession) -> None:
        self.log_line(session, "Starting remote refresh before analysis.")
        self.run(session, ["git", "fetch", "--all", "--tags", "--prune"])
        self.log_line(session, "DONE remote refresh.")

    def remote_names(self, session: CleanupSession) -> list[str]:
        result = self.run(session, ["git", "remote"])
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def historical_paths(self, session: CleanupSession, *, include_remotes: bool = True) -> list[str]:
        # Discovery scans everything (--all) so remote-only history is caught.
        # Local verification passes include_remotes=False because git-filter-repo
        # never rewrites refs/remotes/*; those only clear after a force-push + fetch.
        ref_args = ["--all"] if include_remotes else ["--branches", "--tags"]
        result = self.run(session, ["git", "log", *ref_args, "--name-only", "--pretty=format:"])
        paths = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        return sorted(paths)

    def looks_like_env_path(self, path_text: str) -> bool:
        normalized = path_text.replace("\\", "/")
        basename = Path(normalized).name.lower()
        if not basename:
            return False
        parts = [part for part in basename.split(".") if part]
        if any(part in EXAMPLE_MARKERS for part in parts):
            return False
        if basename == ".env":
            return True
        if basename.startswith(".env"):
            return True
        if basename.endswith(".env"):
            return True
        if ".env." in basename:
            return True
        return False

    def looks_like_custom_target(self, path_text: str, custom_targets: list[str]) -> bool:
        normalized = path_text.replace("\\", "/").strip("/")
        if not normalized:
            return False
        lowered = normalized.lower()
        parts = lowered.split("/")
        for target in custom_targets:
            cleaned = target.strip().replace("\\", "/").strip("/").lower()
            if not cleaned:
                continue
            if lowered == cleaned:
                return True
            if lowered.startswith(cleaned + "/"):
                return True
            if cleaned in parts:
                return True
        return False

    def path_history_details(self, session: CleanupSession, path_text: str) -> dict[str, object]:
        result = self.run(
            session,
            [
                "git",
                "log",
                "--all",
                "--date=iso-strict",
                "--pretty=format:%H%x1f%ad%x1f%an%x1f%s",
                "--",
                path_text,
            ],
        )
        commits: list[dict[str, object]] = []
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            sha, committed_at, author, subject = (line.split("\x1f", 3) + ["", "", "", ""])[:4]
            branch_result = self.run(session, ["git", "branch", "-a", "--contains", sha], check=False)
            refs = [
                item.strip().lstrip("*").strip()
                for item in branch_result.stdout.splitlines()
                if item.strip()
            ]
            commits.append(
                {
                    "commit": sha,
                    "committed_at": committed_at,
                    "author": author,
                    "subject": subject,
                    "refs": refs,
                }
            )
        return {
            "path": path_text,
            "commit_count": len(commits),
            "commits": commits,
        }

    def discover_candidate_paths(self, session: CleanupSession, custom_targets: list[str] | None = None) -> DiscoveryResult:
        targets = [item.strip() for item in (custom_targets or []) if item.strip()]
        matches = [
            path
            for path in self.historical_paths(session)
            if self.looks_like_env_path(path) or self.looks_like_custom_target(path, targets)
        ]
        details = [self.path_history_details(session, path) for path in matches]
        session.paths_file.write_text("".join(f"literal:{path}\n" for path in matches), encoding="utf-8")
        manifest = self.load_manifest(session)
        manifest["candidate_paths"] = matches
        manifest["custom_targets"] = targets
        manifest["candidate_details"] = details
        self.write_manifest(session, manifest)
        self.log_line(session, f"Discovered {len(matches)} candidate path(s).")
        self.log_line(session, "DONE candidate path discovery.")
        return DiscoveryResult(matches=matches, custom_targets=targets, details=details)

    def record_worktree_state(self, session: CleanupSession) -> None:
        status = self.run(session, ["git", "status", "--short", "--branch"]).stdout
        diff = self.run(session, ["git", "diff", "--binary"]).stdout
        cached_diff = self.run(session, ["git", "diff", "--cached", "--binary"]).stdout
        untracked = self.run(session, ["git", "ls-files", "--others", "--exclude-standard"]).stdout.splitlines()

        (session.backup_dir / "git-status.txt").write_text(status, encoding="utf-8")
        (session.backup_dir / "working-tree.diff").write_text(diff, encoding="utf-8")
        (session.backup_dir / "index.diff").write_text(cached_diff, encoding="utf-8")
        (session.backup_dir / "untracked-files.txt").write_text(
            "\n".join(untracked) + ("\n" if untracked else ""),
            encoding="utf-8",
        )

        for relative in untracked:
            source = session.repo_root / relative
            if source.is_file():
                target = session.backup_dir / "untracked" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

    def create_backup(self, session: CleanupSession) -> None:
        if any(session.backup_dir.iterdir()):
            self.log_line(session, f"Backup directory already exists: {session.backup_dir}")
            self.log_line(session, "DONE backup step (reused existing backup directory).")
            return

        self.log_line(session, "Creating safety backup.")
        bundle_path = session.backup_dir / "repo.bundle"
        mirror_dir = session.backup_dir / "mirror.git"
        self.run(session, ["git", "bundle", "create", str(bundle_path), "--all"])
        self.run(session, ["git", "clone", "--mirror", str(session.repo_root), str(mirror_dir)])
        self.record_worktree_state(session)

        manifest = self.load_manifest(session)
        manifest["backup"] = {
            "bundle": str(bundle_path),
            "mirror": str(mirror_dir),
            "status": str(session.backup_dir / "git-status.txt"),
            "working_tree_diff": str(session.backup_dir / "working-tree.diff"),
            "index_diff": str(session.backup_dir / "index.diff"),
            "untracked_snapshot": str(session.backup_dir / "untracked"),
        }
        self.write_manifest(session, manifest)
        self.log_line(session, "Backup created successfully.")
        self.log_line(session, "DONE backup creation.")

    def restore_clone(self, session: CleanupSession, destination: Path) -> RestoreResult:
        manifest = self.load_manifest(session)
        backup = manifest.get("backup")
        if not backup:
            raise RuntimeError("No backup metadata found for this session.")

        destination = destination.resolve()
        if destination.exists():
            if any(destination.iterdir()):
                raise RuntimeError(f"Restore destination is not empty: {destination}")
        else:
            destination.mkdir(parents=True, exist_ok=True)

        mirror_path = Path(backup["mirror"])
        bundle_path = Path(backup["bundle"])

        if mirror_path.exists():
            source_type = "mirror"
            self.run(session, ["git", "clone", str(mirror_path), str(destination)])
        elif bundle_path.exists():
            source_type = "bundle"
            self.run(session, ["git", "clone", str(bundle_path), str(destination)])
        else:
            raise RuntimeError("Neither mirror nor bundle backup is available.")

        manifest["restore"] = {
            "destination": str(destination),
            "source_type": source_type,
        }
        self.write_manifest(session, manifest)
        self.log_line(session, f"Restore clone created from {source_type} backup at: {destination}")
        self.log_line(session, "DONE restore clone creation.")
        return RestoreResult(source_type=source_type, destination=destination)

    def verify_cleanup(
        self, session: CleanupSession, *, include_remotes: bool = False
    ) -> tuple[list[str], list[str]]:
        manifest = self.load_manifest(session)
        candidates = manifest.get("candidate_paths", [])
        current_paths = set(self.historical_paths(session, include_remotes=include_remotes))
        still_present = [path for path in candidates if path in current_paths]
        scope = "local + remote-tracking" if include_remotes else "local branches/tags"
        self.log_line(
            session, f"Verification checked {len(candidates)} candidate path(s) [{scope}]."
        )
        if still_present:
            self.log_line(session, f"{len(still_present)} candidate path(s) still appear in history.")
            if not include_remotes:
                self.log_line(
                    session,
                    "These remain in LOCAL history; the rewrite did not fully purge them.",
                )
        else:
            if include_remotes:
                self.log_line(
                    session, "No candidate paths remain in local OR remote-tracking history."
                )
            else:
                self.log_line(
                    session,
                    "No candidate paths remain in local history. "
                    "refs/remotes/* still hold old history until you force-push and fetch.",
                )
        self.log_line(session, "DONE cleanup verification.")
        return candidates, still_present

    def _drop_remote_tracking_refs(self, session: CleanupSession) -> None:
        # git-filter-repo refuses to rewrite refs/remotes/* and treats their
        # presence as a sign the repo is not a fresh clone, which makes it run in
        # a partial/metadata mode that leaves history untouched. Removing the
        # remote-tracking refs (a disposable cache) lets it do a full rewrite of
        # every local branch and tag. They repopulate cleanly on the next fetch.
        result = self.run(
            session,
            ["git", "for-each-ref", "--format=%(refname)", "refs/remotes/"],
            check=False,
        )
        refs = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not refs:
            return
        self.log_line(session, f"Dropping {len(refs)} remote-tracking ref(s) before rewrite.")
        for ref in refs:
            self.run(session, ["git", "update-ref", "-d", ref], check=False)

    def rewrite_history(self, session: CleanupSession) -> None:
        manifest = self.load_manifest(session)
        if not session.paths_file.exists():
            raise RuntimeError("No candidate path list exists yet. Run discovery first.")
        if not manifest.get("backup"):
            raise RuntimeError("No backup found. Create a backup before rewriting history.")
        filter_repo_command = self.resolve_git_filter_repo_command(session)
        self._drop_remote_tracking_refs(session)
        # Clear any prior filter-repo state so this is a full run, not a metadata-only update.
        stale_state = session.repo_root / ".git" / "filter-repo"
        if stale_state.exists():
            self.log_line(session, "Removing stale .git/filter-repo state for a full rewrite.")
            shutil.rmtree(stale_state, ignore_errors=True)
        self.run(
            session,
            [
                *filter_repo_command,
                "--force",
                "--sensitive-data-removal",
                "--invert-paths",
                "--paths-from-file",
                str(session.paths_file),
            ],
        )
        candidates, still_present = self.verify_cleanup(session)
        manifest["filter_result"] = {
            "attempted": True,
            "candidate_count": len(candidates),
            "still_present_after_filter": still_present,
        }
        self.write_manifest(session, manifest)
        self.log_line(session, "History rewrite step finished.")
        self.log_line(session, "DONE history rewrite.")

    def force_push(self, session: CleanupSession, remote: str) -> tuple[list[str], list[str]]:
        self.run(session, ["git", "push", "--force", "--mirror", remote])
        self.log_line(session, f"Force push completed for remote '{remote}'.")
        # Refresh remote-tracking refs, then verify the SERVER is clean too.
        self.run(session, ["git", "fetch", remote, "--prune", "--tags"], check=False)
        candidates, still_present = self.verify_cleanup(session, include_remotes=True)
        manifest = self.load_manifest(session)
        manifest["post_push_verification"] = {
            "remote": remote,
            "candidate_count": len(candidates),
            "still_present_after_push": still_present,
        }
        self.write_manifest(session, manifest)
        self.log_line(session, "DONE force push.")
        return candidates, still_present

    def prerequisites_summary(self, session: CleanupSession) -> dict[str, str]:
        available = self.git_filter_repo_available(session)
        summary = {
            "repo_root": str(session.repo_root),
            "artifacts": str(session.root),
            "git_available": "yes" if self.git_available() else "no",
            "git_filter_repo_available": "yes" if available else "no",
            "env_examples": ".env, .env.development, .env.local, .env.production, prod.env",
            "default_custom_targets": ", ".join(DEFAULT_CUSTOM_TARGETS),
        }
        self.log_line(session, "DONE prerequisite check.")
        return summary
