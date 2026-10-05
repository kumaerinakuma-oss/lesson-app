from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models


class Invoice(models.Model):
    class Status(models.TextChoices):
        UNPAID = "unpaid", "未払い"
        PAID = "paid", "支払い済み"
        CANCELED = "canceled", "取り消し"

    class Method(models.TextChoices):
        STRIPE = "stripe", "カード決済"
        CASH = "cash", "現金"

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="invoices")
    # 生徒を名簿から消しても、お金の記録は会計上残す必要があるため削除をブロックする
    student = models.ForeignKey("schools.Student", on_delete=models.PROTECT, related_name="invoices")
    billing_month = models.CharField(
        "対象月", max_length=7, validators=[RegexValidator(r"^\d{4}-(0[1-9]|1[0-2])$", "2026-10 の形式で入力してください。")]
    )
    title = models.CharField("項目", max_length=50, default="月謝")
    # 日本円には小数がないため整数で持つ。Stripeの日本円の最低決済額が50円のため下限を合わせる
    amount = models.PositiveIntegerField("金額（円）", validators=[MinValueValidator(50), MaxValueValidator(9_999_999)])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNPAID)
    method = models.CharField(max_length=20, choices=Method.choices, blank=True)
    # 支払い画面を開き直したときに同じ決済を再利用して、二重払いを防ぐために控えておく
    stripe_session_id = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-billing_month", "student__name", "id"]
        constraints = [
            # 一括発行のボタンを2回押しても、同じ月・同じ項目の請求が二重に作られないようにする
            models.UniqueConstraint(
                fields=["student", "billing_month", "title"],
                condition=~models.Q(status="canceled"),
                name="unique_active_invoice",
            ),
        ]

    def __str__(self):
        return f"{self.billing_month} {self.student} {self.title} {self.amount}円"


class StripeEvent(models.Model):
    # Stripeは同じ通知を複数回送ることがあるため、処理済みのIDを記録して二重処理を防ぐ（冪等性）
    event_id = models.CharField(max_length=255, primary_key=True)
    type = models.CharField(max_length=100)
    processed_at = models.DateTimeField(auto_now_add=True)
