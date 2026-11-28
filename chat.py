import os
import httpx
from fastapi import APIRouter, Body, HTTPException, Request

from security import DailyCounter, client_ip

router = APIRouter()

KEY = os.environ.get("GEMINI_API_KEY", "").strip()
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
# Matches the FREE plan promise in the apps (10 questions per day).
LIMIT = int(os.environ.get("CHAT_DAILY_LIMIT", "10"))
INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_cnt = DailyCounter()

ERRORS = {
    "no_key": {"uz": "AI hozircha ulanmagan: serverda GEMINI_API_KEY sozlanmagan.",
               "ru": "ИИ пока не подключён: на сервере не задан GEMINI_API_KEY.",
               "kaa": "AI házirshe jalǵanbaǵan: serverde GEMINI_API_KEY sazlanbaǵan."},
    "limit": {"uz": "Kunlik savol limiti tugadi. Ertaga yana urinib ko‘ring.",
              "ru": "Дневной лимит вопросов исчерпан. Попробуйте завтра.",
              "kaa": "Kúnlik soraw limiti tawsıldı. Erteń qayta urınıp kóriń."},
    "empty": {"uz": "Savol bo‘sh.", "ru": "Вопрос пустой.", "kaa": "Soraw bos."},
    "bad_key": {"uz": "Gemini API kaliti noto‘g‘ri yoki loyiha ruxsati yoqilmagan.",
                "ru": "Ключ Gemini API неверный или доступ проекта не включён.",
                "kaa": "Gemini API gilti nadurıs yamasa joybar ruxsatı qosılmaǵan."},
    "quota": {"uz": "Gemini so‘rov limiti vaqtincha tugadi. Keyinroq urinib ko‘ring.",
              "ru": "Лимит запросов Gemini временно исчерпан. Попробуйте позже.",
              "kaa": "Gemini soraw limiti waqıtsha tawsıldı. Keyinirek urınıp kóriń."},
    "busy": {"uz": "Gemini xizmati vaqtincha band. Birozdan keyin qayta urinib ko‘ring.",
             "ru": "Сервис Gemini временно перегружен. Повторите попытку чуть позже.",
             "kaa": "Gemini xızmeti waqıtsha bánt. Azdan keyin qayta urınıp kóriń."},
    "timeout": {"uz": "Gemini javobi vaqtida kelmadi. Qayta urinib ko‘ring.",
                "ru": "Gemini не ответил вовремя. Попробуйте ещё раз.",
                "kaa": "Gemini juwabı waqtında kelmedi. Qayta urınıp kóriń."},
    "failed": {"uz": "Gemini javob bermadi yoki xizmatga ulanib bo‘lmadi.",
               "ru": "Gemini не ответил или сервис недоступен.",
               "kaa": "Gemini juwap bermedi yamasa xızmetke jalǵanıw múmkin bolmadı."},
}


def _err(status: int, code: str, language: str):
    raise HTTPException(status, ERRORS[code].get(language, ERRORS[code]["uz"]))

SYSTEM = (
    "Sen SHO'R RADAR platformasining Sho'r Agronom yordamchisisan. "
    "O'zbekistonda tuproq sho'rlanishi, sho'r yuvish, sug'orish va ekin tanlash "
    "bo'yicha fermerlarga tushunarli, amaliy va ehtiyotkor maslahat ber. "
    "Foydalanuvchining dala ma'lumotlaridan foydalan. Sun'iy yo'ldoshdan olingan "
    "SI va NDVI tuproq EC qiymatini bevosita o'lchamasligini, laboratoriya yoki "
    "kalibrlangan EC-metr bilan tasdiqlash kerakligini eslat. Aniq bilmasang, "
    "taxmin qilma va mahalliy agronom bilan maslahatlashishni ayt. "
    "Pestitsid yoki boshqa kimyoviy vositalarning xavfli dozalarini tavsiya qilma."
)

LANGUAGE_INSTRUCTIONS = {
    "uz": "Javobni faqat o'zbek tilida, lotin yozuvida yoz.",
    "ru": "Отвечай только на русском языке. Используй простой и понятный фермеру язык.",
    "kaa": "Juwaptı tek Qaraqalpaq tilinde jaz. Ápiwayı hám fermerge túsinikli sózlerdi qollan."
}


@router.post("/chat")
async def chat(req: Request, payload: dict = Body(...)):
    language = str(payload.get("language", "uz")).lower().strip()
    if language not in LANGUAGE_INSTRUCTIONS:
        language = "uz"
    if not KEY:
        _err(503, "no_key", language)

    day_key = client_ip(req)
    if _cnt.get(day_key) >= LIMIT:
        _err(429, "limit", language)

    question = str(payload.get("q", ""))[:500].strip()
    if not question:
        _err(400, "empty", language)

    # Count attempts before calling the paid upstream API, including failed attempts.
    _cnt.increment(day_key)

    field = payload.get("field") or {}
    if not isinstance(field, dict):
        field = {}
    context = (
        "Dala ma'lumotlari: nomi=%s, ekin=%s, maydon=%s ga, viloyat=%s, "
        "EC=%s dS/m, manba=%s, ma'lumot holati=%s, koordinatalar=%s,%s, NDVI=%s, SI=%s, NDMI=%s, "
        "sun'iy yo'ldosh davri=%s kun, tuproq=%s, drenaj=%s, sizot suvi=%s m."
        % (
            field.get("n") or "ko'rsatilmagan",
            field.get("c") or "ko'rsatilmagan",
            field.get("ga") or "ko'rsatilmagan",
            field.get("r") or "ko'rsatilmagan",
            field.get("ec") if field.get("ec") is not None else "mavjud emas",
            field.get("ec_source") or "ko'rsatilmagan",
            field.get("ec_status") or "mavjud emas",
            field.get("lat", "mavjud emas"),
            field.get("lon", "mavjud emas"),
            field.get("ndvi", "mavjud emas"),
            field.get("si", "mavjud emas"),
            field.get("ndmi", "mavjud emas"),
            field.get("period_days", "mavjud emas"),
            field.get("soil") or "ko'rsatilmagan",
            field.get("drainage") or "ko'rsatilmagan",
            field.get("groundwater_m", "mavjud emas"),
        )
    )

    transcript = []
    history = payload.get("history") or []
    if isinstance(history, list):
        for item in history[-6:]:
            if not isinstance(item, dict):
                continue
            role = {"bot": "assistant"}.get(item.get("role"), item.get("role"))
            message = item.get("content") or item.get("text")
            if item.get("error"):
                continue  # never feed client-side error notices back to the model
            if role in ("user", "assistant") and message:
                transcript.append(
                    ("Fermer" if role == "user" else "Sho'r Agronom")
                    + ": " + str(message)[:600]
                )

    if not transcript or not transcript[-1].endswith(question):
        transcript.append("Fermer: " + question)

    request_body = {
        "model": MODEL,
        "system_instruction": SYSTEM + "\n" + LANGUAGE_INSTRUCTIONS[language] + "\n" + context,
        "input": "\n".join(transcript),
        "generation_config": {
            "temperature": 0.4,
            "max_output_tokens": 300
        }
    }

    try:
        timeout = httpx.Timeout(connect=8.0, read=18.0, write=10.0, pool=8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                INTERACTIONS_URL,
                headers={
                    "x-goog-api-key": KEY,
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
    except httpx.TimeoutException:
        response = None
    except httpx.HTTPError:
        response = None

    answer = ""
    if response is not None and response.status_code == 200:
        try:
            data = response.json()
            for step in data.get("steps", []):
                if step.get("type") == "model_output":
                    for part in step.get("content", []):
                        if isinstance(part, dict) and part.get("text"):
                            answer_parts = part["text"]
                            answer += answer_parts
            answer = answer.strip()
        except (AttributeError, TypeError, ValueError):
            answer = ""

    # The Interactions endpoint can temporarily return 5xx. Fall back to the
    # standard generateContent endpoint so the farmer can still get an answer.
    if response is None or (not answer and response.status_code not in (401, 403, 429)):
        fallback_body = {
            "systemInstruction": {"parts": [{"text": SYSTEM + "\n" + LANGUAGE_INSTRUCTIONS[language] + "\n" + context}]},
            "contents": [{"role": "user", "parts": [{"text": "\n".join(transcript)}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 300}
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=8.0, read=24.0, write=10.0, pool=8.0)) as client:
                fallback = await client.post(
                    GENERATE_URL.format(model=MODEL),
                    headers={"x-goog-api-key": KEY, "Content-Type": "application/json"},
                    json=fallback_body,
                )
            if fallback.status_code == 200:
                payload_out = fallback.json()
                answer = "".join(
                    part.get("text", "")
                    for candidate in payload_out.get("candidates", [])
                    for part in candidate.get("content", {}).get("parts", [])
                    if isinstance(part, dict)
                ).strip()
            elif fallback.status_code in (401, 403):
                _err(502, "bad_key", language)
            elif fallback.status_code == 429:
                _err(503, "quota", language)
            else:
                print("Gemini generateContent error:", fallback.status_code, fallback.text[:300], flush=True)
        except httpx.TimeoutException:
            _err(504, "timeout", language)
        except httpx.HTTPError:
            pass

    if not answer:
        upstream_status = response.status_code if response is not None else 0
        if upstream_status == 401 or upstream_status == 403:
            _err(502, "bad_key", language)
        if upstream_status == 429:
            _err(503, "quota", language)
        if upstream_status == 503:
            _err(503, "busy", language)
        if response is not None:
            print("Gemini Interactions API error:", response.status_code, response.text[:300], flush=True)
        _err(502, "failed", language)

    return {"answer": answer, "left": max(0, LIMIT - _cnt.get(day_key)), "model": MODEL}
