from django.db import models
from django.db.models import F, Q

ANNUAL_ALLOWANCE = 28


class Employee(models.Model):
    full_name = models.CharField("ФИО", max_length=255)
    position = models.CharField("Должность", max_length=255)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"

    def __str__(self):
        return self.full_name


class Vacation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "На согласовании"
        APPROVED = "approved", "Согласовано"
        REJECTED = "rejected", "Отклонено"
        CANCELLED = "cancelled", "Отменено"

    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="vacations",
        verbose_name="Сотрудник",
    )
    start_date = models.DateField("Начало")
    end_date = models.DateField("Окончание")
    status = models.CharField(
        "Статус", max_length=10, choices=Status.choices, default=Status.PENDING
    )
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлена", auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Заявка на отпуск"
        verbose_name_plural = "Заявки на отпуск"
        indexes = [models.Index(fields=["employee", "start_date", "end_date"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")),
                name="vacation_dates_ordered",
            ),
            models.CheckConstraint(
                condition=Q(start_date__year=F("end_date__year")),
                name="vacation_single_year",
            ),
            models.CheckConstraint(
                condition=Q(
                    status__in=["pending", "approved", "rejected", "cancelled"]
                ),
                name="vacation_valid_status",
            ),
        ]

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    def __str__(self):
        return f"{self.employee}: {self.start_date} — {self.end_date}"


class StatusChange(models.Model):
    vacation = models.ForeignKey(
        Vacation,
        on_delete=models.PROTECT,
        related_name="history",
        verbose_name="Заявка",
    )
    from_status = models.CharField(
        "Предыдущий статус",
        max_length=10,
        choices=Vacation.Status.choices,
        blank=True,
    )
    to_status = models.CharField(
        "Новый статус", max_length=10, choices=Vacation.Status.choices
    )
    changed_by = models.CharField("Кто изменил", max_length=255)
    changed_at = models.DateTimeField("Время", auto_now_add=True)

    class Meta:
        ordering = ["changed_at", "id"]
        verbose_name = "Изменение статуса"
        verbose_name_plural = "История статусов"

    def __str__(self):
        return (
            f"Заявка {self.vacation_id}: {self.from_status} → {self.to_status}"
        )
