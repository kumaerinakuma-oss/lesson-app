from collections import Counter

from django import forms
from django.template.defaultfilters import filesizeformat

from schools.grades import grade_of_cohort
from schools.models import Student, StudentGroup

from .models import EXTENSION_TO_KIND, Announcement, AnnouncementGrade, Comment

MAX_FILE_SIZE = 100 * 1024 * 1024  # 動画を想定し、スマホで撮った数分の動画が収まる大きさにする
MAX_FILES = 10


class MultipleFileInput(forms.ClearableFileInput):
    # Django標準のファイル欄は1つしか選べないため、複数選択を許可する
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": ",".join(f".{e}" for e in EXTENSION_TO_KIND)}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data] if data else []
        if len(files) > MAX_FILES:
            raise forms.ValidationError(f"一度に添付できるのは{MAX_FILES}個までです。")
        for f in files:
            super().clean(f, initial)
            # 画面の accept 指定はブラウザ側の目安にすぎず簡単に回避できるため、サーバー側でも拡張子を確認する
            ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
            if ext not in EXTENSION_TO_KIND:
                raise forms.ValidationError(f"「{f.name}」は添付できない形式です。")
            if f.size > MAX_FILE_SIZE:
                raise forms.ValidationError(f"「{f.name}」は大きすぎます（上限 {filesizeformat(MAX_FILE_SIZE)}）。")
        return files


class AnnouncementForm(forms.ModelForm):
    target_grades = forms.TypedMultipleChoiceField(
        label="対象の学年", required=False, coerce=int, widget=forms.CheckboxSelectMultiple
    )
    target_groups = forms.ModelMultipleChoiceField(
        label="対象のグループ", queryset=StudentGroup.objects.none(), required=False, widget=forms.CheckboxSelectMultiple
    )
    target_students = forms.ModelMultipleChoiceField(
        label="対象の会員", queryset=Student.objects.none(), required=False, widget=forms.CheckboxSelectMultiple
    )
    attachments = MultipleFileField(
        label="添付ファイル",
        required=False,
        help_text="画像・動画・音声（wav/mp3/m4a）・PDF。複数選択できます。",
    )

    class Meta:
        model = Announcement
        fields = ("title", "body", "audience")
        widgets = {"body": forms.Textarea(attrs={"rows": 8}), "audience": forms.RadioSelect}

    field_order = ["title", "body", "audience", "target_grades", "target_groups", "target_students", "attachments"]

    def __init__(self, *args, school, **kwargs):
        super().__init__(*args, **kwargs)
        # 他の教室のグループや会員を宛先にできないよう、選択肢を自分の教室のものに限定する
        students = school.students.filter(status=Student.Status.ACTIVE)
        self.fields["target_groups"].queryset = school.groups.all()
        self.fields["target_students"].queryset = students.order_by("name")

        counts = Counter(s.cohort for s in students if s.cohort is not None)
        selected = set()
        if self.instance.pk:
            selected = set(self.instance.target_grades.values_list("cohort", flat=True))
            self.initial.setdefault("target_grades", list(selected))
            self.initial.setdefault("target_groups", list(self.instance.target_groups.all()))
            self.initial.setdefault("target_students", list(self.instance.target_students.all()))
        # 今いる学年だけを選択肢にする。編集時は、対象の子が全員退会した学年も選択を保てるよう残す
        self.fields["target_grades"].choices = [
            (c, f"{grade_of_cohort(c)}（{counts.get(c, 0)}人）") for c in sorted(set(counts) | selected, reverse=True)
        ]

    def clean(self):
        cleaned = super().clean()
        required = {
            Announcement.Audience.GRADE: ("target_grades", "学年を1つ以上選んでください。"),
            Announcement.Audience.GROUP: ("target_groups", "グループを1つ以上選んでください。"),
            Announcement.Audience.INDIVIDUAL: ("target_students", "会員を1人以上選んでください。"),
        }
        field, message = required.get(cleaned.get("audience"), (None, None))
        # 宛先を選び忘れると誰にも届かないお知らせになるため、保存前に止める
        if field and not cleaned.get(field):
            self.add_error(field, message)
        return cleaned

    def save_targets(self, announcement):
        """選んだ宛先の種類に合うものだけを保存する。切り替え前に選んでいた別の種類の宛先は消す。"""
        data, audience = self.cleaned_data, announcement.audience
        A = Announcement.Audience
        announcement.target_groups.set(data["target_groups"] if audience == A.GROUP else [])
        announcement.target_students.set(data["target_students"] if audience == A.INDIVIDUAL else [])
        announcement.target_grades.all().delete()
        if audience == A.GRADE:
            AnnouncementGrade.objects.bulk_create(
                AnnouncementGrade(announcement=announcement, cohort=c) for c in set(data["target_grades"])
            )


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ("body",)
        labels = {"body": ""}
        widgets = {"body": forms.Textarea(attrs={"rows": 2, "placeholder": "コメントを書く"})}
