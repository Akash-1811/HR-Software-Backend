# Generated manually — scope LeaveType to Office.

import django.db.models.deletion
from django.db import migrations, models


def assign_office_from_org(apps, schema_editor):
    LeaveType = apps.get_model("Leaves", "LeaveType")
    Office = apps.get_model("Organization", "Office")
    for lt in LeaveType.objects.all():
        office = (
            Office.objects.filter(organization_id=lt.organization_id, is_active=True)
            .order_by("id")
            .first()
        )
        if office is None:
            office = Office.objects.filter(organization_id=lt.organization_id).order_by("id").first()
        if office is None:
            raise RuntimeError(
                f"LeaveType id={lt.pk}: no Office exists for organization_id={lt.organization_id}. "
                "Create at least one office before migrating."
            )
        lt.office_id = office.pk
        lt.save(update_fields=["office_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("Leaves", "0001_initial"),
        ("Organization", "0008_department_and_employee_department"),
    ]

    operations = [
        migrations.AddField(
            model_name="leavetype",
            name="office",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="leave_types",
                to="Organization.office",
            ),
        ),
        migrations.RunPython(assign_office_from_org, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="leavetype",
            name="office",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="leave_types",
                to="Organization.office",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="leavetype",
            name="leave_type_org_code_unique",
        ),
        migrations.RemoveIndex(
            model_name="leavetype",
            name="leave_type_organiz_332242_idx",
        ),
        migrations.RemoveField(
            model_name="leavetype",
            name="organization",
        ),
        migrations.AddIndex(
            model_name="leavetype",
            index=models.Index(fields=["office", "is_active"], name="leave_type_office_active_idx"),
        ),
        migrations.AddConstraint(
            model_name="leavetype",
            constraint=models.UniqueConstraint(fields=("office", "code"), name="leave_type_office_code_unique"),
        ),
        migrations.AlterModelOptions(
            name="leavetype",
            options={"ordering": ["office", "name"]},
        ),
    ]
