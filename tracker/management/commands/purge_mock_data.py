from django.core.management.base import BaseCommand

from tracker.retention import purge_old_mock_data


class Command(BaseCommand):
    help = "Delete detailed mock-interview integrity logs older than MOCK_RETENTION_DAYS."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None)

    def handle(self, *args, **options):
        count = purge_old_mock_data(options["days"])
        self.stdout.write(f"Purged integrity logs from {count} session(s).")
