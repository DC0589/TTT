"""Database storage usage against the plan limit."""
import os

from django.conf import settings
from django.db import connection

WARN_PERCENT = 70
CRITICAL_PERCENT = 90


def human(size):
    size = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def _postgres_usage():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_database_size(current_database())")
        total = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT c.relname, pg_total_relation_size(c.oid), COALESCE(s.n_live_tup, 0)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
            WHERE c.relkind = 'r' AND n.nspname = current_schema()
            ORDER BY 2 DESC
            """
        )
        tables = [(name, int(size), int(rows)) for name, size, rows in cursor.fetchall()]
    return int(total), tables


def _sqlite_usage():
    path = connection.settings_dict["NAME"]
    total = os.path.getsize(path) if path and os.path.exists(str(path)) else 0
    tables = []
    with connection.cursor() as cursor:
        try:
            cursor.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name ORDER BY 2 DESC")
            sizes = dict(cursor.fetchall())
        except Exception:
            sizes = {}
        for name in connection.introspection.table_names(cursor):
            cursor.execute(f'SELECT COUNT(*) FROM "{name}"')
            tables.append((name, int(sizes.get(name, 0)), cursor.fetchone()[0]))
    tables.sort(key=lambda t: t[1], reverse=True)
    return total, tables


def database_usage():
    limit = settings.DATABASE_LIMIT_MB * 1024 * 1024
    total, tables = _postgres_usage() if connection.vendor == "postgresql" else _sqlite_usage()
    percent = round(100 * total / limit, 1) if limit else 0.0
    level = "critical" if percent >= CRITICAL_PERCENT else "warning" if percent >= WARN_PERCENT else "ok"
    return {
        "vendor": connection.vendor,
        "used": total, "used_label": human(total),
        "limit": limit, "limit_label": human(limit),
        "free": max(limit - total, 0), "free_label": human(max(limit - total, 0)),
        "percent": percent, "bar_percent": min(percent, 100), "level": level,
        "tables": [
            {
                "name": name, "size": size, "size_label": human(size), "rows": rows,
                "share": round(100 * size / total, 1) if total else 0.0,
            }
            for name, size, rows in tables[:15]
        ],
    }
