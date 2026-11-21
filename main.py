import os, time, datetime as dt
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

CID = os.environ.get("CDSE_CLIENT_ID", "")
SEC = os.environ.get("CDSE_CLIENT_SECRET", "")
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
STAT_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"

app = FastAPI(title="Shor Radar API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_tok = {"v": None, "exp": 0}
_cache = {}

EVALSCRIPT = """//VERSION=3
function setup(){return{input:[{bands:["B03","B04","B08","SCL","dataMask"]}],
output:[{id:"si",bands:1},{id:"ndvi",bands:1},{id:"dataMask",bands:1}]}}
function evaluatePixel(s){
 var ok=s.dataMask==1&&[3,8,9,10,11].indexOf(s.SCL)<0?1:0;
 return{si:[Math.sqrt(s.B03*s.B04)],ndvi:[(s.B08-s.B04)/(s.B08+s.B04+1e-6)],dataMask:[ok]}}"""

async def token():
    if _tok["v"] and time.time() < _tok["exp"] - 60:
        return _tok["v"]
    if not CID or not SEC:
        raise HTTPException(500, "CDSE_CLIENT_ID / CDSE_CLIENT_SECRET sozlanmagan")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(TOKEN_URL, data={"grant_type": "client_credentials",
                                          "client_id": CID, "client_secret": SEC})
    if r.status_code != 200:
        raise HTTPException(502, "Token olinmadi: " + r.text[:200])
    j = r.json()
    _tok.update(v=j["access_token"], exp=time.time() + j.get("expires_in", 300))
    return _tok["v"]

def level(si, ndvi):
    # Taxminiy qoida: ochiq tuproq + yuqori yorqinlik = sho'r. EC o'lchovi o'rnini bosmaydi.
    if ndvi > 0.5:
        return "Past", 1.5
    if si < 0.16: return "Past", 1.5
    if si < 0.24: return "Oʻrta", 4.0
    return "Yuqori", 8.0

@app.get("/")
def root():
    return {"ok": True, "service": "Shor Radar API"}
    return HTMLResponse(INDEX)

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/salinity")
async def salinity(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180), days: int = 60):
    key = (round(lat, 4), round(lon, 4), days, dt.date.today().isoformat())
    if key in _cache:
        return _cache[key]
    end = dt.datetime.utcnow().replace(microsecond=0)
    start = end - dt.timedelta(days=days)
    dx, dy = 0.0007, 0.0005
    body = {
      "input": {"bounds": {"bbox": [lon-dx, lat-dy, lon+dx, lat+dy],
                           "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
                "data": [{"type": "sentinel-2-l2a", "dataFilter": {"maxCloudCoverage": 40}}]},
      "aggregation": {"timeRange": {"from": start.isoformat()+"Z", "to": end.isoformat()+"Z"},
                      "aggregationInterval": {"of": "P"+str(days)+"D"},
                      "evalscript": EVALSCRIPT, "resx": 0.0001, "resy": 0.0001}}
    t = await token()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(STAT_URL, json=body, headers={"Authorization": "Bearer "+t})
    if r.status_code != 200:
        raise HTTPException(502, "Sentinel Hub xatosi: " + r.text[:300])
    try:
        o = r.json()["data"][0]["outputs"]
        si = o["si"]["bands"]["B0"]["stats"]["mean"]
        ndvi = o["ndvi"]["bands"]["B0"]["stats"]["mean"]
    except Exception:
        raise HTTPException(404, "Bu nuqta uchun bulutsiz suratlar topilmadi")
    lv, ec = level(si, ndvi)
    res = {"lat": lat, "lon": lon, "period_days": days, "si": round(si, 3),
           "ndvi": round(ndvi, 3), "level": lv, "ec_estimate": ec,
           "note": "Sunʼiy yoʻldosh asosida taxminiy baho; EC-metr bilan tasdiqlang."}
    _cache[key] = res
    return res


INDEX = r'''<!DOCTYPE html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>SHOʻR RADAR</title>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#eef3ec;--card:#fff;--ink:#12261c;--mut:#5d7366;--g:#0f4a33;--g2:#1f7a52;--mint:#dcebdf;--line:#d9e3db;padding-top:env(safe-area-inset-top,0px)}
html,body{height:100%;margin:0}*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font:15px Manrope,system-ui,sans-serif;display:flex;justify-content:center}
#app{width:100%;max-width:480px;height:100%;display:flex;flex-direction:column}
main{flex:1;overflow-y:auto;padding:0 16px 24px}
.hd{display:flex;justify-content:space-between;align-items:center;padding:14px 0 6px}
.lg{display:flex;gap:10px;align-items:center;font-size:11px;line-height:1.1;font-weight:800;letter-spacing:1px}.lg i{background:var(--g);color:#fff;width:38px;height:38px;border-radius:11px;display:grid;place-items:center}
.lg b{font-size:15px}.bl{border:1px solid var(--line);background:var(--card);border-radius:50%;width:40px;height:40px;color:var(--ink)}
h1{font-size:30px;margin:10px 0 4px;font-weight:800;letter-spacing:-.6px}h2{font-size:19px;margin:20px 0 8px;font-weight:800}
.mut{color:var(--mut);font-size:13px}.cap{font-size:10.5px;font-weight:800;letter-spacing:1px;color:var(--mut)}
.tag{display:inline-block;background:var(--mint);color:var(--g2);font-size:10.5px;font-weight:800;padding:4px 9px;border-radius:6px;letter-spacing:.6px}
.card{background:var(--card);border-radius:20px;padding:16px;margin:10px 0;box-shadow:0 1px 3px #0f4a3314}
.hero{background:linear-gradient(155deg,#0f4a33,#082b1d);color:#fff;border-radius:24px;padding:18px;margin:10px 0}
.hero .tag{background:#ffffff1f;color:#d5ebdc}.big{font-size:46px;font-weight:800;letter-spacing:-1px}
.row{display:flex;gap:10px}.row>*{flex:1}.c{text-align:center}
.btn{display:block;width:100%;border:0;border-radius:15px;background:var(--g);color:#fff;font:800 15px Manrope,sans-serif;padding:15px;margin-top:10px}
.btn.alt{background:var(--mint);color:var(--g2)}
input,select{width:100%;padding:13px;border-radius:12px;border:1px solid var(--line);background:#fff;color:var(--ink);font:600 15px Manrope,sans-serif;margin:6px 0 12px}
label{font-size:11px;font-weight:800;color:var(--mut);letter-spacing:.5px}
nav{display:flex;background:var(--card);border-top:1px solid var(--line);padding:6px 4px calc(6px + env(safe-area-inset-bottom,0px))}
nav button{flex:1;border:0;background:none;color:var(--mut);font:700 11px Manrope,sans-serif;padding:6px 0;border-radius:14px}nav button.on{color:var(--g2);background:var(--mint)}nav svg{display:block;margin:0 auto 2px}
.seg{display:flex;background:#e2ebe3;border-radius:14px;padding:4px;margin:12px 0}.seg button{flex:1;border:0;background:none;padding:9px;border-radius:11px;font:800 13px Manrope,sans-serif;color:var(--mut)}.seg .on{background:#fff;color:var(--ink)}
.chip{border-radius:999px;font-size:11px;font-weight:800;padding:4px 10px;color:#fff}
.fr{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:13px 0;border-bottom:1px solid var(--line)}.fr:last-child{border:0}
.tile{background:var(--card);border-radius:18px;padding:14px;font-weight:800;font-size:13px;box-shadow:0 1px 3px #0f4a3314}.tile svg{color:var(--g2);margin-bottom:8px}
.box{background:#f2f6f1;border-radius:12px;padding:10px}.box b{display:block;font-size:14px}
.note{background:#fbeed7;border-radius:14px;padding:12px;font-size:13px;color:#7a5313;margin:10px 0}
.m{max-width:86%;padding:10px 13px;border-radius:16px;line-height:1.4;margin:6px 0}.m.u{margin-left:auto;background:var(--g);color:#fff}.m.a{background:var(--card)}
.qs button{border:1px solid var(--line);background:#fff;border-radius:999px;padding:8px 12px;margin:0 6px 6px 0;font:700 12px Manrope,sans-serif}
.soon{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:13px 0;border-bottom:1px solid var(--line)}.soon:last-child{border:0}
</style></head><body><div id="app"><main id="v"></main><nav id="n"></nav></div>
<script>
const F=[{id:1,n:"Oq oltin",c:"Paxta",ga:12.4,ec:4.2,r:"Sirdaryo",lat:40.55,lon:68.65,x:[[60,40],[120,30],[135,90],[70,100]]},
{id:2,n:"Bahor",c:"Bugʻdoy",ga:8.1,ec:1.6,r:"Sirdaryo",lat:40.6,lon:68.8,x:[[160,20],[230,30],[225,80],[165,75]]},
{id:3,n:"Shoʻrxok",c:"Arpa",ga:5.7,ec:9.3,r:"Xorazm",lat:41.3,lon:60.5,x:[[20,110],[90,115],[85,170],[25,165]]}];
const MH={Paxta:[7.7,5.2],Bugʻdoy:[6,7.1],Arpa:[8,5]};
const lv=e=>e<2?["Past","#4aa56a"]:e<6?["Oʻrta","#f1a43a"]:["Yuqori","#d9694b"];
const IC={home:"M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",map:"M9 4L3 6v14l6-2 6 2 6-2V4l-6 2zM9 4v14M15 6v14",cam:"M4 8h3l2-3h6l2 3h3v11H4zM12 17a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z",calc:"M6 3h12v18H6zM9 8h6M9 12h6M9 16h4",chat:"M4 5h16v11H9l-5 4z",bell:"M6 17v-6a6 6 0 1 1 12 0v6l2 2H4zM10 21h4",leaf:"M5 19C5 9 11 4 20 4c0 9-5 15-15 15zM5 19l8-8"};
const sv=n=>`<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="${IC[n]}"/></svg>`;
let S={tab:0,seg:0,scr:null,f:F[0],p:null,chat:[]},HS=[];
try{S.p=JSON.parse(localStorage.getItem("sr_p"));HS=JSON.parse(localStorage.getItem("sr_h"))||[]}catch(e){}
const $=h=>document.getElementById("v").innerHTML=h;
const T=[["Asosiy","home"],["Maydon","map"],["Tekshiruv","cam"],["Hisob","calc"],["Agronom","chat"]];
const hd=()=>`<div class="hd"><div class="lg" onclick="go(0)"><i>${sv("leaf")}</i><span>SHOʻR<br><b>RADAR</b></span></div><button class="bl" onclick="S.scr='n';render()">${sv("bell")}</button></div>`;
const go=i=>{S.tab=i;S.scr=null;render()},pick=i=>{S.f=F[i];render()};
function pt(v,r){const a=Math.PI*(1-Math.min(v,12)/12);return[100+r*Math.cos(a),100-r*Math.sin(a)]}
function arc(a,b,c){const p=pt(a,80),q=pt(b,80);return`<path d="M${p[0]} ${p[1]}A80 80 0 0 1 ${q[0]} ${q[1]}" fill="none" stroke="${c}" stroke-width="14"/>`}
function gauge(e){const n=pt(e,80);return`<svg viewBox="0 0 200 120" width="100%">${arc(0,2,"#4aa56a")}${arc(2,6,"#f1a43a")}${arc(6,12,"#d9694b")}<circle cx="${n[0]}" cy="${n[1]}" r="9" fill="#fff"/><text x="100" y="92" text-anchor="middle" fill="#fff" font-size="32" font-weight="800">${e}</text><text x="100" y="110" text-anchor="middle" fill="#b9d4c4" font-size="11">dS/m</text></svg>`}
function render(){const nv=document.getElementById("n");nv.innerHTML=S.p?T.map((t,i)=>`<button class="${S.tab==i&&!S.scr?"on":""}" onclick="go(${i})">${sv(t[1])}${t[0]}</button>`).join(""):"";
if(!S.p)return onb();if(S.scr=="n")return notif();[home,maydon,foto,hisob,agro][S.tab]();document.getElementById("v").scrollTop=0}
function onb(){$(`<div class="hd"><div class="lg"><i>${sv("leaf")}</i><span>SHOʻR<br><b>RADAR</b></span></div></div><span class="tag">DEMO MAʼLUMOT</span><h1>Xush kelibsiz</h1><p class="mut">Profilingizni yarating. Maʼlumotlar faqat shu brauzerda saqlanadi.</p>
<div class="card"><label>ISM YOKI KOʻYALIK NOMI *</label><input id="a" placeholder="Akmal fermer xoʻjaligi"><label>TELEFON RAQAMI</label><input id="t" type="tel" placeholder="+998 90 123 45 67"><label>VILOYAT</label><select id="b"><option>Sirdaryo<option>Xorazm<option>Qoraqalpogʻiston<option>Buxoro<option>Navoiy<option>Samarqand</select>
<label>HISOB TURI</label><div class="row" id="ty"><div class="tile" style="border:2px solid var(--g2);background:#eaf5ec">Fermer<div class="mut">Shaxsiy dalalar</div></div><div class="tile">Tashkilot<div class="mut">Jamoa va klaster</div></div></div>
<div class="note" style="background:#e4f1e6;color:#1f5a3c">FREE tarif bilan boshlaysiz: 1 ta maydon va asosiy funksiyalar.</div><button class="btn" onclick="reg()">Profil yaratish ›</button></div>`)}
function reg(){const n=document.getElementById("a").value.trim();if(!n){document.getElementById("a").style.borderColor="#d9694b";return}
S.p={n,r:document.getElementById("b").value};try{localStorage.setItem("sr_p",JSON.stringify(S.p))}catch(e){}render()}
function home(){const tot=F.reduce((s,f)=>s+f.ga,0),avg=F.reduce((s,f)=>s+f.ec*f.ga,0)/tot,rk=F.filter(f=>f.ec>=2).length,l=lv(avg),C=2*Math.PI*26;
$(`${hd()}<span class="cap">XAYRLI KUN</span><div class="row" style="align-items:center"><h1 style="margin:0">${S.p.n}</h1><div style="flex:0;font-weight:800;white-space:nowrap">26° <span class="mut">Quyoshli</span></div></div>
<div class="hero"><div style="display:flex;justify-content:space-between"><span class="tag">DEMO MAʼLUMOT</span><span style="font-size:12px;color:#b9d4c4" onclick="go(1)">Barcha maydonlar ›</span></div><div class="cap" style="color:#9fc5ae;margin-top:12px">UMUMIY MAYDON</div><div class="big">${tot.toFixed(1)} ga</div><div style="color:#b9d4c4;font-size:13px">${F.length} maydon · ${rk} xavf ostida</div></div>
<div class="row" style="align-items:center"><h2>Shoʻrlanish nazorati</h2></div><div class="card row" style="align-items:center"><svg viewBox="0 0 64 64" style="flex:0 0 74px"><circle cx="32" cy="32" r="26" fill="none" stroke="#e2ebe3" stroke-width="7"/><circle cx="32" cy="32" r="26" fill="none" stroke="${l[1]}" stroke-width="7" stroke-linecap="round" stroke-dasharray="${C*avg/12} ${C}" transform="rotate(-90 32 32)"/><text x="32" y="37" text-anchor="middle" font-size="15" font-weight="800" fill="#12261c">${avg.toFixed(1)}</text></svg><div><span class="chip" style="background:${l[1]}">${l[0]}</span><div style="font-weight:800;margin-top:6px">Oʻrtacha EC, dS/m</div><div class="mut">Gektarga moslangan, demo maʼlumot</div></div></div>
<h2>Nima qilamiz?</h2><div class="row"><div class="tile" onclick="go(2)">${sv("cam")}<br>Tuproq tekshiruvi</div><div class="tile" onclick="go(3)">${sv("calc")}<br>Suv hisobi</div><div class="tile" onclick="go(4)">${sv("chat")}<br>Agronom</div></div>
<div class="card" style="background:var(--mint)" onclick="S.scr='n';render()"><b>Platforma: Pro modullar</b><div class="mut">Monitoring, sugʻorish kalendari, hisobotlar — tez kunda</div></div>
<h2>Soʻnggi tahlillar</h2><div class="card" style="padding:4px 16px">${(HS.length?HS.slice(-3).reverse().map(h=>({n:h.n,ec:h.ec,d:h.d})):F.map(f=>({n:f.n,ec:f.ec,d:f.c}))).map(h=>`<div class="fr"><div><b>${h.n}</b><div class="mut">${h.d}</div></div><span class="chip" style="background:${lv(h.ec)[1]}">EC ${h.ec}</span></div>`).join("")}</div>`)}
function maydon(){const f=S.f,l=lv(f.ec),sg=["Xarita","Roʻyxat","Tarix"];
let b=`<div class="seg">${sg.map((s,i)=>`<button class="${S.seg==i?"on":""}" onclick="S.seg=${i};render()">${s}</button>`).join("")}</div>`;
if(S.seg==0)b+=`<div class="card" style="padding:8px"><svg viewBox="0 0 260 190" width="100%" style="background:#dde8dc;border-radius:14px">${F.map((g,i)=>!g.x.length?"":`<polygon onclick="pick(${i})" points="${g.x.join(" ")}" fill="${lv(g.ec)[1]}" fill-opacity=".8" stroke="#fff" stroke-width="${f.id==g.id?4:1.5}"/><text x="${g.x[0][0]+28}" y="${g.x[0][1]+38}" fill="#fff" font-weight="800" font-size="13" pointer-events="none">0${g.id}</text>`).join("")}</svg><div class="mut" style="padding:6px">● Past &nbsp;<span style="color:#f1a43a">●</span> Oʻrta &nbsp;<span style="color:#d9694b">●</span> Yuqori</div></div>
<div class="card"><div style="display:flex;justify-content:space-between"><span class="cap">TANLANGAN MAYDON</span><span class="chip" style="background:${l[1]}">${l[0]} xavf</span></div><h2 style="margin:6px 0 2px">${f.n}</h2><div class="mut">${f.r} · ${f.lat.toFixed(3)}, ${f.lon.toFixed(3)}</div><div class="row" style="margin-top:12px"><div class="box"><span class="mut">Ekin</span><b>${f.c}</b></div><div class="box"><span class="mut">Maydon</span><b>${f.ga} ga</b></div><div class="box"><span class="mut">EC</span><b>${f.ec} dS/m</b></div></div></div>
<div class="note">${f.sat?"Maʼlumot holati: SUNʼIY YOʻLDOSH. Sentinel-2 (SI "+f.sat.si+", NDVI "+f.sat.ndvi+", oxirgi "+f.sat.period_days+" kun). Taxminiy baho.":"Maʼlumot holati: NAMUNAVIY. Bu qiymat namunaviy, haqiqiy emas. Pastdagi tugma bilan sunʼiy yoʻldoshdan oling."}</div>
<div class="hero"><span class="tag">${f.sat?"SENTINEL-2":"DEMO MAʼLUMOT"}</span><div style="font-size:22px;font-weight:800;margin-top:8px">${f.n}</div>${gauge(f.ec)}<div class="row c" style="font-size:12px;color:#b9d4c4"><div>Past<br><b style="color:#fff">0–2</b></div><div>Oʻrta<br><b style="color:#fff">2–6</b></div><div>Yuqori<br><b style="color:#fff">6+</b></div></div></div>
<div class="card"><span class="cap">KEYINGI QADAM · ILMIY EHTIYOTKORLIK</span><h2 style="margin:6px 0">Oʻlchovni tasdiqlash</h2><p class="mut">Sunʼiy yoʻldosh EC ni aniq oʻlchamaydi. Qaror qilishdan oldin EC-metr bilan tasdiqlang. Tavsiya: ${f.ec>=6?"shoʻr yuvish 120 mm":f.ec>=2?"yengil shoʻr yuvish 100 mm":"shoʻr yuvish shart emas"}.</p><button class="btn" id="sb" onclick="sat()">Sunʼiy yoʻldoshdan maʼlumot olish</button><button class="btn alt" onclick="gps()">Mening joylashuvim (GPS)</button><button class="btn alt" onclick="save()">✓ Tarixga saqlash</button></div>`;
if(S.seg==1)b+=`<h2 style="margin-top:4px">${F.length} ta maydon</h2><div class="card" style="padding:4px 16px">${F.map((g,i)=>`<div class="fr" onclick="pick(${i});S.seg=0;render()"><div><b>${g.n}</b><div class="mut">${g.c} · ${g.ga} ga · ${g.r}</div></div><span class="chip" style="background:${lv(g.ec)[1]}">${lv(g.ec)[0]}</span></div>`).join("")}</div>`;
if(S.seg==2){const h=HS.filter(x=>x.n==f.n);b+=`<h2 style="margin-top:4px">Saqlangan oʻlchovlar</h2><div class="card">${h.length?h.map(x=>`<div class="fr" style="padding:8px 0"><span class="mut">${x.d}</span><div style="flex:1;background:#e2ebe3;border-radius:6px;height:12px"><div style="width:${Math.min(x.ec/12*100,100)}%;background:${lv(x.ec)[1]};height:12px;border-radius:6px"></div></div><b>${x.ec}</b></div>`).join(""):'<p class="mut">Hali oʻlchov yoʻq. Xarita bandida "Tarixga saqlash" ni bosing.</p>'}</div><p class="mut">2017-yildan tarix — Pro, tez kunda.</p>`}
$(hd()+b)}
async function sat(){const f=S.f,b=document.getElementById("sb");if(b){b.disabled=true;b.textContent="Sentinel-2 dan olinmoqda… (~1 daqiqa)"}
try{const r=await fetch("/salinity?lat="+f.lat+"&lon="+f.lon),j=await r.json();if(!r.ok)throw new Error(j.detail||"xato");f.ec=j.ec_estimate;f.sat=j;S.seg=0;render()}catch(e){if(b){b.disabled=false;b.textContent="Xato: "+e.message+" — qayta urining"}}}
function gps(){if(!navigator.geolocation)return alert("GPS yoʻq");navigator.geolocation.getCurrentPosition(p=>{const f={id:9,n:"Mening nuqtam",c:"Paxta",ga:1,ec:0,r:"GPS",lat:+p.coords.latitude.toFixed(5),lon:+p.coords.longitude.toFixed(5),x:[]};F.push(f);S.f=f;S.tab=1;S.seg=0;render();sat()},()=>alert("GPS ruxsati berilmadi"))}
function save(){HS.push({n:S.f.n,ec:S.f.ec,d:new Date().toLocaleDateString("uz")});try{localStorage.setItem("sr_h",JSON.stringify(HS))}catch(e){}S.seg=2;render()}
function foto(){$(`${hd()}<span class="tag">FOTO TEKSHIRUV</span><h1 style="font-size:26px">Tuproq yuzasini suratga oling</h1><p class="mut">Tuz poʻstlogʻi foizi surat rangidan taxminiy hisoblanadi.</p>
<div class="card c"><canvas id="cv" style="max-width:100%;border-radius:14px;display:none"></canvas><div id="ph" style="padding:44px 0;background:var(--mint);border-radius:14px;color:var(--g2)">${sv("cam")}<br><b>Surat yuklang</b></div><input type="file" accept="image/*" capture="environment" onchange="an(this)" style="margin-top:12px"><div id="rs" style="text-align:left"></div></div>
<div class="card"><b>Maslahat</b><p class="mut">1 m balandlikdan, soyasiz, quruq yuzani oling. Natija taxminiy.</p></div>`)}
function an(i){const f=i.files[0];if(!f)return;const im=new Image();im.onload=()=>{const c=document.getElementById("cv"),w=240,h=Math.round(im.height*w/im.width);c.width=w;c.height=h;const x=c.getContext("2d");x.drawImage(im,0,0,w,h);const d=x.getImageData(0,0,w,h).data;let s=0;
for(let k=0;k<d.length;k+=4){const mx=Math.max(d[k],d[k+1],d[k+2]),mn=Math.min(d[k],d[k+1],d[k+2]);if(mn>165&&(mx-mn)/mx<.16)s++}
const p=Math.round(s/(w*h)*100),m=p<10?["Past","tuz deyarli yoʻq",1]:p<30?["Oʻrta","yengil shoʻr yuvish kerak",3]:["Yuqori","zudlik bilan shoʻr yuvish va drenaj tekshiruvi kerak",9];
c.style.display="block";document.getElementById("ph").style.display="none";document.getElementById("rs").innerHTML=`<h2>Tuz poʻstlogʻi: ~${p}%</h2><span class="chip" style="background:${lv(m[2])[1]}">${m[0]}</span><p class="mut">${m[1]}. Taxminiy natija; EC-metr bilan tasdiqlang.</p>`};im.src=URL.createObjectURL(f)}
function hisob(){const f=S.f;
window.calc=()=>{const ga=+document.getElementById("ga").value||0,mm=+document.getElementById("mm").value||0;document.getElementById("o").textContent=Math.round(mm*ga*10).toLocaleString("uz")+" m³";
[80,100,120].forEach(v=>document.getElementById("s"+v).innerHTML=Math.round(v*ga*10).toLocaleString("uz")+" m³<div class='mut'>"+v+" mm</div>");
const k=document.getElementById("ck").value,m=MH[k],e=+document.getElementById("ee").value||0;document.getElementById("lo").textContent="~"+Math.max(0,Math.min(100,Math.round(m[1]*(e-m[0]))))+"% hosil yoʻqotilishi"};
$(`${hd()}<span class="tag">SUV HISOBI</span><h1 style="font-size:26px">Shoʻr yuvish ssenariylari</h1>
<div class="card"><label>MAYDON (ga)</label><input id="ga" type="number" value="${f.ga}" oninput="calc()"><label>SUV QATLAMI (mm)</label><input id="mm" type="number" value="100" oninput="calc()"></div>
<div class="hero c"><div class="cap" style="color:#9fc5ae">ASOSIY SSENARIY · 1 mm × ga × 10</div><div class="big" id="o"></div></div>
<div class="row c"><div class="box" id="s80"></div><div class="box" id="s100" style="background:#dcebdf"></div><div class="box" id="s120"></div></div>
<div class="card" style="margin-top:14px"><h2 style="margin-top:0">Hosil yoʻqotilishi (FAO)</h2><label>EKIN</label><select id="ck" onchange="calc()">${Object.keys(MH).map(c=>`<option ${c==f.c?"selected":""}>${c}`).join("")}</select><label>TUPROQ EC (dS/m)</label><input id="ee" type="number" step=".1" value="${f.ec}" oninput="calc()"><div class="hero c" style="font-size:20px;font-weight:800" id="lo"></div><p class="mut">Maas–Hoffman: Y = 100 − b·(EC − a). Narx hisobi (soʻm) — tez kunda.</p></div>`);calc()}
function ai(q){const f=S.f,l=lv(f.ec)[0].toLowerCase(),t=q.toLowerCase();
if(/yuv|shor|shoʻr|sho‘r/.test(t))return`${f.n}: EC ${f.ec} dS/m (${l}). ${f.ec>=6?"120 mm":f.ec>=2?"100 mm":"qoʻshimcha"} suv bilan yuvish: ${Math.round((f.ec>=6?120:100)*f.ga*10).toLocaleString("uz")} m³. Drenaj ochiqligini tekshiring.`;
if(/hosil|yoʻq|yoq|zarar/.test(t)){const m=MH[f.c]||[7,5];return`${f.c} uchun taxminiy hosil yoʻqotilishi ~${Math.max(0,Math.round(m[1]*(f.ec-m[0])))}%. Hisob bandida boshqa ekin bilan solishtiring.`}
if(/sugʻor|sug‘or|suv/.test(t))return"Shoʻr maydonda kam miqdorda, tez-tez sugʻorish yaxshi: ildiz zonasida tuz toʻplanmaydi. Ertalab yoki kechqurun sugʻoring.";
if(/ekin|arpa|paxta|bugʻdoy/.test(t))return f.ec>=6?"EC yuqori: arpa va paxta nisbatan chidamli, bugʻdoy zaiflashadi.":"Bu EC darajasida paxta va bugʻdoy ekish mumkin.";
return`${f.n} (${f.c}, ${f.ga} ga): EC ${f.ec} dS/m, shoʻrlanish ${l}. Yuvish, hosil yoʻqotilishi yoki sugʻorish haqida soʻrang.`}
function agro(){const q=["Shoʻr yuvish qancha suv kerak?","Hosil qancha yoʻqoladi?","Qachon sugʻorish kerak?"];
$(`${hd()}<div class="card row" style="align-items:center"><div class="lg" style="flex:0"><i>${sv("leaf")}</i></div><div><b>Shoʻr Agronom</b><div class="mut">${S.f.n} natijalarini biladi</div></div><span class="tag" style="flex:0">FREE</span></div>
<div id="ch"><div class="m a">Assalomu alaykum! ${S.f.n} maydoningiz (EC ${S.f.ec} dS/m) boʻyicha nima soʻraysiz?</div>${S.chat.map(m=>`<div class="m ${m[0]}">${m[1]}</div>`).join("")}</div>
<div class="qs" style="margin-top:10px">${q.map(x=>`<button onclick="say(this.textContent)">${x}</button>`).join("")}</div><div class="row" style="margin-top:6px"><input id="qq" placeholder="Savol yozing…" style="margin:0"><button class="btn" style="flex:0 0 90px;margin:0" onclick="say(document.getElementById('qq').value)">Yuborish</button></div><p class="mut">Demo rejim: tayyor qoidalar. Haqiqiy AI keyingi bosqichda.</p>`)}
function say(q){q=(q||"").trim();if(!q)return;S.chat.push(["u",q],["a",ai(q)]);agro();document.getElementById("v").scrollTop=1e5}
function notif(){const P=["Sunʼiy yoʻldosh monitoringi (har 5 kun)","Shoʻrlanish tarixi (2017–)","NDVI ekin salomatligi xaritasi","Sugʻorish kalendari","Shoʻr yuvish rejalashtirgichi","Hosil yoʻqotilishi (soʻmda)","Chang boʻroni va sovuq ogohlantirishi","PDF hisobot"];
$(`<div class="hd"><button class="bl" onclick="go(0)">‹</button><div class="lg"><span>PLATFORMA</span></div><span style="width:40px"></span></div><span class="tag">REJALASHTIRILGAN</span><h1 style="font-size:26px">Bildirishnomalar</h1><p class="mut">Bu modul hozircha namuna. Ishlaydigan xabarlar Pro versiyada.</p>
<div class="card" style="padding:4px 16px">${["Sugʻorish eslatmalari","Eskirgan EC oʻlchovi","Sensor va ob-havo ogohlantirishlari"].map(x=>`<div class="soon"><b>${x}</b><span class="tag">Tez kunda</span></div>`).join("")}</div>
<h2>Pro modullar</h2><div class="card" style="padding:4px 16px">${P.map(x=>`<div class="soon"><span>${x}</span><span class="tag">Tez kunda</span></div>`).join("")}</div>
<div class="hero"><b style="font-size:20px">FREE</b><div style="color:#b9d4c4;font-size:13px">Dala tekshiruvi, surat tahlili, agronom chat, 1 ta maydon</div></div>`)}
render();
</script></body></html>
