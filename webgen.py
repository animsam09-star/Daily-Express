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
# 종목 캔들은 2년치를 싣는다(모달에서 1M~2Y 로 잘라 본다). 늘어난 만큼은
# 종목의 종가 시계열을 빼서 상쇄했다 — 캔들이 있으면 쓰이지 않는 데이터다.
CANDLE_DAYS = 730


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


def _ds_ohlc(ohlc):
    """[(date, open, high, low, close)] -> [[iso, o, h, l, c]]. 캔들차트용.

    2년치를 전부 일봉으로 싣는다. 오래된 구간을 주봉으로 묶으면 용량은
    절반이 되지만, 브라우저에서 이동평균과 일목균형표를 '봉 단위'로 계산하기
    때문에 그 구간의 20일선이 20주선이 되고 구름대도 어긋난다. 지표가 틀리는
    것보다 용량이 낫다 — 11섹터×12종목이면 원본 3MB 남짓이지만 전송은
    압축되므로 실제로는 수백 KB다.
    가격은 소수 2자리면 충분하다 — 자릿수도 용량이다.
    """
    if not ohlc:
        return []
    cut = ohlc[-1][0] - dt.timedelta(days=CANDLE_DAYS)
    return [[d.isoformat(), round(o, 2), round(h, 2), round(l, 2), round(c, 2)]
            for d, o, h, l, c in ohlc if d >= cut]


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
        # 종가 시계열은 싣지 않는다 — 종목 상세는 캔들(ohlc)로 그리므로
        # 쓰이지 않는데 용량만 차지했다.
        "ohlc": _ds_ohlc(h.get("ohlc") or []),
        "note": n.get("note") or "",
        "note_url": n.get("url") or "",
        # 시총 상위가 아니라 최근 흐름으로 뽑힌 자리인지("momentum"/"watch").
        # 표시가 없으면 시총 순서가 어긋난 것처럼 보인다.
        "pick": h.get("pick") or "",
    }


def _summary_item(label, value, chg, unit="", chg_unit="", series=None, fmt="num",
                  ohlc=None):
    # ohlc 를 주면 카드를 눌렀을 때 라인 대신 일봉 캔들 + 일목균형표 구름대가
    # 뜬다. 지수·환율에는 있고 금리(재무부 CMT·네이버)에는 없다.
    return {"label": label, "value": value, "chg": chg, "unit": unit,
            "chg_unit": chg_unit, "series": _ds(series or []), "fmt": fmt,
            "ohlc": _ds_ohlc(ohlc or [])}


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
                                         d.get("series"), ohlc=d.get("ohlc")))
    px = data.get("kr_proxy") or {}
    if px.get("last") is not None:
        # 코스피 야간선물 무료 시세 부재 — EWY(미국장 한국 ETF)가 야간 프록시
        summary.append(_summary_item("코스피 야간 프록시 EWY", round(px["last"], 2),
                                     round(px["chg_pct"], 2), "달러", "%",
                                     px.get("series"), ohlc=px.get("ohlc")))
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
                                     fx.get("series"), ohlc=fx.get("ohlc")))
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
                                         d.get("series"), ohlc=d.get("ohlc")))
    if fx.get("last") is not None:
        summary.append(_summary_item("원/달러", round(fx["last"], 1),
                                     round(fx.get("chg", 0), 1), "원", "원",
                                     fx.get("series"), ohlc=fx.get("ohlc")))
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
    """저장소에 동봉한 Chart.js(+줌 플러그인)를 인라인한다.

    CDN 을 쓰면 사내 프록시·차단 환경에서 페이지 전체가 죽는다(Chart is not
    defined). 동봉본이 없을 때만 CDN 태그로 폴백한다.
    """
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    core = os.path.join(base, "chart.umd.min.js")
    if not os.path.exists(core):
        return ('<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/'
                'dist/chart.umd.min.js"></script>')
    out = []
    for name in ("chart.umd.min.js", "chartjs-plugin-zoom.min.js"):
        p = os.path.join(base, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                out.append("<script>" + fh.read() + "</script>")
    return "\n".join(out)


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
/* 검증된 기본 팔레트(dataviz) 기반. 상승/하락은 국내 관행(빨강/파랑)을
   diverging pair 로 쓴다 — 마크용과 텍스트용 단계를 나눠 대비를 확보. */
:root{
  --up:#e34948;--down:#2a78d6;          /* 마크(막대·점) */
  --up-t:#d03b3b;--down-t:#1c5cab;      /* 텍스트(등락 표기) */
  --plane:#f9f9f7;--surface:#fcfcfb;
  --ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
  --grid:#e1e0d9;--hairline:rgba(11,11,11,.10)}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);font-size:14px;line-height:1.45;
  font-family:system-ui,-apple-system,'Segoe UI','Malgun Gothic','Apple SD Gothic Neo',sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:0 20px 72px}
.topbar{position:sticky;top:0;z-index:20;background:rgba(249,249,247,.92);
  backdrop-filter:blur(6px);border-bottom:1px solid var(--hairline)}
.topbar-in{max-width:1100px;margin:0 auto;padding:13px 20px;
  display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.topbar h1{font-size:16.5px;margin:0;letter-spacing:-.01em}
.topbar .upd{color:var(--muted);font-size:12px}
.topbar a{margin-left:auto;font-size:12.5px;color:var(--ink2);text-decoration:none;
  padding:4px 12px;border:1px solid var(--hairline);border-radius:999px;
  background:var(--surface);transition:border-color .15s}
.topbar a:hover{border-color:rgba(11,11,11,.3)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));
  gap:10px;margin-top:20px}
.card{background:var(--surface);border:1px solid var(--hairline);border-radius:10px;
  padding:12px 14px 10px;cursor:pointer;transition:border-color .15s}
.card:hover{border-color:rgba(11,11,11,.3)}
.card .lb{font-size:12px;color:var(--ink2)}
.card .v{font-size:19px;font-weight:750;margin-top:3px;letter-spacing:-.01em}
.card .c{font-size:12.5px;font-weight:650;margin-top:1px}
.card svg{display:block;width:100%;height:30px;margin-top:8px}
.up{color:var(--up-t)}.down{color:var(--down-t)}
h2{font-size:12.5px;font-weight:700;color:var(--ink2);letter-spacing:.04em;
   margin:34px 0 10px}
h2 .hint{font-weight:400;color:var(--muted);letter-spacing:0}
.panel{background:var(--surface);border:1px solid var(--hairline);
  border-radius:12px;padding:16px}
/* 섹터 카드는 메모 유무·종목 수에 따라 높이가 제각각이었다.
   모든 행을 가장 큰 카드에 맞춰 통일하고, 카드 안에서는 표가 남는 높이를 먹는다. */
.secgrid{display:grid;grid-template-columns:1fr;gap:12px;grid-auto-rows:1fr}
/* min-width:0 이 없으면 그리드 자식이 내용 폭만큼 벌어져 열을 밀어내고,
   페이지 전체에 가로 스크롤이 생겨 화면이 오른쪽으로 치우쳐 보인다. */
.secgrid>.panel{display:flex;flex-direction:column;min-width:0}
.secgrid>.panel>*{min-width:0}
/* 표가 좁은 화면에서 넘치면 표 안에서만 스크롤한다(본문은 절대 안 밀린다) */
.tablewrap{overflow-x:auto}
/* 섹터 코멘트는 길이가 제각각이라, 그대로 두면 아래 표가 카드마다 다른
   높이에서 시작해 옆 카드와 줄이 어긋난다. 두 줄로 고정하고 넘치면 자른다
   (전문은 title 툴팁). 코멘트가 없는 카드도 같은 자리를 비워 둔다. */
.secnote{height:56px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;
         -webkit-box-orient:vertical}
.secnote.empty{background:transparent}
.sechead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.sechead b{font-size:14.5px}
.sechead .spans{color:var(--muted);font-size:11.5px;font-variant-numeric:tabular-nums}
.secnote{font-size:13px;color:var(--ink2);background:#f4f3ef;border-radius:8px;
         padding:8px 11px;margin:9px 0 2px}
table{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}
th{color:var(--muted);font-weight:600;text-align:right;padding:6px 8px;
   font-size:11.5px;border-bottom:1px solid var(--grid);white-space:nowrap}
th:first-child{text-align:left}
td{padding:7px 8px;text-align:right;border-bottom:1px solid var(--grid);
   white-space:nowrap;font-variant-numeric:tabular-nums}
td:first-child{text-align:left;font-weight:650}
tr:last-child td{border-bottom:0}
tr.stk{cursor:pointer}tr.stk:hover{background:rgba(11,11,11,.03)}
.note-row td{font-weight:400;text-align:left;color:var(--ink2);font-size:12.5px;
             padding-top:0}
.note-row a{color:var(--down-t)}
/* 시총 상위가 아니라 최근 흐름으로 뽑힌 자리라는 표시 */
.badge{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;
       font-size:10.5px;font-weight:700;letter-spacing:.02em;vertical-align:1px;
       color:var(--up-t);background:rgba(180,60,40,.10);white-space:nowrap}
/* 캔버스에 !important 로 크기를 강제하면 Chart.js 의 리사이즈 계산과
   충돌해, 마우스를 올릴 때마다 캔버스가 조금씩 눌린다(피드백 루프).
   높이는 래퍼가 갖고 캔버스는 그 안을 절대배치로 채운다. */
.chartbox{position:relative;width:100%;min-width:0}
.chartbox>canvas{position:absolute;top:0;left:0;width:100%;height:100%}
.chartbox.mini{height:200px}
.chartbox.bar{height:360px}
.chartbox.big{height:400px}
dialog{border:1px solid var(--hairline);border-radius:14px;padding:0;
  max-width:860px;width:94vw;background:var(--surface);
  box-shadow:0 16px 48px rgba(11,11,11,.22)}
dialog::backdrop{background:rgba(11,11,11,.38)}
.dlg{padding:18px 20px 20px}
.dlg .head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.dlg .head b{font-size:16.5px;letter-spacing:-.01em}
.dlg .meta{color:var(--ink2);font-size:12.5px}
.dlg .close{margin-left:auto;border:1px solid var(--hairline);background:var(--surface);
  border-radius:999px;padding:4px 13px;cursor:pointer;font-size:12.5px;color:var(--ink2)}
.dlg .close:hover{border-color:rgba(11,11,11,.3)}
.dlg .rets{display:flex;gap:16px;margin:10px 0 6px;font-size:12.5px;
           font-variant-numeric:tabular-nums}
.dlg .rets span{color:var(--muted)}
.flowgrid{display:grid;grid-template-columns:1fr;gap:8px;margin:2px 0 12px;font-size:13px}
.flowline{display:flex;gap:16px;flex-wrap:wrap;font-variant-numeric:tabular-nums}
.flowline .t{color:var(--muted)}
footer{margin-top:40px;color:var(--muted);font-size:12px;line-height:1.6}
.segs{margin-left:8px;display:inline-flex;gap:4px;vertical-align:middle}
.segs button{border:1px solid var(--hairline);background:var(--surface);
  border-radius:999px;padding:3px 11px;font-size:11.5px;cursor:pointer;
  color:var(--ink2);transition:border-color .15s}
.segs button:hover{border-color:rgba(11,11,11,.3)}
.segs button.on{background:var(--ink);border-color:var(--ink);color:#fff;font-weight:700}
/* 두 칸으로 놓이는 화면에서는 표 높이까지 고정해 카드 크기를 완전히 통일한다
   (종목 수·뉴스 줄 수가 카드마다 달라 그냥 두면 높이가 제각각이다).
   한 칸으로 접히는 좁은 화면에서는 카드 안 스크롤이 성가시므로 풀어 준다. */
@media(min-width:760px){
  .secgrid{grid-template-columns:1fr 1fr}
  .secgrid>.panel>.tablewrap{height:340px;overflow-y:auto}
}
</style></head>
<body>
<div class="topbar"><div class="topbar-in">
  <h1 id="title"></h1><span class="upd" id="updated"></span><a id="other"></a>
</div></div>
<div class="wrap">
  <div class="cards" id="cards"></div>

  <h2>섹터별 등락 <span class="segs" id="secbar-segs"></span></h2>
  <div class="panel"><div class="chartbox bar"><canvas id="secbar"></canvas></div></div>

  <h2>섹터 2년 상대성과 <span class="hint">— 2년 전 = 100</span></h2>
  <div class="panel"><div class="chartbox bar"><canvas id="sectrend"></canvas></div></div>

  <div id="flows-sec" style="display:none">
    <h2>외국인 수급</h2>
    <div class="panel">
      <div id="flowlines" class="flowgrid"></div>
      <div class="chartbox mini" style="height:260px"><canvas id="flowchart"></canvas></div>
    </div>
  </div>

  <h2>섹터 상세 <span class="hint">— 차트에 마우스를 올리면 수치, 종목을 클릭하면 상세 차트</span></h2>
  <div class="secgrid" id="sectors"></div>

  <footer>미국 지표는 전일 종가, 국내 금리는 전영업일 기준. 종목 메모는 수집된
  헤드라인·시장 지표에 근거하며 투자 판단이 아닙니다.</footer>
</div>

<dialog id="dlg"><div class="dlg">
  <div class="head"><b id="dlg-title"></b><span class="meta" id="dlg-meta"></span>
    <button class="close" onclick="document.getElementById('dlg').close()">닫기</button></div>
  <div class="rets" id="dlg-rets"></div>
  <div class="segs" id="dlg-segs" style="margin:0 0 8px"></div>
  <div class="chartbox big"><canvas id="dlg-chart"></canvas></div>
</div></dialog>

<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const UP='#e34948', DOWN='#2a78d6';               // 마크용 상승/하락
const MA=[[20,'#eb6834'],[60,'#1baf7a'],[120,'#4a3aa7']];  // 검증된 categorical 슬롯
if(typeof Chart!=='undefined'){
  // 줌 플러그인은 UMD 자동 등록에 의존하지 않고 명시적으로 붙인다
  if(window.ChartZoom) { try{ Chart.register(window.ChartZoom); }catch(e){} }
  Chart.defaults.font.family=getComputedStyle(document.body).fontFamily;
  Chart.defaults.color='#898781';                 // 축·눈금은 muted 잉크
  Chart.defaults.borderColor='#e1e0d9';           // 격자는 hairline
}
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

// 개별 종목은 캔들(일봉)로 그린다. Chart.js 에 캔들 타입이 없어 막대 두 벌로
// 만든다 — 얇은 막대가 고가~저가(꼬리), 굵은 막대가 시가~종가(몸통).
// grouped:false 라야 두 막대가 나란히 서지 않고 겹쳐 그려진다.
function candleChart(canvas, ohlc, {unit='', span=null}={}){
 try{
  const labels=ohlc.map(r=>r[0]), n=ohlc.length;
  const col=r=>r[4]>=r[1]?UP:DOWN;            // 종가>=시가 면 상승(빨강)
  // 시가와 종가가 같은 날(도지)은 몸통 높이가 0 이라 아무것도 안 보인다.
  // 전체 가격 범위의 0.06% 만큼 최소 두께를 준다 — 실제 봉과 구분되는 실선.
  const lo=Math.min(...ohlc.map(r=>r[3])), hi=Math.max(...ohlc.map(r=>r[2]));
  const eps=Math.max((hi-lo)*0.0006, 1e-9);
  const body=r=>{const a=Math.min(r[1],r[4]), b=Math.max(r[1],r[4]);
    return b-a<eps ? [a-eps/2, b+eps/2] : [a,b];};
  // 일목균형표 구름대 — 선행스팬을 26봉 앞으로 밀어 그리므로 미래 라벨이
  // 필요하다(주말을 건너뛴 영업일). 텔레그램 차트와 같은 표준 설정(9/26/52).
  const AHEAD=26, hi9=[],lo9=[],future=[];
  const mid=(from,to)=>{let h=-Infinity,l=Infinity;
    for(let i=from;i<=to;i++){h=Math.max(h,ohlc[i][2]);l=Math.min(l,ohlc[i][3]);}
    return (h+l)/2;};
  const spanA=[], spanB=[];
  for(let i=0;i<n;i++){
    spanA.push(i>=25 ? (mid(i-8,i)+mid(i-25,i))/2 : null);   // (전환9+기준26)/2
    spanB.push(i>=51 ? mid(i-51,i) : null);                  // 52기간 중간
  }
  // 26봉 앞으로 이동: 앞쪽 26칸을 비우고 뒤쪽에 미래 라벨을 붙인다
  const pad=Array(AHEAD).fill(null);
  const cloudA=pad.concat(spanA), cloudB=pad.concat(spanB);
  let d=new Date(labels[n-1]);
  while(future.length<AHEAD){
    d=new Date(d.getTime()+86400000);
    if(d.getUTCDay()!==0 && d.getUTCDay()!==6) future.push(d.toISOString().slice(0,10));
  }
  labels.push(...future);

  const ds=[
    // 구름: 선행1이 선행2보다 위면 양운(붉은), 아래면 음운(푸른)
    {type:'line', label:'일목 구름대', data:cloudA, borderColor:'transparent',
     pointRadius:0, pointHitRadius:0, tension:0, order:9,
     fill:{target:'+1', above:'rgba(227,73,72,.16)', below:'rgba(42,120,214,.16)'}},
    {type:'line', label:'__spanB', data:cloudB, borderColor:'transparent',
     pointRadius:0, pointHitRadius:0, tension:0, order:9, fill:false},
    {label:'고저', data:ohlc.map(r=>[r[3],r[2]]), backgroundColor:ohlc.map(col),
     barPercentage:0.14, categoryPercentage:0.95, grouped:false, order:2},
    {label:'시종', data:ohlc.map(body), backgroundColor:ohlc.map(col),
     barPercentage:0.72, categoryPercentage:0.95, grouped:false, order:1},
  ];
  const closes=ohlc.map(r=>r[4]);
  for(const [w,c] of MA) if(closes.length>w)
    ds.push({type:'line', label:w+'일', data:ma(closes,w), borderColor:c,
             borderWidth:1.1, pointRadius:0, pointHitRadius:0, tension:0, order:0});
  // 2년치를 다 싣고 x 축 범위(인덱스)로 보이는 기간을 정한다. 휠을 굴리면
  // 범위가 좁아지고(줌인) 넓어지며(줌아웃), 그에 따라 y 축도 자동으로 다시
  // 잡힌다 — 네이버 차트와 같은 조작감.
  const total=labels.length;                  // 캔들 + 미래 26봉
  const x0=span?Math.max(0,n-span):0;
  return new Chart(canvas,{type:'bar',data:{labels,datasets:ds},options:{
    responsive:true,maintainAspectRatio:false,animation:false,
    interaction:{mode:'index',intersect:false},
    plugins:{legend:{display:true,labels:{boxWidth:14,font:{size:10},
        // 캔들(고저·시종)은 봉마다 색이 달라 범례에 단색으로 뜨면 오해를 준다
        filter:it=>!['고저','시종','__spanB'].includes(it.text)}},
      tooltip:{callbacks:{title:it=>it[0].label,label:c=>{
        const lb=c.dataset.label;
        if(lb==='__spanB'||lb==='고저'||lb==='일목 구름대') return null;
        if(lb==='시종'){const r=ohlc[c.dataIndex]; if(!r) return null;
          return [`시 ${fmt(r[1])}`,`고 ${fmt(r[2])}`,`저 ${fmt(r[3])}`,`종 ${fmt(r[4])}${unit}`];}
        return c.parsed.y==null?null:lb+': '+fmt(c.parsed.y);}}},
      zoom:{limits:{x:{min:0, max:total-1, minRange:10}},
            pan:{enabled:true, mode:'x', modifierKey:null},
            zoom:{wheel:{enabled:true, speed:0.12}, pinch:{enabled:true},
                  mode:'x',
                  // 확대·축소할 때마다 프리셋 버튼 선택을 해제한다
                  onZoomComplete:({chart})=>{
                    document.querySelectorAll('#dlg-segs button.on')
                      .forEach(b=>b.classList.remove('on'));
                    chart.update('none');}}}},
    scales:{x:{min:x0, max:total-1, ticks:{maxTicksLimit:8,font:{size:10}},
               grid:{display:false}, stacked:false},
            y:{ticks:{font:{size:10},callback:v=>fmt(v,0)},grid:{color:'#e1e0d9'},
               beginAtZero:false}}}});
 }catch(e){console.error('candleChart:',e);return null;}
}

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
            y:{ticks:{font:{size:10},callback:v=>fmt(v,0)},grid:{color:'#e1e0d9'}}}}});
 }catch(e){console.error('lineChart:',e);return null;}
}

// ---- 요약 카드 (스파크라인 + 클릭 → 2년 차트 모달)
function spark(series, chg){
  const pts=(series||[]).slice(-90);              // 최근 약 3개월
  if(pts.length<2) return '';
  const vs=pts.map(p=>p[1]), mn=Math.min(...vs), mx=Math.max(...vs), rg=(mx-mn)||1;
  const W=100, H=30, X=i=>(i/(vs.length-1)*W), Y=v=>(H-2-(v-mn)/rg*(H-4));
  const d=vs.map((v,i)=>`${i?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join('');
  const dot=`<circle cx="${X(vs.length-1).toFixed(1)}" cy="${Y(vs[vs.length-1]).toFixed(1)}"
    r="2.4" fill="${(chg??0)>=0?UP:DOWN}"/>`;
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
    <path d="${d}" fill="none" stroke="#898781" stroke-width="1.3"
      vector-effect="non-scaling-stroke"/>${dot}</svg>`;
}
const cards=document.getElementById('cards');
D.summary.forEach((s,i)=>{
  const el=document.createElement('div');el.className='card';
  el.innerHTML=`<div class="lb">${s.label}</div>
    <div class="v">${fmt(s.value, s.unit==='%'?(s.label.includes('국채')?3:2):s.unit==='bp'?1:s.unit==='원'?1:2)}${s.unit}</div>
    <div class="c ${cls(s.chg)}">${sgn(s.chg)}${s.chg_unit}</div>${spark(s.series,s.chg)}`;
  // 지수·환율은 ohlc 가 있어 캔들 + 일목균형표로 열리고, 금리는 라인으로 열린다
  if(s.series.length||s.ohlc.length) el.onclick=()=>openDlg(s.label,
    `${fmt(s.value)}${s.unit} (${sgn(s.chg)}${s.chg_unit})`, null, s.series,
    s.unit, '', s.ohlc);
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
    // 순서는 시가총액 순으로 고정한다(payload 순서 그대로). 기간을 바꿔도
    // 자리가 그대로라 어제 화면·다른 기간과 눈으로 대조할 수 있다.
    const secs=D.sectors;
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
        scales:{x:{ticks:{callback:v=>sgn(v)+'%',font:{size:10}},grid:{color:'#e1e0d9'}},
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

// ---- 섹터 2년 상대성과 (텔레그램 sectors_2y 와 같은 내용)
try{
  const usable=D.sectors.filter(s=>(s.series||[]).length>30);
  if(usable.length){
    // 성과 좋은 순으로 색을 배분해야 범례가 읽힌다
    const perf=s=>s.series[s.series.length-1][1]/s.series[0][1]*100;
    const sorted=[...usable].sort((a,b)=>perf(b)-perf(a));
    // 날짜 합집합으로 축을 맞춘다(섹터마다 거래일이 미묘하게 다르다)
    const allD=[...new Set(sorted.flatMap(s=>s.series.map(p=>p[0])))].sort();
    const hue=i=>`hsl(${Math.round(i*(360/sorted.length))},68%,45%)`;
    new Chart(document.getElementById('sectrend'),{type:'line',
      data:{labels:allD, datasets:sorted.map((s,i)=>{
        const m=new Map(s.series), base=s.series[0][1];
        return {label:`${s.name} ${(perf(s)).toFixed(0)}`,
          data:allD.map(d=>m.has(d)?m.get(d)/base*100:null),
          borderColor:hue(i), borderWidth:1.35, pointRadius:0, pointHitRadius:6,
          tension:0, spanGaps:true};})},
      options:{responsive:true,maintainAspectRatio:false,
        interaction:{mode:'index',intersect:false},
        plugins:{legend:{position:'right',labels:{boxWidth:12,font:{size:10.5}}},
          tooltip:{callbacks:{label:c=>c.parsed.y==null?null:
            c.dataset.label.replace(/ [-\d.]+$/,'')+': '+fmt(c.parsed.y,1)}}},
        scales:{x:{ticks:{maxTicksLimit:8,font:{size:10}},grid:{display:false}},
                y:{ticks:{font:{size:10}},grid:{color:'#e1e0d9'}}}}});
  }
}catch(e){console.error('sectrend:',e);}

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
  const dsets=[],colors=['#2a78d6','#eb6834'];let li=0;  // 시장별 고정 색(엔티티), 등락색 재사용 금지
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
              y:{ticks:{font:{size:10}},grid:{color:'#e1e0d9'}}}}});
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
      <td>${h.name} <span style="color:var(--sub);font-weight:400">${h.ticker}</span>${
        h.pick?`<span class="badge" title="${h.pick==='momentum'
          ?'시총 상위는 아니지만 최근 3개월 상승률이 높고 최근 한 달도 꺾이지 않은 종목'
          :'항상 표시하도록 지정한 종목'}">${h.pick==='momentum'
          ?'주도주'+(h.returns.m3!=null?` 3M ${sgn(h.returns.m3)}`:'') :'관심'}</span>`:''}</td>
      <td class="${cls(h.chg_pct)}">${sgn(h.chg_pct)}%</td>
      <td>${h.price==null?'–':(D.currency==='₩'?Math.round(h.price).toLocaleString()+'원':'$'+fmt(h.price))}</td>
      <td>${capF(h.market_cap,D.currency)}</td>
      <td style="color:var(--sub);font-size:12px">${spans(h.returns)}</td></tr>`+note;
  }).join('');
  div.innerHTML=`<div class="sechead">
      <b class="${cls(s.chg_pct)}">${s.chg_pct>=0?'▲':'▼'} ${s.name}${s.symbol!==s.name?` (${s.symbol})`:''} ${sgn(s.chg_pct)}%</b>
      <span class="spans">${spans(s.returns)}</span></div>
    <div class="secnote${s.note?'':' empty'}" title="${
      (s.note||'').replace(/"/g,'&quot;')}">${s.note?'💬 '+s.note:''}</div>
    <div class="chartbox mini">${s.series.length?`<canvas id="c-${s.symbol}"></canvas>`:''}</div>
    <div class="tablewrap"><table>
      <thead><tr><th>종목</th><th>등락</th><th>주가</th><th>시총</th><th>기간수익률</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  secWrap.appendChild(div);
  if(s.series.length) lineChart(document.getElementById('c-'+s.symbol), s.series,
    {withMA:false, color:'#0b0b0b', label:s.name});
  div.querySelectorAll('tr.stk').forEach(tr=>tr.onclick=()=>{
    const h=s.holdings.find(x=>x.ticker===tr.dataset.t);
    if(!h)return;
    const meta=[h.price!=null?(D.currency==='₩'?Math.round(h.price).toLocaleString()+'원':'$'+fmt(h.price)):null,
                capF(h.market_cap,D.currency)].filter(Boolean).join(' · ');
    openDlg(`${h.name} (${h.ticker})`,
      `${sgn(h.chg_pct)}%  ·  ${meta}`, h.returns, null,
      '', h.note?`↳ ${h.note}`:'', h.ohlc);
  });
});

// ---- 상세 모달 (지수 차트와 같은 형식: 2년 + 이동평균)
let dlgChart=null;
function openDlg(title, meta, rets, series, unit='', sub='', ohlc=null){
  document.getElementById('dlg-title').textContent=title;
  document.getElementById('dlg-meta').textContent=meta+(sub?'  '+sub:'');
  document.getElementById('dlg-rets').innerHTML=rets?
    ['m1','m3','m6','m12'].map((k,i)=>rets[k]==null?'':
      `<div><span>${['1M','3M','6M','12M'][i]}</span> <b class="${cls(rets[k])}">${sgn(rets[k])}%</b></div>`)
      .filter(Boolean).join(''):'';
  const dlg=document.getElementById('dlg');dlg.showModal();
  if(dlgChart){dlgChart.destroy();dlgChart=null;}
  const cv=document.getElementById('dlg-chart'), segs=document.getElementById('dlg-segs');
  segs.innerHTML='';
  if(ohlc&&ohlc.length){         // 개별 종목: 일봉 캔들 + 기간 프리셋 + 줌
    // 차트는 한 번만 만들고(1년 전체), 프리셋은 x 축 범위만 바꾼다.
    // 다시 그리면 줌 상태가 초기화돼 조작감이 끊긴다.
    const setSpan=days=>{
      const n=ohlc.length, total=dlgChart.data.labels.length;
      dlgChart.options.scales.x.min = days ? Math.max(0, n-days) : 0;
      dlgChart.options.scales.x.max = total-1;   // 구름 앞부분(미래)까지
      dlgChart.update('none');
      segs.querySelectorAll('button').forEach(b=>
        b.classList.toggle('on', +b.dataset.d===days));
    };
    dlgChart=candleChart(cv, ohlc, {unit, span:63});   // 기본 3개월 구간
    // 봉 수 기준(전부 일봉이라 21≈1개월, 252≈1년). 0 은 전체(2년).
    [[21,'1M'],[63,'3M'],[126,'6M'],[252,'1Y'],[0,'2Y']].forEach(([d,label])=>{
      const b=document.createElement('button');
      b.textContent=label; b.dataset.d=d; b.onclick=()=>setSpan(d);
      if(d===63) b.classList.add('on');
      segs.appendChild(b);
    });
    const hint=document.createElement('span');
    hint.style.cssText='margin-left:8px;color:var(--muted);font-size:11.5px';
    hint.textContent='휠: 확대·축소 · 드래그: 이동';
    segs.appendChild(hint);
  } else if(series&&series.length){ // 지수·금리·환율: 2년 라인 + 이동평균
    dlgChart=lineChart(cv, series, {unit});
  }
}
document.getElementById('dlg').onclick=e=>{
  if(e.target.id==='dlg')e.target.close();};
</script>
</body></html>
"""
