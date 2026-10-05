import datetime

from django import forms

from .models import Event
from .recurrence import MAX_OCCURRENCES, MAX_SPAN_DAYS, REPEAT_CHOICES, occurrence_dates

SCOPE_CHOICES = [("one", "この予定だけ"), ("following", "この予定と、これ以降の繰り返しすべて")]


class EventForm(forms.ModelForm):
    repeat = forms.ChoiceField(label="繰り返し", choices=REPEAT_CHOICES, required=False)
    repeat_until = forms.DateField(
        label="繰り返しの終了日", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    scope = forms.ChoiceField(label="変更する範囲", choices=SCOPE_CHOICES, initial="one", widget=forms.RadioSelect)

    class Meta:
        model = Event
        fields = ("title", "date", "start_time", "end_time", "location", "note")
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # 登録済みの予定の繰り返し方を後から変えると、どの回が残るのか分かりにくくなるため、繰り返しの指定は新規作成のときだけにする
            del self.fields["repeat"], self.fields["repeat_until"]
            if not self.instance.series:
                del self.fields["scope"]
        else:
            del self.fields["scope"]

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_time"), cleaned.get("end_time")
        if end and not start:
            self.add_error("start_time", "終了時刻を入れる場合は、開始時刻も入れてください。")
        elif start and end and end <= start:
            self.add_error("end_time", "終了時刻は開始時刻より後にしてください。")

        date, repeat, until = cleaned.get("date"), cleaned.get("repeat"), cleaned.get("repeat_until")
        if repeat and date:
            if not until:
                self.add_error("repeat_until", "繰り返す場合は、終了日を入れてください。")
            elif until < date:
                self.add_error("repeat_until", "終了日は開始日以降にしてください。")
            elif until > date + datetime.timedelta(days=MAX_SPAN_DAYS):
                self.add_error("repeat_until", "繰り返しは1年以内にしてください。")
            elif len(occurrence_dates(date, repeat, until)) > MAX_OCCURRENCES:
                self.add_error("repeat_until", f"繰り返しは{MAX_OCCURRENCES}回までです。終了日を早めてください。")
        return cleaned
