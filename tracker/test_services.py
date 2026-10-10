from unittest.mock import patch

from django.test import TestCase, override_settings

from tracker import tasks
from tracker.models import Company, PlacedStudent
from tracker.services.placements import placed_company_names


class CompanyDedupeTests(TestCase):
    def test_variants_share_one_company(self):
        a = PlacedStudent.objects.create(name="A", company="Deloitte")
        b = PlacedStudent.objects.create(name="B", company="deloitte Pvt Ltd")
        self.assertEqual(a.company_ref_id, b.company_ref_id)
        self.assertEqual(Company.objects.count(), 1)

    def test_placed_company_names_unique(self):
        PlacedStudent.objects.create(name="A", company="HCL")
        PlacedStudent.objects.create(name="B", company="hcl")
        self.assertEqual(len(placed_company_names()), 1)


class TaskRunnerTests(TestCase):
    def test_sync_runs_inline_and_swallows_errors(self):
        self.assertEqual(tasks.enqueue(lambda x: x + 1, 1).result(), 2)

        def boom():
            raise RuntimeError("x")

        self.assertIsNone(tasks.enqueue(boom).result())

    @override_settings(BACKGROUND_TASKS="thread")
    def test_thread_mode_returns_result(self):
        self.assertEqual(tasks.enqueue(lambda: 5).result(timeout=5), 5)


class StorageTests(TestCase):
    def test_usage_numbers_and_levels(self):
        from tracker.services import storage

        usage = storage.database_usage()
        self.assertEqual(usage["limit"], 1024 * 1024 * 1024)
        self.assertEqual(usage["free"], usage["limit"] - usage["used"])
        self.assertIn(usage["level"], {"ok", "warning", "critical"})
        mb = 1024 * 1024
        for used, level in ((100 * mb, "ok"), (800 * mb, "warning"), (1000 * mb, "critical")):
            with patch.object(storage, "_sqlite_usage", return_value=(used, [])):
                self.assertEqual(storage.database_usage()["level"], level)

    def test_page_is_admin_only(self):
        from django.urls import reverse

        from tracker.models import User

        admin = User.objects.create_user("sadm", password="pw-12345", is_admin=True)
        hr = User.objects.create_user("shr", password="pw-12345", is_hr=True, created_by=admin)
        student = User.objects.create_user("sstu", password="pw-12345", is_student=True)
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("storage_usage")), "Database storage")
        for other in (hr, student):
            self.client.force_login(other)
            self.assertEqual(self.client.get(reverse("storage_usage")).status_code, 403)
