from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from vacations.admin import (
    StatusChangeAdmin,
    StatusChangeInline,
    VacationAdmin,
)
from vacations.exceptions import exception_handler
from vacations.models import Employee, StatusChange, Vacation
from vacations.services import change_status, create_vacation


class VacationAPITests(APITestCase):
    def setUp(self):
        clock = patch(
            "vacations.services.timezone.localdate",
            return_value=date(2030, 1, 10),
        )
        clock.start()
        self.addCleanup(clock.stop)
        self.employee = Employee.objects.create(
            full_name="Иванов Иван Иванович", position="Аналитик"
        )

    def apply(self, start="2030-02-01", end="2030-02-07", **extra):
        return self.client.post(
            reverse("vacation-list"),
            {
                "employee": self.employee.pk,
                "start_date": start,
                "end_date": end,
                **extra,
            },
            format="json",
        )

    def transition(self, pk, action, **extra):
        return self.client.post(
            reverse(f"vacation-{action}", args=[pk]), extra, format="json"
        )

    def balance(self, year=2030):
        return self.client.get(
            reverse("employee-balance", args=[self.employee.pk]),
            {"year": year},
        )

    def test_acceptance_flow(self):
        response = self.client.post(
            reverse("employee-list"),
            {"full_name": "Петрова Анна Сергеевна", "position": "Разработчик"},
        )
        self.assertEqual(response.status_code, 201)
        self.employee = Employee.objects.get(pk=response.data["id"])
        response = self.apply(changed_by="Анна")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["days"], 7)
        pk = response.data["id"]
        self.assertEqual(self.balance().data["remaining"], 21)
        response = self.transition(pk, "approve", changed_by="Руководитель")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "approved")
        self.assertEqual(
            self.balance().data,
            {
                "employee": self.employee.pk,
                "year": 2030,
                "total": 28,
                "used": 7,
                "remaining": 21,
            },
        )
        listing = self.client.get(
            reverse("vacation-list"),
            {"employee": self.employee.pk, "year": 2030, "status": "approved"},
        )
        self.assertEqual(listing.data["count"], 1)
        self.assertEqual(listing.data["results"][0]["id"], pk)
        self.assertEqual(
            self.client.get(reverse("vacation-detail", args=[pk])).status_code,
            200,
        )

    def test_employee_list_and_detail(self):
        response = self.client.get(reverse("employee-list"))
        self.assertEqual(response.data["count"], 1)
        response = self.client.get(
            reverse("employee-detail", args=[self.employee.pk])
        )
        self.assertEqual(response.data["full_name"], self.employee.full_name)

    def test_employee_requires_nonblank_name_and_position(self):
        for payload in (
            {},
            {"full_name": " ", "position": "Аналитик"},
            {"full_name": "Иван", "position": ""},
            {"full_name": "x" * 256, "position": "Аналитик"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    self.client.post(
                        reverse("employee-list"), payload
                    ).status_code,
                    400,
                )

    def test_invalid_dates(self):
        cases = [
            ("2030-02-07", "2030-02-01", "end_date"),
            ("2030-01-09", "2030-01-10", "start_date"),
            ("2029-12-01", "2029-12-05", "start_date"),
            ("2030-12-31", "2031-01-01", "end_date"),
            ("not-a-date", "2030-02-01", "start_date"),
            ("2030-02-29", "2030-03-01", "start_date"),
        ]
        for start, end, field in cases:
            with self.subTest(start=start, end=end):
                response = self.apply(start, end)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)
        self.assertFalse(Vacation.objects.exists())
        self.assertFalse(StatusChange.objects.exists())

    def test_today_is_allowed_and_counts_one_day(self):
        response = self.apply("2030-01-10", "2030-01-10")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["days"], 1)
        self.assertEqual(self.balance().data["used"], 1)

    def test_leap_day_counts_as_calendar_day(self):
        response = self.apply("2032-02-28", "2032-03-01")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["days"], 3)
        self.assertEqual(self.balance(2032).data["used"], 3)

    def test_full_28_days_allowed_but_next_day_is_rejected(self):
        self.assertEqual(
            self.apply("2030-02-01", "2030-02-28").status_code, 201
        )
        response = self.apply("2030-03-01", "2030-03-01")
        self.assertEqual(response.status_code, 400)
        self.assertIn("days", response.data)
        self.assertEqual(self.balance().data["remaining"], 0)

    def test_single_request_cannot_exceed_allowance(self):
        response = self.apply("2030-03-01", "2030-03-29")
        self.assertEqual(response.status_code, 400)
        self.assertIn("days", response.data)

    def test_pending_and_approved_both_reserve_days(self):
        first = self.apply("2030-02-01", "2030-02-14").data["id"]
        self.transition(first, "approve")
        self.assertEqual(
            self.apply("2030-03-01", "2030-03-14").status_code, 201
        )
        self.assertEqual(self.balance().data["used"], 28)
        self.assertEqual(
            self.apply("2030-04-01", "2030-04-01").status_code, 400
        )

    def test_balance_is_per_employee_and_year_without_carryover(self):
        self.apply("2030-02-01", "2030-02-28")
        self.assertEqual(self.balance(2031).data["remaining"], 28)
        self.assertEqual(
            self.apply("2031-02-01", "2031-02-28").status_code, 201
        )
        self.employee = Employee.objects.create(
            full_name="Другой сотрудник", position="Тестировщик"
        )
        self.assertEqual(self.balance().data["remaining"], 28)
        self.assertEqual(
            self.apply("2030-02-01", "2030-02-28").status_code, 201
        )

    def test_unused_year_and_last_supported_year(self):
        self.assertEqual(self.balance(2020).data["used"], 0)
        self.assertEqual(
            self.apply("9999-12-31", "9999-12-31").status_code, 201
        )
        self.assertEqual(self.balance(9999).data["used"], 1)

    def test_invalid_balance_queries(self):
        url = reverse("employee-balance", args=[self.employee.pk])
        for query in (
            {},
            {"year": "wrong"},
            {"year": 0},
            {"year": 10000},
            {"year": 2030, "typo": "x"},
        ):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(url, query).status_code, 400)

    def test_overlapping_active_requests_are_rejected_including_boundaries(
        self,
    ):
        self.apply("2030-02-10", "2030-02-15")
        for start, end in (
            ("2030-02-10", "2030-02-15"),
            ("2030-02-11", "2030-02-12"),
            ("2030-02-09", "2030-02-16"),
            ("2030-02-09", "2030-02-10"),
            ("2030-02-15", "2030-02-16"),
        ):
            with self.subTest(start=start, end=end):
                self.assertEqual(self.apply(start, end).status_code, 400)
        self.assertEqual(Vacation.objects.count(), 1)

    def test_adjacent_requests_do_not_overlap(self):
        self.apply("2030-02-10", "2030-02-15")
        self.assertEqual(
            self.apply("2030-02-08", "2030-02-09").status_code, 201
        )
        self.assertEqual(
            self.apply("2030-02-16", "2030-02-17").status_code, 201
        )

    def test_approved_request_also_blocks_overlap(self):
        pk = self.apply().data["id"]
        self.transition(pk, "approve")
        self.assertEqual(self.apply().status_code, 400)

    def test_rejected_and_cancelled_requests_free_balance_and_dates(self):
        for action in ("reject", "cancel"):
            with self.subTest(action=action):
                pk = self.apply("2030-02-01", "2030-02-28").data["id"]
                self.assertEqual(self.transition(pk, action).status_code, 200)
                self.assertEqual(self.balance().data["remaining"], 28)
        self.assertEqual(
            self.apply("2030-02-01", "2030-02-28").status_code, 201
        )

    def test_all_status_transitions(self):
        allowed = {
            "pending": {
                "approve": "approved",
                "reject": "rejected",
                "cancel": "cancelled",
            },
            "approved": {"cancel": "cancelled"},
            "rejected": {},
            "cancelled": {},
        }
        for initial, actions in allowed.items():
            for action in ("approve", "reject", "cancel"):
                with self.subTest(initial=initial, action=action):
                    vacation = Vacation.objects.create(
                        employee=self.employee,
                        start_date=date(2030, 2, 1),
                        end_date=date(2030, 2, 1),
                        status=initial,
                    )
                    before = StatusChange.objects.count()
                    response = self.transition(vacation.pk, action)
                    vacation.refresh_from_db()
                    if action in actions:
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(vacation.status, actions[action])
                        self.assertEqual(
                            StatusChange.objects.count(), before + 1
                        )
                    else:
                        self.assertEqual(response.status_code, 409)
                        self.assertEqual(vacation.status, initial)
                        self.assertEqual(StatusChange.objects.count(), before)

    def test_cancellation_is_forbidden_on_or_after_start(self):
        for start in (date(2030, 1, 9), date(2030, 1, 10)):
            for state in ("pending", "approved"):
                with self.subTest(start=start, state=state):
                    vacation = Vacation.objects.create(
                        employee=self.employee,
                        start_date=start,
                        end_date=date(2030, 1, 11),
                        status=state,
                    )
                    self.assertEqual(
                        self.transition(vacation.pk, "cancel").status_code, 409
                    )

    def test_creation_and_transition_are_recorded_in_history(self):
        pk = self.apply(changed_by="Сотрудник").data["id"]
        self.transition(pk, "approve", changed_by="Руководитель")
        self.transition(pk, "cancel", changed_by="Сотрудник")
        response = self.client.get(reverse("vacation-history", args=[pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [
                (row["from_status"], row["to_status"], row["changed_by"])
                for row in response.data
            ],
            [
                ("", "pending", "Сотрудник"),
                ("pending", "approved", "Руководитель"),
                ("approved", "cancelled", "Сотрудник"),
            ],
        )
        self.assertTrue(all(row["changed_at"] for row in response.data))

    def test_default_actor_is_explicitly_anonymous(self):
        pk = self.apply().data["id"]
        self.transition(pk, "approve")
        self.assertEqual(
            set(StatusChange.objects.values_list("changed_by", flat=True)),
            {"anonymous"},
        )

    def test_actor_cannot_be_empty_or_too_long(self):
        for actor in ("", " ", "x" * 256):
            self.assertEqual(self.apply(changed_by=actor).status_code, 400)
        pk = self.apply().data["id"]
        self.assertEqual(
            self.transition(pk, "approve", changed_by="").status_code, 400
        )
        self.assertEqual(Vacation.objects.get(pk=pk).status, "pending")

    def test_client_cannot_set_status_or_unknown_fields(self):
        self.assertEqual(self.apply(status="approved").status_code, 400)
        self.assertEqual(self.apply(typo="value").status_code, 400)
        pk = self.apply().data["id"]
        self.assertEqual(
            self.transition(pk, "approve", status="rejected").status_code, 400
        )

    def test_missing_fields_and_unknown_employee(self):
        self.assertEqual(
            self.client.post(
                reverse("vacation-list"), {}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(self.apply(employee=99999).status_code, 400)

    def test_missing_resources_return_404(self):
        for route in (
            "employee-detail",
            "vacation-detail",
            "vacation-history",
        ):
            self.assertEqual(
                self.client.get(reverse(route, args=[99999])).status_code, 404
            )
        self.assertEqual(
            self.client.get(
                reverse("employee-balance", args=[99999]), {"year": 2030}
            ).status_code,
            404,
        )
        for action in ("approve", "reject", "cancel"):
            self.assertEqual(self.transition(99999, action).status_code, 404)

    def test_arbitrary_updates_and_deletions_are_not_available(self):
        pk = self.apply().data["id"]
        for route, target in (
            ("vacation-detail", pk),
            ("employee-detail", self.employee.pk),
        ):
            for method in (
                self.client.put,
                self.client.patch,
                self.client.delete,
            ):
                self.assertEqual(
                    method(
                        reverse(route, args=[target]), {}, format="json"
                    ).status_code,
                    405,
                )

    def test_list_filters_and_pagination(self):
        pk = self.apply().data["id"]
        for filters, expected in (
            ({"year": 2031}, 0),
            ({"employee": 99999}, 0),
            ({"status": "approved"}, 0),
            ({"status": "pending", "page": 1}, 1),
        ):
            response = self.client.get(reverse("vacation-list"), filters)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["count"], expected)
        self.assertEqual(
            self.client.get(reverse("vacation-list")).data["results"][0]["id"],
            pk,
        )
        self.assertEqual(
            self.client.get(reverse("vacation-list"), {"page": 2}).status_code,
            404,
        )

    def test_invalid_list_filters_are_not_silently_ignored(self):
        for filters in (
            {"status": "oops"},
            {"employee": "oops"},
            {"year": "oops"},
            {"year": 10000},
            {"page": 0},
            {"typo": "x"},
        ):
            self.assertEqual(
                self.client.get(reverse("vacation-list"), filters).status_code,
                400,
            )

    def test_atomic_creation_rolls_back_if_history_cannot_be_saved(self):
        with patch(
            "vacations.services.StatusChange.objects.create",
            side_effect=RuntimeError("history unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                create_vacation(
                    employee=self.employee,
                    start_date=date(2030, 2, 1),
                    end_date=date(2030, 2, 7),
                    changed_by="test",
                )
        self.assertFalse(Vacation.objects.exists())

    def test_atomic_transition_rolls_back_if_history_cannot_be_saved(self):
        pk = self.apply().data["id"]
        with patch(
            "vacations.services.StatusChange.objects.create",
            side_effect=RuntimeError("history unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                change_status(
                    vacation_id=pk, to_status="approved", changed_by="test"
                )
        self.assertEqual(Vacation.objects.get(pk=pk).status, "pending")
        self.assertEqual(StatusChange.objects.count(), 1)

    def test_service_rejects_unsupported_target_status(self):
        pk = self.apply().data["id"]
        with self.assertRaisesMessage(
            ValidationError, "Недопустимый целевой статус"
        ):
            change_status(
                vacation_id=pk, to_status="pending", changed_by="test"
            )

    def test_schema_and_swagger_are_available(self):
        self.assertEqual(self.client.get(reverse("schema")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("swagger-ui")).status_code, 200
        )
        self.assertEqual(self.client.get("/api/").status_code, 200)

    def test_database_constraints(self):
        for start, end, state in (
            (date(2030, 2, 2), date(2030, 2, 1), "pending"),
            (date(2030, 12, 31), date(2031, 1, 1), "pending"),
            (date(2030, 2, 1), date(2030, 2, 1), "oops"),
        ):
            with self.subTest(start=start, end=end, state=state):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Vacation.objects.create(
                        employee=self.employee,
                        start_date=start,
                        end_date=end,
                        status=state,
                    )


class AdminTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser(
            username="reviewer", email="", password=None
        )
        self.client.force_login(user)
        self.employee = Employee.objects.create(
            full_name="Тест", position="Аналитик"
        )
        self.vacation = Vacation.objects.create(
            employee=self.employee,
            start_date=date(2030, 2, 1),
            end_date=date(2030, 2, 2),
        )
        self.history = StatusChange.objects.create(
            vacation=self.vacation,
            from_status="",
            to_status="pending",
            changed_by="test",
        )

    def test_admin_lists_and_readonly_details(self):
        for model, instance in (
            ("employee", self.employee),
            ("vacation", self.vacation),
            ("statuschange", self.history),
        ):
            self.assertEqual(
                self.client.get(
                    reverse(f"admin:vacations_{model}_changelist")
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.get(
                    reverse(
                        f"admin:vacations_{model}_change", args=[instance.pk]
                    )
                ).status_code,
                200,
            )
        self.assertEqual(
            self.client.get(
                reverse("admin:vacations_vacation_add")
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse(
                    "admin:vacations_vacation_change", args=[self.vacation.pk]
                ),
                {"status": "approved"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse(
                    "admin:vacations_statuschange_delete",
                    args=[self.history.pk],
                )
            ).status_code,
            403,
        )

    def test_readonly_admin_hooks_and_model_labels(self):
        from django.contrib.admin import site

        for admin_class, model in (
            (VacationAdmin, Vacation),
            (StatusChangeAdmin, StatusChange),
        ):
            instance = admin_class(model, site)
            self.assertFalse(instance.has_add_permission(None))
            self.assertFalse(instance.has_change_permission(None))
            self.assertFalse(instance.has_delete_permission(None))
        inline = StatusChangeInline(Vacation, site)
        self.assertFalse(inline.has_add_permission(None))
        self.assertFalse(inline.has_change_permission(None))
        self.assertEqual(str(self.employee), "Тест")
        self.assertIn("2030-02-01", str(self.vacation))
        self.assertIn("pending", str(self.history))


class ExceptionHandlerTests(TestCase):
    def test_database_lock_returns_retryable_503(self):
        from django.db import OperationalError

        response = exception_handler(
            OperationalError("database is locked"), {}
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "1")

    def test_unrelated_database_errors_are_not_hidden(self):
        from django.db import OperationalError

        self.assertIsNone(
            exception_handler(OperationalError("unrelated failure"), {})
        )
