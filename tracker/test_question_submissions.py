import csv
import io

from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .context_processors import celebration_banners
from .models import LearningCourse, StudentQuestionSubmission, User
from .views.courses import student_interview_questions
from .views.question_submissions import (
    admin_question_submission_delete, admin_question_submission_review, admin_question_submissions,
    student_question_upload, student_question_uploads,
)


class QuestionSubmissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = User.objects.create_user("contributor", is_student=True)
        cls.other = User.objects.create_user("reader", is_student=True)
        cls.admin = User.objects.create_user("reviewer", is_admin=True)
        cls.hr = User.objects.create_user("review-hr", is_hr=True, created_by=cls.admin)
        cls.python, _ = LearningCourse.objects.update_or_create(
            name="Python", defaults={"summary": "Python course", "roadmap": "Basics",
                                     "interview_questions": "Original question?"}
        )
        cls.sql, _ = LearningCourse.objects.update_or_create(
            name="SQL", defaults={"summary": "SQL course", "roadmap": "Basics",
                                  "interview_questions": "Original SQL?"}
        )

    def request(self, method, name, user, data=None, args=None):
        request = getattr(RequestFactory(), method)(reverse(name, args=args), data or {})
        request.user = user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def upload(self, content, user=None):
        file = SimpleUploadedFile("questions.csv", content, content_type="text/csv")
        request = self.request("post", "student_question_upload", user or self.student, {"file": file})
        response = student_question_upload(request)
        return response, list(request._messages)

    def review(self, item, decision, user=None, course=None):
        request = self.request("post", "admin_question_submission_review", user or self.admin,
                               data={"course": (course or self.python).pk}, args=[item.pk, decision])
        return admin_question_submission_review(request, item.pk, decision)

    def test_submit_review_publish_and_unpublish(self):
        response, messages = self.upload(b'\xef\xbb\xbftopic,question\nPython,"What is a decorator, exactly?"\nSQL,What is a join?\n')
        self.assertEqual(response.status_code, 302)
        self.assertIn("2 question(s)", str(messages[0]))
        item = StudentQuestionSubmission.objects.get(topic="Python")
        self.assertEqual(item.status, StudentQuestionSubmission.PENDING)
        self.assertEqual(item.question, "What is a decorator, exactly?")
        student_page = self.request("get", "student_interview_questions", self.other)
        self.assertNotIn(item.question.encode(), student_interview_questions(student_page).content)
        self.review(item, "approved")
        item.refresh_from_db()
        self.assertEqual(item.reviewed_by, self.admin)
        self.assertEqual(item.course, self.python)
        self.assertIsNotNone(item.reviewed_at)
        self.assertIn(item.question.encode(), student_interview_questions(student_page).content)
        self.review(item, "rejected", self.hr)
        self.assertNotIn(item.question.encode(), student_interview_questions(student_page).content)

    def test_admin_can_delete_approved_submission_from_student_pages(self):
        item = StudentQuestionSubmission.objects.create(
            student=self.student, topic="Python", question="Unique uploaded question?",
            course=self.python, status=StudentQuestionSubmission.APPROVED,
        )
        page = self.request("get", "student_interview_questions", self.other)
        history = self.request("get", "student_question_uploads", self.student)
        self.assertIn(b"Unique uploaded question?", student_interview_questions(page).content)
        self.assertIn(b"Unique uploaded question?", student_question_uploads(history).content)
        request = self.request("post", "admin_question_submission_delete", self.admin, args=[item.pk])
        response = admin_question_submission_delete(request, item.pk)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(StudentQuestionSubmission.objects.filter(pk=item.pk).exists())
        self.assertNotIn(b"Unique uploaded question?", student_interview_questions(page).content)
        self.assertNotIn(b"Unique uploaded question?", student_question_uploads(history).content)
        self.assertIn(b"Original question?", student_interview_questions(page).content)

    def test_delete_is_admin_only_and_post_only(self):
        item = StudentQuestionSubmission.objects.create(student=self.student, topic="SQL", question="Question?")
        url = reverse("admin_question_submission_delete", args=[item.pk])
        for user in (self.hr, self.student):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                admin_question_submission_delete(
                    self.request("post", "admin_question_submission_delete", user, args=[item.pk]), item.pk
                )
        response = admin_question_submission_delete(
            self.request("get", "admin_question_submission_delete", self.admin, args=[item.pk]), item.pk
        )
        self.assertEqual(response.status_code, 405)
        admin_page = admin_question_submissions(self.request("get", "admin_question_submissions", self.admin))
        hr_page = admin_question_submissions(self.request("get", "admin_question_submissions", self.hr))
        self.assertIn(url.encode(), admin_page.content)
        self.assertNotIn(url.encode(), hr_page.content)
        self.assertTrue(StudentQuestionSubmission.objects.filter(pk=item.pk).exists())

    def test_upload_history_private_and_review_page_visible_to_admin(self):
        self.upload(b"topic,question\nPython,Question one?\n")
        self.upload(b"topic,question\nSQL,Question two?\n", user=self.other)
        request = self.request("get", "student_question_uploads", self.student)
        content = student_question_uploads(request).content
        self.assertIn(b"Question one?", content)
        self.assertNotIn(b"Question two?", content)
        request = self.request("get", "admin_question_submissions", self.admin)
        content = admin_question_submissions(request).content
        self.assertIn(b"Question one?", content)
        self.assertIn(b"Question two?", content)

    def test_invalid_uploads_are_rejected_without_partial_insert(self):
        cases = [
            b"topic,text\nPython,Q\n",
            b"topic,question\nPython,Q\nSQL,\n",
            b"topic,question\nPython,Q\npython,q\n",
            b'topic,question\nPython,"unclosed\n',
            b"topic,question\nPython,\xff\n",
            b"topic,question\nPython,\x00\n",
            b"topic,question\nPython,Q,extra\n",
            b"topic,question\nPython," + b"x" * 2001 + b"\n",
        ]
        for content in cases:
            with self.subTest(content=content[:50]):
                response, messages = self.upload(content)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(messages[0].level_tag, "error")
                self.assertFalse(StudentQuestionSubmission.objects.exists())

    def test_size_and_row_limits(self):
        response, messages = self.upload(b"topic,question\n" + b"x" * 1_000_001)
        self.assertIn("1 MB", str(messages[0]))
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["topic", "question"])
        writer.writerows(("SQL", f"Question {i}?") for i in range(501))
        response, messages = self.upload(stream.getvalue().encode())
        self.assertIn("500", str(messages[0]))
        self.assertFalse(StudentQuestionSubmission.objects.exists())

    def test_exact_row_limit_is_accepted(self):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["topic", "question"])
        writer.writerows(("SQL", f"Question {i}?") for i in range(500))
        _, messages = self.upload(stream.getvalue().encode())
        self.assertIn("500 question(s)", str(messages[0]))
        self.assertEqual(StudentQuestionSubmission.objects.count(), 500)

    def test_invalid_review_does_not_change_status(self):
        item = StudentQuestionSubmission.objects.create(student=self.student, topic="SQL", question="Question?")
        self.review(item, "invalid")
        item.refresh_from_db()
        self.assertEqual(item.status, StudentQuestionSubmission.PENDING)
        self.assertIsNone(item.reviewed_by)

    def test_approval_requires_valid_course(self):
        item = StudentQuestionSubmission.objects.create(student=self.student, topic="SQL", question="Question?")
        request = self.request("post", "admin_question_submission_review", self.hr, {"course": "99999"},
                               args=[item.pk, "approved"])
        admin_question_submission_review(request, item.pk, "approved")
        item.refresh_from_db()
        self.assertEqual(item.status, StudentQuestionSubmission.PENDING)
        self.assertIsNone(item.course)

    def test_submission_pagination_and_course_placement(self):
        StudentQuestionSubmission.objects.bulk_create([
            StudentQuestionSubmission(student=self.student, topic="SQL", question=f"Question {i}?",
                                      status=StudentQuestionSubmission.APPROVED, course=self.sql)
            for i in range(30)
        ])
        request = self.request("get", "student_question_uploads", self.student, {"page": 2})
        self.assertIn(b"Page 2 of 2", student_question_uploads(request).content)
        request = self.request("get", "student_interview_questions", self.other)
        content = student_interview_questions(request).content.decode()
        python_card = content.split('<h2>Python</h2>')[1].split('</article>')[0]
        sql_card = content.split('<h2>SQL</h2>')[1].split('</article>')[0]
        self.assertNotIn("Question 0?", python_card)
        self.assertIn("Question 0?", sql_card)
        self.assertIn("31 prompts", sql_card)

    def test_staff_pending_notification_and_suggested_course(self):
        item = StudentQuestionSubmission.objects.create(student=self.student, topic="sQl", question="Question?")
        for user in (self.admin, self.hr):
            request = self.request("get", "admin_question_submissions", user)
            self.assertEqual(celebration_banners(request)["pending_question_count"], 1)
            content = admin_question_submissions(request).content.decode()
            self.assertIn(f'<option value="{self.sql.pk}" selected>SQL</option>', content)
            self.assertIn("Question?", content)
        self.review(item, "approved", self.hr, self.sql)
        self.assertEqual(celebration_banners(self.request("get", "login", self.hr))["pending_question_count"], 0)

    def test_role_permissions_and_review_method(self):
        item = StudentQuestionSubmission.objects.create(student=self.student, topic="SQL", question="Question?")
        with self.assertRaises(PermissionDenied):
            self.review(item, "approved", self.other)
        with self.assertRaises(PermissionDenied):
            student_question_uploads(self.request("get", "student_question_uploads", self.admin))
        response = admin_question_submission_review(
            self.request("get", "admin_question_submission_review", self.admin, args=[item.pk, "approved"]),
            item.pk, "approved",
        )
        self.assertEqual(response.status_code, 405)
        item.refresh_from_db()
        self.assertEqual(item.status, StudentQuestionSubmission.PENDING)

    def test_navigation_visible_to_correct_roles(self):
        for user, expected, hidden in ((self.student, "student_question_uploads", "admin_question_submissions"),
                                      (self.admin, "admin_question_submissions", "student_question_uploads"),
                                      (self.hr, "admin_question_submissions", "student_question_uploads")):
            request = self.request("get", "login", user)
            content = render_to_string("base.html", {"user": user, "request": request})
            if expected:
                self.assertIn(reverse(expected), content)
            self.assertNotIn(reverse(hidden), content)
