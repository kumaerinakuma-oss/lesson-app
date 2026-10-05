import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from schools.grades import grade_of_cohort


class Announcement(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "下書き"
        PUBLISHED = "published", "公開"

    class Audience(models.TextChoices):
        ALL = "all", "全体"
        GRADE = "grade", "学年"
        GROUP = "group", "グループ"
        INDIVIDUAL = "individual", "個人"

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="announcements")
    # 投稿したスタッフが退職してアカウントを消しても、お知らせそのものは残すため
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    title = models.CharField("タイトル", max_length=200)
    body = models.TextField("本文")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    audience = models.CharField("宛先", max_length=20, choices=Audience.choices, default=Audience.ALL)
    target_groups = models.ManyToManyField("schools.StudentGroup", verbose_name="対象のグループ", blank=True, related_name="+")
    target_students = models.ManyToManyField("schools.Student", verbose_name="対象の会員", blank=True, related_name="+")
    published_at = models.DateTimeField(null=True, blank=True)
    # updated_at は下書きの保存でも変わるため、保護者に「内容が変わった」と知らせる基準は別に持つ
    content_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]

    @property
    def is_edited(self):
        return self.published_at and self.content_updated_at and self.content_updated_at > self.published_at

    def audience_label(self):
        """運営者向けの、宛先の詳しい表示（例：「学年：年長、小学1年」）。"""
        if self.audience == self.Audience.GRADE:
            # 学年の名前は年度で変わるため、公開した時点（下書きなら今）の呼び名で表示する
            when = timezone.localtime(self.published_at).date() if self.published_at else None
            names = [grade_of_cohort(g.cohort, when) for g in self.target_grades.all()]
        elif self.audience == self.Audience.GROUP:
            names = [g.name for g in self.target_groups.all()]
        elif self.audience == self.Audience.INDIVIDUAL:
            names = [s.name for s in self.target_students.all()]
        else:
            return "全体"
        return f"{self.get_audience_display()}：{'、'.join(names)}"

    def __str__(self):
        return self.title


class AnnouncementGrade(models.Model):
    # 「年長」のような学年名で保存すると、翌年度に別の子どもたちが対象になってしまう。
    # 同じ子どもたちを指し続けるよう、年度が変わっても変わらない番号（cohort）で保存する
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="target_grades")
    cohort = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["-cohort"]
        constraints = [
            models.UniqueConstraint(fields=["announcement", "cohort"], name="unique_announcement_grade"),
        ]


class AnnouncementRevision(models.Model):
    # 公開後に書き換えると保護者が「前に何と書いてあったか」を確認できなくなるため、編集前の内容を残す
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="revisions")
    title = models.CharField(max_length=200)
    body = models.TextField()
    # 添付ファイルは実体をすべて残すと容量を圧迫するため、何を追加・削除したかだけを記録する。宛先の変更もここに残す
    attachment_note = models.TextField(blank=True)
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    edited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-edited_at"]


class Reaction(models.Model):
    class Emoji(models.TextChoices):
        # 自由入力にすると表示が崩れたり不適切な絵文字が使われたりするため、選択肢を固定する
        OK = "👍", "了解"
        THANKS = "🙏", "ありがとう"
        SMILE = "😊", "うれしい"
        CHECK = "✅", "確認しました"

    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    emoji = models.CharField(max_length=8, choices=Emoji.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # 連打で同じリアクションが何重にも数えられないよう、DBでも禁止する
        constraints = [
            models.UniqueConstraint(fields=["announcement", "user", "emoji"], name="unique_reaction"),
        ]


class AnnouncementRead(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    # 編集後に読み直したかを判定するため、初回ではなく最後に読んだ日時を持つ
    read_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["announcement", "user"], name="unique_read"),
        ]


# 保護者のスマホでそのまま開ける形式に限定する。HTMLやSVGはスクリプトを仕込めるため許可しない
ALLOWED_EXTENSIONS = {
    "image": ["jpg", "jpeg", "png", "gif", "webp"],
    "video": ["mp4", "mov", "webm"],
    "audio": ["wav", "mp3", "m4a"],
    "pdf": ["pdf"],
}
EXTENSION_TO_KIND = {ext: kind for kind, exts in ALLOWED_EXTENSIONS.items() for ext in exts}


def attachment_upload_to(instance, filename):
    # 元のファイル名をそのまま保存すると、日本語名の文字化けや同名ファイルの上書き、URLからの推測が起きるため、ランダムな名前にする
    ext = filename.rsplit(".", 1)[-1].lower()
    return f"announcements/{instance.announcement.school_id}/{uuid.uuid4().hex}.{ext}"


class AnnouncementAttachment(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=attachment_upload_to)
    # 保存名はランダムにしているため、画面表示とダウンロード時の名前として元の名前を残す
    original_name = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    @property
    def kind(self):
        return EXTENSION_TO_KIND.get(self.file.name.rsplit(".", 1)[-1].lower(), "other")

    def __str__(self):
        return self.original_name


class Comment(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="comments")
    # 退会した保護者のアカウントを消しても、他の人の会話の流れが崩れないようコメントは残す
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    body = models.TextField("コメント", max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
