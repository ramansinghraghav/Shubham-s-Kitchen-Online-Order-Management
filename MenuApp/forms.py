from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import MenuItem, Profile


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
            field.help_text = ""

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if email and User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean_phone(self):
        return self.cleaned_data.get("phone", "").strip()

    def clean_full_name(self):
        full_name = self.cleaned_data.get("full_name", "").strip()
        if not full_name:
            raise ValidationError("Full name is required.")
        return full_name

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

    def clean_phone(self):
        return self.cleaned_data.get("phone", "").strip()

    def clean_full_name(self):
        full_name = self.cleaned_data.get("full_name", "").strip()
        if not full_name:
            raise ValidationError("Full name is required.")
        return full_name


class MenuItemAdminForm(forms.ModelForm):
    full_price = forms.DecimalField(max_digits=10, decimal_places=2, required=True, label="Full Price")
    half_price = forms.DecimalField(max_digits=10, decimal_places=2, required=False, label="Half Price")

    class Meta:
        model = MenuItem
        fields = ["name", "category", "description", "image", "full_price", "half_price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["full_price"].initial = self.instance.full_price if self.instance.full_price is not None else self.instance.price
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean(self):
        cleaned_data = super().clean()
        full_price = cleaned_data.get("full_price")
        half_price = cleaned_data.get("half_price")
        if full_price is None:
            self.add_error("full_price", "Full price is required.")
        if half_price is not None and full_price is not None and half_price >= full_price:
            self.add_error("half_price", "Half price must be lower than full price.")
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.full_price = self.cleaned_data["full_price"]
        instance.price = self.cleaned_data["full_price"]
        instance.half_price = self.cleaned_data.get("half_price")
        if commit:
            instance.save()
        return instance

