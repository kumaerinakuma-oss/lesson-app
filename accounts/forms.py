from django.contrib.auth.forms import UserCreationForm

from .models import User


class SignupForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("email",)
        labels = {"email": "メールアドレス"}

    def save(self, commit=True):
        user = super().save(commit=False)
        # ログインはメールで行うためユーザー名は画面に出さないが、Django標準のUserでは必須かつ一意なので、メールを流用して埋める
        user.username = self.cleaned_data["email"]
        if commit:
            user.save()
        return user
