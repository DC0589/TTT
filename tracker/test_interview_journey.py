from datetime import date

from django.test import TestCase
from django.urls import reverse

from .models import Group, GroupMembership, Interview, InterviewRound, InterviewStatus, User


class InterviewJourneyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("journey-admin", is_admin=True)
        cls.student = User.objects.create_user("journey-student", is_student=True)
        cls.other = User.objects.create_user("journey-other", is_student=True)
        cls.group = Group.objects.create(name="Journey batch", admin=cls.admin)
        GroupMembership.objects.create(group=cls.group, student=cls.student)

    def setUp(self):
        self.client.force_login(self.student)
        self.data = {
            "group": self.group.pk, "company_name": "Example", "role": "Developer",
            "date_of_interview": "2026-01-02", "first_round_type": "other",
            "first_round_description": "Client round", "round_count": "3",
            "round_2_type": "other", "round_2_description": "Casual manager discussion",
            "round_3_type": "",
        }

    def create_interview(self):
        response = self.client.post(reverse("student_interview_add"), self.data)
        self.assertEqual(response.status_code, 302)
        return Interview.objects.get(student=self.student)

    def test_create_planned_rounds_with_custom_names_and_render_progress(self):
        interview = self.create_interview()
        rounds = list(interview.rounds.all())
        self.assertEqual([r.description for r in rounds], [
            "Client round", "Casual manager discussion", "Round 3 (details not set)",
        ])
        self.assertEqual(rounds[0].scheduled_date, date(2026, 1, 2))
        self.assertIsNone(rounds[1].scheduled_date)
        rounds[0].status = InterviewRound.CLEARED
        rounds[0].save(update_fields=["status"])
        rounds[1].status = InterviewRound.REJECTED
        rounds[1].save(update_fields=["status"])
        response = self.client.get(reverse("student_interview_detail", args=[interview.pk]))
        self.assertContains(response, "2 of 3 rounds completed")
        self.assertContains(response, "Casual manager discussion")
        self.client.force_login(self.admin)
        staff_response = self.client.get(reverse("admin_interview_detail", args=[interview.pk]))
        self.assertContains(staff_response, "2 of 3 rounds completed")

    def test_interview_list_shows_search_filters_and_round_progress(self):
        interview = self.create_interview()
        first_round = interview.rounds.first()
        first_round.status = InterviewRound.CLEARED
        first_round.save(update_fields=["status"])

        response = self.client.get(reverse("student_interviews"))

        self.assertContains(response, 'id="interview-search"')
        self.assertContains(response, 'id="interview-status-filter"')
        self.assertContains(response, "1 of 3 rounds completed")
        self.assertContains(response, "Casual manager discussion")
        self.assertContains(response, 'data-status="in-progress"')

    def test_final_result_from_journey_returns_to_interview(self):
        interview = self.create_interview()

        response = self.client.post(
            reverse("interview_status", args=[interview.pk]),
            {"final_status": "not-selected", "edit_mode": "1"},
        )

        self.assertRedirects(
            response, reverse("student_interview_detail", args=[interview.pk])
        )
        self.assertEqual(interview.final_status, "not-selected")

    def test_missing_custom_name_and_invalid_count_rejected(self):
        for data in (
            {**self.data, "first_round_description": ""},
            {**self.data, "round_2_description": ""},
            {**self.data, "round_count": "21"},
        ):
            response = self.client.post(reverse("student_interview_add"), data)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(Interview.objects.filter(student=self.student).exists())

    def test_plan_reduction_protects_details_and_expands_empty_rounds(self):
        interview = self.create_interview()
        url = reverse("interview_round_plan", args=[interview.pk])
        response = self.client.post(url, {"round_count": 1})
        self.assertContains(response, "Only empty pending rounds")
        self.assertEqual(interview.rounds.count(), 3)
        self.assertEqual(self.client.post(url, {"round_count": 2}).status_code, 302)
        self.assertEqual(interview.rounds.count(), 2)
        self.assertEqual(self.client.post(url, {"round_count": 4}).status_code, 302)
        self.assertEqual(list(interview.rounds.values_list("round_number", flat=True)), [1, 2, 3, 4])
        self.assertEqual(interview.rounds.get(round_number=4).description, "Round 4 (details not set)")

    def test_edit_opportunity_with_optional_contact_details(self):
        interview = self.create_interview()
        response = self.client.post(reverse("student_interview_edit", args=[interview.pk]), {
            "group": self.group.pk, "company_name": "Example", "role": "Updated role",
            "date_of_interview": "2026-01-02",
        })
        self.assertEqual(response.status_code, 302)
        interview.refresh_from_db()
        self.assertEqual(interview.role, "Updated role")

    def test_edit_round_custom_name_date_and_result(self):
        interview = self.create_interview()
        rnd = interview.rounds.get(round_number=3)
        url = reverse("interview_round_edit", args=[rnd.pk])
        data = {"round_type": "other", "description": "Client discussion",
                "scheduled_date": "2026-01-02", "scheduled_time": "12:00",
                "status": "cleared", "feedback": "Went well"}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        rnd.refresh_from_db()
        self.assertEqual((rnd.description, rnd.status, rnd.feedback), ("Client discussion", "cleared", "Went well"))
        self.assertContains(self.client.post(url, {**data, "description": ""}), "Enter a name")
        self.assertContains(self.client.post(url, {**data, "feedback": ""}), "Add your feedback")

    def test_early_rejection_does_not_change_rounds(self):
        interview = self.create_interview()
        first = interview.rounds.first()
        first.status = InterviewRound.REJECTED
        first.save(update_fields=["status"])
        self.client.post(reverse("interview_status", args=[interview.pk]), {"final_status": "not-selected"})
        self.assertEqual(InterviewStatus.objects.get(interview=interview).final_status, "not-selected")
        self.assertEqual(list(interview.rounds.values_list("status", flat=True)), ["rejected", "pending", "pending"])
        last = interview.rounds.last()
        self.client.post(reverse("round_update", args=[last.pk]), {"status": "pending", "feedback": ""})
        self.assertTrue(InterviewStatus.objects.filter(interview=interview, final_status="not-selected").exists())

    def test_selected_requires_cleared_rounds_and_offer_does_not_rewrite_rounds(self):
        interview = self.create_interview()
        self.client.post(reverse("interview_status", args=[interview.pk]), {"final_status": "selected"})
        self.assertFalse(InterviewStatus.objects.filter(interview=interview).exists())
        self.client.post(reverse("student_interview_quick", args=[interview.pk]), {"action": "offer"})
        self.assertEqual(list(interview.rounds.values_list("status", flat=True)), ["pending"] * 3)

    def test_other_student_cannot_edit_plan_or_round(self):
        interview = self.create_interview()
        rnd = interview.rounds.first()
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("interview_round_plan", args=[interview.pk]), {"round_count": 1}).status_code, 404)
        self.assertEqual(self.client.get(reverse("interview_round_edit", args=[rnd.pk])).status_code, 404)
