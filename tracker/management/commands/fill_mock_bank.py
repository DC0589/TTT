from django.core.management.base import BaseCommand

from tracker.ai_interview import GeminiAPIError
from tracker.mock_topics import MOCK_TOPICS
from tracker.models import MockQuestionSet
from tracker.services import question_sets


class Command(BaseCommand):
    help = "Pre-generate ready-made mock interview question sets."

    def add_arguments(self, parser):
        parser.add_argument("--per", type=int, default=3, help="Target sets per topic and level.")
        parser.add_argument("--topic", action="append", help="Limit to these topics.")
        parser.add_argument(
            "--difficulty", action="append", choices=["easy", "medium", "hard"],
            help="Limit to these levels.",
        )

    def handle(self, *args, **options):
        topics = options["topic"] or list(MOCK_TOPICS)
        levels = options["difficulty"] or ["easy", "medium", "hard"]
        for topic in topics:
            for level in levels:
                have = MockQuestionSet.objects.filter(
                    topic__iexact=topic, difficulty=level, is_active=True
                ).count()
                for _ in range(max(0, options["per"] - have)):
                    try:
                        questions = question_sets.generate(topic, level)
                    except (GeminiAPIError, question_sets.InvalidQuestions) as error:
                        self.stderr.write(f"{topic}/{level}: {error}")
                        break
                    question_sets.store(topic, level, questions)
                    self.stdout.write(f"{topic}/{level}: added a set")
