"""Business rules shared by all state-changing API operations."""

from datetime import date

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .exceptions import StatusConflict
from .models import ANNUAL_ALLOWANCE, Employee, StatusChange, Vacation

ACTIVE_STATUSES = (Vacation.Status.PENDING, Vacation.Status.APPROVED)


def employee_balance(employee_id: int, year: int) -> dict:
    intervals = Vacation.objects.filter(
        employee_id=employee_id,
        status__in=ACTIVE_STATUSES,
        start_date__gte=date(year, 1, 1),
        start_date__lte=date(year, 12, 31),
    ).values_list("start_date", "end_date")
    used = sum((end - start).days + 1 for start, end in intervals)
    return {
        "employee": employee_id,
        "year": year,
        "total": ANNUAL_ALLOWANCE,
        "used": used,
        "remaining": ANNUAL_ALLOWANCE - used,
    }


def validate_dates(start_date: date, end_date: date) -> None:
    if end_date < start_date:
        raise ValidationError(
            {"end_date": "Окончание не может быть раньше начала."}
        )
    if start_date < timezone.localdate():
        raise ValidationError(
            {"start_date": "Нельзя подать заявку в прошлом."}
        )
    if start_date.year != end_date.year:
        raise ValidationError(
            {"end_date": "Отпуск должен быть в рамках одного года."}
        )


@transaction.atomic
def create_vacation(
    *, employee: Employee, start_date: date, end_date: date, changed_by: str
) -> Vacation:
    # On PostgreSQL this locks one employee; SQLite uses BEGIN IMMEDIATE.
    get_object_or_404(Employee.objects.select_for_update(), pk=employee.pk)
    validate_dates(start_date, end_date)
    active = Vacation.objects.filter(
        employee=employee, status__in=ACTIVE_STATUSES
    )
    if active.filter(
        start_date__lte=end_date, end_date__gte=start_date
    ).exists():
        raise ValidationError(
            {"start_date": "Даты пересекаются с действующей заявкой."}
        )
    days = (end_date - start_date).days + 1
    balance = employee_balance(employee.pk, start_date.year)
    if days > balance["remaining"]:
        remaining = balance["remaining"]
        message = f"Недостаточно дней: доступно {remaining}, нужно {days}."
        raise ValidationError({"days": message})
    vacation = Vacation.objects.create(
        employee=employee, start_date=start_date, end_date=end_date
    )
    StatusChange.objects.create(
        vacation=vacation,
        from_status="",
        to_status=vacation.status,
        changed_by=changed_by,
    )
    return vacation


@transaction.atomic
def change_status(
    *, vacation_id: int, to_status: str, changed_by: str
) -> Vacation:
    # Always lock employee first: creation and status changes share lock order.
    employee_id = get_object_or_404(Vacation, pk=vacation_id).employee_id
    get_object_or_404(Employee.objects.select_for_update(), pk=employee_id)
    vacation = get_object_or_404(
        Vacation.objects.select_for_update(), pk=vacation_id
    )
    if to_status == Vacation.Status.CANCELLED:
        if vacation.status not in ACTIVE_STATUSES:
            raise StatusConflict("Отменить можно только действующую заявку.")
        if vacation.start_date <= timezone.localdate():
            raise StatusConflict("Отпуск уже начался, отмена недоступна.")
    elif to_status in (Vacation.Status.APPROVED, Vacation.Status.REJECTED):
        if vacation.status != Vacation.Status.PENDING:
            raise StatusConflict(
                "Менять решение можно только у заявки на согласовании."
            )
    else:
        raise ValidationError({"status": "Недопустимый целевой статус."})

    previous = vacation.status
    vacation.status = to_status
    vacation.save(update_fields=["status", "updated_at"])
    StatusChange.objects.create(
        vacation=vacation,
        from_status=previous,
        to_status=to_status,
        changed_by=changed_by,
    )
    return vacation
