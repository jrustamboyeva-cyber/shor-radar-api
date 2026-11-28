# shor-radar-api

SHO‘R RADAR uchun FastAPI server: Sentinel-2 ko‘rsatkichlari, sug‘orish va sho‘r yuvish tavsiyalari, Sho‘r Agronom (Gemini).

## Muhit o‘zgaruvchilari (Render → Environment)

| O‘zgaruvchi | Majburiy | Vazifasi |
|---|---|---|
| `CDSE_CLIENT_ID`, `CDSE_CLIENT_SECRET` | Sentinel-2 uchun | Copernicus Data Space OAuth client |
| `GEMINI_API_KEY` | AI uchun | Google AI Studio kaliti. Faqat serverda saqlanadi |
| `GEMINI_MODEL` | yo‘q | Standart: `gemini-3.8-flash`. `/health/verify` bilan tekshiring |
| `CHAT_DAILY_LIMIT` | yo‘q | IP bo‘yicha kunlik savollar soni, standart 10 |
| `TRUSTED_PROXY_HOPS` | yo‘q | `X-Forwarded-For` ga qo‘shadigan proksilar soni. Render uchun 1 |
| `CORS_ORIGINS` | yo‘q | Vergul bilan ajratilgan ruxsat etilgan manbalar, standart `*` |

## Endpointlar

- `GET /health`, `GET /health/integrations`, `POST /health/verify` (30 soniyada bir marta, jonli tekshiruv)
- `GET /salinity?lat&lon&days` — nuqta atrofidagi SI, NDVI, NDMI
- `POST /analyze` — `{"polygon": [[lat, lon], ...]}` yoki `{"lat", "lon"}`; dala chegarasi bo‘yicha
- `GET /history?lat&lon&months`, `POST /history` `{"polygon", "months"}` — oylik qatorlar
- `POST /recommendations` — EC, ekin, tuproq, drenaj, sizot suvi, sug‘orish suvi EC, haftalik ET₀ bo‘yicha tavsiya (`language`: `uz`/`ru`/`kaa`)
- `GET /crops?language=` — ekinlar va FAO chegaralari
- `POST /chat` — Sho‘r Agronom
- `POST /photo` — suratdagi och/neytral piksellar ulushi

Sentinel-2 indekslari tuproq EC qiymatini bevosita o‘lchamaydi. Tavsiya endpointi EC’ni faqat foydalanuvchi kiritgan laboratoriya yoki EC-metr qiymatidan oladi.

## Ishga tushirish va testlar

```bash
pip install -r requirements.txt pytest
uvicorn main:app --reload
pytest -q
```

Testlar Gemini va Copernicus’ga ulanmaydi: ular soxta javoblar bilan tekshiriladi.
