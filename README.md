# student-interview-tracker

Django app where admins manage students and batches, and students track interviews, rounds and outcomes.

## Run locally

1. Create a virtualenv and install dependencies:
   ```bash
   python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. Create the PostgreSQL database (`createdb interview_db`), then configure the environment:
   ```bash
   cp .env.example .env     # edit SECRET_KEY and DATABASE_URL
   ```
   `.env` is loaded automatically. If `DATABASE_URL` is unset, SQLite is used as a fallback. Set a real sender and admin address, plus the SMTP username and provider-generated app password in `.env`. For Gmail, `.env.example` includes the SMTP host and port; turn on two-step verification and create a new app password in Google Account security settings. Add the app password locally as `EMAIL_HOST_PASSWORD` (spaces omitted), never in source code or chat. In development, email is printed to the console only when SMTP is not configured.
3. `python manage.py migrate`
4. `python manage.py seed_demo_data`
5. `python manage.py runserver`
6. `python manage.py test`
7. Verify actual SMTP delivery: `python manage.py test_email --to recipient@example.com`. The command reports missing configuration or SMTP failures without printing the password.

Demo logins (password `demo12345`): `admin`, `alice`, `bob`, `carol`.

## Notes

- Roles: `User.is_admin` / `User.is_student`. Login redirects to `/admin/dashboard/` or `/student/dashboard/`. Django's own admin lives at `/django-admin/`.
- Admins only see and manage batches they created. Students only see and edit their own interviews (others return 404).
- Admins can browse student profiles for their own batches. Removing a student from a batch deletes that batch’s interviews and rounds for the student while preserving interviews in other batches.
- Interview records can include an HR contact name, phone number, and email address.
- Admin-created student accounts are available immediately. External registrations receive a one-time 6-digit email verification code (valid for 10 minutes and 5 attempts); after verification, an administrator must approve the request before a student account or dashboard is created. Admins review verified requests from **Sign-up requests**.
- The student directory supports searchable, paginated card, table, and list views.
- Admin analytics summarize interview outcomes and selection rate, with upcoming interview dates; student dashboards highlight their next interview dates and round progress.
- Rounds are added via AJAX (CSRF token sent in the `X-CSRFToken` header). The final result can only be set once all rounds are cleared or rejected; adding a new round reopens the interview.
- The responsive interface is served from `static/css/app.css` with lightweight interactions in `static/js/app.js`; no frontend build step is required. Google Fonts are optional, with system font fallbacks when they are unavailable.
- Students can use **AI Mock Interview** for ten role-specific questions. Answers are sent for evaluation without blocking the next question; after ending, students can review saved ratings and feedback as it loads. With the student's consent, recordings and snapshots are sent to Google Gemini; the app does not save recordings, transcripts, or snapshots. Per-question scores and written feedback are shared with the student's administrator. Google's API data terms apply. Set `GEMINI_API_KEY` in local `.env` and in Vercel project environment variables. The free tier has provider-defined quotas and availability.
- `python manage.py createsuperuser` creates a user that is automatically flagged `is_admin`.

## Deploying

Vercel does not run migrations. After every deploy that adds a migration, run it against the production database:

```bash
DATABASE_URL=<production url> SECRET_KEY=<key> python manage.py migrate
```

Import the placed-students spreadsheet from the admin "Placed students" page.

## Architecture notes

- `tracker/models/` – data model split by domain (users, batches, learning, interviews, placements incl. `Company`/`Role`, attendance). `User.data_owner` resolves the admin whose data a user works with (an HR user's creating admin).
- `tracker/views/` – thin HTTP handlers per feature. Role decorators live in `tracker/permissions.py`.
- `tracker/services/` – business rules (attendance, placements, registration OTP, notifications).
- `tracker/tasks.py` – `enqueue()` task runner; synchronous by default, `BACKGROUND_TASKS=thread` offloads to a worker thread.
- `tracker/throttle.py` – cache-based rate limiting for sign-in, registration and OTP endpoints (`THROTTLE_ENABLED`).
- `tracker/context_processors.py` – scrolling banners, cached for 5 minutes and cleared when a placement or selection changes.
- Run `make lint` and `make test` before pushing.
