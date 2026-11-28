import os, time
import httpx
from fastapi import APIRouter, Body, HTTPException, Request

router = APIRouter()

# Gemini API key is stored in Render Environment Variables.
KEY = os.environ.get("GEMINI_API_KEY", "").strip()
MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest").strip()
LIMIT = int(os.environ.get("CHAT_DAILY_LIMIT", "15"))

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
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

    contents = []
    history = payload.get("history") or []
    if isinstance(history, list):
        for item in history[-6:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            message = item.get("content") or item.get("text")
            if role in ("user", "assistant") and message:
                contents.append({
                    "role": "user" if role == "user" else "model",
                    "parts": [{"text": str(message)[:600]}],
                })

    # Gemini conversation history must begin with a user message.
    while contents and contents[0]["role"] != "user":
        contents.pop(0)

    # Avoid duplicating the current question if it is already the last history item.
    if contents and contents[-1]["role"] == "user":
        last_text = contents[-1]["parts"][0]["text"].strip()
        if last_text == question:
            contents.pop()
    contents.append({"role": "user", "parts": [{"text": question}]})

    request_body = {
        "system_instruction": {
            "parts": [{"text": SYSTEM + "\n" + LANGUAGE_INSTRUCTIONS[language] + "\n" + context}]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 300,
        },
    }

    try:
        timeout = httpx.Timeout(connect=8.0, read=20.0, write=10.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                GEMINI_URL,
                headers={
                    "x-goog-api-key": KEY,
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
    except httpx.TimeoutException:
        raise HTTPException(504, "Gemini javobi vaqtida kelmadi. Qayta urinib ko'ring.")
    except httpx.HTTPError:
        raise HTTPException(502, "Gemini xizmatiga ulanishda xatolik yuz berdi.")

    if response.status_code != 200:
        print("Gemini API error:", response.status_code, response.text[:1500], flush=True)
        if response.status_code in (401, 403):
            raise HTTPException(502, "Gemini API kaliti noto'g'ri yoki API ruxsati yoqilmagan.")
        if response.status_code == 429:
            raise HTTPException(503, "Gemini so'rov limiti vaqtincha tugadi. Keyinroq urinib ko'ring.")
        raise HTTPException(502, f"Gemini xizmati xatolik qaytardi (HTTP {response.status_code}).")

    try:
        data = response.json()
        parts = data["candidates"][0]["content"]["parts"]
        answer = "".join(part.get("text", "") for part in parts).strip()
    except (KeyError, IndexError, TypeError, ValueError):
        raise HTTPException(502, "Gemini javobini o'qib bo'lmadi. Savolni qayta yuboring.")

    if not answer:
        raise HTTPException(502, "Gemini bo'sh javob qaytardi. Boshqacha savol berib ko'ring.")

    _cnt[day_key] = _cnt.get(day_key, 0) + 1
    return {"answer": answer, "left": LIMIT - _cnt[day_key]}
