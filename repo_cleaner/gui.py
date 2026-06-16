from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from repo_cleaner.engine import CleanupSession, DEFAULT_CUSTOM_TARGETS, RepoCleanerEngine


class RepoCleanerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Repo Cleaner")
        self.root.geometry("1120x760")
        self.root.minsize(980, 700)

        self.log_queue: Queue[str] = Queue()
        self.engine = RepoCleanerEngine(logger=self.log_queue.put)
        self.session: CleanupSession | None = None
        self.busy = False
        self.repo_var = tk.StringVar()
        self.session_var = tk.StringVar(value="No repository selected")
        self.status_var = tk.StringVar(value="Choose a repository to begin.")
        self.custom_targets_var = tk.StringVar(value=", ".join(DEFAULT_CUSTOM_TARGETS))

        self._build_ui()
        self._poll_logs()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=3)
        container.columnconfigure(1, weight=2)
        container.rowconfigure(2, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="Repo Cleaner", font=("Segoe UI", 20, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Step-by-step Git history cleanup for env-style secrets, with backups and guarded force-push.",
            wraplength=820,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        repo_frame = ttk.LabelFrame(container, text="1. Choose Target Repository", padding=12)
        repo_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        repo_frame.columnconfigure(1, weight=1)

        ttk.Label(repo_frame, text="Folder").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(repo_frame, textvariable=self.repo_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(repo_frame, text="Browse", command=self.choose_repo).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(repo_frame, text="Load Repo", command=self.load_repo).grid(row=0, column=3, padx=(8, 0))
        ttk.Label(repo_frame, textvariable=self.session_var).grid(row=1, column=0, columnspan=4, sticky="w", pady=(10, 0))

        notebook = ttk.Notebook(container)
        notebook.grid(row=2, column=0, sticky="nsew", padx=(0, 12))

        steps = ttk.Frame(notebook, padding=12)
        targets_tab = ttk.Frame(notebook, padding=12)
        notebook.add(steps, text="Wizard")
        notebook.add(targets_tab, text="Targets")
        steps.columnconfigure(0, weight=1)
        targets_tab.columnconfigure(0, weight=1)

        buttons = [
            ("Step 0: Refresh Remotes", self.refresh_remotes),
            ("Step 1: Check Prerequisites", self.check_prerequisites),
            ("Step 2: Install git-filter-repo", self.install_filter_repo),
            ("Step 3: Create Safety Backup", self.create_backup),
            ("Step 3b: Restore Clone From Backup", self.restore_from_backup),
            ("Step 4: Discover Env Paths", self.discover_paths),
            ("Step 5: Rewrite History", self.rewrite_history),
            ("Step 6: Verify Cleanup", self.verify_cleanup),
            ("Step 7: Force Push Cleaned History", self.force_push),
            ("Open Artifact Folder", self.open_artifacts),
            ("Open Backup Folder", self.open_backup_folder),
        ]
        self.action_buttons: list[ttk.Button] = []
        for index, (label, command) in enumerate(buttons):
            button = ttk.Button(steps, text=label, command=command)
            button.grid(row=index, column=0, sticky="ew", pady=5)
            self.action_buttons.append(button)

        self.summary = scrolledtext.ScrolledText(steps, height=16, wrap="word", state="disabled")
        self.summary.grid(row=len(buttons), column=0, sticky="nsew", pady=(10, 0))
        steps.rowconfigure(len(buttons), weight=1)

        ttk.Label(
            targets_tab,
            text="Built-in matching includes .env, .env.development, .env.local, .env.production, prod.env, and similar env-style names.",
            wraplength=520,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            targets_tab,
            text="Custom files or directories to remove from history as well. Use commas, for example: .claude, .codex, secrets, backend/private-config",
            wraplength=520,
        ).grid(row=1, column=0, sticky="w", pady=(10, 6))
        ttk.Entry(targets_tab, textvariable=self.custom_targets_var).grid(row=2, column=0, sticky="ew")
        ttk.Label(
            targets_tab,
            text="A custom directory match removes everything historically under that path too.",
            wraplength=520,
        ).grid(row=3, column=0, sticky="w", pady=(10, 0))

        right = ttk.LabelFrame(container, text="Live Logs", padding=12)
        right.grid(row=2, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self.log_output = scrolledtext.ScrolledText(right, wrap="word", state="disabled")
        self.log_output.grid(row=0, column=0, sticky="nsew")

        footer = ttk.Frame(container)
        footer.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Quit", command=self.root.destroy).grid(row=0, column=1, sticky="e")

        self._set_buttons_enabled(False)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled and not self.busy else "disabled"
        for button in self.action_buttons:
            button.configure(state=state)

    def _poll_logs(self) -> None:
        try:
            while True:
                message = self.log_queue.get_nowait()
                self._append_text(self.log_output, message + "\n")
        except Empty:
            pass
        self.root.after(150, self._poll_logs)

    def _append_text(self, widget: scrolledtext.ScrolledText, text: str) -> None:
        widget.configure(state="normal")
        widget.insert("end", text)
        widget.see("end")
        widget.configure(state="disabled")

    def _write_summary(self, text: str) -> None:
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", text)
        self.summary.configure(state="disabled")

    def choose_repo(self) -> None:
        selected = filedialog.askdirectory(title="Choose a Git repository")
        if selected:
            self.repo_var.set(selected)

    def load_repo(self) -> None:
        raw = self.repo_var.get().strip()
        if not raw:
            messagebox.showwarning("Choose Repository", "Pick a repository folder first.")
            return
        try:
            self.session = self.engine.create_session(Path(raw))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Repository Error", str(exc))
            return

        self.session_var.set(f"Loaded: {self.session.repo_root} | Session: {self.session.session_id}")
        self.status_var.set("Repository loaded. Refresh remotes or start with the prerequisite check.")
        self._write_summary(
            f"Repository root: {self.session.repo_root}\n"
            f"Artifact folder: {self.session.root}\n\n"
            "Recommended order:\n"
            "0. Refresh remotes first\n"
            "1. Check prerequisites\n"
            "2. Install git-filter-repo if needed\n"
            "3. Create safety backup\n"
            "3b. Create a restore clone from backup if needed\n"
            "4. Discover env paths and custom targets\n"
            "5. Rewrite history\n"
            "6. Verify cleanup\n"
            "7. Force push when ready"
        )
        self._set_buttons_enabled(True)

    def _require_session(self) -> CleanupSession | None:
        if self.session is None:
            messagebox.showwarning("No Repository", "Load a repository first.")
            return None
        return self.session

    def _run_background(self, status_text: str, work) -> None:
        session = self._require_session()
        if session is None or self.busy:
            return

        self.busy = True
        self.status_var.set(status_text)
        self._set_buttons_enabled(True)

        def runner() -> None:
            try:
                work(session)
            except Exception as exc:  # noqa: BLE001
                error_text = str(exc)
                self.log_queue.put(f"ERROR {error_text}")
                self.root.after(0, lambda message=error_text: messagebox.showerror("Repo Cleaner", message))
            finally:
                self.root.after(0, self._finish_background)

        threading.Thread(target=runner, daemon=True).start()

    def _custom_targets(self) -> list[str]:
        return [item.strip() for item in self.custom_targets_var.get().split(",") if item.strip()]

    def _finish_background(self) -> None:
        self.busy = False
        self.status_var.set("Ready for the next step.")
        self._set_buttons_enabled(True)

    def check_prerequisites(self) -> None:
        def work(session: CleanupSession) -> None:
            summary = self.engine.prerequisites_summary(session)
            text = (
                f"Repository root: {summary['repo_root']}\n"
                f"Artifacts folder: {summary['artifacts']}\n"
                f"Git available: {summary['git_available']}\n"
                f"git-filter-repo available: {summary['git_filter_repo_available']}\n"
                f"Matched examples: {summary['env_examples']}\n\n"
                f"Default custom targets: {summary['default_custom_targets']}\n"
                f"Selected custom targets: {', '.join(self._custom_targets()) or '(none)'}\n\n"
                "Rotate exposed secrets before force-pushing."
            )
            self.root.after(0, lambda: self._write_summary(text))

        self._run_background("Checking prerequisites...", work)

    def refresh_remotes(self) -> None:
        if not messagebox.askyesno(
            "Refresh Remotes",
            "This will fetch all remotes, tags, and prune stale refs before analysis.\n\nContinue?",
        ):
            return

        def work(session: CleanupSession) -> None:
            self.engine.fetch_all(session)
            self.root.after(
                0,
                lambda: self._write_summary(
                    "Remote refresh completed.\n\nFetched all remotes, tags, and pruned stale refs before cleanup analysis."
                ),
            )

        self._run_background("Refreshing remotes...", work)

    def install_filter_repo(self) -> None:
        if not messagebox.askyesno(
            "Install git-filter-repo",
            "The app will try to install git-filter-repo automatically using pip or Homebrew.\n\nContinue?",
        ):
            return

        def work(session: CleanupSession) -> None:
            ok = self.engine.install_git_filter_repo(session)
            text = "git-filter-repo install succeeded." if ok else "git-filter-repo install failed. Check logs for details."
            self.root.after(0, lambda: self._write_summary(text))

        self._run_background("Installing git-filter-repo...", work)

    def create_backup(self) -> None:
        if not messagebox.askyesno(
            "Create Backup",
            "This will create a bundle, mirror clone, and worktree snapshots before any history rewrite.\n\nContinue?",
        ):
            return

        def work(session: CleanupSession) -> None:
            self.engine.create_backup(session)
            self.root.after(0, lambda: self._write_summary(f"Backup created in:\n{session.backup_dir}"))

        self._run_background("Creating safety backup...", work)

    def discover_paths(self) -> None:
        def work(session: CleanupSession) -> None:
            result = self.engine.discover_candidate_paths(session, self._custom_targets())
            matches = result.matches
            if matches:
                blocks: list[str] = []
                for detail in result.details[:12]:
                    commits = detail["commits"] if isinstance(detail["commits"], list) else []
                    blocks.append(f"Path: {detail['path']}")
                    blocks.append(f"Commits: {detail['commit_count']}")
                    for commit_info in commits[:3]:
                        if not isinstance(commit_info, dict):
                            continue
                        refs = ", ".join(commit_info.get("refs", [])) if isinstance(commit_info.get("refs"), list) else ""
                        blocks.append(
                            f"  - {commit_info.get('commit', '')[:12]} | {commit_info.get('committed_at', '')} | {commit_info.get('author', '')}"
                        )
                        blocks.append(f"    {commit_info.get('subject', '')}")
                        blocks.append(f"    Refs: {refs or '(no containing branch names reported)'}")
                    if int(detail["commit_count"]) > 3:
                        blocks.append(f"  ... and {int(detail['commit_count']) - 3} more commit(s)")
                    blocks.append("")
                preview = "\n".join(blocks).strip()
                if len(result.details) > 12:
                    preview += f"\n\n... and {len(result.details) - 12} more matched path(s)"
            else:
                preview = "No matching historical env-style or custom-target paths found."
            self.root.after(
                0,
                lambda: self._write_summary(
                    f"Discovered {len(matches)} candidate path(s).\n"
                    f"Custom targets: {', '.join(result.custom_targets) or '(none)'}\n\n"
                    f"Saved to:\n{session.paths_file}\n\nPreview:\n{preview}"
                ),
            )

        self._run_background("Discovering env-style and custom history paths...", work)

    def restore_from_backup(self) -> None:
        session = self._require_session()
        if session is None:
            return

        target = filedialog.askdirectory(
            title="Choose an empty folder for the restored clone",
            mustexist=False,
        )
        if not target:
            return

        destination = Path(target)
        if destination.exists() and any(destination.iterdir()):
            messagebox.showwarning("Restore Destination", "Choose an empty folder for the restored clone.")
            return

        if not messagebox.askyesno(
            "Restore Clone",
            "This will create a separate clone from the backup mirror or bundle.\n\nContinue?",
        ):
            return

        def work(active_session: CleanupSession) -> None:
            result = self.engine.restore_clone(active_session, destination)
            self.root.after(
                0,
                lambda: self._write_summary(
                    f"Restore clone created.\n\nSource type: {result.source_type}\nDestination: {result.destination}"
                ),
            )

        self._run_background("Creating restore clone from backup...", work)

    def rewrite_history(self) -> None:
        if not messagebox.askyesno(
            "Rewrite History",
            "This rewrites Git history in the selected repository.\n\nMake sure you created a backup first.\n\nContinue?",
        ):
            return

        confirm = simpledialog.askstring("Confirm Rewrite", "Type FILTER to confirm history rewrite:")
        if confirm != "FILTER":
            return

        def work(session: CleanupSession) -> None:
            self.engine.rewrite_history(session)
            self.root.after(0, lambda: self._write_summary("History rewrite finished. Run verification next."))

        self._run_background("Rewriting Git history...", work)

    def verify_cleanup(self) -> None:
        def work(session: CleanupSession) -> None:
            candidates, still_present = self.engine.verify_cleanup(session)
            if still_present:
                body = "\n".join(still_present[:50])
                text = (
                    f"Verification checked {len(candidates)} candidate path(s) in LOCAL history.\n\n"
                    f"Still present locally: {len(still_present)}\n{body}\n\n"
                    "The rewrite did not fully purge these. Re-run Step 5 (Rewrite History)."
                )
            else:
                text = (
                    f"Verification checked {len(candidates)} candidate path(s) in LOCAL history.\n\n"
                    "No candidate paths remain in local branches/tags.\n\n"
                    "Note: refs/remotes/* and the server still hold old history until\n"
                    "you run Step 7 (Force Push), which mirror-pushes and re-verifies the remote."
                )
            self.root.after(0, lambda: self._write_summary(text))

        self._run_background("Verifying cleanup...", work)

    def force_push(self) -> None:
        session = self._require_session()
        if session is None:
            return

        try:
            remotes = self.engine.remote_names(session)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Remote Error", str(exc))
            return

        if not remotes:
            messagebox.showwarning("No Remotes", "No Git remotes were found for this repository.")
            return

        default_remote = "origin" if "origin" in remotes else remotes[0]
        remote = simpledialog.askstring(
            "Choose Remote",
            f"Configured remotes: {', '.join(remotes)}\n\nRemote to force-push:",
            initialvalue=default_remote,
        )
        if not remote:
            return
        if remote not in remotes:
            messagebox.showwarning("Invalid Remote", f"Remote '{remote}' is not configured.")
            return

        if not messagebox.askyesno(
            "Force Push",
            f"This will run:\n\ngit push --force --mirror {remote}\n\nContinue?",
        ):
            return

        confirm = simpledialog.askstring("Confirm Push", "Type PUSH to confirm the force push:")
        if confirm != "PUSH":
            return

        def work(active_session: CleanupSession) -> None:
            candidates, still_present = self.engine.force_push(active_session, remote)
            if still_present:
                body = "\n".join(still_present[:50])
                text = (
                    f"Force push completed for remote '{remote}', but post-push\n"
                    f"verification still found {len(still_present)} of {len(candidates)} path(s) on the server:\n\n"
                    f"{body}\n\nCheck whether another remote or protected ref still holds old history."
                )
            else:
                text = (
                    f"Force push completed for remote '{remote}'.\n\n"
                    f"Post-push verification: none of the {len(candidates)} candidate path(s)\n"
                    "remain in local or remote-tracking history. The server is clean.\n\n"
                    "Tell collaborators to re-clone (or hard-reset), and rotate the exposed secrets."
                )
            self.root.after(0, lambda: self._write_summary(text))

        self._run_background("Force-pushing cleaned history...", work)

    def open_artifacts(self) -> None:
        session = self._require_session()
        if session is None:
            return
        self._open_folder(session.root)

    def open_backup_folder(self) -> None:
        session = self._require_session()
        if session is None:
            return
        self._open_folder(session.backup_dir)

    def _open_folder(self, path: Path) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Open Folder", str(exc))


def main() -> int:
    root = tk.Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    app = RepoCleanerApp(root)
    root.mainloop()
    return 0
