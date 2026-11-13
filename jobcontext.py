"""Per-run context handed to every job.

A job reads the previous published state from ``input_dir`` and writes its
results to ``output_dir``. Both use the data-repo layout (``feed/``, ``geoip/``,
``bgp/``, ...). Scratch files go to ``tmp_dir`` and are never published.

Under the scheduler, ``input_dir`` is a private snapshot of the data repo and
``output_dir`` is a private, initially empty folder that the publisher copies
into the data repo afterwards. In legacy mode (``run.py`` in GitHub Actions)
both point at the same ``DATA_DIR`` checkout, which reproduces the old
write-in-place behaviour.
"""

import hashlib
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

DATA_REPO_RAW_BASE = os.getenv(
    "SG_DATA_RAW_BASE",
    "https://raw.githubusercontent.com/clarkzjw/starlink-geoip-data",
)
DEFAULT_REF = "refs/heads/master"


@dataclass(frozen=True)
class JobContext:
    input_dir: Path
    output_dir: Path
    tmp_dir: Path
    now: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    # Git ref used for raw.githubusercontent.com reads. The scheduler passes
    # the commit SHA that triggered the run so reads never hit a stale CDN copy.
    ref: str = DEFAULT_REF

    @property
    def dt_string(self) -> str:
        return self.now.strftime("%Y%m%d-%H%M")

    @property
    def year_month(self) -> str:
        return self.now.strftime("%Y%m")

    def inp(self, rel: str) -> Path:
        """Path of a previous-state file (may not exist)."""
        return self.input_dir / rel

    def out(self, rel: str) -> Path:
        """Path to write a result file; parent folders are created."""
        path = self.output_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def out_folder(self, rel: str) -> Path:
        """Folder for results under output_dir; created if missing."""
        path = self.output_dir / rel
        path.mkdir(parents=True, exist_ok=True)
        return path

    def tmp(self, name: str) -> Path:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        return self.tmp_dir / name

    def raw_url(self, rel: str) -> str:
        return f"{DATA_REPO_RAW_BASE}/{self.ref}/{rel}"

    def fetch_raw(self, rel: str, attempts: int = 6, delay: float = 10.0) -> bytes:
        """GET a published data-repo file at ``self.ref``.

        Retries on errors: right after a push, a SHA URL can 404 for a moment.
        """
        url = self.raw_url(rel)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                r = httpx.get(url, timeout=60, follow_redirects=True)
                r.raise_for_status()
                return r.content
            except httpx.HTTPError as e:
                last_error = e
                print(f"GET {url} failed (attempt {attempt}/{attempts}): {e}")
                if attempt < attempts:
                    time.sleep(delay)
        raise RuntimeError(f"Could not fetch {url}") from last_error

    @classmethod
    def legacy(cls, data_dir: str | None = None, now: datetime | None = None):
        """Context for run.py: read and write one DATA_DIR checkout in place."""
        d = Path(data_dir or os.getenv("DATA_DIR", "./starlink-geoip-data"))
        return cls(
            input_dir=d,
            output_dir=d,
            tmp_dir=Path(tempfile.mkdtemp(prefix="starlink-geoip-")),
            now=now or datetime.now(tz=timezone.utc),
        )


def md5_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.md5(path.read_bytes()).hexdigest()
