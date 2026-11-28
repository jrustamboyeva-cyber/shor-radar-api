"""Irrigation and leaching recommendations (uz / ru / kaa).

Sources:
- FAO Irrigation and Drainage Paper 29 Rev.1 (Ayers & Westcot): crop salt tolerance
  (Maas-Hoffman threshold/slope) and maintenance leaching requirement
  LR = ECw / (5 * ECe_threshold - ECw).
- Hoffman (1980) reclamation leaching: Dw = Ds * k * ECi / ECf.
- FAO Irrigation and Drainage Paper 56: mid-season crop coefficients (Kc).

The soil EC value must come from a laboratory test or a calibrated EC meter.
Satellite indices are never converted into EC here.
"""
import math

from fastapi import APIRouter, Body, HTTPException

router = APIRouter()

# threshold a (dS/m), slope b (% per dS/m), Kc mid (FAO 56)
CROPS = {
    "cotton":     {"a": 7.7, "b": 5.2,  "kc": 1.15, "uz": "Paxta", "ru": "Хлопчатник", "kaa": "Paxta"},
    "wheat":      {"a": 6.0, "b": 7.1,  "kc": 1.15, "uz": "Bug‘doy", "ru": "Пшеница", "kaa": "Biyday"},
    "barley":     {"a": 8.0, "b": 5.0,  "kc": 1.15, "uz": "Arpa", "ru": "Ячмень", "kaa": "Arpa"},
    "sugar_beet": {"a": 7.0, "b": 5.9,  "kc": 1.20, "uz": "Qand lavlagi", "ru": "Сахарная свёкла", "kaa": "Qant lablebi"},
    "sorghum":    {"a": 6.8, "b": 16.0, "kc": 1.00, "uz": "Oq jo‘xori", "ru": "Сорго", "kaa": "Aq júweri"},
    "alfalfa":    {"a": 2.0, "b": 7.3,  "kc": 0.95, "uz": "Beda", "ru": "Люцерна", "kaa": "Jońıshqa"},
    "tomato":     {"a": 2.5, "b": 9.9,  "kc": 1.15, "uz": "Pomidor", "ru": "Томат", "kaa": "Pomidor"},
    "rice":       {"a": 3.0, "b": 12.0, "kc": 1.20, "uz": "Sholi", "ru": "Рис", "kaa": "Shalı"},
    "maize":      {"a": 1.7, "b": 12.0, "kc": 1.20, "uz": "Makkajo‘xori", "ru": "Кукуруза", "kaa": "Mákke júweri"},
    "potato":     {"a": 1.7, "b": 12.0, "kc": 1.15, "uz": "Kartoshka", "ru": "Картофель", "kaa": "Kartoshka"},
    "onion":      {"a": 1.2, "b": 16.0, "kc": 1.05, "uz": "Piyoz", "ru": "Лук", "kaa": "Piyaz"},
    "mung_bean":  {"a": 1.8, "b": 20.7, "kc": 1.05, "uz": "Mosh", "ru": "Маш", "kaa": "Mash"},
}

# Uzbek crop names used by the web form -> crop id
CROP_ALIASES = {
    "paxta": "cotton", "bug‘doy": "wheat", "bug'doy": "wheat", "arpa": "barley", "sholi": "rice",
    "makkajo‘xori": "maize", "makkajo'xori": "maize", "mosh": "mung_bean", "beda": "alfalfa",
    "pomidor": "tomato", "kartoshka": "potato", "piyoz": "onion", "qand lavlagi": "sugar_beet",
}

# Hoffman (1980) k by soil texture for continuous ponding (approximate; calibrate locally)
TEXTURE_K = {"sand": 0.15, "loam": 0.3, "clay": 0.45}
CLASSES = [(2, "none"), (4, "weak"), (8, "moderate"), (16, "strong"), (math.inf, "extreme")]
ROUND_M3_PER_HA = 1800  # one leaching round, m³/ha

T = {
    "uz": {
        "none": "Sho‘rlanmagan", "weak": "Kuchsiz sho‘rlangan", "moderate": "O‘rtacha sho‘rlangan",
        "strong": "Kuchli sho‘rlangan", "extreme": "Juda kuchli sho‘rlangan",
        "need_ec": "Sho‘r yuvish me’yorini hisoblash uchun tuproq EC qiymati kerak (laboratoriya yoki kalibrlangan EC-metr, 0–60 sm). Sun’iy yo‘ldosh indekslari EC o‘rnini bosmaydi.",
        "no_leach": "Sho‘r yuvish shart emas: EC {crop} uchun chegaradan ({a} dS/m) past.",
        "leach": "Sho‘r yuvish: ~{m3} m³/ga, {rounds} martada (har birida ~{per} m³/ga). Maqsad: EC {eci} → {ecf} dS/m.",
        "timing": "Eng qulay vaqt — noyabr oxiridan fevralgacha. Har navbatdagi suvni oldingisi singib ketgach bering.",
        "drain_poor": "Drenaj yomon: avval kollektor-drenajni tozalang. Aks holda yuvilgan tuz pastga ketmaydi va sizot suvi ko‘tariladi.",
        "drain_none": "Drenaj yo‘q: sho‘r yuvish samarasiz va sizot suvini ko‘taradi. Avval drenaj masalasini mutaxassis bilan hal qiling.",
        "gw_high": "Sizot suvi {gw} m da — juda yaqin. Ortiqcha sug‘orish tuzni yuzaga chiqaradi; suv me’yorini oshirmang.",
        "lr": "Sug‘orish suvining EC’si {ecw} dS/m bo‘lsa, har sug‘orishga ~{lr}% qo‘shimcha suv tuzni ildiz qatlamidan pastga yuvadi (FAO 29).",
        "lr_bad": "Sug‘orish suvi juda sho‘r ({ecw} dS/m): {crop} uchun yaroqsiz. Boshqa suv manbai yoki chidamliroq ekin kerak.",
        "weekly": "Bu hafta uchun taxminiy ehtiyoj: {mm} mm ≈ {m3} m³/ga (ET₀ {et0} mm × Kc {kc}, yuvish ulushi bilan).",
        "loss": "{crop}: kutilayotgan hosil yo‘qotilishi ~{loss}% (FAO, Maas–Hoffman).",
        "gypsum": "EC yuqori: gips (3–5 t/ga) va organik o‘g‘it tuproq tuzilishini yaxshilaydi, yuvish samarasini oshiradi.",
        "texture_note": "Hisob Hoffman koeffitsiyenti (k={k}) bilan — taxminiy. Mahalliy tajriba bilan aniqlashtiring.",
    },
    "ru": {
        "none": "Незасолённая", "weak": "Слабозасолённая", "moderate": "Среднезасолённая",
        "strong": "Сильнозасолённая", "extreme": "Очень сильнозасолённая",
        "need_ec": "Для расчёта промывной нормы нужно значение EC почвы (лаборатория или калиброванный EC-метр, 0–60 см). Спутниковые индексы не заменяют EC.",
        "no_leach": "Промывка не требуется: EC ниже порога для культуры «{crop}» ({a} dS/m).",
        "leach": "Промывка: ~{m3} м³/га, в {rounds} приёма (по ~{per} м³/га). Цель: EC {eci} → {ecf} dS/m.",
        "timing": "Лучшее время — с конца ноября до февраля. Следующую подачу воды давайте после впитывания предыдущей.",
        "drain_poor": "Дренаж плохой: сначала очистите коллекторно-дренажную сеть, иначе соли не уйдут вниз и поднимутся грунтовые воды.",
        "drain_none": "Дренажа нет: промывка неэффективна и поднимает грунтовые воды. Сначала решите вопрос дренажа со специалистом.",
        "gw_high": "Грунтовые воды на глубине {gw} м — очень близко. Избыточный полив выносит соли на поверхность; не увеличивайте норму.",
        "lr": "При EC поливной воды {ecw} dS/m к каждому поливу нужно ~{lr}% дополнительной воды для выноса солей из корнеобитаемого слоя (ФАО 29).",
        "lr_bad": "Поливная вода слишком солёная ({ecw} dS/m) для культуры «{crop}». Нужен другой источник воды или более устойчивая культура.",
        "weekly": "Ориентировочная потребность на эту неделю: {mm} мм ≈ {m3} м³/га (ET₀ {et0} мм × Kc {kc}, с учётом промывной доли).",
        "loss": "{crop}: ожидаемая потеря урожая ~{loss}% (ФАО, Маас–Хоффман).",
        "gypsum": "EC высокий: гипс (3–5 т/га) и органические удобрения улучшают структуру почвы и эффективность промывки.",
        "texture_note": "Расчёт по коэффициенту Хоффмана (k={k}) — ориентировочный. Уточните по местному опыту.",
    },
    "kaa": {
        "none": "Duzlanbaǵan", "weak": "Az duzlanǵan", "moderate": "Ortasha duzlanǵan",
        "strong": "Kúshli duzlanǵan", "extreme": "Júdá kúshli duzlanǵan",
        "need_ec": "Duz juwıw normasın esaplaw ushın topıraqtıń EC mánisi kerek (laboratoriya yamasa kalibrlengen EC-metr, 0–60 sm). Jasalma joldas indeksleri EC ornın baspaydı.",
        "no_leach": "Duz juwıw shárt emes: EC {crop} ushın shegaradan ({a} dS/m) tómen.",
        "leach": "Duz juwıw: ~{m3} m³/ga, {rounds} ret (hár birinde ~{per} m³/ga). Maqset: EC {eci} → {ecf} dS/m.",
        "timing": "Eń qolaylı waqıt — noyabr aqırınan fevralǵa shekem. Keyingi suwdı aldıńǵısı sińip ketkennen keyin beriń.",
        "drain_poor": "Drenaj jaman: aldın kollektor-drenajdı tazalań. Bolmasa juwılǵan duz tómenge ketpeydi hám jer astı suwı kóteriledi.",
        "drain_none": "Drenaj joq: duz juwıw nátiyjesiz hám jer astı suwın kóteredi. Aldın drenaj máselesin qánige menen sheshiń.",
        "gw_high": "Jer astı suwı {gw} m de — júdá jaqın. Artıqsha suwǵarıw duzdı betke shıǵaradı; suw normasın asırmań.",
        "lr": "Suwǵarıw suwınıń EC si {ecw} dS/m bolsa, hár suwǵarıwǵa ~{lr}% qosımsha suw duzdı tamır qatlamınan tómenge juwadı (FAO 29).",
        "lr_bad": "Suwǵarıw suwı júdá duzlı ({ecw} dS/m): {crop} ushın jaramsız. Basqa suw deregi yamasa shıdamlıraq eginlik kerek.",
        "weekly": "Usı hápte ushın shamalıq mútajlik: {mm} mm ≈ {m3} m³/ga (ET₀ {et0} mm × Kc {kc}, juwıw úlesi menen).",
        "loss": "{crop}: kútilip atırǵan ónim joǵaltıw ~{loss}% (FAO, Maas–Hoffman).",
        "gypsum": "EC joqarı: gips (3–5 t/ga) hám organikalıq tóginler topıraq dúzilisin jaqsılaydı, juwıw nátiyjesin asıradı.",
        "texture_note": "Esap Hoffman koefficienti (k={k}) menen — shamalıq. Jergilikli tájiriybe menen anıqlań.",
    },
}


def salinity_class(ec: float) -> str:
    return next(name for limit, name in CLASSES if ec < limit)


def yield_loss(crop: dict, ec: float) -> float:
    return 0.0 if ec <= crop["a"] else min(100.0, crop["b"] * (ec - crop["a"]))


def resolve_crop(value) -> str:
    key = str(value or "cotton").strip().lower()
    return key if key in CROPS else CROP_ALIASES.get(key, "cotton")


def _positive(value, name, upper):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{name} son bo‘lishi kerak")
    if not math.isfinite(number) or number < 0 or number > upper:
        raise HTTPException(422, f"{name} 0 dan {upper} gacha bo‘lishi kerak")
    return number


def build(payload: dict) -> dict:
    lang = str(payload.get("language", "uz")).lower()
    if lang not in T:
        lang = "uz"
    t = T[lang]
    crop_id = resolve_crop(payload.get("crop"))
    crop = CROPS[crop_id]
    crop_name = crop[lang]
    ec = _positive(payload.get("ec"), "EC", 60)
    ecw = _positive(payload.get("water_ec"), "Sug‘orish suvi EC", 20)
    area = _positive(payload.get("area_ha"), "Maydon", 100000) or 1.0
    gw = _positive(payload.get("groundwater_m"), "Sizot suvi chuqurligi", 50)
    et0 = _positive(payload.get("et0_week_mm"), "ET₀", 150)
    drainage = str(payload.get("drainage", "unknown")).lower()
    texture = str(payload.get("soil_texture", "loam")).lower()
    k = TEXTURE_K.get(texture, TEXTURE_K["loam"])
    depth_m = _positive(payload.get("root_depth_m"), "Ildiz qatlami", 2) or 0.6

    tips: list[str] = []
    result = {
        "language": lang, "crop": crop_id, "crop_name": crop_name, "area_ha": area,
        "ec": ec, "ec_source": payload.get("ec_source") or None,
        "salinity_class": None, "salinity_label": None, "yield_loss_percent": None,
        "leaching": None, "maintenance": None, "tips": tips,
        "sources": ["FAO 29 (Maas–Hoffman, LR)", "Hoffman 1980", "FAO 56 (Kc)"],
    }

    if drainage == "none":
        tips.append(t["drain_none"])
    elif drainage == "poor":
        tips.append(t["drain_poor"])
    if gw is not None and gw < 2:
        tips.append(t["gw_high"].format(gw=gw))

    if ec is None:
        tips.insert(0, t["need_ec"])
    else:
        cls = salinity_class(ec)
        loss = round(yield_loss(crop, ec), 1)
        result.update(salinity_class=cls, salinity_label=t[cls], yield_loss_percent=loss)
        tips.append(t["loss"].format(crop=crop_name, loss=loss))
        target = crop["a"]
        if ec <= target:
            tips.insert(0, t["no_leach"].format(crop=crop_name, a=target))
            result["leaching"] = {"needed": False, "target_ec": target}
        else:
            dw_m = depth_m * k * ec / target
            per_ha = int(round(dw_m * 10000 / 50) * 50)
            rounds = max(1, math.ceil(per_ha / ROUND_M3_PER_HA))
            per_round = int(round(per_ha / rounds / 50) * 50)
            result["leaching"] = {
                "needed": True, "target_ec": target, "m3_per_ha": per_ha, "rounds": rounds,
                "m3_per_round": per_round, "total_m3": int(round(per_ha * area)),
                "k": k, "depth_m": depth_m, "effective": drainage not in ("none",),
            }
            tips.insert(0, t["leach"].format(m3=f"{per_ha:,}".replace(",", " "), rounds=rounds,
                                             per=f"{per_round:,}".replace(",", " "), eci=ec, ecf=target))
            tips.insert(1, t["timing"])
            tips.append(t["texture_note"].format(k=k))
            if ec >= 8:
                tips.append(t["gypsum"])

    if ecw is not None:
        denominator = 5 * crop["a"] - ecw
        if denominator <= 0:
            result["maintenance"] = {"water_ec": ecw, "usable": False}
            tips.append(t["lr_bad"].format(ecw=ecw, crop=crop_name))
        else:
            lr = ecw / denominator
            maintenance = {"water_ec": ecw, "usable": True, "leaching_fraction": round(lr, 3)}
            tips.append(t["lr"].format(ecw=ecw, lr=round(lr * 100)))
            if et0 is not None:
                etc = et0 * crop["kc"]
                gross = etc / (1 - min(lr, 0.6))
                maintenance.update(et0_week_mm=et0, kc=crop["kc"], week_mm=round(gross, 1),
                                   week_m3_per_ha=int(round(gross * 10)))
                tips.append(t["weekly"].format(mm=round(gross), m3=int(round(gross * 10)), et0=et0, kc=crop["kc"]))
            result["maintenance"] = maintenance

    return result


@router.post("/recommendations")
def recommendations(payload: dict = Body(...)):
    if not isinstance(payload, dict):
        raise HTTPException(422, "JSON obyekt kerak")
    return build(payload)


@router.get("/crops")
def crops(language: str = "uz"):
    lang = language if language in T else "uz"
    return [{"id": key, "name": value[lang], "threshold": value["a"], "slope": value["b"], "kc": value["kc"]}
            for key, value in CROPS.items()]
