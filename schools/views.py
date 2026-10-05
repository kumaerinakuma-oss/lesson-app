from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.forms import SignupForm

from .forms import ChildForm, GroupForm, StudentForm
from .models import Membership, School, Student, StudentGroup
from .permissions import get_active_membership


@login_required
def home(request):
    # 所属ごとに教室名を表示するため、教室もまとめて取得してSQLの回数を減らす
    memberships = request.user.memberships.select_related("school")
    children = request.user.children.select_related("school")
    # 運営者がトップ画面ですぐ気づけるよう、承認待ちの件数を教室ごとに出す
    memberships = memberships.annotate(
        pending_count=Count("school__students", filter=Q(school__students__status=Student.Status.PENDING))
    )
    return render(request, "schools/home.html", {"memberships": memberships, "children": children})


def join(request, token):
    # トークンが一致しない場合は教室の存在自体を知らせないよう、404にする
    school = get_object_or_404(School, invite_token=token)

    # すでにアカウントを持つ保護者（兄弟の追加・別教室への参加）にはアカウント作成欄を出さないため
    user_form = None if request.user.is_authenticated else SignupForm(request.POST or None, prefix="user")
    child_form = ChildForm(request.POST or None, prefix="child")

    if request.method == "POST":
        # 片方だけ検証すると、もう片方のエラーが一度に表示されず入力し直しが増えるため、両方を先に検証する
        forms_valid = [child_form.is_valid(), user_form is None or user_form.is_valid()]
        if all(forms_valid):
            try:
                # アカウントだけ作られて生徒が登録されない、という中途半端な状態を防ぐため
                with transaction.atomic():
                    user = request.user if user_form is None else user_form.save()
                    # 運営者が自分の招待リンクを開いても、運営者の権限が保護者に上書きされないようにする
                    membership, _ = Membership.objects.get_or_create(
                        school=school,
                        user=user,
                        defaults={"role": Membership.Role.GUARDIAN, "status": Membership.Status.PENDING},
                    )
                    if membership.status == Membership.Status.LEFT:
                        # 一度退会した保護者が再び申請したときは、承認をやり直してもらう
                        membership.status = Membership.Status.PENDING
                        membership.save(update_fields=["status"])
                    student = child_form.save(commit=False)
                    student.school = school
                    student.guardian = user
                    student.status = Student.Status.PENDING
                    student.save()
            except IntegrityError:
                if user_form is None:
                    raise
                # 二重クリックなどで同時に送信されると、入力チェックを両方通過したあとDBの一意制約で弾かれるため、
                # エラー画面ではなく入力画面に戻して知らせる
                user_form.add_error("email", "このメールアドレスはすでに登録されています。ログインしてからこのページを開いてください。")
                return render(request, "schools/join.html", {"school": school, "user_form": user_form, "child_form": child_form})

            if user_form is not None:
                # 登録直後にもう一度ログインさせる手間を省くため
                login(request, user)
            messages.success(request, f"{school.name}に申請しました。運営者の承認をお待ちください。")
            return redirect("home")

    return render(request, "schools/join.html", {"school": school, "user_form": user_form, "child_form": child_form})


def get_staff_school(user, school_id):
    return get_active_membership(user, school_id, staff_only=True).school


@login_required
def approvals(request, school_id):
    school = get_staff_school(request.user, school_id)
    pending = school.students.filter(status=Student.Status.PENDING).select_related("guardian").order_by("id")
    return render(request, "schools/approvals.html", {"school": school, "pending": pending})


@login_required
@require_POST
def decide(request, school_id, student_id, action):
    # 想定外の文字列が「却下」扱いになって生徒が消えないよう、許可する操作を限定する
    if action not in ("approve", "reject"):
        raise Http404
    school = get_staff_school(request.user, school_id)
    # 生徒IDだけで探すと他の教室の生徒も操作できてしまうため、教室でも絞り込む
    student = get_object_or_404(Student, id=student_id, school=school, status=Student.Status.PENDING)

    with transaction.atomic():
        if action == "approve":
            student.status = Student.Status.ACTIVE
            student.save(update_fields=["status"])
            # 子どもが承認された保護者は、お知らせなどを見られるよう教室の一員として承認する
            Membership.objects.filter(school=school, user=student.guardian, status=Membership.Status.PENDING).update(
                status=Membership.Status.ACTIVE
            )
            messages.success(request, f"{student.name}さんを承認しました。")
        else:
            name, guardian = student.name, student.guardian
            # 承認前の申請は出欠や月謝の履歴を持たないため、退会扱いにせず削除して名簿を汚さない
            student.delete()
            # 子どもが1人も残らない承認待ちの保護者は、教室に残しておく理由がないため所属も取り消す
            if not school.students.filter(guardian=guardian).exists():
                Membership.objects.filter(school=school, user=guardian, status=Membership.Status.PENDING).delete()
            messages.success(request, f"{name}さんの申請を却下しました。")

    return redirect("approvals", school_id=school.id)


def sync_guardian_membership(school, guardian):
    """子どもの在籍状況に合わせて、保護者が教室の画面を見られるかどうかを切り替える。"""
    # 運営者・スタッフが自分の子どもを通わせている場合に、運営の権限まで失わないよう保護者の所属だけを対象にする
    membership = Membership.objects.filter(school=school, user=guardian, role=Membership.Role.GUARDIAN).first()
    if membership is None:
        return
    statuses = set(school.students.filter(guardian=guardian).values_list("status", flat=True))
    if Student.Status.ACTIVE in statuses:
        new_status = Membership.Status.ACTIVE
    elif Student.Status.PENDING in statuses:
        new_status = Membership.Status.PENDING
    else:
        # 在籍中の子どもがいなくなった保護者には、お知らせなど教室の情報を見せ続けない
        new_status = Membership.Status.LEFT
    if membership.status != new_status:
        membership.status = new_status
        membership.save(update_fields=["status"])


@login_required
def member_list(request, school_id):
    school = get_staff_school(request.user, school_id)
    status = request.GET.get("status") or Student.Status.ACTIVE
    students = school.students.select_related("group", "guardian")
    if status in Student.Status.values:
        students = students.filter(status=status)
    group_id = request.GET.get("group", "")
    if group_id == "none":
        students = students.filter(group__isnull=True)
    elif group_id.isdigit():
        students = students.filter(group_id=int(group_id))
    context = {
        "school": school,
        "students": students.order_by("group__name", "name"),
        "groups": school.groups.all(),
        "status": status,
        "group_id": group_id,
        "status_choices": Student.Status.choices,
    }
    return render(request, "schools/member_list.html", context)


@login_required
def member_detail(request, school_id, student_id):
    school = get_staff_school(request.user, school_id)
    # 他の教室の生徒の個人情報を見られないよう、自分の教室の生徒の中から探す
    student = get_object_or_404(school.students.select_related("group", "guardian"), pk=student_id)
    form = StudentForm(request.POST or None, instance=student, school=school)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "会員情報を保存しました。")
        return redirect("member_detail", school_id=school_id, student_id=student_id)
    unpaid = student.invoices.filter(status="unpaid")
    context = {
        "school": school,
        "s": student,
        "form": form,
        "unpaid_count": unpaid.count(),
        "unpaid_total": unpaid.aggregate(total=Sum("amount", default=0))["total"],
        "siblings": school.students.filter(guardian=student.guardian).exclude(pk=student.pk),
    }
    return render(request, "schools/member_detail.html", context)


@login_required
@require_POST
def member_status(request, school_id, student_id, action):
    if action not in ("withdraw", "restore"):
        raise Http404
    school = get_staff_school(request.user, school_id)
    student = get_object_or_404(school.students, pk=student_id)
    with transaction.atomic():
        if action == "withdraw" and student.status == Student.Status.ACTIVE:
            # 月謝や出欠の履歴を残すため、行を消さずに状態だけを変える
            student.status = Student.Status.WITHDRAWN
            messages.success(request, f"{student.name}さんを退会にしました。")
        elif action == "restore" and student.status == Student.Status.WITHDRAWN:
            student.status = Student.Status.ACTIVE
            messages.success(request, f"{student.name}さんを在籍に戻しました。")
        student.save(update_fields=["status"])
        sync_guardian_membership(school, student.guardian)
    return redirect("member_detail", school_id=school_id, student_id=student_id)


@login_required
def group_list(request, school_id, pk=None):
    school = get_staff_school(request.user, school_id)
    group = get_object_or_404(school.groups, pk=pk) if pk else None
    form = GroupForm(request.POST or None, instance=group, school=school)
    if request.method == "POST" and form.is_valid():
        group = form.save(commit=False)
        group.school = school
        group.save()
        messages.success(request, "グループを保存しました。")
        return redirect("group_list", school_id=school_id)
    groups = school.groups.annotate(
        # 退会した生徒まで数えると、今そのグループに何人いるのか分からなくなるため在籍中だけ数える
        student_count=Count("students", filter=Q(students__status=Student.Status.ACTIVE))
    )
    return render(request, "schools/group_list.html", {"school": school, "groups": groups, "form": form, "editing": group})


@login_required
@require_POST
def group_delete(request, school_id, pk):
    school = get_staff_school(request.user, school_id)
    get_object_or_404(school.groups, pk=pk).delete()
    messages.success(request, "グループを削除しました。所属していた生徒は未所属になります。")
    return redirect("group_list", school_id=school_id)
