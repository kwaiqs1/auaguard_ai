from django.contrib.auth.decorators import login_required
from django.shortcuts import render

def dashboard(request):
    return render(request, "core/dashboard.html")

def map_view(request):
    return render(request, "core/map.html")

def outlook_view(request):
    return render(request, "core/outlook.html")

def school_view(request):
    return render(request, "core/school.html")

def about_view(request):
    return render(request, "core/about.html")
