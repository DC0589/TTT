from django import forms
import logging

from django.contrib.auth.forms import PasswordResetForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.validators import RegexValidator
from django.utils import timezone

from .models import (
    Group, GroupMembership, Interview, InterviewRound, InterviewStatus, LearningCourse,
    MockQuestion, StudentRegistrationRequest, User,
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
        fields = ("name", "description")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

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
            "time_of_interview", "hr_name", "hr_contact_number", "hr_email", "prep_notes",
        )
        widgets = {
            "date_of_interview": forms.DateInput(attrs={"type": "date"}),
            "time_of_interview": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "prep_notes": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "group": "Batch",
            "time_of_interview": "Interview time",
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
        for name in ("time_of_interview", "hr_name", "hr_contact_number"):
            self.fields[name].required = True


class RoundForm(Styled, forms.ModelForm):
    class Meta:
        model = InterviewRound
        fields = ("description", "scheduled_date")
        widgets = {"scheduled_date": forms.DateInput(attrs={"type": "date"})}

    def clean_description(self):
        d = self.cleaned_data["description"].strip()
        if not d:
            raise forms.ValidationError("Describe the round.")
        return d


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
        fields = ("status",)


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
