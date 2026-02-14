from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Profile

class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)
    city = forms.ChoiceField(choices=[("almaty","Almaty"), ("astana","Astana")])
    sensitivity = forms.ChoiceField(choices=Profile.SENSITIVITY)
    age_group = forms.ChoiceField(choices=Profile.AGE_GROUP)
    asthma_flag = forms.BooleanField(required=False)
    other_resp_flag = forms.BooleanField(required=False)
    activity = forms.ChoiceField(choices=Profile.ACTIVITY)
    language = forms.ChoiceField(choices=Profile.LANG)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2",
                  "city", "sensitivity", "age_group", "asthma_flag", "other_resp_flag", "activity", "language")

class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ("city", "sensitivity", "age_group", "asthma_flag", "other_resp_flag", "activity", "language")
        widgets = {
            "city": forms.Select(),
            "sensitivity": forms.Select(),
            "age_group": forms.Select(),
            "activity": forms.Select(),
            "language": forms.Select(),
        }
