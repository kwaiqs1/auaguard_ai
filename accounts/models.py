from django.conf import settings
from django.db import models

class Profile(models.Model):
    SENSITIVITY = [
        ("normal", "Normal"),
        ("sensitive", "Sensitive"),
        ("high", "High risk"),
    ]
    AGE_GROUP = [
        ("child", "Child"),
        ("teen", "Teen"),
        ("adult", "Adult"),
        ("elderly", "Elderly"),
    ]
    ACTIVITY = [
        ("indoor", "Mostly indoor"),
        ("commute", "Commute"),
        ("outdoor_work", "Outdoor work"),
        ("outdoor_sport", "Outdoor sport"),
    ]
    LANG = [("ru", "Русский"), ("kk", "Қазақша")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    city = models.CharField(max_length=32, default="almaty")
    sensitivity = models.CharField(max_length=16, choices=SENSITIVITY, default="normal")
    age_group = models.CharField(max_length=16, choices=AGE_GROUP, default="adult")
    asthma_flag = models.BooleanField(default=False)
    other_resp_flag = models.BooleanField(default=False)
    activity = models.CharField(max_length=16, choices=ACTIVITY, default="commute")
    language = models.CharField(max_length=4, choices=LANG, default="ru")

    def __str__(self):
        return f"Profile({self.user.username})"
