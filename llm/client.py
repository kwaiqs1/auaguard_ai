import json
import re
import requests
from django.conf import settings

BANNED = [
    r"\bdiagnos", r"\bprescrib", r"\bantibiotic", r"\bsteroid", r"\binhaler",
    r"\bлечени", r"\bназнач", r:contentReference[oaicite:38]{index=38} :contentReference[oaicite:39]{index=39}heck(text: str) -> str:
    t = text.strip()
    for pat in BANNED:
        if re.search(pat, t, re.IGNORECASE):
            return "⚠️ Safe-mode: I can’t provide medical advice. Here are general exposure-reduction steps: reduce time outdoors, keep windows closed during peaks, choose indoor activities, and monitor updates."
    return t

class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, site_url: str = "", app_name: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model
        self.site_url = site_url
        self.app_name = app_name

    @classmethod
    def from_settings(cls):
        return cls(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
            site_url=getattr(settings, "OPENROUTER_SITE_URL", ""),
            app_name=getattr(settings, "OPENROUTER_APP_NAME", "AuaGuardAI"),
        )

    def generate_recommendation(self, payload: dict, mode: str = "today") -> str:
        # Strict JSON in, template out (doc) :contentReference[oaicite:40]{index=40}
        lang = payload.get("language", "ru")

        system = (
            "You are AuaGuard AI. You MUST follow these rules:\n"
            "1) NO medical diagnosis, NO drugs, NO treatment instructions.\n"
            "2) Do NOT invent measurements. Use ONLY fields in the JSON.\n"
            "3) Output must be short, practical, and template-based.\n"
            "4) Include :contentReference[oaicite:41]{index=41}
            "5) Language must be exactly the requested language.\n"
        )

        if mode == "school":
            template_ru = (
                "School Mode card.\n"
                "Return 4 bullet lines:\n"
                "• Decision: Outdoor OK / Caution / Indoors\n"
                "• Why: 1 sentence with PM2.5 + wind/pressure + trend\n"
                "• Action: 1 sentence (what school should do)\n"
                "• Confidence: (0.xx) + sources\n"
            )
            template_kk = (
                "Мектеп режимі.\n"
                "4 жол:\n"
                "• Шешім: Outdoor OK / Caution / Indoors\n"
                "• Неге: PM2.5 + жел/қысым + тренд\n"
                "• Әрекет: мектепке ұсыныс\n"
                "• Сенім: (0.xx) + дереккөз\n"
            )
        else:
            template_ru = (
                "Today decision card.\n"
                "Return 4 bullet lines:\n"
                "• Decision: short\n"
                "• Why: 1 sentence with PM2.5 + wind/pressure + trend\n"
                "• What to do: 1–2 practical steps\n"
                "• Confidence: (0.xx) + sources + updated time\n"
            )
            template_kk = (
                "Бүгінгі ұсыныс.\n"
                "4 жол:\n"
                "• Бүгін: қысқа шешім\n"
                "• Себебі: PM2.5 + жел/қысым + тренд\n"
                "• Ұсыныс: 1–2 қадам\n"
                "• Сенім: (0.xx) + дереккөз + уақыт\n"
            )

        user = {
            "task": "Generate a safe recommendation card.",
            "mode": mode,
            "template": template_kk if lang == "kk" else template_ru,
            "data": payload,
        }

        if not self.api_key:
            # fallback without LLM
            return self._fallback(payload, mode)

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # OpenRouter optional headers
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.app_name:
            headers["X-Title"] = self.app_name

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            "temperature": 0.2,
        }

        r = requests.post(url, headers=headers, json=body, timeout=30)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        return _postcheck(text)

    def _fallback(self, payload: dict, mode: str):
        # deterministic safe text if LLM key missing
        pm = payload.get("pm25_ug_m3")
        conf = payload.get("confidence")
        ts = payload.get("timestamp_local")
        lang = payload.get("language", "ru")
        if lang == "kk":
            return f"• Бүгін: қауіп деңгейін төмендетіңіз.\n• Себебі: PM2.5={pm} µg/m³.\n• Ұсыныс: далада уақытты азайтыңыз, спортты үйде жасаңыз.\n• Сенім: {conf:.2f}. Дереккөз: OpenAQ+OpenWeather; {ts}."
        return f"• Decision: reduce exposure today.\n• Why: PM2.5={pm} µg/m³.\n• What to do: spend less time outdoors; move sport indoors.\n• Confidence: {conf:.2f}. Source: OpenAQ+OpenWeather; updated {ts}."
