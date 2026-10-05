import logging

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Invoice

logger = logging.getLogger(__name__)


def stripe_ready():
    return bool(settings.STRIPE_SECRET_KEY)


def create_checkout_session(invoice, user, success_url, cancel_url):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    # カード番号は自分のサーバーで一切扱わず、Stripeが用意する支払い画面に任せる
    return stripe.checkout.Session.create(
        mode="payment",
        line_items=[
            {
                "price_data": {
                    "currency": "jpy",
                    "unit_amount": invoice.amount,
                    "product_data": {"name": f"{invoice.billing_month} {invoice.title}（{invoice.student.name}）"},
                },
                "quantity": 1,
            }
        ],
        # 支払い完了の通知が届いたときに、どの請求の支払いかを特定するための目印
        client_reference_id=str(invoice.id),
        customer_email=user.email,
        success_url=success_url,
        cancel_url=cancel_url,
    )


def retrieve_session(session_id):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe.checkout.Session.retrieve(session_id)


def mark_paid(*, session_id, client_reference_id, payment_status, amount_total, currency):
    """Stripeの決済情報をもとに請求を支払い済みにする。何度呼ばれても結果が変わらないようにしてある。"""
    # コンビニ払いなどは「手続き完了」と「入金完了」が別なので、入金が確定したものだけを扱う
    if payment_status != "paid" or not str(client_reference_id or "").isdigit():
        return False
    with transaction.atomic():
        # Webhookと支払い後の画面が同時に処理しても二重に更新しないよう、行をロックして確認する
        invoice = Invoice.objects.select_for_update().filter(id=int(client_reference_id)).first()
        if invoice is None:
            logger.error("支払い通知に対応する請求がありません: session=%s", session_id)
            return False
        if invoice.status == Invoice.Status.PAID:
            if invoice.stripe_session_id != session_id:
                # 別の決済でも入金されている＝二重払いの可能性があるため、返金対応できるよう記録に残す
                logger.error("二重払いの可能性: invoice=%s session=%s", invoice.id, session_id)
            return False
        if invoice.status == Invoice.Status.CANCELED:
            # 取り消した請求に入金があった場合は返金が必要なので、支払い済みにはせず記録に残す
            logger.error("取り消し済みの請求に入金がありました: invoice=%s session=%s", invoice.id, session_id)
            return False
        # 金額や通貨が請求と違う決済で「支払い済み」にしてしまわないよう照合する
        if amount_total != invoice.amount or str(currency).lower() != "jpy":
            logger.error("金額が一致しません: invoice=%s session=%s amount=%s", invoice.id, session_id, amount_total)
            return False
        invoice.status = Invoice.Status.PAID
        invoice.method = Invoice.Method.STRIPE
        invoice.stripe_session_id = session_id
        invoice.paid_at = timezone.now()
        invoice.save(update_fields=["status", "method", "stripe_session_id", "paid_at"])
        return True


def expire_session(session_id):
    """請求を取り消したあとに古い支払い画面から入金されないよう、Stripe側の支払い画面を無効にする。"""
    if not session_id or not stripe_ready():
        return
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        stripe.checkout.Session.expire(session_id)
    except stripe.StripeError:
        # すでに期限切れ・支払い済みの場合は無効化できないが、取り消し自体は続行してよい
        logger.warning("支払い画面を無効にできませんでした: session=%s", session_id)
