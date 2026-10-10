from django.core.management.base import BaseCommand, CommandError

from tracker.models import PlacedStudent
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
        existing = {
            (n.lower(), c.lower())
            for n, c in PlacedStudent.objects.values_list("name", "company")
        }
        created = []
        for entry in entries:
            key = (entry["name"].lower(), entry["company"].lower())
            if key not in existing:
                existing.add(key)
                created.append(PlacedStudent(**entry))
        PlacedStudent.objects.bulk_create(created)
        self.stdout.write(self.style.SUCCESS(
            f"Imported {len(created)} placement(s); skipped {len(entries) - len(created)} duplicate(s)."
        ))
