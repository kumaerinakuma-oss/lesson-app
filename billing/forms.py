from django import forms
from django.utils import timezone

from .models import Invoice


class BulkInvoiceForm(forms.ModelForm):
    use_student_fee = forms.BooleanField(
        label="会員ごとに設定した月謝金額を使う",
        required=False,
        initial=True,
        help_text="チェックを外すと、全員に下の金額で請求します（教材費など）。",
    )

    class Meta:
        model = Invoice
        fields = ("billing_month", "title", "use_student_fee", "amount")
        labels = {"amount": "金額（円）※月謝金額が未設定の会員に使います"}
        widgets = {"billing_month": forms.TextInput(attrs={"type": "month"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["billing_month"].initial = timezone.localdate().strftime("%Y-%m")
        # 全員に月謝金額が設定されていれば共通の金額は不要なため、必須にしない
        self.fields["amount"].required = False

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("use_student_fee") and not cleaned.get("amount"):
            self.add_error("amount", "全員に同じ金額で請求する場合は、金額を入れてください。")
        return cleaned
