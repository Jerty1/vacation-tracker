from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Employee, Vacation
from .serializers import (
    ActorSerializer,
    BalanceQuerySerializer,
    BalanceSerializer,
    EmployeeSerializer,
    StatusChangeSerializer,
    VacationCreateSerializer,
    VacationFilterSerializer,
    VacationSerializer,
)
from .services import change_status, employee_balance


class CreateReadViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """No arbitrary updates or deletion of business records."""

    lookup_value_regex = r"[0-9]+"


class EmployeeViewSet(CreateReadViewSet):
    queryset = Employee.objects.all()
    serializer_class = EmployeeSerializer

    @extend_schema(
        parameters=[OpenApiParameter("year", int, required=True)],
        responses=BalanceSerializer,
    )
    @action(detail=True, methods=["get"])
    def balance(self, request, pk=None):
        employee = self.get_object()
        query = BalanceQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(
            employee_balance(employee.pk, query.validated_data["year"])
        )


@extend_schema_view(list=extend_schema(parameters=[VacationFilterSerializer]))
class VacationViewSet(CreateReadViewSet):
    queryset = Vacation.objects.all()
    serializer_class = VacationSerializer

    def get_serializer_class(self):
        if self.action == "create":
            return VacationCreateSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list":
            query = VacationFilterSerializer(data=self.request.query_params)
            query.is_valid(raise_exception=True)
            filters = query.validated_data
            if "employee" in filters:
                queryset = queryset.filter(employee_id=filters["employee"])
            if "year" in filters:
                queryset = queryset.filter(start_date__year=filters["year"])
            if "status" in filters:
                queryset = queryset.filter(status=filters["status"])
        return queryset

    @extend_schema(
        request=VacationCreateSerializer,
        responses={201: VacationSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vacation = serializer.save()
        return Response(
            VacationSerializer(vacation).data, status=status.HTTP_201_CREATED
        )

    def _change_status(self, request, target):
        body = ActorSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        vacation = change_status(
            vacation_id=self.kwargs["pk"],
            to_status=target,
            changed_by=body.validated_data["changed_by"],
        )
        return Response(VacationSerializer(vacation).data)

    @extend_schema(request=ActorSerializer, responses=VacationSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._change_status(request, Vacation.Status.APPROVED)

    @extend_schema(request=ActorSerializer, responses=VacationSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._change_status(request, Vacation.Status.REJECTED)

    @extend_schema(request=ActorSerializer, responses=VacationSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._change_status(request, Vacation.Status.CANCELLED)

    @extend_schema(responses=StatusChangeSerializer(many=True))
    @action(detail=True, methods=["get"], pagination_class=None)
    def history(self, request, pk=None):
        vacation = self.get_object()
        return Response(
            StatusChangeSerializer(vacation.history.all(), many=True).data
        )
