from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    # 保護者はメールでログインするほうが自然なため、メールを一意にしてログインIDにする
    email = models.EmailField(unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]