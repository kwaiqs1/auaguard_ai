import json
import re
from typing import Any, Dict, List, Optional

import requests
from django.conf import settings


class LLMError(RuntimeError):
    pass


class LLMClient:
    """
    OpenRouter (OpenAI-compatible) client for generating short health recommendations.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        site_url: str = "",
        app_name: str = "AuaGuardAI",
        timeout_s: int = 35,
    ):
        self.base_url = (base_url or "").rstrip("/") or "https://openrouter.ai/api/v1"
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.site_url = (site_url or "").strip()
        self.app_name = (app_name or "").strip() or "AuaGuardAI"
        self.timeout_s = int(timeout_s)

        if not self.model:
            raise LLMError("LLM_MODEL is missing.")
        if not self.api_key:
            raise LLMError("LLM_API_KEY is missing. Put your OpenRouter key into LLM_API_KEY or OPENROUTER_API_KEY in .env")

        self.session = requests.Session()

    @classmethod
    def from_settings(cls) -> "LLMClient":
        return cls(
            base_url=getattr(settings, "LLM_BASE_URL", "https://openrouter.ai/api/v1"),
            api_key=getattr(settings, "LLM_API_KEY", ""),
            model=getattr(settings, "LLM_MODEL", "deepseek/deepseek-r1-0528:free"),
            site_url=getattr(settings, "OPENROUTER_SITE_URL", ""),
            app_name=getattr(settings, "OPENROUTER_APP_NAME", "AuaGuardAI"),
        )

    def _headers(self) -> Dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Optional but recommended by OpenRouter for analytics/rate limits attribution
        if self.site_url:
            h["HTTP-Referer"] = self.site_url
        if self.app_name:
            h["X-Title"] = self.app_name
        return h

    @staticmethod
    def _clean(text: str) -> str:
        t = (text or "").strip()
        # remove excessive markdown noise if model returns it
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t

    def _chat(self, messages: List[Dict[str, str]], *, temperature: float = 0.4, max_tokens: int = 350) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }

        try:
            r = self.session.post(url, headers=self._headers(), json=payload, timeout=self.timeout_s)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            raise LLMError(f"OpenRouter request failed: {e}")

        if isinstance(data, dict) and data.get("error"):
            raise LLMError(str(data.get("error")))

        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            raise LLMError(f"Unexpected OpenRouter response shape: {json.dumps(data)[:500]}")

        content = self._clean(content)
        if not content:
            raise LLMError("Model returned empty content.")
        return content

    def generate_recommendation(self, payload: Dict[str, Any], *, mode: str = "today") -> str:
        lang = (payload.get("language") or "ru").lower()
        if lang.startswith("en"):
            out_lang = "English"
        else:
            out_lang = "Russian"

        system = (
            "You are AuaGuard AI — a health-first air quality assistant. "
            "You provide practical, safe, non-alarmist recommendations for outdoor activity and exposure reduction. "
            "You never invent measurements; you rely only on the provided payload. "
            f"Write your answer in {out_lang}."
        )

        if mode == "school":
            instructions = (
                "Task: advise whether a morning outdoor activity (school PE/outdoor break) is OK.\n"
                "Output format:\n"
                "1) Decision: Outdoor OK / Caution / Indoors\n"
                "2) 2–4 short bullet recommendations (mask/route/time/ventilation)\n"
                "3) One-line explanation referencing PM2.5/AQI and the user sensitivity.\n"
                "Keep it short (max ~120 words)."
            )
        else:
            instructions = (
                "Task: give personal recommendations for today.\n"
                "Output format:\n"
                "• 1 short summary sentence\n"
                "• 4–7 bullet points, actionable (time windows, mask, ventilation, indoor exercise, medication reminder only if explicitly in profile)\n"
                "• If air is Good/Moderate, still give light preventive tips.\n"
                "Keep it short (max ~160 words)."
            )

        user = (
            instructions
            + "\n\nPayload (JSON):\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )

        return self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.35 if mode == "school" else 0.45,
            max_tokens=420 if mode == "today" else 340,
        )
