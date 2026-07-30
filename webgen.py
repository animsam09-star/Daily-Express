"""텔레그램으로 보내는 브리핑을 웹 대시보드 HTML 로도 만든다.

Cloudflare Pages 에 올릴 정적 파일을 생성한다 — 서버가 없으므로 데이터는
JSON 으로 페이지에 심고, 차트는 브라우저에서 Chart.js 로 그린다.
매트플롯립 이미지와 달리 **호버 툴팁으로 수치를 읽을 수 있고**, 종목 행을
클릭하면 지수 차트와 같은 형식의 상세 차트(2년 추이 + 이동평균 + 기간
수익률)가 모달로 뜬다.

파일 구성 (site/):
  index.html   us / kr 로 가는 랜딩
  us.html      미국 마켓 브리핑 (build_us)
  kr.html      한국증시 마감 브리핑 (build_kr)

어느 시장 페이지도 같은 템플릿을 쓴다. 시장별 차이는 DATA JSON 안에 있다.
생성 실패는 발송을 막지 않는다(호출부에서 try/except).
"""
from __future__ import annotations

import datetime as dt
import json
import os

# 최근 6개월은 일별, 그 이전은 주별로 다운샘플해 페이지 크기를 줄인다.
# 종목 55개 × 500포인트를 그대로 실으면 페이지가 1MB 를 넘는다.
DAILY_KEEP_DAYS = 182


def _ds(series):
    """[(date, value)] -> [[iso, value]]. 오래된 구간은 주별(금요일 근사)."""
    if not series:
        return []
    cut = series[-1][0] - dt.timedelta(days=DAILY_KEEP_DAYS)
    out, last_week = [], None
    for d, v in series:
        if v is None:
            continue
        if d >= cut:
            out.append([d.isoformat(), round(float(v), 4)])
        else:
            wk = d.isocalendar()[:2]
            if wk != last_week:
                out.append([d.isoformat(), round(float(v), 4)])
                last_week = wk
    return out


def _returns(r):
    r = r or {}
    return {k: (round(r[k], 2) if r.get(k) is not None else None)
            for k in ("m1", "m3", "m6", "m12")}


def _cap_krw(v):
    """시가총액 숫자 그대로 넘기고 표기는 JS 가 한다. None 은 0."""
    return float(v) if v else 0


def _holding(h, notes):
    n = (notes or {}).get(h["ticker"]) or {}
    return {
        "ticker": h["ticker"],
        "name": h.get("name") or h["ticker"],
        "price": h.get("price"),
        "market_cap": _cap_krw(h.get("market_cap")),
        "chg_pct": (round(h["chg_pct"], 2) if h.get("chg_pct") is not None else None),
        "returns": _returns(h.get("returns")),
        "series": _ds(h.get("series") or []),
        "note": n.get("note") or "",
        "note_url": n.get("url") or "",
    }


def _summary_item(label, value, chg, unit="", chg_unit="", series=None, fmt="num"):
    return {"label": label, "value": value, "chg": chg, "unit": unit,
            "chg_unit": chg_unit, "series": _ds(series or []), "fmt": fmt}


def build_us(data, path):
    idx = data.get("indices") or {}
    now = data.get("ust_now") or {}
    hist = data.get("ust_hist") or {}
    fx = data.get("fx") or {}
    dom = data.get("domestic") or {}
    notes = data.get("notes") or {}
    sector_notes = data.get("sector_notes") or {}
    holdings = data.get("holdings") or {}

    summary = []
    for name in ("Dow", "S&P500", "Nasdaq"):
        d = idx.get(name)
        if d:
            summary.append(_summary_item(name, round(d["last"], 2),
                                         round(d["chg_pct"], 2), "", "%",
                                         d.get("series")))
    for sym, label in (("US2Y", "미국채 2년"), ("US10Y", "미국채 10년"),
                       ("US30Y", "미국채 30년")):
        q = now.get(sym)
        if q:
            summary.append(_summary_item(label, round(q["yield"], 3),
                                         round(q["chg_bp"], 1), "%", "bp",
                                         hist.get(sym)))
    if fx.get("last") is not None:
        summary.append(_summary_item("원/달러", round(fx["last"], 1),
                                     round(fx.get("chg", 0), 1), "원", "원",
                                     fx.get("series")))
    corp, spread = dom.get("corp_aa3y") or {}, dom.get("spread") or {}
    if corp.get("last") is not None:
        summary.append(_summary_item("회사채 AA- 3년", round(corp["last"], 2),
                                     round(corp.get("chg", 0), 0), "%", "bp",
                                     corp.get("series")))
    if spread.get("last") is not None:
        summary.append(_summary_item("AA- 3년 Spread", round(spread["last"], 1),
                                     round(spread.get("chg", 0), 0), "bp", "bp",
                                     spread.get("series")))

    sectors = []
    for s in data.get("sectors") or []:
        sectors.append({
            "symbol": s["symbol"], "name": s["name"],
            "chg_pct": round(s["chg_pct"], 2),
            "returns": _returns(s.get("returns")),
            "series": _ds(s.get("series") or []),
            "note": sector_notes.get(s["symbol"]) or "",
            "holdings": [_holding(h, notes) for h in holdings.get(s["symbol"]) or []],
        })

    payload = {
        "market": "us",
        "title": "미국 마켓 브리핑",
        "updated": dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
                     .strftime("%Y.%m.%d %H:%M KST"),
        "currency": "$",
        "summary": summary,
        "sectors": sectors,
        "flows": None,
        "other": {"href": "kr.html", "label": "한국증시 마감 →"},
    }
    _write(path, payload)
    return path


def build_kr(data, path):
    idx = data.get("indices") or {}
    fx = data.get("fx") or {}
    dom = data.get("domestic") or {}
    notes = data.get("notes") or {}
    holdings = data.get("holdings") or {}
    flows = data.get("flows") or {}

    summary = []
    for name in ("코스피", "코스닥"):
        d = idx.get(name)
        if d:
            summary.append(_summary_item(name, round(d["last"], 2),
                                         round(d["chg_pct"], 2), "", "%",
                                         d.get("series")))
    if fx.get("last") is not None:
        summary.append(_summary_item("원/달러", round(fx["last"], 1),
                                     round(fx.get("chg", 0), 1), "원", "원",
                                     fx.get("series")))
    for key, label in (("govt_3y", "국고채 3년"), ("corp_aa3y", "회사채 AA- 3년"),
                       ("spread", "AA- 3년 Spread")):
        d = dom.get(key) or {}
        if d.get("last") is not None:
            unit = "bp" if key == "spread" else "%"
            summary.append(_summary_item(label, round(d["last"], 2),
                                         round(d.get("chg", 0), 0), unit, "bp",
                                         d.get("series")))

    sectors = []
    for s in data.get("sectors") or []:
        sectors.append({
            "symbol": s["symbol"], "name": s["name"],
            "chg_pct": round(s["chg_pct"], 2),
            "returns": _returns(s.get("returns")),
            # 한국 섹터 시계열은 main_kr 이 sector_series 로 만들어 넣는다
            "series": _ds(s.get("web_series") or []),
            "note": "",
            "holdings": [_holding(h, notes) for h in holdings.get(s["symbol"]) or []],
        })

    flow_out = None
    if flows:
        flow_out = {}
        for mkt, f in flows.items():
            flow_out[mkt] = {
                "today": f.get("today"),
                "windows": {k: (v.get("외국인") if v else None)
                            for k, v in (f.get("windows") or {}).items()},
                "foreign_cum": _ds(f.get("foreign_cum") or []),
            }

    payload = {
        "market": "kr",
        "title": "한국증시 마감 브리핑",
        "updated": dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
                     .strftime("%Y.%m.%d %H:%M KST"),
        "currency": "₩",
        "summary": summary,
        "sectors": sectors,
        "flows": flow_out,
        "other": {"href": "us.html", "label": "미국 마켓 브리핑 →"},
    }
    _write(path, payload)
    return path


def write_index(outdir):
    """us/kr 로 가는 랜딩. 각 실행이 매번 다시 써도 무해하다."""
    html = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>마켓 브리핑</title><style>
body{font-family:system-ui,'Malgun Gothic',sans-serif;background:#f6f7f9;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
a{display:block;padding:28px 44px;margin:12px;border-radius:14px;background:#fff;
box-shadow:0 1px 4px rgba(0,0,0,.08);text-decoration:none;color:#1a1a1a;
font-size:20px;font-weight:700}a:hover{box-shadow:0 3px 10px rgba(0,0,0,.14)}
</style></head><body>
<a href="us.html">🇺🇸 미국 마켓 브리핑</a>
<a href="kr.html">🇰🇷 한국증시 마감 브리핑</a>
</body></html>"""
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, "index.html")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(html)
    return p


def _chartjs():
    """저장소에 동봉한 Chart.js 를 인라인한다.

    CDN 을 쓰면 사내 프록시·차단 환경에서 페이지 전체가 죽는다(Chart is not
    defined). 동봉본이 없을 때만 CDN 태그로 폴백한다.
    """
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "vendor", "chart.umd.min.js")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return "<script>" + fh.read() + "</script>"
    return ('<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/'
            'dist/chart.umd.min.js"></script>')


def _write(path, payload):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # </script> 가 데이터에 들어 있으면 태그가 닫혀버린다
    blob = blob.replace("</", "<\\/")
    html = _TEMPLATE.replace("__CHARTJS__", _chartjs()).replace("__DATA__", blob)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


# ---------------------------------------------------------------- 템플릿
_TEMPLATE = r"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>마켓 브리핑</title>
__CHARTJS__
<style>
:root{--up:#d32f2f;--down:#1565c0;--text:#1a1a1a;--sub:#667085;--line:#e6e8ec}
*{box-sizing:border-box}
body{font-family:system-ui,'Malgun Gothic','Apple SD Gothic Neo',sans-serif;
     margin:0;background:#f6f7f9;color:var(--text)}
.wrap{max-width:1080px;margin:0 auto;padding:20px 16px 60px}
header{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin:6px 0 18px}
header h1{font-size:22px;margin:0}
header .upd{color:var(--sub);font-size:13px}
header a{margin-left:auto;font-size:14px;color:var(--down);text-decoration:none}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(158px,1fr));gap:10px}
.card{background:#fff;border-radius:12px;padding:12px 14px;cursor:pointer;
      box-shadow:0 1px 3px rgba(0,0,0,.07);transition:box-shadow .15s}
.card:hover{box-shadow:0 3px 10px rgba(0,0,0,.14)}
.card .lb{font-size:12.5px;color:var(--sub)}
.card .v{font-size:19px;font-weight:800;margin-top:2px}
.card .c{font-size:13px;font-weight:700;margin-top:1px}
.up{color:var(--up)}.down{color:var(--down)}
h2{font-size:16px;margin:28px 0 10px}
.panel{background:#fff;border-radius:12px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.07)}
.secgrid{display:grid;grid-template-columns:1fr;gap:14px}
.sechead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.sechead b{font-size:15px}
.sechead .spans{color:var(--sub);font-size:12px;font-variant-numeric:tabular-nums}
.secnote{font-size:13px;color:#3b4252;background:#f2f4f7;border-radius:8px;
         padding:8px 10px;margin:8px 0 2px}
table{width:100%;border-collapse:collapse;margin-top:8px;font-size:13.5px}
th{color:var(--sub);font-weight:600;text-align:right;padding:6px 8px;
   border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child{text-align:left}
td{padding:7px 8px;text-align:right;border-bottom:1px solid var(--line);
   white-space:nowrap;font-variant-numeric:tabular-nums}
td:first-child{text-align:left;font-weight:700}
tr.stk{cursor:pointer}tr.stk:hover{background:#f6f8fb}
.note-row td{font-weight:400;text-align:left;color:#3b4252;font-size:12.5px;
             padding-top:0;border-bottom:1px solid var(--line)}
.note-row a{color:var(--down)}
canvas.mini{width:100%!important;height:210px!important}
canvas.bar{width:100%!important;height:340px!important}
dialog{border:0;border-radius:14px;padding:0;max-width:860px;width:94vw;
       box-shadow:0 12px 40px rgba(0,0,0,.25)}
dialog::backdrop{background:rgba(15,23,42,.45)}
.dlg{padding:18px}
.dlg .head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.dlg .head b{font-size:17px}
.dlg .meta{color:var(--sub);font-size:13px}
.dlg .close{margin-left:auto;border:0;background:#eef0f4;border-radius:8px;
            padding:6px 12px;cursor:pointer;font-size:13px}
.dlg .rets{display:flex;gap:14px;margin:8px 0 4px;font-size:13px;
           font-variant-numeric:tabular-nums}
.dlg .rets span{color:var(--sub)}
canvas.big{width:100%!important;height:380px!important}
.flowgrid{display:grid;grid-template-columns:1fr;gap:10px;margin-top:8px;font-size:13.5px}
.flowline{display:flex;gap:16px;flex-wrap:wrap;font-variant-numeric:tabular-nums}
.flowline .t{color:var(--sub)}
footer{margin-top:34px;color:var(--sub);font-size:12px}
.segs{margin-left:8px;display:inline-flex;gap:4px;vertical-align:middle}
.segs button{border:1px solid var(--line);background:#fff;border-radius:8px;
  padding:3px 10px;font-size:12px;cursor:pointer;color:var(--sub)}
.segs button.on{background:#1a1a1a;border-color:#1a1a1a;color:#fff;font-weight:700}
@media(min-width:760px){.secgrid{grid-template-columns:1fr 1fr}}
</style></head>
<body>
<div class="wrap">
  <header>
    <h1 id="title"></h1><span class="upd" id="updated"></span><a id="other"></a>
  </header>

  <div class="cards" id="cards"></div>

  <h2>섹터별 등락 <span class="segs" id="secbar-segs"></span></h2>
  <div class="panel"><canvas id="secbar" class="bar"></canvas></div>

  <div id="flows-sec" style="display:none">
    <h2>외국인 수급</h2>
    <div class="panel">
      <div id="flowlines" class="flowgrid"></div>
      <canvas id="flowchart" class="mini" style="height:260px!important"></canvas>
    </div>
  </div>

  <h2>섹터 상세 <span style="color:var(--sub);font-size:12.5px;font-weight:400">
    — 차트에 마우스를 올리면 수치, 종목을 클릭하면 상세 차트</span></h2>
  <div class="secgrid" id="sectors"></div>

  <footer>미국 지표는 전일 종가, 국내 금리는 전영업일 기준. 종목 메모는 수집된
  헤드라인·시장 지표에 근거하며 투자 판단이 아닙니다.</footer>
</div>

<dialog id="dlg"><div class="dlg">
  <div class="head"><b id="dlg-title"></b><span class="meta" id="dlg-meta"></span>
    <button class="close" onclick="document.getElementById('dlg').close()">닫기</button></div>
  <div class="rets" id="dlg-rets"></div>
  <canvas id="dlg-chart" class="big"></canvas>
</div></dialog>

<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const UP='#d32f2f', DOWN='#1565c0', MA=[[20,'#e6a23c'],[60,'#67c23a'],[120,'#909399']];
const fmt=(v,d=2)=>v==null?'–':v.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const sgn=v=>v==null?'–':(v>=0?'+':'')+fmt(v,Math.abs(v)>=100?0:2);
const cls=v=>v==null?'':(v>=0?'up':'down');
const capF=(v,cur)=>{if(!v)return'';const t=cur==='₩'?1e12:1e12;
  return v>=t?(v/t).toFixed(1)+(cur==='₩'?'조원':'조달러')
            :(v/(t/1e4)).toFixed(0)+(cur==='₩'?'억원':'억달러');};

document.getElementById('title').textContent=D.title;
document.getElementById('updated').textContent='업데이트 '+D.updated;
const oth=document.getElementById('other');
oth.textContent=D.other.label; oth.href=D.other.href;

function ma(vals,w){const o=[];let acc=0;
  for(let i=0;i<vals.length;i++){acc+=vals[i];if(i>=w)acc-=vals[i-w];
    o.push(i>=w-1?acc/w:null);}return o;}

// 차트 하나가 실패해도(라이브러리 미로드 등) 표·카드는 계속 보여야 한다
function lineChart(canvas, series, {label='종가', withMA=true, unit='', color='#1a1a1a'}={}){
 try{
  const labels=series.map(p=>p[0]), vals=series.map(p=>p[1]);
  const ds=[{label, data:vals, borderColor:color, borderWidth:1.7,
             pointRadius:0, pointHitRadius:8, tension:0}];
  if(withMA) for(const [w,c] of MA) if(vals.length>w)
    ds.push({label:w+'일',data:ma(vals,w),borderColor:c,borderWidth:1,
             pointRadius:0,pointHitRadius:0,tension:0});
  return new Chart(canvas,{type:'line',data:{labels,datasets:ds},options:{
    responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
    plugins:{legend:{display:withMA,labels:{boxWidth:14,font:{size:10}}},
      tooltip:{callbacks:{label:c=>c.dataset.label+': '+fmt(c.parsed.y)+unit}}},
    scales:{x:{ticks:{maxTicksLimit:8,font:{size:10}},grid:{display:false}},
            y:{ticks:{font:{size:10},callback:v=>fmt(v,0)},grid:{color:'#eef0f3'}}}}});
 }catch(e){console.error('lineChart:',e);return null;}
}

// ---- 요약 카드 (클릭 → 2년 차트 모달)
const cards=document.getElementById('cards');
D.summary.forEach((s,i)=>{
  const el=document.createElement('div');el.className='card';
  el.innerHTML=`<div class="lb">${s.label}</div>
    <div class="v">${fmt(s.value, s.unit==='%'?(s.label.includes('국채')?3:2):s.unit==='bp'?1:s.unit==='원'?1:2)}${s.unit}</div>
    <div class="c ${cls(s.chg)}">${sgn(s.chg)}${s.chg_unit}</div>`;
  if(s.series.length) el.onclick=()=>openDlg(s.label,
    `${fmt(s.value)}${s.unit} (${sgn(s.chg)}${s.chg_unit})`, null, s.series, s.unit);
  cards.appendChild(el);
});

// ---- 섹터 막대 (호버 수치, 당일/기간 전환)
try{
  const PERIODS=[['d','당일',s=>s.chg_pct],['m1','1M',s=>s.returns.m1],
    ['m3','3M',s=>s.returns.m3],['m6','6M',s=>s.returns.m6],
    ['m12','12M',s=>s.returns.m12]];
  let barChart=null;
  function drawBar(key){
    const per=PERIODS.find(p=>p[0]===key);
    // 선택한 기간 기준으로 내림차순 정렬해 다시 그린다
    const secs=[...D.sectors].sort((a,b)=>(per[2](b)??-1e9)-(per[2](a)??-1e9));
    const vals=secs.map(s=>per[2](s));
    if(barChart)barChart.destroy();
    barChart=new Chart(document.getElementById('secbar'),{type:'bar',data:{
      labels:secs.map(s=>s.name+(s.symbol!==s.name?` (${s.symbol})`:'')),
      datasets:[{data:vals,
        backgroundColor:vals.map(v=>(v??0)>=0?UP:DOWN),borderRadius:3,
        maxBarThickness:26}]},
      options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,
        plugins:{legend:{display:false},
          tooltip:{callbacks:{label:c=>`${per[1]} ${sgn(c.parsed.x)}%`}}},
        scales:{x:{ticks:{callback:v=>sgn(v)+'%',font:{size:10}},grid:{color:'#eef0f3'}},
                y:{ticks:{font:{size:11}},grid:{display:false}}},
        onClick:(e,els)=>{if(els.length){const s=secs[els[0].index];
          document.getElementById('sec-'+s.symbol)?.scrollIntoView({behavior:'smooth'});}}}});
    document.querySelectorAll('#secbar-segs button').forEach(b=>
      b.classList.toggle('on',b.dataset.k===key));
  }
  const segs=document.getElementById('secbar-segs');
  PERIODS.forEach(([k,label])=>{
    const b=document.createElement('button');
    b.textContent=label;b.dataset.k=k;b.onclick=()=>drawBar(k);
    segs.appendChild(b);
  });
  drawBar('d');
}catch(e){console.error('secbar:',e);}

// ---- 외국인 수급 (한국판)
if(D.flows){
  document.getElementById('flows-sec').style.display='block';
  const fl=document.getElementById('flowlines');
  const toJo=v=>v==null?'–':(v>=0?'+':'')+(Math.abs(v)>=1e4?(v/1e4).toFixed(2)+'조':Math.round(v).toLocaleString()+'억');
  for(const [mkt,f] of Object.entries(D.flows)){
    const w=f.windows||{};
    const line=document.createElement('div');line.className='flowline';
    line.innerHTML=`<b>${mkt}</b><span><span class="t">당일</span> ${toJo((f.today||{})['외국인'])}</span>`+
      ['3M','6M','12M','24M'].filter(k=>w[k]!=null)
        .map(k=>`<span><span class="t">${k}</span> ${toJo(w[k])}</span>`).join('');
    fl.appendChild(line);
  }
  // 두 시장의 거래일이 미묘하게 달라 날짜 합집합에 맞춰 정렬한다
  const allDates=[...new Set(Object.values(D.flows)
    .flatMap(f=>f.foreign_cum.map(p=>p[0])))].sort();
  const dsets=[],colors=[UP,DOWN];let li=0;
  for(const [mkt,f] of Object.entries(D.flows)){
    if(!f.foreign_cum.length)continue;
    const m=new Map(f.foreign_cum);
    dsets.push({label:mkt,data:allDates.map(d=>m.has(d)?m.get(d)/1e4:null),
      borderColor:colors[li++%2],borderWidth:1.7,pointRadius:0,pointHitRadius:8,
      tension:0,spanGaps:true});
  }
  try{
  if(dsets.length) new Chart(document.getElementById('flowchart'),{type:'line',
    data:{labels:allDates,datasets:dsets},options:{responsive:true,maintainAspectRatio:false,
      interaction:{mode:'index',intersect:false},
      plugins:{legend:{labels:{boxWidth:14,font:{size:11}}},
        tooltip:{callbacks:{label:c=>c.parsed.y==null?null:
          c.dataset.label+': '+(c.parsed.y>=0?'+':'')+c.parsed.y.toFixed(2)+'조'}},
        title:{display:true,text:'외국인 누적 순매수 2년 (조원)',align:'start',
               font:{size:13,weight:'bold'},color:'#1a1a1a'}},
      scales:{x:{ticks:{maxTicksLimit:8,font:{size:10}},grid:{display:false}},
              y:{ticks:{font:{size:10}},grid:{color:'#eef0f3'}}}}});
  }catch(e){console.error('flowchart:',e);}
}

// ---- 섹터 상세
const spans=r=>['m1','m3','m6','m12'].map((k,i)=>
  r[k]==null?'':`${['1M','3M','6M','12M'][i]} ${sgn(r[k])}`).filter(Boolean).join('  ');
const secWrap=document.getElementById('sectors');
D.sectors.forEach(s=>{
  const div=document.createElement('div');div.className='panel';div.id='sec-'+s.symbol;
  const rows=s.holdings.map((h,i)=>{
    const note=h.note?`<tr class="note-row"><td colspan="5">↳ ${h.note}
      ${h.note_url?` <a href="${h.note_url}" target="_blank" rel="noopener">기사</a>`:''}</td></tr>`:'';
    return `<tr class="stk" data-t="${h.ticker}">
      <td>${h.name} <span style="color:var(--sub);font-weight:400">${h.ticker}</span></td>
      <td class="${cls(h.chg_pct)}">${sgn(h.chg_pct)}%</td>
      <td>${h.price==null?'–':(D.currency==='₩'?Math.round(h.price).toLocaleString()+'원':'$'+fmt(h.price))}</td>
      <td>${capF(h.market_cap,D.currency)}</td>
      <td style="color:var(--sub);font-size:12px">${spans(h.returns)}</td></tr>`+note;
  }).join('');
  div.innerHTML=`<div class="sechead">
      <b class="${cls(s.chg_pct)}">${s.chg_pct>=0?'▲':'▼'} ${s.name}${s.symbol!==s.name?` (${s.symbol})`:''} ${sgn(s.chg_pct)}%</b>
      <span class="spans">${spans(s.returns)}</span></div>
    ${s.note?`<div class="secnote">💬 ${s.note}</div>`:''}
    ${s.series.length?`<canvas class="mini" id="c-${s.symbol}"></canvas>`:''}
    <table><thead><tr><th>종목</th><th>등락</th><th>주가</th><th>시총</th><th>기간수익률</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
  secWrap.appendChild(div);
  if(s.series.length) lineChart(document.getElementById('c-'+s.symbol), s.series,
    {withMA:false, color:s.chg_pct>=0?UP:DOWN, label:s.name});
  div.querySelectorAll('tr.stk').forEach(tr=>tr.onclick=()=>{
    const h=s.holdings.find(x=>x.ticker===tr.dataset.t);
    if(!h)return;
    const meta=[h.price!=null?(D.currency==='₩'?Math.round(h.price).toLocaleString()+'원':'$'+fmt(h.price)):null,
                capF(h.market_cap,D.currency)].filter(Boolean).join(' · ');
    openDlg(`${h.name} (${h.ticker})`,
      `${sgn(h.chg_pct)}%  ·  ${meta}`, h.returns, h.series,
      '', h.note?`↳ ${h.note}`:'');
  });
});

// ---- 상세 모달 (지수 차트와 같은 형식: 2년 + 이동평균)
let dlgChart=null;
function openDlg(title, meta, rets, series, unit='', sub=''){
  document.getElementById('dlg-title').textContent=title;
  document.getElementById('dlg-meta').textContent=meta+(sub?'  '+sub:'');
  document.getElementById('dlg-rets').innerHTML=rets?
    ['m1','m3','m6','m12'].map((k,i)=>rets[k]==null?'':
      `<div><span>${['1M','3M','6M','12M'][i]}</span> <b class="${cls(rets[k])}">${sgn(rets[k])}%</b></div>`)
      .filter(Boolean).join(''):'';
  const dlg=document.getElementById('dlg');dlg.showModal();
  if(dlgChart){dlgChart.destroy();dlgChart=null;}
  if(series&&series.length)
    dlgChart=lineChart(document.getElementById('dlg-chart'), series, {unit});
}
document.getElementById('dlg').onclick=e=>{
  if(e.target.id==='dlg')e.target.close();};
</script>
</body></html>
"""
