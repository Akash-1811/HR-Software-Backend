# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Biometric", "0005_biometricdevice"),
    ]

    operations = [
        migrations.AddField(
            model_name="biometricdevice",
            name="serial_number",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="biometricdevice",
            name="ip_address",
            field=models.CharField(
                blank=True,
                default="",
                max_length=45,
                help_text="IPv4, IPv6, or hostname",
            ),
        ),
        migrations.AddField(
            model_name="biometricdevice",
            name="device_location",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="biometricdevice",
            name="device_direction",
            field=models.CharField(
                blank=True,
                choices=[
                    ("in", "In"),
                    ("out", "Out"),
                    ("alternate_in_out", "Alternate In/Out"),
                ],
                default="",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="biometricdevice",
            name="device_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("face", "Face"),
                    ("finger", "Finger"),
                    ("both", "Both"),
                    ("rfid", "RFID"),
                ],
                default="",
                max_length=16,
            ),
        ),
    ]
