import logging
import os

from config.wsgi import application

app = application

logger = logging.getLogger(__name__)


def _migrate_on_start():
    if os.environ.get("AUTO_MIGRATE", "1") != "1":
        return
    from django.core.management import call_command
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            locked = connection.vendor == "postgresql"
            if locked:
                cursor.execute("SELECT pg_advisory_lock(727274)")
            try:
                call_command("migrate", interactive=False, verbosity=0)
            finally:
                if locked:
                    cursor.execute("SELECT pg_advisory_unlock(727274)")
    except Exception:
        logger.exception("Automatic migration failed")


_migrate_on_start()
