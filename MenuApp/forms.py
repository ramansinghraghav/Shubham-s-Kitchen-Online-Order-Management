from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Profile


class SignUpForm(UserCreationForm):
    full_name = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=20, required=False)
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    profile_photo = forms.ImageField(required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "full_name", "email", "phone", "address", "profile_photo", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            css_class = "form-control"
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("rows", 3)
            if name == 'profile_photo':
                field.widget.attrs["class"] = 'form-control form-control-sm'
            else:
                field.widget.attrs["class"] = css_class

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data.get("email", "")
        if commit:
            user.save()
            Profile.objects.create(
                user=user,
                full_name=self.cleaned_data.get("full_name", ""),
                phone=self.cleaned_data.get("phone", ""),
                address=self.cleaned_data.get("address", ""),
                profile_photo=self.cleaned_data.get("profile_photo"),
            )
        return user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["full_name", "phone", "address", "profile_photo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs["class"] = "form-control"
            if name == "profile_photo":
                field.widget.attrs["class"] = "form-control form-control-sm"


