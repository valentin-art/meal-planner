"""Бэкап SQLite: снимок через backup API, а не cp на ходу.

Запуск в контейнере (cron на хосте):
    docker exec meal-planner meal-planner-backup
Копии — в <каталог БД>/backups (в образе /data/backups), хранятся последние
--keep штук. Восстановление — остановить контейнер и положить копию на место
mealplan.db (см. implementation-plan.md, «Бэкап»).
"""

import argparse
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

from meal_planner.db.repository import db_path_from_env

DEFAULT_KEEP = 14
PREFIX = "mealplan-"
SUFFIX = ".db"


class BackupError(Exception):
    pass


def backup(db_path: Path, dest_dir: Path, keep: int, now: datetime) -> Path:
    """Снимок db_path в dest_dir/mealplan-YYYYmmdd-HHMMSS.db; старше keep — удаляются."""
    if not db_path.is_file():
        # connect() создал бы пустую БД — бэкапить нечего, это ошибка настройки.
        raise BackupError(f"Нет файла БД: {db_path}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{PREFIX}{now:%Y%m%d-%H%M%S}{SUFFIX}"
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        # Источник только на чтение; backup() берёт согласованный снимок,
        # даже если приложение в этот момент пишет.
        with (
            closing(
                sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
            ) as src,
            closing(sqlite3.connect(tmp)) as dst,
        ):
            src.backup(dst)
            result = integrity(dst)
        if result != "ok":
            raise BackupError(f"Копия не прошла integrity_check: {result}")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    prune(dest_dir, keep)
    return target


def integrity(conn: sqlite3.Connection) -> str:
    (result,) = conn.execute("PRAGMA integrity_check").fetchone()
    return result


def prune(dest_dir: Path, keep: int) -> None:
    # Имена с меткой времени сортируются хронологически; чужие файлы не трогаем.
    copies = sorted(dest_dir.glob(f"{PREFIX}*{SUFFIX}"))
    for old in copies[: max(len(copies) - keep, 0)]:
        old.unlink()


def main(argv: list[str] | None = None) -> None:
    """Entry point `meal-planner-backup`."""
    db_path = db_path_from_env()
    parser = argparse.ArgumentParser(description="Бэкап SQLite meal-planner")
    parser.add_argument("--dir", type=Path, default=db_path.parent / "backups")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    args = parser.parse_args(argv)
    if args.keep < 1:
        parser.error("--keep должен быть ≥ 1")
    try:
        print(backup(db_path, args.dir, args.keep, datetime.now()))
    except (BackupError, sqlite3.Error) as e:
        sys.exit(f"meal-planner-backup: {e}")
