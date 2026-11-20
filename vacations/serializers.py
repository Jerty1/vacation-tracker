from rest_framework import serializers

from .models import Employee, StatusChange, Vacation
from .services import create_vacation


class StrictInputMixin:
    """Reject typos and read-only inputs instead of silently ignoring them."""

    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            writable = {
                name
                for name, field in self.fields.items()
                if not field.read_only
            }
            unexpected = set(data.keys()) - writable
            if unexpected:
                raise serializers.ValidationError(
                    {
                        key: "Неизвестное или недоступное для записи поле."
                        for key in unexpected
                    }
                )
        return super().to_internal_value(data)


class EmployeeSerializer(StrictInputMixin, serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = ["id", "full_name", "position", "created_at"]
        read_only_fields = ["id", "created_at"]


class ActorSerializer(StrictInputMixin, serializers.Serializer):
    changed_by = serializers.CharField(max_length=255, default="anonymous")


class VacationCreateSerializer(StrictInputMixin, serializers.ModelSerializer):
    changed_by = serializers.CharField(
        max_length=255, default="anonymous", write_only=True
    )

    class Meta:
        model = Vacation
        fields = ["employee", "start_date", "end_date", "changed_by"]

    def create(self, validated_data):
        return create_vacation(**validated_data)


class VacationSerializer(serializers.ModelSerializer):
    days = serializers.IntegerField(read_only=True)
    status_display = serializers.CharField(
        source="get_status_display", read_only=True
    )

    class Meta:
        model = Vacation
        fields = [
            "id",
            "employee",
            "start_date",
            "end_date",
            "days",
            "status",
            "status_display",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class StatusChangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = StatusChange
        fields = ["id", "from_status", "to_status", "changed_by", "changed_at"]
        read_only_fields = fields


class BalanceQuerySerializer(StrictInputMixin, serializers.Serializer):
    year = serializers.IntegerField(min_value=1, max_value=9999)


class BalanceSerializer(serializers.Serializer):
    employee = serializers.IntegerField()
    year = serializers.IntegerField()
    total = serializers.IntegerField()
    used = serializers.IntegerField()
    remaining = serializers.IntegerField()


class VacationFilterSerializer(StrictInputMixin, serializers.Serializer):
    employee = serializers.IntegerField(min_value=1, required=False)
    year = serializers.IntegerField(
        min_value=1, max_value=9999, required=False
    )
    status = serializers.ChoiceField(
        choices=Vacation.Status.choices, required=False
    )
    page = serializers.IntegerField(min_value=1, required=False)
