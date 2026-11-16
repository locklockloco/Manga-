"""Durable local job and review storage.

SQLite is authoritative; JSON/CSV reports are rebuildable projections. Each
manifest commit points at files which orchestration has already staged under
immutable revision names. The database keeps previous snapshots for undo/redo;
old referenced images must therefore not be removed by projection cleanup.

Connections are short-lived, use WAL and synchronous=FULL, and never span image
processing. A separate OS advisory lock protects chapter-wide writers without
blocking ordinary previews or pause/cancel requests. The lock file may remain on
disk after a crash: only the kernel lock determines whether a writer is active.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any

WORKSPACE_VERSION = 3
SCHEMA_VERSION = 1


class WriterBusy(ValueError):
    """Another process currently owns this chapter's writer lock."""


class JobPaused(RuntimeError):
    """An acknowledged pause. Completed checkpoints remain available."""


class JobCancelled(RuntimeError):
    """An acknowledged cancellation. Completed checkpoints remain available."""


class ResourceLimitError(ValueError):
    """A preflight estimate exceeds available disk or memory."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode(value: Any) -> str:
    # Refuse NaN/Infinity: these cannot be faithfully projected into JSON reports.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _decode(value: str | None, default=None):
    return json.loads(value) if value is not None else default


def resource_usage(root: str | Path) -> dict:
    """Best-effort observations; unknown values stay None, never fabricated."""
    root = Path(root).expanduser().resolve()
    existing = root
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    disk = shutil.disk_usage(existing)
    available = None
    rss = None
    try:
        if Path('/proc/meminfo').exists():
            for line in Path('/proc/meminfo').read_text().splitlines():
                if line.startswith('MemAvailable:'):
                    available = int(line.split()[1]) * 1024
                    break
        elif os.name == 'nt':
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                            ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                            ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                            ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                            ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
            mem = MEMORYSTATUSEX()
            mem.dwLength = ctypes.sizeof(mem)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
                available = int(mem.ullAvailPhys)
        else:
            available = int(os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE'))
    except (OSError, ValueError, AttributeError):
        pass
    # Respect container/cgroup limits, where host MemAvailable is misleading.
    try:
        maximum = Path('/sys/fs/cgroup/memory.max').read_text().strip()
        current = int(Path('/sys/fs/cgroup/memory.current').read_text())
        if maximum != 'max':
            cgroup_available = max(0, int(maximum) - current)
            available = cgroup_available if available is None else min(available, cgroup_available)
    except (OSError, ValueError):
        pass
    try:
        if Path('/proc/self/statm').exists():
            rss = int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
        else:
            import resource
            import sys
            maximum_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss = int(maximum_rss * (1 if sys.platform == 'darwin' else 1024))
    except (ImportError, OSError, ValueError, AttributeError):
        pass
    peak_rss=None
    try:
        import resource,sys
        peak_rss=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024))
    except (ImportError,ValueError,OSError,AttributeError):
        pass
    return {'disk_free_bytes': disk.free, 'disk_total_bytes': disk.total,
            'available_memory_bytes': available, 'process_memory_bytes': rss,
            'peak_process_memory_bytes':peak_rss, 'observed_at': utc_now()}


def preflight(root: str | Path, estimated_output_bytes: int = 0,
              estimated_memory_bytes: int = 0, reserve_bytes: int = 64 * 1024 * 1024,
              max_memory_bytes: int | None = None, min_free_bytes: int | None = None) -> dict:
    """Check estimates without allocating large buffers or deleting any files.

    ``max_memory_bytes`` is an optional application budget in addition to observed
    available memory. Unknown memory availability is reported, not treated as zero.
    Disk estimate should include retained revisions and staging requirements.
    """
    for value in (estimated_output_bytes, estimated_memory_bytes, reserve_bytes):
        if int(value) != value or value < 0:
            raise ValueError('Resource estimates must be nonnegative integer byte counts')
    observed = resource_usage(root)
    required_disk = int(estimated_output_bytes) + max(int(reserve_bytes), int(min_free_bytes or 0))
    if observed['disk_free_bytes'] < required_disk:
        raise ResourceLimitError(f"Insufficient disk space: {observed['disk_free_bytes']} available bytes, {required_disk} estimated including reserve")
    available = observed['available_memory_bytes']
    if max_memory_bytes is not None:
        if max_memory_bytes < 0:
            raise ValueError('Memory budget must be nonnegative')
        available = max_memory_bytes if available is None else min(available, max_memory_bytes)
    if available is not None and estimated_memory_bytes > available:
        raise ResourceLimitError(f'Insufficient memory budget: {available} available bytes, {estimated_memory_bytes} estimated')
    return {**observed, 'estimated_output_bytes': int(estimated_output_bytes),
            'estimated_memory_bytes': int(estimated_memory_bytes), 'required_disk_bytes': required_disk,
            'memory_budget_bytes': available, 'passed': True}


@contextmanager
def writer_lock(root: str | Path, blocking: bool = False):
    """Hold one cross-process writer lock, automatically released on termination.

    The descriptor stays open throughout the context. Never unlink the lock file:
    doing so would allow a competing writer to lock a different inode. PID text is
    diagnostic only and cannot keep a crashed chapter permanently locked.
    """
    folder = Path(root).expanduser().resolve() / 'review'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'writer.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    try:
        if os.name == 'nt':
            import msvcrt
            # Windows byte-range locks require at least one byte in the file.
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'\0')
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise WriterBusy('Another writer is active for this chapter') from exc
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise WriterBusy('Another writer is active for this chapter') from exc
        acquired = True
        # Keep Windows' first lock byte intact while replacing diagnostics.
        details = _encode({'pid': os.getpid(), 'started_at': utc_now(), 'workspace_version': WORKSPACE_VERSION}).encode()
        os.lseek(fd, 1, os.SEEK_SET)
        os.write(fd, details)
        os.ftruncate(fd, 1 + len(details))
        os.fsync(fd)
        yield fd
    finally:
        if acquired:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class Store:
    """One durable chapter state, with no long-lived SQLite connection."""
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        folder = self.root / 'review'
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / 'state.sqlite3'
        with self._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER REFERENCES snapshots(id),
                    operation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    manifest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS items (
                    kind TEXT NOT NULL,
                    id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    PRIMARY KEY(kind,id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event TEXT NOT NULL,
                    details TEXT NOT NULL
                );
            ''')
            db.execute('BEGIN IMMEDIATE')
            existing_schema = self._get(db, 'schema_version')
            if existing_schema not in (None, SCHEMA_VERSION):
                raise ValueError(f'Unsupported state database schema: {existing_schema}')
            self._put(db, 'schema_version', SCHEMA_VERSION)
            if self._get(db, 'job') is None:
                self._put(db, 'job', {'status': 'NEW', 'stage': None, 'created_at': utc_now(),
                                      'workspace_version': WORKSPACE_VERSION, 'pid': None,
                                      'algorithm_version': None, 'config': {}, 'current': 0, 'total': 0,
                                      'skipped': 0, 'warnings': [], 'error': None})
            if self._get(db, 'control') is None:
                self._put(db, 'control', 'run')
            if self._get(db, 'redo') is None:
                self._put(db, 'redo', [])
            db.commit()

    @contextmanager
    def _connection(self, write: bool = False):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA busy_timeout=15000')
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db, key, default=None):
        row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return _decode(row[0], default) if row else default

    @staticmethod
    def _put(db, key, value):
        db.execute('INSERT INTO state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (key, _encode(value)))

    @staticmethod
    def _event(db, name, details):
        db.execute('INSERT INTO events(created_at,event,details) VALUES (?,?,?)', (utc_now(), name, _encode(details)))

    def _next_id(self, db):
        value = int(self._get(db, 'commit_sequence', 0)) + 1
        self._put(db, 'commit_sequence', value)
        return value

    def _snapshot(self, db, manifest, operation, parent):
        data = _encode(manifest)
        checksum = hashlib.sha256(data.encode()).hexdigest()
        commit_id = self._next_id(db)
        db.execute('INSERT INTO snapshots(id,parent_id,operation,created_at,checksum,manifest) VALUES (?,?,?,?,?,?)',
                   (commit_id, parent, operation, utc_now(), checksum, data))
        return commit_id

    def _validate_files(self, manifest):
        paths = set()
        for source in manifest.get('sources', []):
            if source.get('path'):
                paths.add(source['path'])
        for panel in manifest.get('panels', []):
            for key in ('file', 'reconstructed_file'):
                if panel.get(key):
                    paths.add(panel[key])
        for rel in sorted(paths):
            path = (self.root / rel).resolve()
            if not path.is_relative_to(self.root):
                raise ValueError(f'Manifest references a path outside the chapter: {rel}')
            if not path.is_file():
                raise ValueError(f'Manifest references an incomplete/missing file: {rel}')
            with path.open('rb') as handle:
                os.fsync(handle.fileno())
        # Fsync containing directories on platforms that support directory fds.
        if os.name != 'nt':
            directories = {self.root}
            directories.update((self.root / rel).resolve().parent for rel in paths)
            for folder in directories:
                fd = os.open(folder, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)

    def load_manifest(self) -> dict | None:
        with self._connection() as db:
            current = self._get(db, 'current_manifest')
            if current is not None:
                if hashlib.sha256(_encode(current).encode()).hexdigest() != self._get(db, 'current_checksum'):
                    raise ValueError('Durable manifest checksum mismatch')
                return current
            head = self._get(db, 'head')
            if head is None:
                return None
            row = db.execute('SELECT manifest,checksum FROM snapshots WHERE id=?', (head,)).fetchone()
            if row is None:
                raise ValueError('Durable manifest head is missing; database needs recovery')
            if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
                raise ValueError('Durable manifest checksum mismatch')
            return json.loads(row[0])

    def commit_manifest(self, m: dict, operation: str = 'manifest_commit', before: dict | None = None) -> int:
        # Validation/fsync precede BEGIN IMMEDIATE; readers and control requests
        # remain usable while large output sets are flushed to disk.
        _encode(m)
        self._validate_files(m)
        if before is not None:
            self._validate_files(before)
        with self._connection(write=True) as db:
            parent = self._get(db, 'head')
            if operation == 'checkpoint':
                commit_id = self._next_id(db)
            else:
                if parent is None and before is not None:
                    parent = self._snapshot(db, before, 'import_existing_baseline', None)
                commit_id = self._snapshot(db, m, operation, parent)
                self._put(db, 'head', commit_id)
                self._put(db, 'redo', [])
            self._put(db, 'current_manifest', m)
            self._put(db, 'current_checksum', hashlib.sha256(_encode(m).encode()).hexdigest())
            self._put(db, 'current_commit', commit_id)
            self._event(db, 'commit', {'commit_id': commit_id, 'operation': operation})
        return commit_id

    def undo(self) -> dict:
        with self._connection(write=True) as db:
            head = self._get(db, 'head')
            row = db.execute('SELECT parent_id FROM snapshots WHERE id=?', (head,)).fetchone() if head else None
            if not row or row[0] is None:
                raise ValueError('No previous review state to undo')
            redo = self._get(db, 'redo', [])
            redo.append(head)
            self._put(db, 'redo', redo)
            self._put(db, 'head', row[0])
            self._event(db, 'undo', {'from': head, 'to': row[0]})
            result = db.execute('SELECT manifest FROM snapshots WHERE id=?', (row[0],)).fetchone()[0]
            self._put(db, 'current_manifest', json.loads(result))
            self._put(db, 'current_checksum', hashlib.sha256(result.encode()).hexdigest())
            self._put(db, 'current_commit', row[0])
        return json.loads(result)

    def redo(self) -> dict:
        with self._connection(write=True) as db:
            redo = self._get(db, 'redo', [])
            if not redo:
                raise ValueError('No review state to redo')
            head = self._get(db, 'head')
            target = redo.pop()
            row = db.execute('SELECT parent_id,manifest FROM snapshots WHERE id=?', (target,)).fetchone()
            if not row or row[0] != head:
                raise ValueError('Redo history no longer belongs to the current state')
            self._put(db, 'head', target)
            self._put(db, 'redo', redo)
            self._event(db, 'redo', {'from': head, 'to': target})
            self._put(db, 'current_manifest', json.loads(row[1]))
            self._put(db, 'current_checksum', hashlib.sha256(row[1].encode()).hexdigest())
            self._put(db, 'current_commit', target)
        return json.loads(row[1])

    def snapshots(self) -> list[dict]:
        with self._connection() as db:
            head = self._get(db, 'head')
            rows = db.execute('SELECT id,parent_id,operation,created_at,checksum FROM snapshots ORDER BY id').fetchall()
        return [dict(id=r[0], parent_id=r[1], operation=r[2], created_at=r[3], checksum=r[4], current=r[0] == head) for r in rows]

    def referenced_files(self) -> set[str]:
        """All historical references, including undo states and job checkpoints."""
        result = set()
        with self._connection() as db:
            manifests = [json.loads(r[0]) for r in db.execute('SELECT manifest FROM snapshots')]
            current = self._get(db, 'current_manifest')
            if current is not None:
                manifests.append(current)
            metadata = [json.loads(r[0]) for r in db.execute('SELECT metadata FROM items')]
        for m in manifests:
            result.update(s['path'] for s in m.get('sources', []) if s.get('path'))
            for p in m.get('panels', []):
                result.update(p[k] for k in ('file', 'reconstructed_file') if p.get(k))
        for item in metadata:
            for value in (item, item.get('panel', {}), item.get('source', {})):
                for key in ('file', 'path', 'reconstructed_file'):
                    if isinstance(value, dict) and isinstance(value.get(key), str):
                        result.add(value[key])
        return result

    def job(self) -> dict:
        with self._connection() as db:
            result = self._get(db, 'job', {})
        return result

    def update_job(self, **fields) -> dict:
        with self._connection(write=True) as db:
            result = self._get(db, 'job', {})
            result.update(fields)
            result['updated_at'] = utc_now()
            result['workspace_version'] = WORKSPACE_VERSION
            self._put(db, 'job', result)
        return result

    def item(self, kind: str, id: str, status: str, **metadata) -> dict:
        if not kind or not id:
            raise ValueError('Checkpoint kind and ID cannot be empty')
        with self._connection(write=True) as db:
            old = db.execute('SELECT metadata FROM items WHERE kind=? AND id=?', (kind, id)).fetchone()
            details = _decode(old[0], {}) if old else {}
            details.update(metadata)
            at = utc_now()
            db.execute('INSERT INTO items(kind,id,status,updated_at,metadata) VALUES (?,?,?,?,?) '
                       'ON CONFLICT(kind,id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at,metadata=excluded.metadata',
                       (kind, id, status, at, _encode(details)))
        return {**details, 'kind': kind, 'id': id, 'status': status, 'updated_at': at}

    def items(self, kind: str) -> list[dict]:
        with self._connection() as db:
            rows = db.execute('SELECT id,status,updated_at,metadata FROM items WHERE kind=? ORDER BY id', (kind,)).fetchall()
        return [{**json.loads(row[3]), 'kind': kind, 'id': row[0], 'status': row[1], 'updated_at': row[2]} for row in rows]

    def request_control(self, action: str) -> dict:
        if action not in {'pause', 'cancel', 'resume'}:
            raise ValueError('Control action must be pause, cancel, or resume')
        value = 'run' if action == 'resume' else action
        with self._connection(write=True) as db:
            self._put(db, 'control', value)
            self._event(db, 'control', {'action': action})
            job = self._get(db, 'job', {})
            job.update(control=value, control_requested_at=utc_now())
            if action == 'resume':
                job['status'] = 'RESUMING'
                job['error'] = None
            self._put(db, 'job', job)
        return job

    def control(self) -> str:
        with self._connection() as db:
            return self._get(db, 'control', 'run')

    def checkpoint(self, stage: str, current: int, total: int, **info) -> dict:
        if any(isinstance(value, bool) or int(value) != value for value in (current, total)) or current < 0 or total < 0 or current > total:
            raise ValueError('Checkpoint counts must be integers satisfying 0 <= current <= total')
        observations = resource_usage(self.root)
        with self._connection(write=True) as db:
            job = self._get(db, 'job', {})
            control = self._get(db, 'control', 'run')
            at = utc_now()
            job.update(info)
            job.update(stage=stage, current=int(current), total=int(total), pid=os.getpid(), updated_at=at,
                       workspace_version=WORKSPACE_VERSION, resources=observations)
            job.setdefault('started_at', at)
            try:
                job['elapsed_seconds'] = max(0, (datetime.now(timezone.utc)-datetime.fromisoformat(job['started_at'])).total_seconds())
            except (ValueError, TypeError):
                job['elapsed_seconds'] = None
            job['status'] = 'PAUSED' if control == 'pause' else 'CANCELLED' if control == 'cancel' else 'RUNNING'
            self._put(db, 'job', job)
        if control == 'pause':
            raise JobPaused(f'Job paused at {stage}: {current}/{total}')
        if control == 'cancel':
            raise JobCancelled(f'Job cancelled at {stage}: {current}/{total}')
        return job
