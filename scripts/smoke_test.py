"""Run the HTTP acceptance flow, restart and verify persistence."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent


def http(base_url, path, method="GET", body=None, expected=200):
    request = Request(
        base_url + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        response = urlopen(request, timeout=5)
    except HTTPError as exc:
        response = exc
    with response:
        data = json.load(response)
        if response.status != expected:
            raise AssertionError(f"{method} {path}: {response.status}, {data}")
        return data


@contextmanager
def server(env, base_url, port):
    with tempfile.TemporaryFile(mode="w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "manage.py",
                "runserver",
                f"127.0.0.1:{port}",
                "--noreload",
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read())
                try:
                    http(base_url, "/api/")
                    break
                except (URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise RuntimeError(
                    "The development server did not start in 20 seconds."
                )
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main():
    with tempfile.TemporaryDirectory(prefix="vacation-smoke-") as temp:
        env = {
            **os.environ,
            "DB_ENGINE": "sqlite",
            "SQLITE_PATH": str(Path(temp) / "acceptance.sqlite3"),
            "DJANGO_DEBUG": "true",
            "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost",
        }
        subprocess.run(
            [sys.executable, "manage.py", "migrate", "--noinput"],
            cwd=ROOT,
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        year = date.today().year + 1
        with server(env, base_url, port):
            employee = http(
                base_url,
                "/api/employees/",
                "POST",
                {
                    "full_name": "Тестовый сотрудник",
                    "position": "Аналитик",
                },
                expected=201,
            )
            vacation = http(
                base_url,
                "/api/vacations/",
                "POST",
                {
                    "employee": employee["id"],
                    "start_date": f"{year}-02-01",
                    "end_date": f"{year}-02-07",
                },
                expected=201,
            )
            if vacation["status"] != "pending" or vacation["days"] != 7:
                raise AssertionError(vacation)
            balance_url = (
                f"/api/employees/{employee['id']}/balance/?year={year}"
            )
            if http(base_url, balance_url)["remaining"] != 21:
                raise AssertionError("Pending days were not reserved.")
            vacation_url = f"/api/vacations/{vacation['id']}/"
            http(
                base_url,
                vacation_url + "approve/",
                "POST",
                {"changed_by": "reviewer"},
            )
            http(base_url, vacation_url + "reject/", "POST", {}, expected=409)
        # A new Python process uses the same database file.
        with server(env, base_url, port):
            if http(base_url, vacation_url)["status"] != "approved":
                raise AssertionError("Vacation did not survive the restart.")
            if (
                http(base_url, f"/api/employees/{employee['id']}/")["id"]
                != employee["id"]
            ):
                raise AssertionError("Employee did not survive the restart.")
            if http(base_url, balance_url)["used"] != 7:
                raise AssertionError("Balance changed after the restart.")
            listing = http(
                base_url,
                f"/api/vacations/?employee={employee['id']}&year={year}",
            )
            if listing["count"] != 1:
                raise AssertionError(listing)
        print("PASS: HTTP acceptance flow and data persistence after restart.")


if __name__ == "__main__":
    main()
