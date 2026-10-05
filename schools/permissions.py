from django.shortcuts import get_object_or_404

from .models import Membership

STAFF_ROLES = [Membership.Role.OWNER, Membership.Role.STAFF]


def get_active_membership(user, school_id, staff_only=False):
    # 教室ごとの画面はどれも「承認済みの所属者か」を確認する必要があるため、判定を1か所にまとめて漏れを防ぐ
    # 権限がない場合に教室の存在自体を知らせないよう、403ではなく404にする
    filters = {"user": user, "school_id": school_id, "status": Membership.Status.ACTIVE}
    if staff_only:
        filters["role__in"] = STAFF_ROLES
    return get_object_or_404(Membership.objects.select_related("school"), **filters)


def is_staff(membership):
    return membership.role in STAFF_ROLES
