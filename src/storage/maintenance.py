from __future__ import annotations
import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_NAMES = ('playlist', 'songs', 'reels')

def integrity_check(path: Path) -> str:
    with sqlite3.connect(path) as c:
        row = c.execute('PRAGMA integrity_check').fetchone()
    return str(row[0]) if row else 'unknown'

def _copy_sqlite(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(dst)
    try:
        src_conn.backup(dst_conn)
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()

def backup_databases(root: Path, out_dir: Path, db_dir: Path | None = None) -> Path:
    """Create a complete consistent backup, or leave no partial backup behind."""
    root, out_dir = Path(root), Path(out_dir)
    source_db_dir = Path(db_dir) if db_dir is not None else root / 'db'
    sources = {name: source_db_dir / f'{name}.db' for name in DB_NAMES}
    # Preflight the full database set before creating a destination directory.
    for name, src in sources.items():
        if not src.is_file():
            raise FileNotFoundError(src)
        check = integrity_check(src)
        if check.lower() != 'ok':
            raise RuntimeError(f'DB_BACKUP_FAILED: {src} integrity={check}')

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    dest = out_dir / stamp
    dest.mkdir(parents=True, exist_ok=False)
    manifest = {'created_at': stamp, 'databases': {}}
    try:
        for name, src in sources.items():
            dst = dest / f'{name}.db'
            _copy_sqlite(src, dst)
            copied_check = integrity_check(dst)
            if copied_check.lower() != 'ok':
                raise RuntimeError(f'DB_BACKUP_FAILED: copied database {dst} integrity={copied_check}')
            manifest['databases'][name] = {'path': dst.name, 'integrity_check': copied_check}
        temp_manifest = dest / 'manifest.json.tmp'
        temp_manifest.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        temp_manifest.replace(dest / 'manifest.json')
        return dest
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise

def restore_databases(root: Path, backup_dir: Path, db_dir: Path | None = None) -> None:
    """Validate and stage every DB before replacing any configured target DB."""
    root, backup_dir = Path(root), Path(backup_dir)
    target_db_dir = Path(db_dir) if db_dir is not None else root / 'db'
    sources = {name: backup_dir / f'{name}.db' for name in DB_NAMES}
    for name, src in sources.items():
        if not src.is_file():
            raise FileNotFoundError(src)
        check = integrity_check(src)
        if check.lower() != 'ok':
            raise RuntimeError(f'DB_RESTORE_FAILED: {src} integrity={check}')

    target_db_dir.mkdir(parents=True, exist_ok=True)
    transaction_dir = target_db_dir / f'.restore-{uuid.uuid4().hex}'
    transaction_dir.mkdir(parents=False, exist_ok=False)
    staged = {name: transaction_dir / f'{name}.db' for name in DB_NAMES}
    originals = {name: transaction_dir / f'{name}.previous.db' for name in DB_NAMES}
    replaced: list[str] = []
    try:
        # Stage and validate all new files on the same filesystem as the targets.
        for name, src in sources.items():
            _copy_sqlite(src, staged[name])
            check = integrity_check(staged[name])
            if check.lower() != 'ok':
                raise RuntimeError(f'DB_RESTORE_FAILED: staged database {staged[name]} integrity={check}')

        # Retain previous files in the transaction folder so a filesystem error can
        # roll back any already-replaced database rather than leaving a mixed set.
        import os
        for name in DB_NAMES:
            target = target_db_dir / f'{name}.db'
            if target.exists():
                os.replace(target, originals[name])
            os.replace(staged[name], target)
            replaced.append(name)
    except Exception:
        import os
        for name in reversed(DB_NAMES):
            target = target_db_dir / f'{name}.db'
            previous = originals[name]
            if name in replaced:
                target.unlink(missing_ok=True)
            if previous.exists():
                os.replace(previous, target)
        raise
    finally:
        shutil.rmtree(transaction_dir, ignore_errors=True)

def migrate_databases(root: Path, db_dir: Path | None = None) -> dict[str, str]:
    # This operation intentionally performs an integrity verification only. It is not
    # a schema migration; versioned migrations must be explicit and data-preserving.
    root = Path(root)
    source_db_dir = Path(db_dir) if db_dir is not None else root / 'db'
    results: dict[str, str] = {}
    for name in DB_NAMES:
        path = source_db_dir / f'{name}.db'
        if not path.exists():
            results[name] = 'missing'
            continue
        results[name] = integrity_check(path)
        if results[name].lower() != 'ok':
            raise RuntimeError(f'DB_MIGRATION_FAILED: {path} integrity={results[name]}')
    return results
