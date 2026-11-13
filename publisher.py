import fcntl
import logging
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("publisher")


class PublishError(RuntimeError):
    pass


class Publisher:
    def __init__(
        self,
        state_dir: Path,
        remote: str,
        branch: str = "master",
        lock_path: Path | None = None,
    ):
        self.state_dir = Path(state_dir)
        self.remote = remote
        self.branch = branch
        self.lock_path = Path(lock_path or self.state_dir.parent / "publish.lock")
        self.identity: list[str] = []
        name, email = os.getenv("SG_GIT_USER_NAME"), os.getenv("SG_GIT_USER_EMAIL")
        if name:
            self.identity += ["-c", f"user.name={name}"]
        if email:
            self.identity += ["-c", f"user.email={email}"]

    # -- git helpers -------------------------------------------------------

    def git(
        self,
        *args: str,
        check: bool = True,
        stdin: str | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess:
        cmd = ["git", *self.identity, *args]
        r = subprocess.run(
            cmd, cwd=cwd or self.state_dir, input=stdin, capture_output=True, text=True
        )
        if check and r.returncode != 0:
            raise PublishError(
                f"{' '.join(cmd)} failed ({r.returncode}): {r.stderr.strip()}"
            )
        return r

    @contextmanager
    def locked(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def ensure_clone(self) -> None:
        with self.locked():
            if (self.state_dir / ".git").exists():
                return
            partial = self.state_dir.with_name(self.state_dir.name + ".partial")
            shutil.rmtree(partial, ignore_errors=True)
            partial.parent.mkdir(parents=True, exist_ok=True)
            log.info("cloning %s into %s", self.remote, self.state_dir)
            # Blobless: full commit history, file contents fetched on demand.
            self.git(
                "clone",
                "--filter=blob:none",
                "--single-branch",
                "--branch",
                self.branch,
                self.remote,
                str(partial),
                cwd=partial.parent,
            )
            partial.rename(self.state_dir)

    def _sync(self) -> None:
        """Make the clone exactly match the remote branch. Call under lock."""
        self.git("fetch", "--prune", "origin", self.branch)
        self.git("reset", "--hard", f"origin/{self.branch}")
        self.git("clean", "-ffd")

    # -- public API --------------------------------------------------------

    def snapshot(self, paths: tuple[str, ...] | list[str], dest: Path) -> None:
        """Copy the latest published version of ``paths`` into ``dest``."""
        dest.mkdir(parents=True, exist_ok=True)
        if not paths:
            return
        with self.locked():
            self._sync()
            for rel in paths:
                src = self.state_dir / rel
                dst = dest / rel
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                elif src.is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)

    def publish(
        self,
        job_name: str,
        owns: tuple[str, ...],
        src_dir: Path,
        message: str,
        attempts: int = 5,
    ) -> str | None:
        """Commit and push the files under ``src_dir``.

        Returns the new commit SHA, or None if nothing changed.
        """
        rels = sorted(
            p.relative_to(src_dir).as_posix() for p in src_dir.rglob("*") if p.is_file()
        )
        outside = [r for r in rels if not any(r.startswith(o) for o in owns)]
        if outside:
            raise PublishError(
                f"{job_name} wrote outside its paths {owns}: {outside[:10]}"
            )
        if not rels:
            log.info("%s: no output files", job_name)
            return None

        with self.locked():
            for attempt in range(1, attempts + 1):
                self._sync()
                for rel in rels:
                    dst = self.state_dir / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src_dir / rel, dst)
                self.git("add", "--pathspec-from-file=-", stdin="\n".join(rels) + "\n")
                if self.git("diff", "--cached", "--quiet", check=False).returncode == 0:
                    log.info(
                        "%s: output identical to published data; nothing to commit",
                        job_name,
                    )
                    self.git("reset", "--hard", "HEAD")
                    return None
                self.git("commit", "--quiet", "-m", message)
                push = self.git(
                    "push", "origin", f"HEAD:refs/heads/{self.branch}", check=False
                )
                if push.returncode == 0:
                    sha = self.git("rev-parse", "HEAD").stdout.strip()
                    log.info("%s: pushed %s (%d files)", job_name, sha[:10], len(rels))
                    return sha
                log.warning(
                    "%s: push rejected (attempt %d/%d): %s",
                    job_name,
                    attempt,
                    attempts,
                    push.stderr.strip(),
                )
                if attempt < attempts:
                    time.sleep(min(2**attempt, 30))
            raise PublishError(f"{job_name}: push failed after {attempts} attempts")
