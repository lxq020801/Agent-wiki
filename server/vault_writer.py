"""Cross-process serialization for writes to one knowledge vault.

Vault transactions manage Markdown and related asset files only. Git history is
owned by the user or an external backup tool and is deliberately outside this
module's responsibilities.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import tempfile
import threading
from pathlib import Path
from typing import Iterator


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
VAULT_GIT_STATUS = "not_managed"


def _thread_lock(key: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextlib.contextmanager
def vault_write_transaction(vault_path: Path | str) -> Iterator[None]:
    """Serialize read-modify-write sequences across workers and entrypoints."""
    resolved = str(Path(vault_path).expanduser().resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()
    lock_root = Path(tempfile.gettempdir()) / f"agent-wiki-vault-locks-{os.getuid()}"
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(lock_root, 0o700)
    lock_path = lock_root / f"{digest}.lock"

    with _thread_lock(resolved):
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
