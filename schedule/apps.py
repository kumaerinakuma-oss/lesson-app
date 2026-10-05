from django.apps import AppConfig


class ScheduleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    # Python標準の calendar モジュールと名前がぶつかって読み込めなくなるのを避けるため、schedule という名前にする
    name = "schedule"
