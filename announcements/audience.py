from django.db.models import Q

from schools.models import Membership, Student

from .models import Announcement

A = Announcement.Audience


def visible_to_guardian_q(school, user):
    """保護者が見てよいお知らせの条件。自分の在籍中の子どもが宛先に含まれるものだけにする。"""
    children = list(Student.objects.filter(school=school, guardian=user, status=Student.Status.ACTIVE))
    cohorts = {c.cohort for c in children if c.cohort is not None}
    group_ids = {c.group_id for c in children if c.group_id}
    return (
        Q(audience=A.ALL)
        | Q(audience=A.GRADE, target_grades__cohort__in=cohorts)
        | Q(audience=A.GROUP, target_groups__in=group_ids)
        | Q(audience=A.INDIVIDUAL, target_students__in=[c.id for c in children])
    )


def target_students(announcement):
    """このお知らせの宛先になっている、在籍中の生徒。"""
    students = Student.objects.filter(school=announcement.school, status=Student.Status.ACTIVE)
    if announcement.audience == A.GRADE:
        cohorts = set(announcement.target_grades.values_list("cohort", flat=True))
        # 学年は生年月日から計算する値でDBの列ではないため、取得してから絞り込む
        return [s for s in students if s.cohort in cohorts]
    if announcement.audience == A.GROUP:
        return list(students.filter(group__in=announcement.target_groups.all()))
    if announcement.audience == A.INDIVIDUAL:
        return list(students.filter(id__in=announcement.target_students.values("id")))
    return list(students)


def target_guardians(announcement):
    """既読の分母になる保護者。宛先でない保護者まで「未読」に数えないようにする。"""
    memberships = announcement.school.memberships.filter(
        role=Membership.Role.GUARDIAN, status=Membership.Status.ACTIVE
    ).select_related("user")
    if announcement.audience == A.ALL:
        return [m.user for m in memberships]
    guardian_ids = {s.guardian_id for s in target_students(announcement)}
    return [m.user for m in memberships if m.user_id in guardian_ids]
