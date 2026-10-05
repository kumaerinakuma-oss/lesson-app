from django.utils import timezone

PRESCHOOL = {4: "年少", 5: "年中", 6: "年長"}


def fiscal_year(today=None):
    today = today or timezone.localdate()
    # 学年は4月1日に切り替わるため、1〜3月は前の年の年度として扱う
    return today.year if today.month >= 4 else today.year - 1


def cohort_of(birth_date):
    """同じ学年になる子どもをまとめる番号（その学年の4月2日がある年）を返す。

    学年の名前は毎年変わるが、この番号は一生変わらないため、「どの学年の子か」を保存するときに使う。
    """
    if birth_date is None:
        return None
    # 4月1日生まれは法律上3月31日に年を取るので、1つ上の学年（早生まれ）になる。境目は4月2日
    return birth_date.year if (birth_date.month, birth_date.day) >= (4, 2) else birth_date.year - 1


def grade_of_cohort(cohort, today=None):
    if cohort is None:
        return ""
    n = fiscal_year(today) - cohort  # 年長=6、小1=7 になる数え方
    if n < 0:
        return ""
    if n < 4:
        return "未就園児"
    if n in PRESCHOOL:
        return PRESCHOOL[n]
    if n <= 12:
        return f"小学{n - 6}年"
    if n <= 15:
        return f"中学{n - 12}年"
    if n <= 18:
        return f"高校{n - 15}年"
    return "高校卒業以上"


def school_grade(birth_date, today=None):
    """生年月日から、日本の学年（4月始まり）を返す。"""
    return grade_of_cohort(cohort_of(birth_date), today)
