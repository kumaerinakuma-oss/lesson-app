import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from schools.models import Membership, Student
from schools.permissions import get_active_membership, is_staff

from .audience import target_guardians, visible_to_guardian_q
from .forms import AnnouncementForm, CommentForm
from .models import Announcement, AnnouncementAttachment, AnnouncementRead, AnnouncementRevision, Comment, Reaction


def visible_announcements(membership):
    qs = membership.school.announcements.all()
    if not is_staff(membership):
        # 下書きは作成途中の内容なので、保護者には見せない。宛先に含まれないお知らせも見せない
        # 宛先との結合で同じお知らせが複数行になるため、IDの絞り込みに使って重複を避ける
        allowed = qs.filter(visible_to_guardian_q(membership.school, membership.user)).values("id")
        qs = qs.filter(status=Announcement.Status.PUBLISHED, id__in=allowed)
    return qs


def is_unread(announcement, read_at):
    # 一度読んでいても、そのあと内容が編集されていれば読み直してもらうため「未読」扱いにする
    if announcement.status != Announcement.Status.PUBLISHED:
        return False
    return read_at is None or read_at < announcement.content_updated_at


@login_required
def announcement_list(request, school_id):
    membership = get_active_membership(request.user, school_id)
    # 一覧で添付・コメントの有無を示すため、件数をまとめて数える
    announcements = list(visible_announcements(membership).annotate(
            # 2種類を同時に数えると結合で件数が掛け算になるため、重複を除いて数える
            attachment_count=Count("attachments", distinct=True),
            comment_count=Count("comments", distinct=True),
        ))
    # 1件ずつ既読を調べるとSQLが件数分発行されるため、自分の既読をまとめて取得する
    reads = dict(
        AnnouncementRead.objects.filter(user=request.user, announcement__in=announcements).values_list(
            "announcement_id", "read_at"
        )
    )
    for a in announcements:
        a.unread = is_unread(a, reads.get(a.id))
    return render(
        request,
        "announcements/list.html",
        {"school": membership.school, "announcements": announcements, "is_staff": is_staff(membership)},
    )


@login_required
def announcement_detail(request, school_id, pk):
    membership = get_active_membership(request.user, school_id)
    # 他の教室のお知らせIDを指定されても表示しないよう、見てよい一覧の中から探す
    announcement = get_object_or_404(visible_announcements(membership), pk=pk)

    if announcement.status == Announcement.Status.PUBLISHED:
        # 編集後に開き直したことも記録するため、作成だけでなく日時の更新も行う
        AnnouncementRead.objects.update_or_create(
            announcement=announcement, user=request.user, defaults={"read_at": timezone.now()}
        )

    counts = dict(announcement.reactions.values_list("emoji").annotate(n=Count("id")))
    mine = set(announcement.reactions.filter(user=request.user).values_list("emoji", flat=True))
    reactions = [
        {"emoji": value, "label": label, "count": counts.get(value, 0), "mine": value in mine}
        for value, label in Reaction.Emoji.choices
    ]

    context = {
        "school": membership.school,
        "a": announcement,
        "reactions": reactions,
        "revisions": announcement.revisions.select_related("edited_by"),
        "attachments": announcement.attachments.all(),
        "comments": comments_with_names(announcement, request.user, membership),
        "comment_form": CommentForm(),
        "is_staff": is_staff(membership),
        # 個人宛の場合に他の家庭の子どもの名前が見えないよう、詳しい宛先は運営者にだけ渡す
        "audience_label": announcement.audience_label() if is_staff(membership) else "",
    }
    if is_staff(membership) and announcement.status == Announcement.Status.PUBLISHED:
        context.update(read_status(announcement))
    return render(request, "announcements/detail.html", context)


def read_status(announcement):
    # 運営者が「誰に個別で声をかけるべきか」を判断できるよう、件数だけでなく未読者の名前も出す
    guardians = target_guardians(announcement)
    reads = dict(announcement.reads.values_list("user_id", "read_at"))
    unread_users = [u for u in guardians if is_unread(announcement, reads.get(u.id))]
    return {"guardian_count": len(guardians), "read_count": len(guardians) - len(unread_users), "unread_users": unread_users}


@login_required
def announcement_create(request, school_id):
    membership = get_active_membership(request.user, school_id, staff_only=True)
    form = AnnouncementForm(request.POST or None, request.FILES or None, school=membership.school)
    if request.method == "POST" and form.is_valid():
        announcement = form.save(commit=False)
        announcement.school = membership.school
        announcement.author = request.user
        if "publish" in request.POST:
            publish(announcement)
        with transaction.atomic():
            announcement.save()
            form.save_targets(announcement)
            save_attachments(announcement, form.cleaned_data["attachments"])
        messages.success(request, "公開しました。" if "publish" in request.POST else "下書きを保存しました。")
        return redirect("announcement_detail", school_id=school_id, pk=announcement.pk)
    return render(request, "announcements/form.html", {"school": membership.school, "form": form})


def publish(announcement):
    now = timezone.now()
    announcement.status = Announcement.Status.PUBLISHED
    announcement.published_at = now
    announcement.content_updated_at = now


@login_required
def announcement_edit(request, school_id, pk):
    membership = get_active_membership(request.user, school_id, staff_only=True)
    announcement = get_object_or_404(membership.school.announcements, pk=pk)
    # フォームに入力値が反映される前の内容を、履歴として残すために控えておく
    before = {"title": announcement.title, "body": announcement.body}
    old_audience = announcement.audience_label()
    form = AnnouncementForm(request.POST or None, request.FILES or None, instance=announcement, school=membership.school)

    if request.method == "POST" and form.is_valid():
        new_files = form.cleaned_data["attachments"]
        # 他のお知らせの添付IDを送り込まれても消せないよう、このお知らせの添付に限定する
        to_delete = list(announcement.attachments.filter(id__in=request.POST.getlist("delete_attachments")))

        with transaction.atomic():
            announcement = form.save(commit=False)
            announcement.save()
            form.save_targets(announcement)
            if announcement.status == Announcement.Status.PUBLISHED:
                text_changed = any(f in form.changed_data for f in ("title", "body"))
                notes = [attachment_note(new_files, to_delete)]
                new_audience = announcement.audience_label()
                if new_audience != old_audience:
                    # あとから宛先を広げた・狭めたことが分かるよう、履歴に残す
                    notes.append(f"宛先：{old_audience} → {new_audience}")
                note = "\n".join(n for n in notes if n)
                # 保存ボタンを押しただけで中身が変わっていない場合に、保護者へ無駄な「未読」を出さないため
                if text_changed or note:
                    AnnouncementRevision.objects.create(
                        announcement=announcement, edited_by=request.user, attachment_note=note, **before
                    )
                    announcement.content_updated_at = timezone.now()
                    announcement.save(update_fields=["content_updated_at"])
            elif "publish" in request.POST:
                publish(announcement)
                announcement.save()
            save_attachments(announcement, new_files)
            delete_attachments(to_delete)
        messages.success(request, "保存しました。")
        return redirect("announcement_detail", school_id=school_id, pk=announcement.pk)

    return render(
        request,
        "announcements/form.html",
        {"school": membership.school, "form": form, "a": announcement, "attachments": announcement.attachments.all()},
    )


def save_attachments(announcement, files):
    for f in files:
        AnnouncementAttachment.objects.create(
            announcement=announcement, file=f, original_name=f.name[:255], size=f.size
        )


def delete_attachments(attachments):
    for att in attachments:
        name = att.file.name
        att.delete()
        # DBの削除が取り消された場合にファイルだけ消えて表示できなくなるのを防ぐため、確定してから実体を消す
        transaction.on_commit(lambda name=name: AnnouncementAttachment.file.field.storage.delete(name))


def attachment_note(new_files, deleted):
    lines = [f"追加：{f.name}" for f in new_files] + [f"削除：{a.original_name}" for a in deleted]
    return "\n".join(lines)


@login_required
def attachment_file(request, school_id, pk, attachment_id):
    membership = get_active_membership(request.user, school_id)
    # 添付は教室の関係者だけに見せるものなので、公開フォルダに置かずお知らせと同じ権限確認を通してから返す
    announcement = get_object_or_404(visible_announcements(membership), pk=pk)
    att = get_object_or_404(announcement.attachments, pk=attachment_id)
    content_type = mimetypes.guess_type(att.file.name)[0] or "application/octet-stream"
    return FileResponse(
        att.file.open("rb"),
        content_type=content_type,
        # 画像・動画・音声・PDFはブラウザ内で再生・表示できるよう、ダウンロードではなくその場で開く
        as_attachment=att.kind == "other",
        filename=att.original_name,
    )


@login_required
@require_POST
def toggle_reaction(request, school_id, pk):
    membership = get_active_membership(request.user, school_id)
    # 宛先に含まれない保護者がURLを直接指定しても操作できないよう、見てよいお知らせの中から探す
    announcement = get_object_or_404(visible_announcements(membership), pk=pk, status=Announcement.Status.PUBLISHED)
    emoji = request.POST.get("emoji")
    # 画面を改ざんして選択肢にない文字列を送られても保存しないため
    if emoji in Reaction.Emoji.values:
        # 同じボタンでつける・外すを切り替えられるよう、既にあれば削除する
        deleted, _ = Reaction.objects.filter(announcement=announcement, user=request.user, emoji=emoji).delete()
        if not deleted:
            Reaction.objects.get_or_create(announcement=announcement, user=request.user, emoji=emoji)
    return redirect("announcement_detail", school_id=school_id, pk=pk)


def display_names(school, user_ids):
    # 保護者同士でメールアドレスが見えると個人情報の漏えいになるため、「〇〇さんの保護者」という表示にする
    names = {}
    for m in school.memberships.filter(user_id__in=user_ids):
        if m.role != Membership.Role.GUARDIAN:
            names[m.user_id] = m.get_role_display()
    children = {}
    for s in Student.objects.filter(school=school, guardian_id__in=user_ids).exclude(status=Student.Status.WITHDRAWN):
        children.setdefault(s.guardian_id, []).append(s.name)
    for uid, kids in children.items():
        names.setdefault(uid, "・".join(kids) + "さんの保護者")
    return names


def comments_with_names(announcement, user, membership):
    comments = list(announcement.comments.all())
    names = display_names(announcement.school, {c.author_id for c in comments if c.author_id})
    for c in comments:
        c.display_name = names.get(c.author_id, "退会したユーザー")
        # 他人のコメントを消せると会話を改ざんできてしまうため、本人か運営側だけに限る
        c.can_delete = c.author_id == user.id or is_staff(membership)
    return comments


@login_required
@require_POST
def comment_create(request, school_id, pk):
    membership = get_active_membership(request.user, school_id)
    # 宛先に含まれない保護者がURLを直接指定しても操作できないよう、見てよいお知らせの中から探す
    announcement = get_object_or_404(visible_announcements(membership), pk=pk, status=Announcement.Status.PUBLISHED)
    form = CommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.announcement = announcement
        comment.author = request.user
        comment.save()
    else:
        messages.error(request, "コメントを入力してください（1000文字以内）。")
    # 再読み込みで同じコメントが二重投稿されないよう、保存後は画面を移動させる
    return redirect(f"{announcement_url(school_id, pk)}#comments")


@login_required
@require_POST
def comment_delete(request, school_id, pk, comment_id):
    membership = get_active_membership(request.user, school_id)
    announcement = get_object_or_404(visible_announcements(membership), pk=pk)
    comment = get_object_or_404(announcement.comments, pk=comment_id)
    if comment.author_id != request.user.id and not is_staff(membership):
        raise Http404
    comment.delete()
    messages.success(request, "コメントを削除しました。")
    return redirect(f"{announcement_url(school_id, pk)}#comments")


def announcement_url(school_id, pk):
    return reverse("announcement_detail", kwargs={"school_id": school_id, "pk": pk})


@login_required
@require_POST
def announcement_delete(request, school_id, pk):
    membership = get_active_membership(request.user, school_id, staff_only=True)
    announcement = get_object_or_404(membership.school.announcements, pk=pk)
    title = announcement.title
    with transaction.atomic():
        # お知らせを消すと添付のDB行は連動して消えるが、保存したファイルの実体は残ってしまうため明示的に消す
        delete_attachments(list(announcement.attachments.all()))
        announcement.delete()
    messages.success(request, f"「{title}」を削除しました。")
    return redirect("announcement_list", school_id=school_id)
