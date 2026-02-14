from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.utils.translation import activate

from .forms import SignupForm, ProfileForm

@require_http_methods(["GET", "POST"])
def signup_view(request):
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.email = form.cleaned_data["email"]
            user.save()

            # profile auto-created by signal
            p = user.profile
            p.city = form.cleaned_data["city"]
            p.sensitivity = form.cleaned_data["sensitivity"]
            p.age_group = form.cleaned_data["age_group"]
            p.asthma_flag = form.cleaned_data["asthma_flag"]
            p.other_resp_flag = form.cleaned_data["other_resp_flag"]
            p.activity = form.cleaned_data["activity"]
            p.language = form.cleaned_data["language"]
            p.save()

            login(request, user)
            activate(p.language)
            return redirect("/")
    else:
        form = SignupForm()
    return render(request, "accounts/signup.html", {"form": form})

@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            if hasattr(user, "profile"):
                activate(user.profile.language)
            return redirect("/")
    else:
        form = AuthenticationForm(request)
    return render(request, "accounts/login.html", {"form": form})

def logout_view(request):
    logout(request)
    return redirect("/")

@login_required
@require_http_methods(["GET", "POST"])
def profile_view(request):
    p = request.user.profile
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=p)
        if form.is_valid():
            form.save()
            activate(p.language)
            return redirect("/profile/")
    else:
        form = ProfileForm(instance=p)
    return render(request, "accounts/profile.html", {"form": form})
