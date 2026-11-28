import os, time
import httpx
from fastapi import APIRouter, Body, HTTPException, Request

router = APIRouter()

KEY = os.environ.get("GEMINI_API_KEY", "").strip()
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
LIMIT = int(os.environ.get("CHAT_DAILY_LIMIT", "15"))
INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_cnt = {}

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
    if not KEY:
        raise HTTPException(503, "Gemini API kaliti sozlanmagan: GEMINI_API_KEY")

    ip = (
        req.headers.get("x-forwarded-for")
        or (req.client.host if req.client else "?")
    ).split(",")[0].strip()
    day_key = ip + time.strftime("%Y%m%d")

    if _cnt.get(day_key, 0) >= LIMIT:
        raise HTTPException(429, "Kunlik savol limiti tugadi. Ertaga yana urinib ko'ring.")

    question = str(payload.get("q", ""))[:500].strip()
    if not question:
        raise HTTPException(400, "Savol bo'sh")

    language = str(payload.get("language", "uz")).lower().strip()
    if language not in LANGUAGE_INSTRUCTIONS:
        language = "uz"

    field = payload.get("field") or {}
    if not isinstance(field, dict):
        field = {}
    context = (
        "Dala ma'lumotlari: nomi=%s, ekin=%s, maydon=%s ga, viloyat=%s, "
        "EC=%s dS/m, manba=%s, ma'lumot holati=%s, koordinatalar=%s,%s, NDVI=%s, SI=%s."
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
        )
    )

    transcript = []
    history = payload.get("history") or []
    if isinstance(history, list):
        for item in history[-6:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            message = item.get("content") or item.get("text")
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
                raise HTTPException(502, "Gemini API kaliti noto'g'ri yoki loyiha ruxsati yoqilmagan.")
            elif fallback.status_code == 429:
                raise HTTPException(503, "Gemini so'rov limiti vaqtincha tugadi. Keyinroq urinib ko'ring.")
        except httpx.TimeoutException:
            raise HTTPException(504, "Gemini javobi vaqtida kelmadi. Keyinroq qayta urinib ko'ring.")
        except httpx.HTTPError:
            pass

    if not answer:
        upstream_status = response.status_code if response is not None else 0
        if upstream_status == 401 or upstream_status == 403:
            raise HTTPException(502, "Gemini API kaliti noto'g'ri yoki loyiha ruxsati yoqilmagan.")
        if upstream_status == 429:
            raise HTTPException(503, "Gemini so'rov limiti vaqtincha tugadi. Keyinroq urinib ko'ring.")
        if upstream_status == 503:
            raise HTTPException(503, "Gemini xizmati vaqtincha band. Birozdan keyin qayta urinib ko'ring.")
        if response is not None:
            print("Gemini Interactions API error:", response.status_code, response.text[:300], flush=True)
        raise HTTPException(502, "Gemini javob bermadi yoki xizmatga ulanib bo'lmadi.")

    if not answer:
        raise HTTPException(502, "Gemini bo'sh javob qaytardi. Boshqacha savol berib ko'ring.")

    _cnt[day_key] = _cnt.get(day_key, 0) + 1
    return {"answer": answer, "left": LIMIT - _cnt[day_key]}
