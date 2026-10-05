import calendar
import datetime

# 終了日の入れ間違いで何百件も予定が作られるのを防ぐための上限
MAX_OCCURRENCES = 60
MAX_SPAN_DAYS = 366

REPEAT_CHOICES = [
    ("", "繰り返さない"),
    ("weekly", "毎週"),
    ("biweekly", "隔週（2週間ごと）"),
    ("monthly_weekday", "毎月（第n・同じ曜日）"),
    ("monthly_date", "毎月（同じ日付）"),
]


def add_months(year, month, n):
    index = year * 12 + (month - 1) + n
    return index // 12, index % 12 + 1


def nth_weekday(year, month, weekday, nth):
    """その月の第nth・weekday曜日の日付を返す。存在しなければNone（例：第5土曜がない月）。"""
    first_weekday, days_in_month = calendar.monthrange(year, month)
    day = 1 + (weekday - first_weekday) % 7 + (nth - 1) * 7
    return datetime.date(year, month, day) if day <= days_in_month else None


def occurrence_dates(start, repeat, until):
    """startからuntilまでの、繰り返しの日付の一覧を返す（startを含む）。"""
    if not repeat:
        return [start]
    dates = []
    if repeat in ("weekly", "biweekly"):
        step = datetime.timedelta(days=7 if repeat == "weekly" else 14)
        d = start
        while d <= until:
            dates.append(d)
            d += step
    else:
        nth = (start.day - 1) // 7 + 1
        n = 0
        while True:
            year, month = add_months(start.year, start.month, n)
            if datetime.date(year, month, 1) > until:
                break
            if repeat == "monthly_weekday":
                d = nth_weekday(year, month, start.weekday(), nth)
            else:
                # 31日など、その日付がない月は別の日にずらさず飛ばす（勝手に日付を変えると運営者の意図と食い違うため）
                d = datetime.date(year, month, start.day) if start.day <= calendar.monthrange(year, month)[1] else None
            if d and start <= d <= until:
                dates.append(d)
            n += 1
    return dates
