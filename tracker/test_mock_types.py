import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from tracker.models import MockInterviewScore, MockInterviewSession, MockQuestionSet, User
from tracker.services import question_sets
from tracker.views.mock import student_mock_interview, student_mock_interview_ai


class MockTypeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = User.objects.create_user("mock-type-student", is_student=True)

    def questions(self, kind):
        return [
            {
                "text": f"{kind} question {n}?", "type": kind,
                **({"example_input": "[1, 2]", "example_output": "3"} if kind == "coding" else {}),
            }
            for n in range(10)
        ]

    def request(self, method, name, data=None):
        url = reverse(name)
        if method == "post":
            request = RequestFactory().post(url, json.dumps(data), content_type="application/json")
        else:
            request = RequestFactory().get(url)
        request.user = self.student
        return request

    def test_prompts_for_both_types_and_focus(self):
        for focus in ("", "recursion"):
            interview = question_sets.build_prompt("Python", "medium", focus=focus)
            self.assertIn("realistic scenario", interview)
            self.assertIn("Do not ask standalone definitions", interview)
            definition = question_sets.build_prompt("Python", "medium", focus=focus, question_style="definition")
            self.assertIn("Every question must ask for a definition", definition)
            self.assertIn("Do not ask scenario-based questions", definition)
            self.assertIn("Do not include code snippets", interview)
            self.assertNotIn("[code]", interview)
            coding = question_sets.build_prompt("Python", "medium", focus=focus, test_type="coding")
            self.assertIn("All questions must be hands-on coding", coding)
            self.assertIn("starter", coding)
            self.assertIn("example_input", coding)
            self.assertIn("example_output", coding)
            self.assertIn("resulting rows and columns", coding)
            if focus:
                self.assertIn("specifically about 'recursion'", interview)
                self.assertIn("specifically about 'recursion'", coding)

    def test_response_requires_exactly_ten_matching_question_types(self):
        for kind, test_type in (("concept", "interview"), ("coding", "coding")):
            questions = self.questions(kind)
            parsed = question_sets.parse_questions({"questions": questions}, "Python", test_type)
            self.assertEqual(len(parsed), 10)
            self.assertTrue(all(q["type"] == kind for q in parsed))
            if kind == "coding":
                self.assertEqual((parsed[0]["example_input"], parsed[0]["example_output"]), ("[1, 2]", "3"))
            for invalid in (questions[:-1], questions + questions[:1], questions[:-1] + self.questions(
                "coding" if kind == "concept" else "concept"
            )[:1]):
                with self.assertRaises(question_sets.InvalidQuestions):
                    question_sets.parse_questions({"questions": invalid}, "Python", test_type)

    def test_missing_or_invalid_examples_and_overlong_questions_are_rejected(self):
        for field, value in (("example_input", ""), ("example_output", None),
                             ("example_input", "x" * 1001), ("text", "x" * 501)):
            questions = self.questions("coding")
            questions[0][field] = value
            with self.assertRaises(question_sets.InvalidQuestions):
                question_sets.parse_questions({"questions": questions}, "Python", "coding")

    @override_settings(MOCK_BANK_MIN_SETS=1)
    def test_banks_are_separate_and_legacy_sets_are_not_served(self):
        legacy = MockQuestionSet.objects.create(
            topic="Python", difficulty="medium", questions=self.questions("coding")
        )
        self.assertIsNone(question_sets.serve(self.student, "Python", "medium"))
        interview = question_sets.store("Python", "medium", self.questions("concept"))
        coding = question_sets.store("Python", "medium", self.questions("coding"), test_type="coding")
        self.assertEqual(legacy.test_type, "legacy")
        self.assertEqual(question_sets.serve(self.student, "Python", "medium"), interview)
        self.assertEqual(question_sets.serve(self.student, "Python", "medium", test_type="coding"), coding)

    @override_settings(MOCK_BANK_MIN_SETS=1)
    def test_old_coding_sets_with_no_examples_are_never_served_or_reused(self):
        old = self.questions("coding")
        for question in old:
            del question["example_output"]
        MockQuestionSet.objects.create(topic="Python", difficulty="medium", test_type="coding", questions=old)
        self.assertIsNone(question_sets.serve(self.student, "Python", "medium", test_type="coding"))
        new = question_sets.store("Python", "medium", self.questions("coding"), test_type="coding")
        self.assertEqual(question_sets.serve(self.student, "Python", "medium", test_type="coding"), new)
        self.assertEqual(MockQuestionSet.objects.count(), 2)

    @patch("tracker.services.question_sets.generate_json")
    def test_start_creates_matching_sessions_and_defaults_to_interview(self, generate):
        for test_type, kind in (("interview", "concept"), ("coding", "coding")):
            generate.return_value = {"questions": self.questions(kind)}
            data = {"action": "question", "consent": True, "role": "Python", "focus": "recursion"}
            if test_type == "coding":
                data["test_type"] = test_type
            response = student_mock_interview_ai(self.request("post", "student_mock_interview_ai", data))
            self.assertEqual(response.status_code, 200)
            result = json.loads(response.content)
            self.assertTrue(all(q["type"] == kind for q in result["questions"]))
            session = MockInterviewSession.objects.get(pk=result["session_id"])
            self.assertEqual((session.test_type, session.question_set.test_type), (test_type, test_type))
            self.assertEqual(session.focus, "recursion")

    @patch("tracker.services.question_sets.generate_json")
    def test_rejects_mixed_ai_response_and_invalid_test_type(self, generate):
        generate.return_value = {"questions": self.questions("coding")}
        data = {"action": "question", "consent": True, "role": "Python"}
        response = student_mock_interview_ai(self.request("post", "student_mock_interview_ai", data))
        self.assertEqual(response.status_code, 502)
        self.assertFalse(MockInterviewSession.objects.exists())
        data["test_type"] = "unknown"
        response = student_mock_interview_ai(self.request("post", "student_mock_interview_ai", data))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(generate.call_count, 1)

    def test_setup_shows_both_choices_and_default(self):
        response = student_mock_interview(self.request("get", "student_mock_interview"))
        content = response.content.decode()
        self.assertIn('name="test_type" value="interview" required', content)
        self.assertIn('name="test_type" value="coding"', content)
        self.assertIn('value="scenario">Scenario-based questions', content)
        self.assertIn('value="definition">Definition and concept questions', content)
        self.assertLess(content.index('value="interview"'), content.index('value="coding"'))
        self.assertIn("Session readiness", content)
        self.assertIn("data-setup-step=\"3\"", content)
        self.assertIn("Example input", content)
        self.assertIn("Expected output", content)

    def test_scoring_and_history_keep_examples_from_saved_question(self):
        questions = self.questions("coding")
        questions[0]["example_input"] = "x" * 1000
        questions[0]["example_output"] = "y" * 1000
        question_set = MockQuestionSet.objects.create(
            topic="Python", difficulty="medium", test_type="coding", questions=questions
        )
        session = MockInterviewSession.objects.create(
            student=self.student, role="Python", test_type="coding", question_set=question_set
        )
        response = student_mock_interview_ai(self.request("post", "student_mock_interview_ai", {
            "action": "feedback", "consent": True, "role": "Python", "session_id": session.pk,
            "question_number": 1, "question": "incorrect client question", "code": "print(3)",
            "language": "python", "code_output": "3", "lite": True,
        }))
        self.assertEqual(response.status_code, 200)
        score = MockInterviewScore.objects.get(session=session)
        self.assertIn(questions[0]["text"], score.question)
        self.assertIn(questions[0]["example_input"], score.question)
        self.assertIn(questions[0]["example_output"], score.question)
        self.assertIn(score.question, score.payload["parts"][0]["text"])
        self.assertNotIn("incorrect client question", score.question)

    @patch("tracker.services.question_sets.generate")
    def test_prefill_counts_only_selected_type_and_overall_focus(self, generate):
        MockQuestionSet.objects.create(topic="Python", difficulty="medium", questions=self.questions("concept"))
        incomplete = self.questions("coding")
        for question in incomplete:
            del question["example_input"]
        MockQuestionSet.objects.create(
            topic="Python", difficulty="medium", test_type="coding", questions=incomplete
        )
        MockQuestionSet.objects.create(
            topic="Python", difficulty="medium", test_type="coding", focus="recursion",
            questions=self.questions("coding"),
        )
        generate.return_value = self.questions("coding")
        call_command(
            "fill_mock_bank", "--topic", "Python", "--difficulty", "medium",
            "--test-type", "coding", "--per", "1", stdout=StringIO(),
        )
        generate.assert_called_once_with("Python", "medium", test_type="coding", question_style="")
        sets = MockQuestionSet.objects.filter(test_type="coding", focus="")
        self.assertEqual(sets.count(), 2)
        self.assertEqual(sum(question_sets.has_coding_examples(item.questions) for item in sets), 1)

    @patch("tracker.services.question_sets.generate_json")
    def test_incomplete_coding_response_returns_error_without_session(self, generate):
        incomplete = self.questions("coding")
        del incomplete[0]["example_output"]
        generate.return_value = {"questions": incomplete}
        response = student_mock_interview_ai(self.request("post", "student_mock_interview_ai", {
            "action": "question", "consent": True, "role": "SQL", "test_type": "coding",
        }))
        self.assertEqual(response.status_code, 502)
        self.assertIn("example input and output", json.loads(response.content)["error"])
        self.assertFalse(MockInterviewSession.objects.exists())

    @override_settings(MOCK_BANK_MIN_SETS=1)
    def test_interview_banks_are_separate_and_old_mixed_sets_are_not_used(self):
        MockQuestionSet.objects.create(
            topic="Python", difficulty="medium", test_type="interview", questions=self.questions("concept")
        )
        self.assertIsNone(question_sets.serve(self.student, "Python", "medium"))
        scenario = question_sets.store("Python", "medium", self.questions("concept"), question_style="scenario")
        definition = question_sets.store("Python", "medium", self.questions("concept"), question_style="definition")
        self.assertNotEqual(scenario.pk, definition.pk)
        self.assertEqual(question_sets.serve(self.student, "Python", "medium", question_style="scenario"), scenario)
        self.assertEqual(question_sets.serve(self.student, "Python", "medium", question_style="definition"), definition)

    @patch("tracker.services.question_sets.generate_json")
    def test_style_is_sent_to_generator_and_stored_with_session(self, generate):
        generate.return_value = {"questions": self.questions("concept")}
        for style, phrase in (("scenario", "realistic scenario"), ("definition", "Every question must ask for a definition")):
            response = student_mock_interview_ai(self.request("post", "student_mock_interview_ai", {
                "action": "question", "consent": True, "role": "Python", "question_style": style,
            }))
            self.assertEqual(response.status_code, 200)
            session = MockInterviewSession.objects.get(pk=json.loads(response.content)["session_id"])
            self.assertEqual(session.question_style, style)
            self.assertEqual(session.question_set.question_style, style)
            self.assertIn(phrase, generate.call_args.args[0][0]["text"])

    @patch("tracker.services.question_sets.generate_json")
    def test_invalid_style_is_rejected(self, generate):
        for invalid in ("mixed", "", None, []):
            response = student_mock_interview_ai(self.request("post", "student_mock_interview_ai", {
                "action": "question", "consent": True, "role": "Python", "question_style": invalid,
            }))
            self.assertEqual(response.status_code, 400)
        generate.assert_not_called()

    @patch("tracker.services.question_sets.generate")
    def test_prefill_only_counts_the_selected_interview_style(self, generate):
        question_sets.store("Python", "medium", self.questions("concept"), question_style="scenario")
        generate.return_value = self.questions("concept")
        call_command(
            "fill_mock_bank", "--topic", "Python", "--difficulty", "medium",
            "--question-style", "definition", "--per", "1", stdout=StringIO(),
        )
        generate.assert_called_once_with("Python", "medium", test_type="interview", question_style="definition")
        self.assertTrue(MockQuestionSet.objects.filter(question_style="definition").exists())
