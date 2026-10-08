from django.db import migrations

from tracker.mock_bank import QUESTION_BANK

CODE_PREFIX = "[code] "


def seed(apps, schema_editor):
    MockQuestion = apps.get_model("tracker", "MockQuestion")
    if MockQuestion.objects.exists():
        return
    rows = []
    for topic, levels in QUESTION_BANK.items():
        for difficulty, items in levels.items():
            for item in items:
                coding = item.startswith(CODE_PREFIX)
                rows.append(MockQuestion(
                    topic=topic, difficulty=difficulty,
                    kind="coding" if coding else "concept",
                    text=item[len(CODE_PREFIX):] if coding else item,
                ))
    MockQuestion.objects.bulk_create(rows)


class Migration(migrations.Migration):
    dependencies = [("tracker", "0019_mock_question_bank")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
