import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from ..forms import (
    LearningCourseForm,
)
from ..models import (
    LearningCourse,
)

logger = logging.getLogger(__name__)

from ..permissions import admin_required, student_required


@admin_required
def admin_courses(request):
    return render(request, "tracker/admin/courses.html", {
        "courses": LearningCourse.objects.all(),
    })


@admin_required
def admin_course_add(request):
    form = LearningCourseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        course = form.save()
        messages.success(request, "Course content created.")
        return redirect("admin_course_edit", pk=course.pk)
    return render(request, "tracker/admin/course_edit.html", {
        "form": form,
        "page_title": "Add a course",
        "submit_label": "Create course",
    })


@admin_required
def admin_course_edit(request, pk):
    course = get_object_or_404(LearningCourse, pk=pk)
    form = LearningCourseForm(request.POST or None, instance=course)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{course.name} course content updated.")
        return redirect("admin_course_edit", pk=course.pk)
    return render(request, "tracker/admin/course_edit.html", {
        "course": course,
        "form": form,
        "page_title": f"Edit {course.name}",
        "submit_label": "Save course content",
    })


@admin_required
def admin_course_preview(request, pk):
    return render(request, "tracker/admin/course_preview.html", {
        "course": get_object_or_404(LearningCourse, pk=pk),
    })


@student_required
def student_courses(request):
    return render(request, "tracker/student/curriculum.html", {
        "active_tab": "courses",
        "courses": LearningCourse.objects.all(),
        "page_title": "Courses",
        "page_eyebrow": "YOUR LEARNING ROADMAP",
    })


@student_required
def student_interview_questions(request):
    return render(request, "tracker/student/curriculum.html", {
        "active_tab": "questions",
        "courses": LearningCourse.objects.all(),
        "page_title": "Interview questions",
        "page_eyebrow": "PRACTICE FOR YOUR NEXT STEP",
    })
