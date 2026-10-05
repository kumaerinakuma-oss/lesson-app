from django.conf import settings
from django.db import models


class MessageTemplate(models.Model):
    class Kind(models.TextChoices):
        # 保護者が送る文面と運営者が返す文面は内容も使う人も違うため、同じ表で種類を分けて管理する
        NOTICE = "notice", "保護者の連絡用"
        REPLY = "reply", "運営者の返信用"

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="message_templates")
    kind = models.CharField("種類", max_length=20, choices=Kind.choices)
    label = models.CharField("ボタンの表示名", max_length=30)
    body = models.TextField("文面", max_length=500)
    sort_order = models.PositiveIntegerField("並び順", default=0)

    class Meta:
        ordering = ["kind", "sort_order", "id"]

    def __str__(self):
        return self.label


# 教室を作った直後から使えるよう、よくある文面を最初に用意する（あとから運営者が自由に変更できる）
DEFAULT_TEMPLATES = [
    (MessageTemplate.Kind.NOTICE, "体調不良", "体調不良のため、お休みします。"),
    (MessageTemplate.Kind.NOTICE, "学校行事", "学校行事のため、お休みします。"),
    (MessageTemplate.Kind.NOTICE, "家庭の都合", "家庭の都合により、お休みします。"),
    (MessageTemplate.Kind.NOTICE, "遅れます", "到着が遅れます。よろしくお願いします。"),
    (MessageTemplate.Kind.NOTICE, "早退します", "途中で早退します。よろしくお願いします。"),
    (MessageTemplate.Kind.REPLY, "承知しました", "ご連絡ありがとうございます。承知しました。"),
    (MessageTemplate.Kind.REPLY, "お大事に", "ご連絡ありがとうございます。承知しました。どうぞお大事になさってください。"),
    (MessageTemplate.Kind.REPLY, "振替の案内", "承知しました。振替をご希望の場合は、ご都合のよい日をお知らせください。"),
]


def ensure_default_templates(school):
    # 運営者がすべて削除した場合にまで復活させると迷惑なので、「一度も用意していない教室」だけに作る
    if school.templates_seeded:
        return
    MessageTemplate.objects.bulk_create(
        MessageTemplate(school=school, kind=kind, label=label, body=body, sort_order=i)
        for i, (kind, label, body) in enumerate(DEFAULT_TEMPLATES)
    )
    school.templates_seeded = True
    school.save(update_fields=["templates_seeded"])


class AttendanceNotice(models.Model):
    class Type(models.TextChoices):
        ABSENCE = "absence", "欠席"
        LATE = "late", "遅刻"
        EARLY = "early", "早退"

    class Status(models.TextChoices):
        UNREAD = "unread", "未確認"
        CONFIRMED = "confirmed", "確認済み"

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="attendance_notices")
    # 生徒を名簿から消しても、過去にいつ休んだかの記録は残す必要があるため削除をブロックする
    student = models.ForeignKey("schools.Student", on_delete=models.PROTECT, related_name="attendance_notices")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    target_date = models.DateField("日付")
    type = models.CharField("種類", max_length=20, choices=Type.choices, default=Type.ABSENCE)
    # 定型文をあとから書き換えても過去の連絡内容が変わらないよう、参照ではなく文面そのものを保存する
    body = models.TextField("連絡内容", max_length=1000)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNREAD)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-target_date", "-created_at"]

    def __str__(self):
        return f"{self.target_date} {self.student} {self.get_type_display()}"


class AttendanceReply(models.Model):
    notice = models.ForeignKey(AttendanceNotice, on_delete=models.CASCADE, related_name="replies")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    # 送った人があとで運営者でなくなっても、当時どの立場で書いたかが分かるよう記録しておく
    from_staff = models.BooleanField(default=False)
    body = models.TextField("返信", max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
