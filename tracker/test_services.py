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
