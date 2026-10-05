from django.contrib import admin
from django.urls import include, path

from billing import views as billing_views
from schools import views as school_views

urlpatterns = [
    path("admin/", admin.site.urls),
    # ログイン・ログアウト・パスワード変更の画面は、Django標準のものを使う
    path("accounts/", include("django.contrib.auth.urls")),
    path("join/<str:token>/", school_views.join, name="join"),
    path("schools/<int:school_id>/approvals/", school_views.approvals, name="approvals"),
    # 承認と却下は処理がほぼ同じため、URLの一部で切り替えて1つのviewにまとめる
    path(
        "schools/<int:school_id>/approvals/<int:student_id>/<str:action>/",
        school_views.decide,
        name="decide",
    ),
    # お知らせはすべて教室単位の機能なので、URLに教室IDを含めてどの教室の話かを明確にする
    path("schools/<int:school_id>/announcements/", include("announcements.urls")),
    path("schools/<int:school_id>/attendance/", include("attendance.urls")),
    path("schools/<int:school_id>/members/", school_views.member_list, name="member_list"),
    path("schools/<int:school_id>/members/<int:student_id>/", school_views.member_detail, name="member_detail"),
    path(
        "schools/<int:school_id>/members/<int:student_id>/<str:action>/",
        school_views.member_status,
        name="member_status",
    ),
    path("schools/<int:school_id>/groups/", school_views.group_list, name="group_list"),
    path("schools/<int:school_id>/groups/<int:pk>/", school_views.group_list, name="group_edit"),
    path("schools/<int:school_id>/groups/<int:pk>/delete/", school_views.group_delete, name="group_delete"),
    path("schools/<int:school_id>/calendar/", include("schedule.urls")),
    path("schools/<int:school_id>/billing/", include("billing.urls")),
    # Stripeからの通知は特定の教室の画面ではないため、教室IDを含まないURLで受け取る
    path("stripe/webhook/", billing_views.stripe_webhook, name="stripe_webhook"),
    path("", school_views.home, name="home"),
]
