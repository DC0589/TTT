import logging

from django import forms
from django.contrib.auth.forms import PasswordResetForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.validators import RegexValidator
from django.utils import timezone

from .models import (
    AttendanceSettings,
    Group,
    GroupMembership,
    Interview,
    InterviewNote,
    InterviewRound,
    InterviewStatus,
    LearningCourse,
    LeaveRequest,
    MockQuestion,
    PlacedStudent,
    Selection,
    StudentRegistrationRequest,
    User,
)

INPUT = "form-control"


class Styled:
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", INPUT)


class StudentForm(Styled, UserCreationForm):
    """Used by admins to create student accounts directly."""
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = (
            "username", "email", "referred_by", "mobile_number", "graduation",
            "department", "hometown", "parent_name", "parent_mobile_number", "skills",
        )
        labels = {
            "referred_by": "Who referred the student",
            "mobile_number": "Student mobile number",
            "graduation": "Graduation",
            "department": "Department",
            "hometown": "Where the student is from",
            "parent_name": "Parent name",
            "parent_mobile_number": "Parent mobile number",
            "skills": "Skills",
        }
        help_texts = {
            "graduation": "Degree, graduation year, or other graduation details.",
            "skills": "Separate skills with commas.",
        }
        widgets = {"skills": forms.Textarea(attrs={"rows": 3})}

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_student = True
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


class HRUserForm(Styled, UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ("username", "email")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_hr = True
        user.is_admin = False
        user.is_student = False
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


class StudentRegistrationForm(Styled, forms.Form):
    username = forms.CharField(
        max_length=150, validators=User._meta.get_field("username").validators
    )
    email = forms.EmailField()
    referred_by = forms.CharField(label="Who referred you", max_length=150, required=False)
    mobile_number = forms.CharField(label="Your mobile number", max_length=30)
    graduation = forms.CharField(
        label="Graduation", max_length=150,
        help_text="Degree and graduation year, for example B.Tech 2024.",
    )
    department = forms.CharField(label="Department", max_length=150)
    hometown = forms.CharField(label="Where are you from", max_length=150)
    parent_name = forms.CharField(label="Parent name", max_length=150)
    parent_mobile_number = forms.CharField(label="Parent mobile number", max_length=30)
    skills = forms.CharField(
        label="Skills", widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Separate skills with commas.",
    )
    password1 = forms.CharField(
        label="Password", strip=False, widget=forms.PasswordInput
    )
    password2 = forms.CharField(
        label="Confirm password", strip=False, widget=forms.PasswordInput
    )

    def clean_username(self):
        StudentRegistrationRequest.objects.filter(
            status=StudentRegistrationRequest.AWAITING_VERIFICATION,
            verification_expires_at__lte=timezone.now(),
        ).update(
            status=StudentRegistrationRequest.EXPIRED,
            password_hash="",
            verification_code_hash="",
            resolved_at=timezone.now(),
        )
        username = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is already in use.")
        if StudentRegistrationRequest.objects.filter(
            username__iexact=username,
            status__in=(
                StudentRegistrationRequest.AWAITING_VERIFICATION,
                StudentRegistrationRequest.AWAITING_APPROVAL,
            ),
        ).exists():
            raise forms.ValidationError("A registration with that username is already pending.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("That email address is already in use.")
        if StudentRegistrationRequest.objects.filter(
            email__iexact=email,
            status__in=(
                StudentRegistrationRequest.AWAITING_VERIFICATION,
                StudentRegistrationRequest.AWAITING_APPROVAL,
            ),
        ).exists():
            raise forms.ValidationError("A registration with that email is already pending.")
        return email

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("The two password fields did not match.")
        if password2:
            validate_password(
                password2,
                User(username=self.cleaned_data.get("username", ""), email=self.cleaned_data.get("email", "")),
            )
        return password2


class RegistrationOTPForm(Styled, forms.Form):
    code = forms.CharField(
        label="6-digit verification code",
        min_length=6,
        max_length=6,
        validators=[RegexValidator(r"^\d{6}$", "Enter the 6-digit code from your email.")],
        widget=forms.TextInput(attrs={
            "inputmode": "numeric",
            "autocomplete": "one-time-code",
            "pattern": "[0-9]{6}",
            "maxlength": "6",
            "placeholder": "000000",
        }),
    )


class GroupForm(Styled, forms.ModelForm):
    class Meta:
        model = Group
        fields = ("name", "description", "is_active")
        labels = {"is_active": "Active batch"}
        help_texts = {"is_active": "Only students in active batches mark attendance."}
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check"}),
        }

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if len(name) < 2:
            raise forms.ValidationError("Enter at least 2 characters.")
        return name


class LearningCourseForm(Styled, forms.ModelForm):
    class Meta:
        model = LearningCourse
        fields = (
            "name", "summary", "level", "duration", "prerequisites",
            "learning_outcomes", "tools", "roadmap", "interview_questions",
            "sort_order",
        )
        widgets = {
            "summary": forms.Textarea(attrs={"rows": 3}),
            "prerequisites": forms.Textarea(attrs={"rows": 3}),
            "learning_outcomes": forms.Textarea(attrs={"rows": 5}),
            "tools": forms.Textarea(attrs={"rows": 3}),
            "roadmap": forms.Textarea(attrs={"rows": 7}),
            "interview_questions": forms.Textarea(attrs={"rows": 8}),
        }

    def clean_name(self):
        return self.cleaned_data["name"].strip()


class AddMemberForm(Styled, forms.Form):
    student = forms.ModelChoiceField(queryset=User.objects.none())

    def __init__(self, *a, group, **k):
        super().__init__(*a, **k)
        self.group = group
        self.fields["student"].queryset = User.objects.filter(
            is_student=True, memberships__isnull=True
        )

    def save(self):
        return GroupMembership.objects.create(group=self.group, student=self.cleaned_data["student"])


class InterviewForm(Styled, forms.ModelForm):
    class Meta:
        model = Interview
        fields = (
            "group", "company_name", "role", "job_posting_url", "date_of_interview",
            "time_of_interview", "interview_type", "hr_name", "hr_contact_number", "hr_email", "prep_notes",
        )
        widgets = {
            "date_of_interview": forms.DateInput(attrs={"type": "date"}),
            "time_of_interview": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "prep_notes": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "group": "Batch",
            "time_of_interview": "Interview time",
            "interview_type": "Interview type",
            "hr_name": "HR contact name",
            "hr_contact_number": "HR contact number",
            "hr_email": "HR email address",
            "job_posting_url": "Job posting URL",
            "prep_notes": "Preparation notes",
        }
        help_texts = {"hr_email": "Enter the HR contact’s email address."}

    def __init__(self, *a, student, **k):
        super().__init__(*a, **k)
        self.fields["group"].queryset = Group.objects.filter(memberships__student=student)
        self.fields["group"].empty_label = "Select a batch"
        self.student = student
        if not self.instance.pk:
            self.fields["first_round_type"] = forms.ChoiceField(
                label="Which round is this?",
                choices=[("", "Select the round"), *InterviewRound.TYPE_CHOICES])
        for name in ("time_of_interview", "hr_name", "hr_contact_number", "interview_type"):
            self.fields[name].required = True
        self.fields["interview_type"].choices = [("", "Select a type")] + list(Interview.TYPE_CHOICES)
        self.fields["confirm_clash"] = forms.BooleanField(
            required=False, widget=forms.HiddenInput(), label="Save anyway")
        self.fields["confirm_clash"].widget.attrs.setdefault("class", INPUT)

    def clean(self):
        data = super().clean()
        if self.errors or self.cleaned_data.get("confirm_clash"):
            return data
        others = Interview.objects.filter(student=self.student)
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)
        problems = []
        company, role = data.get("company_name"), data.get("role")
        if company and role and others.filter(
                company_name__iexact=company.strip(), role__iexact=role.strip()).exists():
            problems.append(f"You already have an interview for {role} at {company}.")
        when, at = data.get("date_of_interview"), data.get("time_of_interview")
        if when and at and others.filter(date_of_interview=when, time_of_interview=at).exists():
            problems.append("You already have another interview at the same date and time.")
        if problems:
            self.fields["confirm_clash"].widget = forms.CheckboxInput(attrs={"class": ""})
            raise forms.ValidationError(problems + ["Tick “Save anyway” below if this is intentional, then save again."])
        return data


class RoundForm(Styled, forms.ModelForm):
    class Meta:
        model = InterviewRound
        fields = ("round_type", "description", "scheduled_date", "scheduled_time")
        widgets = {
            "scheduled_date": forms.DateInput(attrs={"type": "date"}),
            "scheduled_time": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, interview=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.interview = interview
        self.fields["round_type"].required = True
        self.fields["description"].required = False

    def clean(self):
        data = super().clean()
        rtype = data.get("round_type")
        desc = (data.get("description") or "").strip()
        if rtype and not desc:
            if rtype == "other":
                self.add_error("description", "Describe the round.")
            else:
                desc = dict(InterviewRound.TYPE_CHOICES)[rtype]
        data["description"] = desc
        if self.interview is not None and self.interview.rounds.exists():
            if not data.get("scheduled_date"):
                self.add_error("scheduled_date", "Date is required for the next round.")
            if not data.get("scheduled_time"):
                self.add_error("scheduled_time", "Time is required for the next round.")
        return data


class RoundScheduleForm(forms.ModelForm):
    class Meta:
        model = InterviewRound
        fields = ("scheduled_date",)


class InterviewNotesForm(forms.ModelForm):
    class Meta:
        model = Interview
        fields = ("prep_notes",)


class RoundStatusForm(forms.ModelForm):
    class Meta:
        model = InterviewRound
        fields = ("status", "feedback")

    def clean(self):
        data = super().clean()
        status = data.get("status")
        feedback = (data.get("feedback") or "").strip()
        data["feedback"] = feedback
        if status and status != InterviewRound.PENDING:
            rnd = self.instance
            if rnd.scheduled_date:
                from datetime import datetime
                from datetime import time as dtime
                when = datetime.combine(rnd.scheduled_date, rnd.scheduled_time or dtime.min)
                if timezone.make_aware(when) > timezone.now():
                    raise forms.ValidationError("You can update the result only after the round time has passed.")
            if not feedback:
                self.add_error("feedback", "Add your feedback on how the round went.")
        return data


class FinalStatusForm(Styled, forms.ModelForm):
    class Meta:
        model = InterviewStatus
        fields = ("final_status",)

    def __init__(self, *a, interview, **k):
        super().__init__(*a, **k)
        self.interview = interview
        self.fields["final_status"].choices = [
            ("", "Choose a result"),
            *self.fields["final_status"].choices,
        ]

    def clean_final_status(self):
        rounds = list(self.interview.rounds.all())
        if not rounds:
            raise forms.ValidationError("Add at least one round first.")
        if any(r.status == InterviewRound.PENDING for r in rounds):
            raise forms.ValidationError("Complete all rounds before setting the final result.")
        return self.cleaned_data["final_status"]


class StudentPasswordResetForm(PasswordResetForm):
    def get_users(self, email):
        return (user for user in super().get_users(email) if user.is_student)

    def send_mail(self, *args, **kwargs):
        try:
            super().send_mail(*args, **kwargs)
        except Exception:
            logging.getLogger(__name__).exception("Password reset email failed")


class MockQuestionForm(Styled, forms.ModelForm):
    class Meta:
        model = MockQuestion
        fields = ("topic", "difficulty", "kind", "language", "text", "is_active")
        widgets = {"text": forms.Textarea(attrs={"rows": 4})}
        help_texts = {
            "language": "Only used for coding questions. Leave as topic default unless needed.",
            "text": "For SQL coding questions, describe the tables and expected output in words.",
        }

    def clean_topic(self):
        return " ".join(self.cleaned_data["topic"].split())

    def clean_text(self):
        return self.cleaned_data["text"].strip()

    def clean(self):
        data = super().clean()
        if data.get("kind") != MockQuestion.CODING:
            data["language"] = ""
        return data


class NoteReplyForm(forms.Form):
    text = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={"rows": 2}))

    def clean_text(self):
        text = self.cleaned_data["text"].strip()
        if not text:
            raise forms.ValidationError("Write a reply first.")
        return text


class InterviewAdminNotesForm(Styled, forms.ModelForm):
    class Meta:
        model = InterviewNote
        fields = ("text", "visible_to_student")
        widgets = {"text": forms.Textarea(attrs={"rows": 4})}
        labels = {"text": "Add a trainer note", "visible_to_student": "Show this note to the student"}

    def clean_text(self):
        text = self.cleaned_data["text"].strip()
        if not text:
            raise forms.ValidationError("Write a note first.")
        return text


class BatchAwareSelect(forms.Select):
    batches_by_student = {}

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        pk = str(getattr(value, "value", value))
        if pk in self.batches_by_student:
            option["attrs"]["data-batches"] = " ".join(
                str(g) for g in self.batches_by_student[pk]
            )
        return option


class SelectionForm(Styled, forms.ModelForm):
    batch = forms.ModelChoiceField(
        queryset=Group.objects.none(), required=False, empty_label="All batches",
        label="Batch", help_text="Pick a batch to narrow the student list.",
    )

    class Meta:
        model = Selection
        fields = ("batch", "student", "company", "role")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["batch"].queryset = Group.objects.order_by("name")
        batches = {}
        for student_id, group_id in GroupMembership.objects.values_list("student_id", "group_id"):
            batches.setdefault(student_id, []).append(group_id)
        widget = BatchAwareSelect(attrs={"class": INPUT})
        widget.batches_by_student = {str(k): v for k, v in batches.items()}
        self.fields["student"].widget = widget
        self.fields["student"].queryset = User.objects.filter(is_student=True).order_by("username")
        self.fields["student"].label_from_instance = lambda u: (
            f"{u.get_full_name()} ({u.username})" if u.get_full_name() else u.username
        )

    def clean(self):
        data = super().clean()
        batch, student = data.get("batch"), data.get("student")
        if batch and student and not batch.memberships.filter(student=student).exists():
            self.add_error("student", "This student is not in the selected batch.")
        return data

    def clean_company(self):
        return " ".join(self.cleaned_data["company"].split())

    def clean_role(self):
        return " ".join(self.cleaned_data["role"].split())


class PlacedStudentForm(Styled, forms.ModelForm):
    class Meta:
        model = PlacedStudent
        fields = ("name", "batch", "company", "year")

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

    def clean_company(self):
        return " ".join(self.cleaned_data["company"].split())


class LeaveRequestForm(Styled, forms.ModelForm):
    MAX_DAYS = 30

    class Meta:
        model = LeaveRequest
        fields = ("start_date", "end_date", "reason")
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(attrs={"rows": 3, "maxlength": 1000}),
        }

    def __init__(self, *args, student=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.student = student

    def clean(self):
        data = super().clean()
        start, end = data.get("start_date"), data.get("end_date")
        if start and start < timezone.localdate():
            self.add_error("start_date", "Leave can't start in the past.")
        if start and end:
            if end < start:
                self.add_error("end_date", "End date can't be before the start date.")
            elif (end - start).days + 1 > self.MAX_DAYS:
                self.add_error("end_date", f"Leave can be at most {self.MAX_DAYS} days.")
            elif self.student and LeaveRequest.objects.filter(
                student=self.student, status__in=[LeaveRequest.PENDING, LeaveRequest.APPROVED],
                start_date__lte=end, end_date__gte=start,
            ).exists():
                self.add_error(None, "You already have a leave request covering some of these dates.")
        return data

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if len(reason) < 5:
            raise forms.ValidationError("Please give a short reason.")
        return reason


class AttendanceSettingsForm(Styled, forms.ModelForm):
    class Meta:
        model = AttendanceSettings
        fields = ("centre_lat", "centre_lng", "radius_m", "block_outside", "late_after", "auto_close_at")
        labels = {
            "centre_lat": "Centre latitude", "centre_lng": "Centre longitude",
            "radius_m": "Allowed radius (metres)", "block_outside": "Block check-in outside the radius",
            "late_after": "Late after", "auto_close_at": "Auto check-out time",
        }
        widgets = {
            "late_after": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "auto_close_at": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }

    def clean(self):
        data = super().clean()
        lat, lng = data.get("centre_lat"), data.get("centre_lng")
        if (lat is None) != (lng is None):
            raise forms.ValidationError("Enter both latitude and longitude, or leave both empty to turn the geofence off.")
        if lat is not None and not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise forms.ValidationError("Latitude must be between -90 and 90 and longitude between -180 and 180.")
        if data.get("block_outside") and lat is None:
            raise forms.ValidationError("Set the centre location before turning on blocking.")
        radius = data.get("radius_m")
        if radius is not None and not (20 <= radius <= 50000):
            self.add_error("radius_m", "Use a radius between 20 m and 50,000 m.")
        return data
