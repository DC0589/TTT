import random
import secrets

from django.conf import settings

from ..ai_interview import generate_json
from ..mock_bank import DIFFICULTY_GUIDE, pick_seed_questions
from ..mock_topics import pick_focus_areas, topic_language
from ..models import MockInterviewSession, MockQuestionSet

QUESTION_COUNT = 10
CODING_QUESTION_COUNT = 3


class InvalidQuestions(Exception):
    pass


def build_prompt(role, difficulty, previous_questions=()):
    seeds = pick_seed_questions(role, difficulty, exclude=previous_questions)
    focus_areas = pick_focus_areas(role)
    default_language = topic_language(role)
    prompt = (
        f"Create exactly {QUESTION_COUNT} distinct, concise mock interview questions "
        f"on the topic '{role}'. Mix conceptual, scenario-based and practical questions of "
        "and put them in a varied order. "
        f"{DIFFICULTY_GUIDE[difficulty]} Every question must match this difficulty level. "
        f"Exactly {CODING_QUESTION_COUNT} of them must be hands-on coding questions that "
        "the candidate answers by writing code; the rest are spoken questions. "
        "Coding questions must test logic implementation: problem solving with loops, "
        "conditions, string/list/dictionary manipulation, algorithms, pattern printing, "
        "step-by-step data transformation, or SQL queries built from clear business logic. "
        "Do not ask for memorised syntax, library trivia or setup/configuration code. Each "
        "must be solvable with plain language features (and small sample data) in a few "
        "minutes, with a clearly stated input and expected output. "
    )
    if focus_areas:
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
    prompt += (
        f"Variation token: {secrets.token_hex(4)}. "
        "Return JSON with one field, questions, an array of exactly "
        f"{QUESTION_COUNT} objects. Each object has: text (the question), type "
        "('concept' or 'coding'), and for coding questions only: language ('python', 'sql' or "
        f"'pyspark'; prefer '{default_language}' for this topic) and starter (a short starter "
        "code snippet; for SQL include the CREATE TABLE and INSERT statements for small sample "
        "data so the query can be run). Do not include answers or commentary."
    )
    return prompt


def parse_questions(result, role):
    raw_questions = result.get("questions")
    if not isinstance(raw_questions, list) or len(raw_questions) < QUESTION_COUNT:
        raise InvalidQuestions("The AI did not return ten questions. Please try again.")
    questions = []
    for item in raw_questions:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        text = item["text"].strip()[:500]
        if not text:
            continue
        question = {"text": text, "type": "concept"}
        if item.get("type") == "coding":
            language = item.get("language")
            if language not in {"python", "sql", "pyspark"}:
                language = topic_language(role)
            starter = item.get("starter")
            question.update({
                "type": "coding",
                "language": language,
                "starter": starter[:2000] if isinstance(starter, str) else "",
            })
        questions.append(question)
        if len(questions) == QUESTION_COUNT:
            break
    if len(questions) != QUESTION_COUNT:
        raise InvalidQuestions("The AI returned invalid questions. Please try again.")
    return questions


def generate(role, difficulty, previous_questions=()):
    """Ask the AI for a fresh question set. Raises GeminiAPIError or InvalidQuestions."""
    prompt = build_prompt(role, difficulty, previous_questions)
    return parse_questions(generate_json([{"text": prompt}]), role)


def store(topic, difficulty, questions):
    first = questions[0]["text"]
    for existing in MockQuestionSet.objects.filter(topic__iexact=topic, difficulty=difficulty)[:50]:
        if existing.questions and existing.questions[0].get("text") == first:
            return existing
    return MockQuestionSet.objects.create(
        topic=topic, difficulty=difficulty, questions=questions
    )


def serve(student, topic, difficulty):
    """Return an unseen stored set for this student, once the bank has enough variety."""
    seen = MockInterviewSession.objects.filter(
        student=student, question_set__isnull=False
    ).values_list("question_set_id", flat=True)
    candidates = list(
        MockQuestionSet.objects.filter(
            topic__iexact=topic, difficulty=difficulty, is_active=True
        ).exclude(pk__in=list(seen)).values_list("pk", flat=True)[:50]
    )
    if len(candidates) < settings.MOCK_BANK_MIN_SETS:
        return None
    return MockQuestionSet.objects.get(pk=random.choice(candidates))
