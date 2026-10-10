"""Cold backup of the complete persistent directory; refuses an active runtime.

Stop the app after tasks finish, run with permission to read the data directory,
then restart the app. Backups contain private data; store off-server as well.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import sqlite3
import tarfile
from uuid import uuid4


def backup(data, destination):
    data = Path(data).resolve(strict=True)
    destination = Path(destination).resolve()
    if destination == data or data in destination.parents:
        raise ValueError('备份目录必须位于数据目录之外')
    database = data / 'resume.sqlite3'
    if not database.is_file():
        raise ValueError('没有找到 resume.sqlite3；请核对数据目录')
    with (data / 'runtime.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('服务仍在使用数据，请等任务结束并停止 app 后再备份') from exc
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('数据库完整性检查失败，未生成备份')
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = 'resume-data-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8] + '.tar.gz'
        target = destination / name
        partial = destination / (name + '.partial')
        try:
            with open(partial, 'xb', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
                with tarfile.open(fileobj=stream, mode='w:gz', dereference=False) as archive:
                    for child in sorted(data.iterdir()):
                        if child.name != 'runtime.lock':
                            archive.add(child, arcname='data/' + child.name, recursive=True)
                stream.flush()
                os.fsync(stream.fileno())
            partial.rename(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        print(backup(args.data, args.output))
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        raise SystemExit(str(exc))


if __name__ == '__main__':
    main()
