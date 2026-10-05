import secrets

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .grades import cohort_of, school_grade


def generate_invite_token():
    # 教室IDをそのままリンクに使うと、数字を変えるだけで他の教室に申請できてしまうため、推測できない文字列にする
    return secrets.token_urlsafe(32)


class School(models.Model):
    name = models.CharField(max_length=100)
    invite_token = models.CharField(max_length=64, unique=True, default=generate_invite_token)
    # 出欠連絡の定型文の初期セットを作ったかどうか。運営者が全部消したあとに勝手に復活させないため
    templates_seeded = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def regenerate_invite_token(self):
        # リンクが外部に漏れたときに、古いリンクを使えなくするため
        self.invite_token = generate_invite_token()
        self.save(update_fields=["invite_token"])

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "運営者"
        STAFF = "staff", "スタッフ"
        GUARDIAN = "guardian", "保護者"

    class Status(models.TextChoices):
        PENDING = "pending", "承認待ち"
        ACTIVE = "active", "承認済み"
        # 子どもが全員退会した保護者。所属を消すと過去の支払いや連絡の持ち主が分からなくなるため、状態で表す
        LEFT = "left", "退会"

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices)
    # 招待リンクだけで教室の情報を見られないよう、運営者が承認するまでは権限を持たせない
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        # 同じ教室に同じ人が二重登録されると権限判定があいまいになるため禁止する
        constraints = [
            models.UniqueConstraint(fields=["school", "user"], name="unique_membership"),
        ]

    def __str__(self):
        return f"{self.user} @ {self.school} ({self.role})"


class StudentGroup(models.Model):
    """クラス・曜日別などの、生徒のまとまり。"""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="groups")
    name = models.CharField("グループ名", max_length=50)

    class Meta:
        ordering = ["name"]
        constraints = [
            # 同じ名前のグループが2つあると、どちらに入れたのか区別できなくなるため禁止する
            models.UniqueConstraint(fields=["school", "name"], name="unique_group_name"),
        ]

    def __str__(self):
        return self.name


class Student(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "承認待ち"
        ACTIVE = "active", "在籍"
        WITHDRAWN = "withdrawn", "退会"

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="students")
    # 保護者アカウントを消しても支払い・出欠の履歴が消えないよう、削除をブロックする
    guardian = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="children")
    name = models.CharField("名前", max_length=100)
    # 学年は毎年変わるため保存せず、生年月日から表示のたびに計算する。機能追加前に登録された生徒のため空を許す
    birth_date = models.DateField("生年月日", null=True, blank=True)
    # グループを削除しても生徒は残すため、未所属に戻す
    group = models.ForeignKey(
        StudentGroup, verbose_name="グループ", on_delete=models.SET_NULL, null=True, blank=True, related_name="students"
    )
    # 兄弟割引やコースの違いで生徒ごとに金額が違うため、生徒に持たせる
    monthly_fee = models.PositiveIntegerField(
        "月謝金額（円）", null=True, blank=True, validators=[MinValueValidator(50), MaxValueValidator(9_999_999)]
    )
    # 承認済みの保護者が兄弟を追加するときも確認できるよう、生徒ごとに状態を持たせる
    # 退会しても過去の記録を残すため、削除せず「退会」にする
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    @property
    def cohort(self):
        return cohort_of(self.birth_date)

    @property
    def grade(self):
        return school_grade(self.birth_date)

    def __str__(self):
        return self.name
