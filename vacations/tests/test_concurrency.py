from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections, connections
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from vacations.models import Employee, Vacation
from vacations.services import create_vacation, employee_balance


class ConcurrentRequestTests(TransactionTestCase):
    def setUp(self):
        clock = patch(
            "vacations.services.timezone.localdate",
            return_value=date(2030, 1, 1),
        )
        clock.start()
        self.addCleanup(clock.stop)
        self.employee = Employee.objects.create(
            full_name="Сотрудник", position="Аналитик"
        )

    def run_concurrently(self, requests):
        ready = Barrier(len(requests))

        def worker(request):
            close_old_connections()
            try:
                ready.wait(timeout=10)
                url, body = request
                response = APIClient().post(url, body, format="json")
                return response.status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            return sorted(pool.map(worker, requests))

    def test_two_parallel_requests_cannot_overdraw_balance(self):
        requests = [
            (
                reverse("vacation-list"),
                {
                    "employee": self.employee.pk,
                    "start_date": "2030-02-01",
                    "end_date": "2030-02-20",
                },
            ),
            (
                reverse("vacation-list"),
                {
                    "employee": self.employee.pk,
                    "start_date": "2030-03-01",
                    "end_date": "2030-03-20",
                },
            ),
        ]
        self.assertEqual(self.run_concurrently(requests), [201, 400])
        self.assertEqual(employee_balance(self.employee.pk, 2030)["used"], 20)
        self.assertEqual(Vacation.objects.count(), 1)

    def test_two_parallel_overlapping_requests_create_only_one_record(self):
        body = {
            "employee": self.employee.pk,
            "start_date": "2030-02-01",
            "end_date": "2030-02-07",
        }
        self.assertEqual(
            self.run_concurrently([(reverse("vacation-list"), body)] * 2),
            [201, 400],
        )
        self.assertEqual(Vacation.objects.count(), 1)

    def test_two_parallel_decisions_cannot_both_succeed(self):
        vacation = create_vacation(
            employee=self.employee,
            start_date=date(2030, 2, 1),
            end_date=date(2030, 2, 7),
            changed_by="test",
        )
        requests = [
            (reverse("vacation-approve", args=[vacation.pk]), {}),
            (reverse("vacation-reject", args=[vacation.pk]), {}),
        ]
        self.assertEqual(self.run_concurrently(requests), [200, 409])
        vacation.refresh_from_db()
        self.assertIn(vacation.status, ("approved", "rejected"))
        self.assertEqual(vacation.history.count(), 2)
