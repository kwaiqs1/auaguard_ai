def app_context(request):
    lang = getattr(request, "LANGUAGE_CODE", "ru")
    return {
        "APP_NAME": "AuaGuard AI",
        "APP_TAGLINE_RU": "Прогноз смога и персональные рекомендации для Алматы",
        "APP_TAGLINE_KK": "Алматыға арналған смог болжамы және жеке ұсыныстар",
        "LANG_CODE": lang,
    }
