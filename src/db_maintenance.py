from __future__ import annotations
import json
import sqlite3
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

def backup_databases(root: Path, out_dir: Path) -> Path:
    root, out_dir = Path(root), Path(out_dir)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    dest = out_dir / stamp
    dest.mkdir(parents=True, exist_ok=False)
    manifest = {'created_at': stamp, 'databases': {}}
    for name in DB_NAMES:
        src = root / 'db' / f'{name}.db'
        if not src.exists():
            raise FileNotFoundError(src)
        check = integrity_check(src)
        if check.lower() != 'ok':
            raise RuntimeError(f'DB_BACKUP_FAILED: {src} integrity={check}')
        dst = dest / f'{name}.db'
        _copy_sqlite(src, dst)
        manifest['databases'][name] = {'path': dst.name, 'integrity_check': integrity_check(dst)}
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return dest

def restore_databases(root: Path, backup_dir: Path) -> None:
    root, backup_dir = Path(root), Path(backup_dir)
    for name in DB_NAMES:
        src = backup_dir / f'{name}.db'
        if not src.exists():
            raise FileNotFoundError(src)
        check = integrity_check(src)
        if check.lower() != 'ok':
            raise RuntimeError(f'DB_RESTORE_FAILED: {src} integrity={check}')
    for name in DB_NAMES:
        dst = root / 'db' / f'{name}.db'
        tmp = dst.with_suffix('.restore.tmp')
        tmp.unlink(missing_ok=True)
        _copy_sqlite(backup_dir / f'{name}.db', tmp)
        tmp.replace(dst)

def migrate_databases(root: Path) -> dict[str, str]:
    # The unified release has a fixed schema. Initialization is idempotent; migration
    # verifies all existing stores before normal startup instead of silently altering data.
    root = Path(root)
    results: dict[str, str] = {}
    for name in DB_NAMES:
        path = root / 'db' / f'{name}.db'
        if not path.exists():
            results[name] = 'missing'
            continue
        results[name] = integrity_check(path)
        if results[name].lower() != 'ok':
            raise RuntimeError(f'DB_MIGRATION_FAILED: {path} integrity={results[name]}')
    return results
