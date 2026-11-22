import os, time
import httpx
from fastapi import APIRouter, Body, HTTPException, Request

router = APIRouter()
KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("CHAT_MODEL", "claude-haiku-5-5")
LIMIT = int(os.environ.get("CHAT_DAILY_LIMIT", "15"))
_cnt = {}

SYSTEM = ("Sen 'Shoʻr Agronom'san: Oʻzbekistonda tuproq shoʻrlanishi, shoʻr yuvish, sugʻorish va ekin tanlash boʻyicha "
          "yordamchi. Faqat oʻzbek tilida, oddiy fermerga tushunarli, 3-5 gapda javob ber. "
          "Foydalanuvchining dalasi maʼlumotlari quyida berilgan, javobda ulardan foydalan. "
          "EC qiymatlari sunʼiy yoʻldoshdan taxminiy olingan, buni va EC-metr bilan tasdiqlash kerakligini eslat. "
          "Aniq bilmasang, taxmin qilma, agronom bilan maslahatlashishni ayt. Pestitsid va dori dozalarini aytma.")

@router.post("/chat")
async def chat(req: Request, payload: dict = Body(...)):
    if not KEY:
        raise HTTPException(503, "AI kaliti sozlanmagan")
    ip = (req.headers.get("x-forwarded-for") or (req.client.host if req.client else "?")).split(",")[0].strip()
    k = ip + time.strftime("%Y%m%d")
    if _cnt.get(k, 0) >= LIMIT:
        raise HTTPException(429, "Kunlik savol limiti tugadi. Ertaga yana urinib koʻring.")
    q = str(payload.get("q", ""))[:500].strip()
    if not q:
        raise HTTPException(400, "Savol boʻsh")
    f = payload.get("field") or {}
    ctx = "Dala: %s, ekin: %s, maydon: %s ga, viloyat: %s, EC: %s dS/m%s." % (
        f.get("n"), f.get("c"), f.get("ga"), f.get("r"), f.get("ec"),
        " (Sentinel-2 asosida)" if f.get("sat") else " (namunaviy)")
    msgs = []
    for m in (payload.get("history") or [])[-6:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            msgs.append({"role": m["role"], "content": str(m["content"])[:600]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if msgs and msgs[-1]["role"] == "user":
        msgs.pop()
    msgs.append({"role": "user", "content": q})
    async with httpx.AsyncClient(timeout=40) as c:
        r = await c.post("https://api.anthropic.com/v1/messages",
                         headers={"x-api-key": KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                         json={"model": MODEL, "max_tokens": 400, "system": SYSTEM + " " + ctx, "messages": msgs})
    if r.status_code != 200:
        raise HTTPException(502, "AI javob bermadi")
    _cnt[k] = _cnt.get(k, 0) + 1
    txt = "".join(b.get("text", "") for b in r.json().get("content", []))
    return {"answer": txt, "left": LIMIT - _cnt[k]}

