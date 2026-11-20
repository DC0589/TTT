from django.core.management.base import BaseCommand

from tracker.ai_interview import GeminiAPIError
from tracker.mock_topics import MOCK_TOPICS
from tracker.models import MockQuestionSet
from tracker.models.learning import MOCK_TYPE_CODING, MOCK_TYPE_INTERVIEW
from tracker.services import question_sets


class Command(BaseCommand):
    help = "Pre-generate ready-made mock interview question sets."

    def add_arguments(self, parser):
        parser.add_argument("--per", type=int, default=3, help="Target sets per topic and level.")
        parser.add_argument("--topic", action="append", help="Limit to these topics.")
        parser.add_argument(
            "--test-type", choices=[MOCK_TYPE_INTERVIEW, MOCK_TYPE_CODING],
            default=MOCK_TYPE_INTERVIEW, help="Pre-generate interview or coding-test sets.",
        )
        parser.add_argument(
            "--difficulty", action="append", choices=["easy", "medium", "hard"],
            help="Limit to these levels.",
        )

    def handle(self, *args, **options):
        topics = options["topic"] or list(MOCK_TOPICS)
        levels = options["difficulty"] or ["easy", "medium", "hard"]
        test_type = options["test_type"]
        for topic in topics:
            for level in levels:
                sets = MockQuestionSet.objects.filter(
                    topic__iexact=topic, focus="", difficulty=level, test_type=test_type, is_active=True
                )
                have = (
                    sum(question_sets.has_coding_examples(item.questions) for item in sets.iterator())
                    if test_type == MOCK_TYPE_CODING else sets.count()
                )
                for _ in range(max(0, options["per"] - have)):
                    try:
                        questions = question_sets.generate(topic, level, test_type=test_type)
                    except (GeminiAPIError, question_sets.InvalidQuestions) as error:
                        self.stderr.write(f"{topic}/{level}: {error}")
                        break
                    question_sets.store(topic, level, questions, test_type=test_type)
                    self.stdout.write(f"{topic}/{level}/{test_type}: added a set")
