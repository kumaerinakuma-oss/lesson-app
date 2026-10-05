import calendar
import datetime
import re
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from attendance.models import AttendanceNotice
from schools.permissions import get_active_membership, is_staff

from .forms import EventForm
from .models import Event
from .recurrence import occurrence_dates


def parse_month(value):
    """「2026-10」の形式を月初の日付に変換する。おかしな値なら今月にする。"""
    # URLは誰でも書き換えられるため、形式が違う値でエラー画面にならないよう今月に戻す
    if value and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        year, month = map(int, value.split("-"))
        if 2000 <= year <= 2100:
            return datetime.date(year, month, 1)
    return timezone.localdate().replace(day=1)


def month_url(school_id, date):
    return f"{reverse('calendar', kwargs={'school_id': school_id})}?month={date:%Y-%m}"


@login_required
def calendar_view(request, school_id):
    membership = get_active_membership(request.user, school_id)
    staff = is_staff(membership)
    first = parse_month(request.GET.get("month"))
    # 日本のカレンダーに合わせて日曜始まりにする。前後の月の日も含めた週単位の日付が返る
    weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(first.year, first.month)
    range_start, range_end = weeks[0][0], weeks[-1][-1]

    # 1日ずつ問い合わせるとSQLが日数分発行されるため、表示範囲をまとめて取得して日付ごとに振り分ける
    events_by_date = {}
    for e in membership.school.events.filter(date__range=(range_start, range_end)):
        events_by_date.setdefault(e.date, []).append(e)

    notices = membership.school.attendance_notices.filter(target_date__range=(range_start, range_end))
    if not staff:
        # 他の家庭の欠席が分からないよう、保護者には自分の子どもの連絡だけをカレンダーに出す
        notices = notices.filter(student__guardian=request.user)
    notices_by_date = {}
    for n in notices.select_related("student"):
        notices_by_date.setdefault(n.target_date, []).append(n)

    today = timezone.localdate()
    grid = [
        [
            {
                "date": d,
                "in_month": d.month == first.month,
                "is_today": d == today,
                "events": events_by_date.get(d, []),
                "notices": notices_by_date.get(d, []),
            }
            for d in week
        ]
        for week in weeks
    ]
    context = {
        "school": membership.school,
        "is_staff": staff,
        "first": first,
        "grid": grid,
        "prev_url": month_url(school_id, (first - datetime.timedelta(days=1)).replace(day=1)),
        "next_url": month_url(school_id, (first + datetime.timedelta(days=32)).replace(day=1)),
        "this_url": reverse("calendar", kwargs={"school_id": school_id}),
        # スマホでは升目が小さく文字を読みにくいため、同じ月の予定を下に一覧でも出す
        "month_events": [e for d in sorted(events_by_date) if d.month == first.month for e in events_by_date[d]],
    }
    return render(request, "schedule/calendar.html", context)


@login_required
def event_edit(request, school_id, pk=None):
    membership = get_active_membership(request.user, school_id, staff_only=True)
    # 他の教室の予定を書き換えられないよう、自分の教室の予定の中から探す
    event = get_object_or_404(membership.school.events, pk=pk) if pk else None
    old_date = event.date if event else None
    initial = {}
    if event is None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", request.GET.get("date", "")):
        # カレンダーの日付を押して追加するとき、その日が最初から入っているようにする
        initial["date"] = request.GET["date"]
    form = EventForm(request.POST or None, instance=event, initial=initial)

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            if event is None:
                count = create_events(membership.school, request.user, form)
                messages.success(request, f"{count}件の予定を登録しました。" if count > 1 else "予定を保存しました。")
            elif data.get("scope") == "following":
                count = update_following(membership.school, event, old_date, form)
                messages.success(request, f"{count}件の予定を変更しました。")
            else:
                form.save()
                messages.success(request, "予定を保存しました。")
        return redirect(month_url(school_id, data["date"]))

    return render(request, "schedule/event_form.html", {"school": membership.school, "form": form, "e": event})


def create_events(school, user, form):
    data = form.cleaned_data
    dates = occurrence_dates(data["date"], data.get("repeat"), data.get("repeat_until"))
    # 1回だけの予定には印をつけず、2回以上のときだけ同じIDでまとめる
    series = uuid.uuid4() if len(dates) > 1 else None
    fields = {name: data[name] for name in ("title", "start_time", "end_time", "location", "note")}
    Event.objects.bulk_create(
        Event(school=school, created_by=user, series=series, date=d, **fields) for d in dates
    )
    return len(dates)


def update_following(school, event, old_date, form):
    data = form.cleaned_data
    # 日付を変えた場合は、以降の回も同じ日数だけずらす（例：土曜から日曜に変えたら、以降もすべて日曜になる）
    shift = data["date"] - old_date
    following = list(school.events.filter(series=event.series, date__gte=old_date))
    for e in following:
        for name in ("title", "start_time", "end_time", "location", "note"):
            setattr(e, name, data[name])
        e.date = e.date + shift
    Event.objects.bulk_update(following, ["title", "date", "start_time", "end_time", "location", "note"])
    return len(following)


@login_required
@require_POST
def event_delete(request, school_id, pk):
    membership = get_active_membership(request.user, school_id, staff_only=True)
    event = get_object_or_404(membership.school.events, pk=pk)
    date = event.date
    if event.series and request.POST.get("scope") == "following":
        # 過去の回は「実際に行われた記録」なので残し、この回以降だけを消す
        count, _ = membership.school.events.filter(series=event.series, date__gte=date).delete()
        messages.success(request, f"{count}件の予定を削除しました。")
    else:
        event.delete()
        messages.success(request, "予定を削除しました。")
    return redirect(month_url(school_id, date))
