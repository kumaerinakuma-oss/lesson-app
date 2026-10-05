import datetime

from django import forms
from django.utils import timezone

from .models import Student, StudentGroup


def validate_birth_date(date):
    today = timezone.localdate()
    # 年の入れ間違い（2026年生まれを2062年と入れるなど）に入力時点で気づけるようにする
    if date > today:
        raise forms.ValidationError("未来の日付は入力できません。")
    if date < today - datetime.timedelta(days=365 * 100):
        raise forms.ValidationError("生年月日を確認してください。")
    return date


class ChildForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ("name", "birth_date")
        labels = {"name": "お子さまの名前", "birth_date": "お子さまの生年月日"}
        widgets = {"birth_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 以前に登録された生徒のためにDB上は空を許しているが、新しく登録するときは必ず入れてもらう
        self.fields["birth_date"].required = True

    def clean_birth_date(self):
        return validate_birth_date(self.cleaned_data["birth_date"])


class StudentForm(forms.ModelForm):
    """運営者が会員情報を編集するためのフォーム。"""

    class Meta:
        model = Student
        fields = ("name", "birth_date", "group", "monthly_fee")
        widgets = {"birth_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, school, **kwargs):
        super().__init__(*args, **kwargs)
        # 他の教室のグループに入れられないよう、選択肢を自分の教室のグループに限定する
        self.fields["group"].queryset = school.groups.all()
        self.fields["group"].empty_label = "（未所属）"

    def clean_birth_date(self):
        date = self.cleaned_data["birth_date"]
        return validate_birth_date(date) if date else date


class GroupForm(forms.ModelForm):
    class Meta:
        model = StudentGroup
        fields = ("name",)

    def __init__(self, *args, school, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        # DBの一意制約に任せるとエラー画面になるため、先に確認して入力欄にメッセージを出す
        if self.school.groups.filter(name=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("同じ名前のグループがすでにあります。")
        return name
