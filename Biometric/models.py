from django.db import models


class BiometricLog(models.Model):
    """
    Raw punch log from ESSL device. IMMUTABLE: never update, never delete.
    emp_code and device_id stored as received; matching to Employee is done at processing.
    """

    emp_code = models.CharField(max_length=64, db_index=True)
    punch_time = models.DateTimeField(db_index=True)
    device_id = models.CharField(max_length=128, blank=True)
    raw_payload = models.TextField(blank=True)  # JSON or raw text from device
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "biometric_log"
        ordering = ["punch_time", "id"]
        verbose_name = "Biometric log"
        verbose_name_plural = "Biometric logs"

    def __str__(self):
        return f"{self.emp_code} @ {self.punch_time}"


class DummyEsslBiometricAttendanceData(models.Model):
    """
    Dummy/biometric device log table mirroring DeviceLogs structure
    (e.g. DeviceLogs_10_2005) for testing or staging.
    """

    DeviceLogId = models.BigIntegerField(unique=True, db_index=True)
    DownloadDate = models.DateTimeField(null=True, blank=True)
    DeviceId = models.CharField(max_length=128, blank=True)
    UserId = models.CharField(max_length=64, db_index=True, blank=True)
    LogDate = models.DateTimeField(null=True, blank=True, db_index=True)
    Direction = models.CharField(max_length=32, blank=True)
    AttDirection = models.CharField(max_length=32, blank=True)
    C1 = models.CharField(max_length=64, blank=True)
    C2 = models.CharField(max_length=64, blank=True)
    C3 = models.CharField(max_length=64, blank=True)
    C4 = models.CharField(max_length=64, blank=True)
    C5 = models.CharField(max_length=64, blank=True)
    C6 = models.CharField(max_length=64, blank=True)
    C7 = models.CharField(max_length=64, blank=True)
    WorkCode = models.CharField(max_length=64, blank=True)
    hrapp_syncstatus = models.CharField(max_length=32, blank=True)

    class Meta:
        db_table = "DummyEsslBiometricAttendanceData"
        ordering = ["-LogDate", "DeviceLogId"]
        verbose_name = "Dummy biometric attendance data"
        verbose_name_plural = "Dummy biometric attendance data"

    def __str__(self):
        return f"{self.UserId} @ {self.LogDate} (DeviceLogId={self.DeviceLogId})"


class BiometricDevice(models.Model):
    """
    Biometric device mapped to an office. device_id is the ID that appears in
    ESSL device logs (e.g. 119, 103). Used in the frontend to map devices to offices.
    """

    class DeviceType(models.TextChoices):
        FACE = "face", "Face"
        FINGER = "finger", "Finger"
        BOTH = "both", "Both"
        RFID = "rfid", "RFID"

    class DeviceDirection(models.TextChoices):
        IN = "in", "In"
        OUT = "out", "Out"
        ALTERNATE = "alternate_in_out", "Alternate In/Out"

    office = models.ForeignKey(
        "Organization.Office",
        on_delete=models.CASCADE,
        related_name="biometric_devices",
    )
    device_id = models.CharField(max_length=128, db_index=True)
    name = models.CharField(max_length=255, blank=True)
    serial_number = models.CharField(max_length=128, blank=True)
    ip_address = models.CharField(
        max_length=45,
        blank=True,
        help_text="IPv4, IPv6, or hostname",
    )
    device_location = models.CharField(max_length=255, blank=True)
    device_direction = models.CharField(
        max_length=32,
        blank=True,
        choices=DeviceDirection.choices,
    )
    device_type = models.CharField(
        max_length=16,
        blank=True,
        choices=DeviceType.choices,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "biometric_device"
        ordering = ["office", "device_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["office", "device_id"],
                name="biometric_device_office_device_id_unique",
            )
        ]
        verbose_name = "Biometric device"
        verbose_name_plural = "Biometric devices"

    def __str__(self):
        return f"{self.device_id} ({self.office.name})"
