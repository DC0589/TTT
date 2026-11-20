import random
import secrets

from django.conf import settings

from ..ai_interview import generate_json
from ..mock_bank import DIFFICULTY_GUIDE, pick_seed_questions
from ..mock_topics import pick_focus_areas, topic_language
from ..models import MockInterviewSession, MockQuestionSet
from ..models.learning import MOCK_TYPE_CODING, MOCK_TYPE_INTERVIEW

QUESTION_COUNT = 10


class InvalidQuestions(Exception):
    pass


def build_prompt(role, difficulty, previous_questions=(), focus="", test_type=MOCK_TYPE_INTERVIEW):
    seeds = (
        [q for q in pick_seed_questions(role, difficulty, exclude=previous_questions) if q.startswith("[code] ")]
        if not focus and test_type == MOCK_TYPE_CODING else []
    )
    focus_areas = [focus] if focus else pick_focus_areas(role)
    default_language = topic_language(role)
    prompt = (
        f"Create exactly {QUESTION_COUNT} distinct, concise mock interview questions "
        f"on the topic '{role}', in a varied order. "
        f"{DIFFICULTY_GUIDE[difficulty]} Every question must match this difficulty level. "
    )
    if test_type == MOCK_TYPE_CODING:
        prompt += (
            "All questions must be hands-on coding questions answered by writing code. "
            "Coding questions must test logic implementation: problem solving with loops, "
            "conditions, string/list/dictionary manipulation, algorithms, pattern printing, "
            "step-by-step data transformation, or SQL queries built from clear business logic. "
            "Do not ask for memorised syntax, library trivia or setup/configuration code. Each "
            "must be solvable with plain language features (and small sample data) in a few "
            "minutes. State the task, input format, output format, important constraints and "
            "edge-case rules unambiguously. Supply a concrete example input and its exact expected "
            "output for every question, and make sure they agree with the task. For SQL, show "
            "the sample table rows in the example input and the resulting rows and columns in "
            "the expected output. Starter data must match the example input. "
        )
    else:
        prompt += (
            "Mix definitions, conceptual explanations and realistic scenario-based questions "
        "about the selected topic. Phrase each question clearly and provide enough context "
        "to understand what is being asked. Every question must be answerable verbally "
        "without writing, reading, completing, debugging, tracing or predicting the output "
        "of code or SQL queries. Do not include code snippets or ask for syntax. "
        )
    if focus:
        prompt += (
            f"Every question must be specifically about '{focus}' within {role}; "
            "if several topics are listed, spread the questions across all of them; "
            "do not ask about other areas. "
        )
    elif focus_areas:
        prompt += f"Draw from these focus areas for this session: {', '.join(focus_areas)}. "
    if seeds:
        prompt += (
            "Real interview questions to include in this session. Use them as questions "
            "(lightly clean the wording, and write any missing sample data for coding ones). "
            "Items starting with [code] are coding questions. Fill the remaining slots with "
            "new questions of the same style and level:\n- "
            + "\n- ".join(seeds) + "\n"
        )
    if previous_questions:
        prompt += (
            "The candidate has already been asked the questions below in earlier sessions. "
            "Do not repeat or lightly reword any of them:\n- "
            + "\n- ".join(question[:150] for question in previous_questions) + "\n"
        )
    response_format = (
        "'coding', example_input (a nonempty string showing concrete sample input), "
        "example_output (a nonempty string showing the exact expected output), "
        "language ('python', 'sql' or 'pyspark'; "
        f"prefer '{default_language}' for this topic), and starter (a short starter code snippet; "
        "for SQL include CREATE TABLE and INSERT statements for small sample data)"
        if test_type == MOCK_TYPE_CODING else "'concept'; do not include code, language or starter"
    )
    prompt += (
        f"Variation token: {secrets.token_hex(4)}. "
        "Return JSON with one field, questions, an array of exactly "
        f"{QUESTION_COUNT} objects. Each object has: text (the question), type {response_format}. "
        "Do not include answers or commentary."
    )
    return prompt


def parse_questions(result, role, test_type=MOCK_TYPE_INTERVIEW):
    raw_questions = result.get("questions")
    if not isinstance(raw_questions, list) or len(raw_questions) != QUESTION_COUNT:
        raise InvalidQuestions("The AI did not return ten questions. Please try again.")
    questions = []
    for item in raw_questions:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        text = item["text"].strip()
        if not text or len(text) > 500:
            continue
        expected_type = "coding" if test_type == MOCK_TYPE_CODING else "concept"
        if item.get("type") != expected_type:
            raise InvalidQuestions("The AI returned the wrong kind of questions. Please try again.")
        question = {"text": text, "type": "concept"}
        if item.get("type") == "coding":
            example_input = item.get("example_input")
            example_output = item.get("example_output")
            if not all(
                isinstance(value, str) and value.strip() and len(value) <= 1000
                for value in (example_input, example_output)
            ):
                raise InvalidQuestions("The AI did not provide clear example input and output. Please try again.")
            language = item.get("language")
            if language not in ("python", "sql", "pyspark"):
                language = topic_language(role)
            starter = item.get("starter")
            if isinstance(starter, str) and len(starter) > 2000:
                raise InvalidQuestions("The AI returned an incomplete coding starter. Please try again.")
            question.update({
                "type": "coding",
                "language": language,
                "starter": starter if isinstance(starter, str) else "",
                "example_input": example_input.strip(),
                "example_output": example_output.strip(),
            })
        questions.append(question)
        if len(questions) == QUESTION_COUNT:
            break
    if len(questions) != QUESTION_COUNT:
        raise InvalidQuestions("The AI returned invalid questions. Please try again.")
    return questions


def has_coding_examples(questions):
    if not isinstance(questions, list) or len(questions) != QUESTION_COUNT:
        return False
    for question in questions:
        if not isinstance(question, dict) or question.get("type") != "coding":
            return False
        if not isinstance(question.get("text"), str) or not question["text"].strip() or len(question["text"]) > 500:
            return False
        if any(
            not isinstance(question.get(field), str) or not question[field].strip()
            or len(question[field]) > 1000
            for field in ("example_input", "example_output")
        ):
            return False
    return True


def generate(role, difficulty, previous_questions=(), focus="", test_type=MOCK_TYPE_INTERVIEW):
    """Ask the AI for a fresh question set. Raises GeminiAPIError or InvalidQuestions."""
    prompt = build_prompt(role, difficulty, previous_questions, focus, test_type)
    return parse_questions(generate_json([{"text": prompt}]), role, test_type)


def store(topic, difficulty, questions, focus="", test_type=MOCK_TYPE_INTERVIEW):
    first = questions[0]["text"]
    for existing in MockQuestionSet.objects.filter(
        topic__iexact=topic, difficulty=difficulty, focus=focus, test_type=test_type
    )[:50]:
        if (
            existing.questions and existing.questions[0].get("text") == first
            and (test_type != MOCK_TYPE_CODING or has_coding_examples(existing.questions))
        ):
            return existing
    return MockQuestionSet.objects.create(
        topic=topic, focus=focus, difficulty=difficulty, questions=questions, test_type=test_type
    )


def serve(student, topic, difficulty, focus="", test_type=MOCK_TYPE_INTERVIEW):
    """Return an unseen stored set for this student, once the bank has enough variety."""
    seen = MockInterviewSession.objects.filter(
        student=student, question_set__isnull=False
    ).values_list("question_set_id", flat=True)
    candidates = []
    for candidate in MockQuestionSet.objects.filter(
            topic__iexact=topic, difficulty=difficulty, focus=focus, test_type=test_type, is_active=True
        ).exclude(pk__in=seen).iterator():
        if test_type == MOCK_TYPE_CODING and not has_coding_examples(candidate.questions):
            continue
        candidates.append(candidate)
        if len(candidates) == 50:
            break
    if len(candidates) < settings.MOCK_BANK_MIN_SETS:
        return None
    return random.choice(candidates)
