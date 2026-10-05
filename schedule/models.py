from django.conf import settings
from django.db import models


class Event(models.Model):
    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="events")
    title = models.CharField("予定名", max_length=100)
    date = models.DateField("日付")
    # 「終日の行事」や「時間未定」の予定も登録できるよう、時刻は任意にする
    start_time = models.TimeField("開始時刻", null=True, blank=True)
    end_time = models.TimeField("終了時刻", null=True, blank=True)
    location = models.CharField("場所", max_length=100, blank=True)
    note = models.TextField("メモ", max_length=1000, blank=True)
    # 繰り返しの予定は1回ずつ別の行として保存し、同じIDでひとまとめにする。
    # 「この回だけ休講にする」「この回だけ時間を変える」といった個別の変更をそのまま行えるようにするため
    series = models.UUIDField(null=True, blank=True, db_index=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # 時刻が未定の予定は、その日の先頭に並べる
        ordering = ["date", models.F("start_time").asc(nulls_first=True), "id"]
        indexes = [models.Index(fields=["school", "date"])]

    def __str__(self):
        return f"{self.date} {self.title}"
