from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("Shifts", "0004_shift_is_default_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="shift",
            name="weekoff_days",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="shift",
            name="min_working_hours",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
        ),
        migrations.AddField(
            model_name="shift",
            name="lunch_break_minutes",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="shift",
            name="tea_break_minutes",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="shift",
            name="lunch_break_paid",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="shift",
            name="tea_breaks_paid",
            field=models.BooleanField(default=True),
        ),
    ]
