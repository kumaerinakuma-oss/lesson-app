from django import forms
from django.utils import timezone

from schools.models import Student

from .models import AttendanceNotice, AttendanceReply, MessageTemplate


class NoticeForm(forms.ModelForm):
    class Meta:
        model = AttendanceNotice
        fields = ("student", "target_date", "type", "body")
        labels = {"student": "お子さま"}
        widgets = {
            "target_date": forms.DateInput(attrs={"type": "date"}),
            "body": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, school, user, **kwargs):
        super().__init__(*args, **kwargs)
        # 他の家庭の子どもや他の教室の子どもの連絡を出せないよう、選択肢を自分の在籍中の子どもに限定する
        self.fields["student"].queryset = Student.objects.filter(
            school=school, guardian=user, status=Student.Status.ACTIVE
        )
        self.fields["student"].empty_label = None
        self.fields["target_date"].initial = timezone.localdate()

    def clean_target_date(self):
        date = self.cleaned_data["target_date"]
        # 日付の選び間違いで過去の日に連絡が入ると運営者が見落とすため、今日より前は受け付けない
        if date < timezone.localdate():
            raise forms.ValidationError("今日以降の日付を選んでください。")
        return date


class ReplyForm(forms.ModelForm):
    class Meta:
        model = AttendanceReply
        fields = ("body",)
        labels = {"body": ""}
        widgets = {"body": forms.Textarea(attrs={"rows": 3, "placeholder": "返信を書く"})}


class TemplateForm(forms.ModelForm):
    class Meta:
        model = MessageTemplate
        fields = ("kind", "label", "body", "sort_order")
        widgets = {"body": forms.Textarea(attrs={"rows": 3})}
