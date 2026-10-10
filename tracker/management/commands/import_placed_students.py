from django.core.management.base import BaseCommand, CommandError

from tracker.services.placements import import_placements
from tracker.placed_import import parse_placements, read_csv_rows, read_xlsx_rows


class Command(BaseCommand):
    help = "Import placed students (Name, Batch, Company, Year) from an .xlsx or .csv file."

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, **options):
        path = options["path"]
        try:
            with open(path, "rb") as f:
                rows = read_csv_rows(f) if path.lower().endswith(".csv") else read_xlsx_rows(f)
        except (OSError, ValueError, KeyError) as exc:
            raise CommandError(f"Could not read {path}: {exc}")
        entries, error = parse_placements(rows)
        if error:
            raise CommandError(error)
        created, skipped = import_placements(entries)
        self.stdout.write(self.style.SUCCESS(
            f"Imported {created} placement(s); skipped {skipped} duplicate(s)."
        ))
