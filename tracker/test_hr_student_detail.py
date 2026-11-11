from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.template.loader import render_to_string
from django.urls import reverse

from .models import Group, GroupMembership, User
from .views.students import admin_student_detail, hr_students


class HRStudentDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("profile-admin", is_admin=True)
        cls.hr = User.objects.create_user("profile-hr", is_hr=True, created_by=cls.admin)
        cls.student = User.objects.create_user("profile-student", is_student=True)
        cls.created_student = User.objects.create_user("profile-created", is_student=True, created_by=cls.hr)
        cls.other_admin = User.objects.create_user("profile-other-admin", is_admin=True)
        cls.outsider = User.objects.create_user("profile-outsider", is_student=True)
        group = Group.objects.create(name="Our batch", admin=cls.admin)
        GroupMembership.objects.create(group=group, student=cls.student)
        other = Group.objects.create(name="Other batch", admin=cls.other_admin)
        GroupMembership.objects.create(group=other, student=cls.outsider)

    def request(self, user, student):
        request = RequestFactory().get(reverse("admin_student_detail", args=[student.pk]))
        request.user = user
        return request

    @patch("tracker.services.mock_scoring.process_pending")
    @patch("tracker.views.students.account_lock.lock_if_inactive")
    def test_hr_can_view_batch_and_own_students_without_admin_controls(self, lock, scoring):
        for student in (self.student, self.created_student):
            response = admin_student_detail(self.request(self.hr, student), student.pk)
            self.assertEqual(response.status_code, 200)
            self.assertIn(student.username.encode(), response.content)
            self.assertIn(reverse("hr_students").encode(), response.content)
            self.assertNotIn(reverse("student_delete", args=[student.pk]).encode(), response.content)
            self.assertNotIn(reverse("admin_student_reset_password", args=[student.pk]).encode(), response.content)

    @patch("tracker.services.mock_scoring.process_pending")
    @patch("tracker.views.students.account_lock.lock_if_inactive")
    def test_admin_still_has_administrative_controls(self, lock, scoring):
        response = admin_student_detail(self.request(self.admin, self.student), self.student.pk)
        self.assertIn(reverse("student_delete", args=[self.student.pk]).encode(), response.content)
        self.assertIn(reverse("admin_student_reset_password", args=[self.student.pk]).encode(), response.content)

    def test_other_students_and_student_role_cannot_access(self):
        with self.assertRaises(Http404):
            admin_student_detail(self.request(self.hr, self.outsider), self.outsider.pk)
        with self.assertRaises(PermissionDenied):
            admin_student_detail(self.request(self.student, self.student), self.student.pk)

    @patch("tracker.views.students.account_lock.lock_inactive_students")
    def test_hr_directory_links_to_profiles(self, lock):
        request = RequestFactory().get(reverse("hr_students"))
        request.user = self.hr
        request.session = {}
        response = hr_students(request)
        self.assertIn(reverse("admin_student_detail", args=[self.student.pk]).encode(), response.content)
        self.assertIn(reverse("admin_student_detail", args=[self.created_student.pk]).encode(), response.content)
        self.assertNotIn(self.outsider.username.encode(), response.content)

    def test_staff_sidebar_order_and_role_links(self):
        for user, students_url in ((self.admin, "admin_students"), (self.hr, "hr_students")):
            with self.subTest(user=user.username):
                request = RequestFactory().get("/")
                request.user = user
                html = render_to_string("base.html", {"user": user, "request": request})
                sidebar = html.split('<nav class="side-nav"', 1)[1].split("</nav>", 1)[0]
                names = ["Login activity", "Mock reviews", "Students", "Batches", "Leaves"]
                positions = [sidebar.index(f"<span>{name}</span>") for name in names]
                self.assertEqual(positions, sorted(positions))
                for name in names:
                    self.assertEqual(sidebar.count(f"<span>{name}</span>"), 1)
                self.assertIn(f'href="{reverse(students_url)}"', sidebar)

    def test_student_sidebar_has_no_staff_links(self):
        request = RequestFactory().get("/")
        request.user = self.student
        html = render_to_string("base.html", {"user": self.student, "request": request})
        sidebar = html.split('<nav class="side-nav"', 1)[1].split("</nav>", 1)[0]
        self.assertNotIn("<span>Login activity</span>", sidebar)
        self.assertNotIn("<span>Mock reviews</span>", sidebar)
        self.assertNotIn("<span>Students</span>", sidebar)
        self.assertNotIn("<span>Batches</span>", sidebar)
