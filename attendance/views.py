from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from schools.models import Student
from schools.permissions import get_active_membership, is_staff

from .forms import NoticeForm, ReplyForm, TemplateForm
from .models import AttendanceNotice, MessageTemplate, ensure_default_templates


def visible_notices(membership):
    qs = membership.school.attendance_notices.select_related("student")
    # 欠席理由は家庭の事情を含むため、保護者には自分の子どもの連絡だけを見せる
    if not is_staff(membership):
        qs = qs.filter(student__guardian=membership.user)
    return qs


def templates_for(school, kind):
    ensure_default_templates(school)
    return school.message_templates.filter(kind=kind)


@login_required
def notice_list(request, school_id):
    membership = get_active_membership(request.user, school_id)
    notices = visible_notices(membership).annotate(reply_count=Count("replies"))
    only_unread = request.GET.get("status") == "unread"
    if only_unread:
        notices = notices.filter(status=AttendanceNotice.Status.UNREAD)
    context = {
        "school": membership.school,
        "notices": notices,
        "is_staff": is_staff(membership),
        "only_unread": only_unread,
        # 子どもが在籍していない人に「連絡する」ボタンを出しても使えないため、表示を切り替える
        "can_send": Student.objects.filter(
            school=membership.school, guardian=request.user, status=Student.Status.ACTIVE
        ).exists(),
    }
    return render(request, "attendance/list.html", context)


@login_required
def notice_create(request, school_id):
    membership = get_active_membership(request.user, school_id)
    form = NoticeForm(request.POST or None, school=membership.school, user=request.user)
    if not form.fields["student"].queryset.exists():
        messages.error(request, "在籍中のお子さまがいないため、出欠連絡を送れません。")
        return redirect("notice_list", school_id=school_id)

    if request.method == "POST" and form.is_valid():
        notice = form.save(commit=False)
        notice.school = membership.school
        notice.sender = request.user
        notice.save()
        messages.success(request, "出欠連絡を送りました。")
        return redirect("notice_detail", school_id=school_id, pk=notice.pk)

    return render(
        request,
        "attendance/notice_form.html",
        {
            "school": membership.school,
            "form": form,
            "templates": templates_for(membership.school, MessageTemplate.Kind.NOTICE),
        },
    )


@login_required
def notice_detail(request, school_id, pk):
    membership = get_active_membership(request.user, school_id)
    # 他の家庭の連絡IDを指定されても表示しないよう、見てよい一覧の中から探す
    notice = get_object_or_404(visible_notices(membership), pk=pk)
    staff = is_staff(membership)
    form = ReplyForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        reply = form.save(commit=False)
        reply.notice = notice
        reply.sender = request.user
        reply.from_staff = staff
        reply.save()
        # 運営者が返信したということは内容を確認したということなので、別に確認ボタンを押す手間を省く
        if staff and notice.status == AttendanceNotice.Status.UNREAD:
            notice.status = AttendanceNotice.Status.CONFIRMED
            notice.save(update_fields=["status"])
        return redirect("notice_detail", school_id=school_id, pk=pk)

    context = {
        "school": membership.school,
        "n": notice,
        "replies": notice.replies.all(),
        "form": form,
        "is_staff": staff,
        "templates": templates_for(membership.school, MessageTemplate.Kind.REPLY) if staff else [],
    }
    return render(request, "attendance/detail.html", context)


@login_required
@require_POST
def notice_confirm(request, school_id, pk):
    membership = get_active_membership(request.user, school_id, staff_only=True)
    notice = get_object_or_404(membership.school.attendance_notices, pk=pk)
    notice.status = AttendanceNotice.Status.CONFIRMED
    notice.save(update_fields=["status"])
    messages.success(request, "確認済みにしました。")
    # 一覧から続けて何件も確認できるよう、押した画面に戻す
    if request.POST.get("next") == "list":
        return redirect("notice_list", school_id=school_id)
    return redirect("notice_detail", school_id=school_id, pk=pk)


@login_required
def template_list(request, school_id):
    membership = get_active_membership(request.user, school_id, staff_only=True)
    ensure_default_templates(membership.school)
    return render(
        request,
        "attendance/template_list.html",
        {"school": membership.school, "templates": membership.school.message_templates.all()},
    )


@login_required
def template_edit(request, school_id, pk=None):
    membership = get_active_membership(request.user, school_id, staff_only=True)
    # 他の教室の定型文を書き換えられないよう、自分の教室の定型文の中から探す
    template = get_object_or_404(membership.school.message_templates, pk=pk) if pk else None
    form = TemplateForm(request.POST or None, instance=template)
    if request.method == "POST" and form.is_valid():
        template = form.save(commit=False)
        template.school = membership.school
        template.save()
        messages.success(request, "定型文を保存しました。")
        return redirect("template_list", school_id=school_id)
    return render(
        request, "attendance/template_form.html", {"school": membership.school, "form": form, "t": template}
    )


@login_required
@require_POST
def template_delete(request, school_id, pk):
    membership = get_active_membership(request.user, school_id, staff_only=True)
    get_object_or_404(membership.school.message_templates, pk=pk).delete()
    messages.success(request, "定型文を削除しました。")
    return redirect("template_list", school_id=school_id)
