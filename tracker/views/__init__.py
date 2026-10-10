"""HTTP views grouped by feature. URLs and tests may import from `tracker.views`."""
from .activity import login_activity  # noqa: F401
from .admin_interviews import (  # noqa: F401
    admin_interview_detail,
    admin_interview_quick,
    admin_interviews,
    admin_interviews_export,
    admin_reports,
    interview_quick,
)
from .attendance import (  # noqa: F401
    staff_attendance,
    staff_attendance_report,
    staff_attendance_settings,
    staff_leave_review,
    staff_leaves,
    student_attendance,
    student_attendance_mark,
    student_leave_apply,
    student_leave_cancel,
)
from .auth import (  # noqa: F401
    RoleLoginView,
    home,
    register,
    registration_submitted,
    resend_registration_otp,
    verify_registration,
)
from .batches import (  # noqa: F401
    admin_group_add,
    admin_group_detail,
    admin_group_edit,
    admin_group_member_add,
    admin_groups,
    group_delete,
    member_remove,
)
from .calendar import admin_calendar, student_calendar  # noqa: F401
from .courses import (  # noqa: F401
    admin_course_add,
    admin_course_edit,
    admin_course_preview,
    admin_courses,
    student_courses,
    student_interview_questions,
)
from .dashboard import admin_dashboard, student_dashboard  # noqa: F401
from .interviews import (  # noqa: F401
    interview_delete,
    interview_status,
    round_add,
    round_schedule,
    round_update,
    student_interview_add,
    student_interview_detail,
    student_interview_edit,
    student_interview_progress,
    student_interviews,
)
from .mock import (  # noqa: F401
    admin_mock_interviews,
    admin_mock_interviews_export,
    admin_mock_interviews_report,
    student_mock_interview,
    student_mock_interview_ai,
    student_mock_interview_health,
)
from .notes import admin_interview_notes, admin_note_reply, interview_notes, student_note_reply, student_notes  # noqa: F401
from .placements import (  # noqa: F401
    admin_placed_delete,
    admin_placed_edit,
    admin_placed_import,
    admin_placed_students,
    staff_selection_delete,
    staff_selections,
)
from .questions import (  # noqa: F401
    admin_question_add,
    admin_question_delete,
    admin_question_edit,
    admin_questions,
    admin_questions_export,
    admin_questions_import,
)
from .registrations import (  # noqa: F401
    admin_registrations,
    registration_approve,
    registration_reject,
    registrations_pending_count,
)
from .storage import storage_usage  # noqa: F401
from .students import (  # noqa: F401
    admin_hr_user_add,
    admin_student_add,
    admin_student_detail,
    admin_student_reset_password,
    admin_students,
    hr_students,
    student_delete,
)
from .system import cron_close_attendance, cron_purge_mock_data, cron_score_mock, csrf_failure  # noqa: F401
