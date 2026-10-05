import json
import logging
import re

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from schools.models import Student
from schools.permissions import get_active_membership, is_staff

from . import services
from .forms import BulkInvoiceForm
from .models import Invoice, StripeEvent

logger = logging.getLogger(__name__)


def visible_invoices(membership):
    qs = membership.school.invoices.select_related("student")
    if not is_staff(membership):
        # 他の家庭の支払い状況が見えないよう、自分の子どもの請求だけにする。取り消した請求は保護者に見せる必要がない
        qs = qs.filter(student__guardian=membership.user).exclude(status=Invoice.Status.CANCELED)
    return qs


def list_url(school_id, month=""):
    url = reverse("invoice_list", kwargs={"school_id": school_id})
    # 戻り先を送信値のURLそのままにすると外部サイトへ誘導されるおそれがあるため、月の形式だけを受け取って組み立てる
    return f"{url}?month={month}" if re.fullmatch(r"\d{4}-\d{2}", month or "") else url


@login_required
def invoice_list(request, school_id):
    membership = get_active_membership(request.user, school_id)
    staff = is_staff(membership)
    invoices = visible_invoices(membership)
    month = request.GET.get("month", "")
    if month:
        invoices = invoices.filter(billing_month=month)
    context = {"school": membership.school, "invoices": invoices, "is_staff": staff, "month": month}
    if staff:
        # 運営者が「いくら回収できていて、いくら残っているか」をひと目で把握できるようにする
        context["summary"] = invoices.aggregate(
            paid=Sum("amount", filter=Q(status=Invoice.Status.PAID), default=0),
            unpaid=Sum("amount", filter=Q(status=Invoice.Status.UNPAID), default=0),
        )
    return render(request, "billing/list.html", context)


@login_required
def invoice_bulk_create(request, school_id):
    membership = get_active_membership(request.user, school_id, staff_only=True)
    form = BulkInvoiceForm(request.POST or None)
    students = Student.objects.filter(school=membership.school, status=Student.Status.ACTIVE)

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        created = duplicated = no_amount = 0
        for student in students:
            # 兄弟割引などで会員ごとに金額が違うため、設定があればそちらを優先し、なければ共通の金額を使う
            amount = (data["use_student_fee"] and student.monthly_fee) or data["amount"]
            if not amount:
                no_amount += 1
                continue
            try:
                # 1人分の重複エラーで全員分が取り消されないよう、1件ずつ独立して保存する
                with transaction.atomic():
                    Invoice.objects.create(
                        school=membership.school,
                        student=student,
                        billing_month=data["billing_month"],
                        title=data["title"],
                        amount=amount,
                    )
                created += 1
            except IntegrityError:
                # すでに同じ月・同じ項目の請求がある生徒は飛ばす（ボタンの押し直しで二重請求にしないため）
                duplicated += 1
        notes = []
        if duplicated:
            notes.append(f"{duplicated}件は作成済みのため省略")
        if no_amount:
            # 黙って飛ばすと請求漏れに気づけないため、金額が決まらなかった人数を必ず知らせる
            notes.append(f"{no_amount}人は月謝金額が未設定のため未作成")
        messages.success(request, f"{created}件の請求を作成しました。" + (f"（{'、'.join(notes)}）" if notes else ""))
        return redirect(list_url(school_id, data["billing_month"]))

    context = {
        "school": membership.school,
        "form": form,
        "student_count": students.count(),
        "no_fee_count": students.filter(monthly_fee__isnull=True).count(),
    }
    return render(request, "billing/bulk_form.html", context)


@login_required
@require_POST
def invoice_pay(request, school_id, pk):
    membership = get_active_membership(request.user, school_id)
    invoice = get_object_or_404(visible_invoices(membership), pk=pk, status=Invoice.Status.UNPAID)
    if not services.stripe_ready():
        messages.error(request, "カード決済の設定がまだ完了していません。教室にお問い合わせください。")
        return redirect("invoice_list", school_id=school_id)

    try:
        session = None
        if invoice.stripe_session_id:
            # 支払いボタンを押し直すたびに決済を作ると二重払いの原因になるため、有効な支払い画面が残っていれば使い回す
            existing = services.retrieve_session(invoice.stripe_session_id)
            if existing.status == "open":
                session = existing
            elif existing.status == "complete":
                return redirect("invoice_return", school_id=school_id, pk=pk)
        if session is None:
            return_url = request.build_absolute_uri(reverse("invoice_return", kwargs={"school_id": school_id, "pk": pk}))
            session = services.create_checkout_session(
                invoice,
                request.user,
                success_url=return_url,
                cancel_url=request.build_absolute_uri(reverse("invoice_list", kwargs={"school_id": school_id})),
            )
            invoice.stripe_session_id = session.id
            invoice.save(update_fields=["stripe_session_id"])
    except stripe.StripeError:
        logger.exception("Stripeの支払い画面を作成できませんでした: invoice=%s", invoice.id)
        messages.error(request, "支払い画面を開けませんでした。時間をおいてもう一度お試しください。")
        return redirect("invoice_list", school_id=school_id)

    return redirect(session.url)


@login_required
def invoice_return(request, school_id, pk):
    """Stripeの支払い画面から戻ってきたときの画面。"""
    membership = get_active_membership(request.user, school_id)
    invoice = get_object_or_404(visible_invoices(membership), pk=pk)

    if invoice.status == Invoice.Status.UNPAID and invoice.stripe_session_id and services.stripe_ready():
        try:
            # 「戻ってきた＝支払った」とは限らない（URLを直接開くこともできる）ため、Stripeに問い合わせて確かめる
            session = services.retrieve_session(invoice.stripe_session_id)
            services.mark_paid(
                session_id=session.id,
                client_reference_id=session.client_reference_id,
                payment_status=session.payment_status,
                amount_total=session.amount_total,
                currency=session.currency,
            )
            invoice.refresh_from_db()
        except stripe.StripeError:
            # ここで確認できなくても、あとから届くWebhookで支払い済みになるため画面は表示する
            logger.exception("支払い状況を確認できませんでした: invoice=%s", invoice.id)

    if invoice.status == Invoice.Status.PAID:
        messages.success(request, "お支払いありがとうございました。")
    else:
        messages.info(request, "お支払いを確認中です。反映まで少し時間がかかることがあります。")
    return redirect("invoice_list", school_id=school_id)


@login_required
@require_POST
def invoice_cash(request, school_id, pk):
    membership = get_active_membership(request.user, school_id, staff_only=True)
    with transaction.atomic():
        invoice = get_object_or_404(
            membership.school.invoices.select_for_update(), pk=pk, status=Invoice.Status.UNPAID
        )
        invoice.status = Invoice.Status.PAID
        invoice.method = Invoice.Method.CASH
        invoice.paid_at = timezone.now()
        invoice.save(update_fields=["status", "method", "paid_at"])
    # 現金で受け取ったあとにカードでも払われると二重払いになるため、開いている支払い画面を無効にする
    services.expire_session(invoice.stripe_session_id)
    messages.success(request, "現金で受領済みにしました。")
    return redirect(list_url(school_id, request.POST.get("month", "")))


@login_required
@require_POST
def invoice_cancel(request, school_id, pk):
    membership = get_active_membership(request.user, school_id, staff_only=True)
    with transaction.atomic():
        # 支払い済みの請求を取り消すと入金の記録と食い違うため、未払いのものだけ取り消せるようにする
        invoice = get_object_or_404(
            membership.school.invoices.select_for_update(), pk=pk, status=Invoice.Status.UNPAID
        )
        invoice.status = Invoice.Status.CANCELED
        invoice.save(update_fields=["status"])
    services.expire_session(invoice.stripe_session_id)
    messages.success(request, "請求を取り消しました。")
    return redirect(list_url(school_id, request.POST.get("month", "")))


# Stripeのサーバーから直接届く通知で、ブラウザのフォーム送信ではないためCSRFトークンを持てない。
# 代わりに、Stripeの署名を検証して本物の通知かどうかを確かめる
@csrf_exempt
@require_POST
def stripe_webhook(request):
    if not settings.STRIPE_WEBHOOK_SECRET:
        # 署名用の秘密鍵が空のまま検証すると誰でも正しい署名を作れてしまうため、未設定なら必ず拒否する
        return HttpResponseBadRequest("webhook secret is not configured")
    try:
        stripe.Webhook.construct_event(
            request.body, request.headers.get("Stripe-Signature", ""), settings.STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.SignatureVerificationError):
        # 署名が合わない通知は、第三者が「支払い済み」を偽装している可能性があるため受け付けない
        return HttpResponseBadRequest("invalid signature")

    event = json.loads(request.body)
    if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        session = event["data"]["object"]
        with transaction.atomic():
            # 同じ通知が再送されても二重に処理しないよう、処理済みのIDなら何もしない
            _, is_new = StripeEvent.objects.get_or_create(event_id=event["id"], defaults={"type": event["type"]})
            if is_new:
                services.mark_paid(
                    session_id=session["id"],
                    client_reference_id=session.get("client_reference_id"),
                    payment_status=session.get("payment_status"),
                    amount_total=session.get("amount_total"),
                    currency=session.get("currency"),
                )
    # 200以外を返すとStripeが同じ通知を送り続けるため、処理対象外の通知にも成功を返す
    return HttpResponse(status=200)
