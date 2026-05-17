from django import forms
from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from .models import MenuItem, Profile


class SignUpForm(forms.ModelForm):
    full_name = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=20, required=True)
    password1 = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", strip=False, widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ("full_name", "email", "phone", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
            field.help_text = ""

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if email and User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "").strip()
        if not phone:
            raise ValidationError("Mobile number is required.")
        if User.objects.filter(username__iexact=phone).exists() or Profile.objects.filter(phone__iexact=phone).exists():
            raise ValidationError("An account with this mobile number already exists.")
        return phone

    def clean_full_name(self):
        full_name = self.cleaned_data.get("full_name", "").strip()
        if not full_name:
            raise ValidationError("Full name is required.")
        return full_name

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The two password fields didn’t match.")

        if password2:
            user = User(username=cleaned_data.get("phone", ""), email=cleaned_data.get("email", ""))
            try:
                password_validation.validate_password(password2, user)
            except ValidationError as error:
                self.add_error("password2", error)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["phone"]
        user.email = self.cleaned_data.get("email", "")
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            Profile.objects.create(
                user=user,
                full_name=self.cleaned_data.get("full_name", ""),
                phone=self.cleaned_data.get("phone", ""),
                address="",
            )
        return user


class MobileLoginForm(forms.Form):
    phone = forms.CharField(label="Mobile number", max_length=20)
    password = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput)

    error_messages = {
        "invalid_login": "Please enter a correct mobile number and password.",
        "inactive": "This account is inactive.",
    }

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user_cache = None
        super().__init__(*args, **kwargs)
        self.fields["phone"].widget.attrs.update({"class": "form-control", "placeholder": "Mobile number"})
        self.fields["password"].widget.attrs.update({"class": "form-control", "placeholder": "Password"})

    def clean_phone(self):
        return self.cleaned_data.get("phone", "").strip()

    def clean(self):
        cleaned_data = super().clean()
        mobile = cleaned_data.get("phone")
        password = cleaned_data.get("password")

        if mobile and password:
            username = mobile
            profile = Profile.objects.filter(phone__iexact=mobile).select_related("user").first()
            if profile:
                username = profile.user.username

            self.user_cache = authenticate(self.request, username=username, password=password)
            if self.user_cache is None:
                raise ValidationError(self.error_messages["invalid_login"], code="invalid_login")
            self.confirm_login_allowed(self.user_cache)

        return cleaned_data

    def confirm_login_allowed(self, user):
        if not user.is_active:
            raise ValidationError(self.error_messages["inactive"], code="inactive")

    def get_user(self):
        return self.user_cache


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
