from django.db import OperationalError
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler


class StatusConflict(APIException):
    status_code = 409
    default_detail = "Этот переход статуса недоступен."
    default_code = "status_conflict"


class DatabaseBusy(APIException):
    status_code = 503
    default_detail = "База данных занята. Повторите запрос."
    default_code = "database_busy"

    def __init__(self):
        super().__init__()
        self.wait = 1


def exception_handler(exc, context):
    if isinstance(exc, OperationalError) and "locked" in str(exc).lower():
        exc = DatabaseBusy()
    return drf_exception_handler(exc, context)
