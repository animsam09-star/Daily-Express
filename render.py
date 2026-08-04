"""차트 생성 — 지표별 2개년 추이 + 이동평균선(20/60/120일), 섹터 등락 막대."""
from __future__ import annotations

import datetime as dt
import os
import re
from html import escape

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates            # noqa: E402
import matplotlib.pyplot as plt              # noqa: E402
from matplotlib import font_manager          # noqa: E402

import biz                                   # noqa: E402

# 상승 빨강 / 하락 파랑 (국내 관행)
UP, DOWN = "#d32f2f", "#1565c0"
LINE, GRID, TEXT = "#1a1a1a", "#e6e6e6", "#333333"
MA_STYLE = [(20, "#e6a23c"), (60, "#67c23a"), (120, "#909399")]


def setup_font() -> str:
    """윈도우(맑은 고딕)·리눅스(나눔) 어느 쪽에서든 한글이 깨지지 않게."""
    for name in ("Malgun Gothic", "NanumGothic", "NanumBarunGothic",
                 "AppleGothic", "Noto Sans CJK KR", "DejaVu Sans"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
        except Exception:                     # noqa: BLE001
            continue
        plt.rcParams["font.family"] = name
        plt.rcParams["axes.unicode_minus"] = False
        return name
    plt.rcParams["axes.unicode_minus"] = False
    return "default"


def _ma(values, window):
    """단순이동평균. 데이터가 모자란 구간은 None."""
    out, acc = [], 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= window:
            acc -= values[i - window]
        out.append(acc / window if i >= window - 1 else None)
    return out


CLOUD_UP, CLOUD_DOWN = "#e8a0a0", "#a0b8e8"     # 양운(붉은) / 음운(푸른)


def _ichimoku(ohlc):
    """일목균형표 선행스팬 1·2.

    입력은 [(date, high, low, close)] 또는 [(date, open, high, low, close)] 둘 다
    받는다 — 시가는 캔들차트에만 필요해서 소스마다 형태가 다르다.
    전환선 9, 기준선 26, 선행스팬2 52, 선행 이동 26 — 표준 설정.
    구름대는 26일 앞으로 밀어 그리므로 미래 구간이 생긴다.
    """
    if len(ohlc) < 78:                          # 52 + 26. 모자라면 그리지 않는다
        return None
    if len(ohlc[0]) == 5:                       # (date, open, high, low, close)
        ohlc = [(d, h, l, c) for d, _o, h, l, c in ohlc]
    dates = [d for d, _, _, _ in ohlc]
    highs = [h for _, h, _, _ in ohlc]
    lows = [l for _, _, l, _ in ohlc]

    def mid(n, i):
        if i + 1 < n:
            return None
        return (max(highs[i + 1 - n:i + 1]) + min(lows[i + 1 - n:i + 1])) / 2

    conv = [mid(9, i) for i in range(len(ohlc))]
    base = [mid(26, i) for i in range(len(ohlc))]
    span_a = [None if conv[i] is None or base[i] is None else (conv[i] + base[i]) / 2
              for i in range(len(ohlc))]
    span_b = [mid(52, i) for i in range(len(ohlc))]

    # 26 거래일 앞으로. 마지막 구간은 영업일 간격을 그대로 이어 붙인다
    step = (dates[-1] - dates[-27]) / 26 if len(dates) > 27 else dt.timedelta(days=1)
    future = [dates[-1] + step * (i + 1) for i in range(26)]
    return dates[26:] + future, span_a[:-26] + span_a[-26:], span_b[:-26] + span_b[-26:]


RS_COLOR = "#8e44ad"


def _rs_series(series, bench_series):
    """지수 대비 상대강도. 구간 시작을 100 으로 잡은 (종목/지수) 비율.

    100 을 넘으면 그 구간 동안 지수를 앞선 것이다. 절대 주가만 보면
    시장이 밀어올린 것인지 종목이 잘한 것인지 구분되지 않는다.
    """
    if not bench_series or len(series) < 2:
        return None
    bm = dict(bench_series)
    pairs = [(d, v / bm[d]) for d, v in series if d in bm and bm[d]]
    if len(pairs) < 30:
        return None
    base = pairs[0][1]
    if not base:
        return None
    return [d for d, _ in pairs], [r / base * 100.0 for _, r in pairs]


def line_chart(path, title, series, *, value_fmt="{:,.2f}", unit="",
               change=None, change_fmt="{:+.1f}", change_unit="", ohlc=None,
               bench_series=None, bench_label="지수"):
    """2개년 종가 + 이동평균선 1장.

    ohlc 를 주면 일목균형표 구름대를, bench_series 를 주면 우측 축에
    지수 대비 상대강도선을 얹는다.
    """
    if not series or len(series) < 2:
        return None
    dates = [d for d, _ in series]
    vals = [v for _, v in series]

    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=140)

    cloud = _ichimoku(ohlc) if ohlc else None
    if cloud:
        cd, sa, sb = cloud
        n = min(len(cd), len(sa), len(sb))
        cd, sa, sb = cd[:n], sa[:n], sb[:n]
        ok = [i for i in range(n) if sa[i] is not None and sb[i] is not None]
        if ok:
            cdx = [cd[i] for i in ok]
            sax = [sa[i] for i in ok]
            sbx = [sb[i] for i in ok]
            ax.fill_between(cdx, sax, sbx, where=[a >= b for a, b in zip(sax, sbx)],
                            color=CLOUD_UP, alpha=0.45, lw=0, zorder=1,
                            interpolate=True, label="일목 구름대")
            ax.fill_between(cdx, sax, sbx, where=[a < b for a, b in zip(sax, sbx)],
                            color=CLOUD_DOWN, alpha=0.45, lw=0, zorder=1,
                            interpolate=True)
    ax.plot(dates, vals, color=LINE, lw=1.6, zorder=5, label="종가")
    for win, color in MA_STYLE:
        if len(vals) > win:
            ax.plot(dates, _ma(vals, win), color=color, lw=1.1,
                    alpha=0.9, zorder=4, label=f"{win}일 이동평균")

    last = vals[-1]
    ax.scatter([dates[-1]], [last], s=26, color=UP if (change or 0) >= 0 else DOWN, zorder=6)

    head = f"{title}   {value_fmt.format(last)}{unit}"
    if change is not None:
        head += f"  ({change_fmt.format(change)}{change_unit})"
    ax.set_title(head, fontsize=13, fontweight="bold", color=TEXT, loc="left", pad=12)

    ax.grid(True, color=GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT, labelsize=9)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m"))

    rs = _rs_series(series, bench_series)
    handles, labels = ax.get_legend_handles_labels()
    if rs:
        rd, rv = rs
        ax2 = ax.twinx()
        ax2.plot(rd, rv, color=RS_COLOR, lw=1.3, ls="--", alpha=0.85, zorder=3,
                 label=f"{bench_label} 대비 상대강도(우)")
        ax2.axhline(100, color=RS_COLOR, lw=0.8, ls=":", alpha=0.5)
        ax2.tick_params(colors=RS_COLOR, labelsize=8)
        ax2.spines["right"].set_color(RS_COLOR)
        for side in ("top", "left", "bottom"):
            ax2.spines[side].set_visible(False)
        ax2.grid(False)
        h2, l2 = ax2.get_legend_handles_labels()
        handles, labels = handles + h2, labels + l2

    ax.legend(handles, labels, loc="upper left", fontsize=8, frameon=False, ncol=3)

    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def sector_chart(path, sectors, title="미국증시 섹터별 등락"):
    """섹터 일간 등락률 가로 막대."""
    if not sectors:
        return None
    names = [f"{s['name']} ({s['symbol']})" for s in sectors][::-1]
    vals = [s["chg_pct"] for s in sectors][::-1]
    colors = [UP if v >= 0 else DOWN for v in vals]

    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=140)
    bars = ax.barh(names, vals, color=colors, height=0.68, zorder=3)
    span = max(abs(min(vals)), abs(max(vals)), 0.1)
    for bar, v in zip(bars, vals):
        off = span * 0.03
        ax.text(v + (off if v >= 0 else -off), bar.get_y() + bar.get_height() / 2,
                f"{v:+.1f}%", va="center", ha="left" if v >= 0 else "right",
                fontsize=9, color=TEXT, fontweight="bold")

    ax.axvline(0, color="#999999", lw=1)
    ax.set_xlim(-span * 1.35, span * 1.35)
    ax.set_title(title, fontsize=13, fontweight="bold", color=TEXT, loc="left", pad=12)
    ax.grid(True, axis="x", color=GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=TEXT, labelsize=9)
    ax.set_xticklabels([])

    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def flow_chart(path, series_map, title="외국인 누적 순매수 2년 (조원)"):
    """수급 누적 시계열을 겹쳐 그린다. 입력은 {시장명: [(date, 억원)]}.

    가격 차트와 달리 0 을 기준으로 오르내리는 값이라 이동평균 대신
    0 선을 긋는다. 표시는 조원 단위.
    """
    usable = [(lab, s) for lab, s in (series_map or {}).items() if s and len(s) > 30]
    if not usable:
        return None

    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=140)
    palette = [UP, DOWN, "#e6a23c"]
    for (lab, s), color in zip(usable, palette):
        dates = [d for d, _ in s]
        vals = [v / 1e4 for _, v in s]          # 억원 -> 조원
        ax.plot(dates, vals, lw=1.6, color=color, zorder=4,
                label=f"{lab} {vals[-1]:+,.1f}조")
        ax.scatter([dates[-1]], [vals[-1]], s=22, color=color, zorder=5)

    ax.axhline(0, color="#999999", lw=1, ls="--", zorder=2)
    ax.set_title(title, fontsize=13, fontweight="bold", color=TEXT, loc="left", pad=12)
    ax.grid(True, color=GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT, labelsize=9)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m"))
    ax.legend(loc="upper left", fontsize=9, frameon=False)

    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def sector_trend_chart(path, sectors, title="미국증시 섹터별 2개년 상대성과 (2년 전 = 100)"):
    """11개 섹터를 2년 전 100 기준으로 정규화해 한 장에 겹쳐 그린다."""
    usable = [s for s in sectors if len(s.get("series") or []) > 30]
    if not usable:
        return None

    # 성과 좋은 순으로 색을 배분해야 범례가 읽힌다
    def perf(s):
        v = [x for _, x in s["series"]]
        return v[-1] / v[0] * 100.0

    usable = sorted(usable, key=perf, reverse=True)
    cmap = plt.get_cmap("turbo")
    colors = [cmap(i / max(len(usable) - 1, 1)) for i in range(len(usable))]

    fig, ax = plt.subplots(figsize=(9.6, 5.4), dpi=140)
    for s, color in zip(usable, colors):
        dates = [d for d, _ in s["series"]]
        base = s["series"][0][1]
        vals = [v / base * 100.0 for _, v in s["series"]]
        ax.plot(dates, vals, lw=1.35, color=color, zorder=4,
                label=f"{s['name']} {vals[-1]:.0f}")

    ax.axhline(100, color="#999999", lw=1, ls="--", zorder=2)
    ax.set_title(title, fontsize=13, fontweight="bold", color=TEXT, loc="left", pad=12)
    ax.grid(True, color=GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT, labelsize=9)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m"))
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5),
              fontsize=8.5, frameon=False)

    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def build_all(data, outdir):
    """발송 순서대로 차트 경로 리스트를 만든다. 텔레그램 앨범 상한(10장)에 맞춘다."""
    os.makedirs(outdir, exist_ok=True)
    setup_font()
    charts = []

    def add(fn, *a, caption=None, solo=False, **kw):
        # solo=True 는 앨범에 묶지 않고 한 장씩 보낸다.
        # 앨범(미디어 그룹)은 사진별 캡션이 앨범 화면에 표시되지 않아,
        # 캡션이 본문인 섹터 차트는 개별 전송해야 한 묶음으로 읽힌다.
        try:
            p = fn(*a, **kw)
            if p:
                charts.append({"path": p, "caption": caption, "solo": solo})
        except Exception as e:                # noqa: BLE001
            print(f"[chart] 실패 {a[0] if a else ''}: {type(e).__name__}: {e}")

    idx = data.get("indices") or {}
    for name in ("Dow", "S&P500", "Nasdaq"):
        d = idx.get(name)
        if d:
            add(line_chart, os.path.join(outdir, f"{name.replace('&','')}.png"),
                f"{name} 2년 추이", d["series"],
                change=d["chg_pct"], change_unit="%", ohlc=d.get("ohlc"))

    now, hist = data.get("ust_now") or {}, data.get("ust_hist") or {}
    for sym in ("US2Y", "US10Y", "US30Y"):
        s = list(hist.get(sym) or [])
        if s:
            q = now.get(sym) or {}
            # 히스토리는 재무부 CMT(D-1), 현재값은 CNBC 실시간이라 소스가 다르다.
            # 마지막 점을 CNBC 값으로 맞춰 본문 수치와 차트 제목이 어긋나지 않게 한다.
            if q.get("yield") is not None:
                today = dt.date.today()
                if s[-1][0] >= today:
                    s[-1] = (s[-1][0], q["yield"])
                else:
                    s.append((today, q["yield"]))
            add(line_chart, os.path.join(outdir, f"{sym}.png"),
                f"미국 국채 {q.get('label', sym)} 2년 추이", s,
                value_fmt="{:,.3f}", unit="%",
                change=q.get("chg_bp"), change_fmt="{:+.1f}", change_unit="bp")

    fx = data.get("fx")
    if fx:
        add(line_chart, os.path.join(outdir, "USDKRW.png"),
            "원/달러 환율 2년 추이", fx["series"],
            unit="원", change=fx["chg"], change_unit="원", ohlc=fx.get("ohlc"))

    dom = data.get("domestic") or {}
    corp = dom.get("corp_aa3y")
    if corp and corp.get("last") is not None:
        add(line_chart, os.path.join(outdir, "corp_aa3y.png"),
            "회사채 AA- 3년 2년 추이", corp["series"],
            value_fmt="{:,.2f}", unit="%",
            change=corp["chg"], change_fmt="{:+.0f}", change_unit="bp")
    sp = dom.get("spread")
    if sp and sp.get("last") is not None:
        add(line_chart, os.path.join(outdir, "spread_aa3y.png"),
            "회사채 AA- 3년 스프레드 2년 추이", sp["series"],
            value_fmt="{:,.1f}", unit="bp",
            change=sp["chg"], change_fmt="{:+.0f}", change_unit="bp")

    # 앨범 1: 지수·금리·환율 요약 + 섹터 일간 등락 막대 (10장)
    sectors = data.get("sectors") or []
    add(sector_chart, os.path.join(outdir, "sectors_daily.png"), sectors)

    # 앨범 2~3: 섹터 상대성과 1장 + 섹터별 개별 2개년 추이 11장
    add(sector_trend_chart, os.path.join(outdir, "sectors_2y.png"), sectors)
    holdings = data.get("holdings") or {}
    notes = data.get("notes") or {}
    sector_notes = data.get("sector_notes") or {}
    for s in sectors:                          # 시가총액 큰 순(자리 고정)
        if s.get("series"):
            add(line_chart, os.path.join(outdir, f"sector_{s['symbol']}.png"),
                f"{s['name']} ({s['symbol']}) 2년 추이", s["series"],
                unit="", change=s["chg_pct"], change_unit="%", ohlc=s.get("ohlc"),
                bench_series=(idx.get("S&P500") or {}).get("series"),
                bench_label="S&P500",
                caption=holdings_caption(s, holdings.get(s["symbol"]), notes,
                                         bench=(idx.get("S&P500") or {}).get("returns"),
                                         sector_note=sector_notes.get(s["symbol"])),
                solo=True)
    return charts


def _cap_fmt(v, cur="달러"):
    """시가총액을 한국식 단위로. 4759668391936 -> 4.76조달러 / 71.7조원"""
    if not v:
        return ""
    # 소수점 한 자리까지. 1조 미만은 억 단위로 내려 자릿수를 지킨다.
    # 1,000조를 넘으면 소수점은 군더더기라 뗀다(삼성전자 1,572조원).
    if v >= 1e15:
        return f"{v / 1e12:,.0f}조{cur}"
    if v >= 1e12:
        return f"{v / 1e12:,.1f}조{cur}"
    return f"{v / 1e8:,.0f}억{cur}"


CAPTION_LIMIT = 1000        # 텔레그램 상한 1024 에 여유를 둔다
NOTE_MAX = 70               # 종목 메모 최대 글자수(두 문장까지 허용)
SECTOR_NOTE_MAX = 120       # 섹터 종합 코멘트 최대 글자수
MAX_NOTES = 3               # 섹터당 뉴스 최대 개수(등락 큰 종목 우선)
TELEGRAM_TOP = 5            # 캡션에 싣는 시총 상위 종목 수(웹은 10종목)
TAG_RE = re.compile(r"<[^>]+>")


def _visible_len(html_text: str) -> int:
    """텔레그램 캡션 상한은 '엔티티 파싱 후' 글자수 기준이다.

    즉 <b> 같은 태그와 <a href="..."> 의 URL 은 세지 않고, 링크는 보이는
    글자('기사')만 센다. HTML 원문 길이로 재면 크게 과대평가돼서
    실제로는 들어갈 뉴스가 통째로 잘려나간다.
    """
    return len(TAG_RE.sub("", html_text))


def holdings_caption(sector, holdings, notes=None, cur="달러", px="${:,.2f}", bench=None,
                     sector_note=None):
    """섹터 차트에 붙일 캡션: 섹터 종합 코멘트 + 상위 5종목 주가·등락 + 종목별 뉴스.

    텔레그램 HTML 에는 글자 크기 지정이 없다. 쓸 수 있는 강약은 굵게/보통/기울임뿐이라
    종목명·등락은 <b>, 뉴스는 보통 글씨로 두어 위계를 만든다.
    사명에 '&' 가 들어가는 종목(AT&T 등)이 있어 이스케이프는 필수다.
    """
    if not holdings:
        return None
    notes = notes or {}
    if sector_note:
        sector_note = sector_note[:SECTOR_NOTE_MAX]

    # 뉴스는 섹터당 MAX_NOTES 개까지만. 중요도가 높은 것부터,
    # 같은 중요도면 그날 크게 움직인 종목을 앞세운다.
    ranked = sorted(
        (h for h in holdings if h["ticker"] in notes),
        key=lambda h: (notes[h["ticker"]].get("importance", 0),
                       abs(h.get("chg_pct") or 0)),
        reverse=True)
    picked = {h["ticker"]: notes[h["ticker"]] for h in ranked[:MAX_NOTES]}

    # 그래도 상한을 넘기면 잘라내지 않고 단계적으로 줄인다.
    # 태그 중간에서 잘리면 텔레그램이 메시지 전체를 거부하기 때문이다.
    # 줄이는 순서: 주도주 자리 → 뉴스 길이 → 뉴스 개수.
    # 뉴스를 먼저 줄이면 '왜 움직였나'가 사라져 브리핑의 핵심이 빠진다.
    # 데이터에는 섹터당 10종목이 들어 있다(웹 대시보드용). 텔레그램 캡션은
    # 1,024자 제한이 빡빡해 앞 TELEGRAM_TOP 개만 싣는다.
    core = [h for h in holdings if not h.get("pick")][:TELEGRAM_TOP]
    holdings = core + [h for h in holdings if h.get("pick")]
    for hs in (holdings, core + [h for h in holdings if h.get("pick")][:1], core):
        for note_cap in (NOTE_MAX, 45, 30):
            cap = _build_caption(sector, hs, picked, note_cap, cur, px, bench,
                                 sector_note)
            if _visible_len(cap) <= CAPTION_LIMIT:
                return cap
    for keep in (2, 1):
        subset = dict(list(picked.items())[:keep])
        cap = _build_caption(sector, core, subset, 30, cur, px, bench, sector_note)
        if _visible_len(cap) <= CAPTION_LIMIT:
            return cap
    # 최후 수단: 섹터 코멘트까지 내려놓고 종목 표만 남긴다
    return _build_caption(sector, core, {}, NOTE_MAX, cur, px, bench)


NAME_DROP = re.compile(
    r"\b(INC|CORP|CORPORATION|CO|THE|PLC|LTD|LLC|COMPANY|HOLDINGS?|GROUP"
    r"|CL|CLASS|SER|SERIES|SHARES|SHS|COMMON|STOCK|ORD|SA|NV|AG)\b|\s+[A-C]$",
    re.I)


def _short_name(name: str, limit: int = 18) -> str:
    """SSGA 표기를 읽을 수 있는 회사명으로. 티커만으로는 어딘지 모르기 때문이다.

    'EXXONMOBIL HOLDINGS CORP' -> 'Exxonmobil'
    'AT+T INC' -> 'AT&T'   (SSGA 는 & 를 + 로 쓴다)
    """
    # 한글 사명은 이미 읽기 좋은 형태다. 영문 규칙(법인격 제거·대소문자 변환)을
    # 적용하면 'SK하이닉스'가 'Sk하이닉스'로 망가진다.
    if re.search(r"[가-힣]", name):
        return name.strip()
    s = name.replace("+", "&")
    s = NAME_DROP.sub(" ", s)
    s = " ".join(s.split()).strip(" ,/-")
    if not s:
        return name.title()
    # 짧은 약어(IBM, AT&T)는 대문자를 유지하고, 일반 단어는 첫 글자만 대문자로
    words = [w if (len(w) <= 4 and not w.isalpha()) or (w.isupper() and len(w) <= 3)
             else w.capitalize() for w in s.split()]
    # 길면 글자를 자르지 말고 뒤 단어를 떨어뜨린다("Verizon Communicat" 방지)
    out = ""
    for w in words:
        cand = f"{out} {w}".strip()
        if len(cand) > limit and out:
            break
        out = cand
    return out.strip(" &+-,·") or " ".join(words)[:limit]


def _arrow(v):
    if v is None:
        return "•"
    return "▲" if v > 0 else ("▼" if v < 0 else "—")


SPAN_KEYS = (("m1", "1M"), ("m3", "3M"), ("m6", "6M"), ("m12", "12M"))


def _spans(returns):
    """1M/3M/6M/12M 를 자리맞춤해 한 줄로. 종목 간 세로로 눈이 맞는다."""
    r = returns or {}
    out = [f"{lab} {r[k]:+5.1f}" for k, lab in SPAN_KEYS if r.get(k) is not None]
    return "  ".join(out)



def _build_caption(sector, holdings, notes, note_cap=NOTE_MAX, cur="달러", px="${:,.2f}", bench=None,
                   sector_note=None):
    # 섹터 헤더: 이름·당일 등락(굵게) + 기간 수익률 + 섹터 종합 코멘트
    chg = sector.get("chg_pct")
    sec_label = (sector["name"] if sector["name"] == sector["symbol"]
                 else f"{sector['name']} ({sector['symbol']})")
    blocks = [f"{_arrow(chg)} <b>{escape(sec_label)}  {chg:+.1f}%</b>"]
    sec_spans = _spans(sector.get("returns"))
    if sec_spans:
        blocks[0] += f"\n<code>{sec_spans}</code>"
    if sector_note:
        blocks[0] += f"\n<i>{escape(sector_note)}</i>"
    # 지수 대비 상대수익률은 글로 쓰지 않는다. 차트 우측 축의 상대강도선이
    # 같은 정보를 더 잘 보여주고, 캡션은 길이 여유가 뉴스에 쓰이는 편이 낫다.

    # 티커만 봐서는 무슨 회사인지 모른다. 사명 옆에 사업을 한두 단어로 붙인다.
    biz_map = biz.describe([h["ticker"] for h in holdings])

    opened = False
    for h in holdings:
        c = h.get("chg_pct")
        # 시총 상위와 주도주는 뽑은 기준이 아예 다르다. 이어서 쓰면 주도주가
        # 시총 순위 뒤에 붙은 것처럼 읽히므로 소제목으로 갈라 놓는다.
        if h.get("pick") and not opened:
            opened = True
            blocks.append("<b>· 주도주 (최근 3개월 상승률 상위) ·</b>")
        # 1행: 방향 표시 + 티커 + 사업 + 당일 등락 (굵게 — 가장 먼저 읽히는 줄)
        label = f"{_short_name(h['name'])} ({h['ticker']})"
        what = biz_map.get(h["ticker"])
        if what:
            label += f" / {what}"
        if h.get("pick") == "watch":
            label = "☆ " + label
        head = (f"{_arrow(c)} <b>{escape(label)}  {c:+.1f}%</b>" if c is not None
                else f"• <b>{escape(label)}</b>")
        # 2행: 주가·시총 (고정폭이라 종목 간 자리가 맞는다)
        meta = [px.format(h["price"])] if h.get("price") is not None else []
        cap = _cap_fmt(h.get("market_cap"), cur)
        if cap:
            meta.append(cap)
        lines = [head]
        if meta:
            lines.append(f"<code>{'  ·  '.join(meta)}</code>")
        sp = _spans(h.get("returns"))
        if sp:
            lines.append(f"<code>{sp}</code>")

        n = notes.get(h["ticker"])
        if n and n.get("note"):
            note = n["note"][:note_cap]
            link = f' <a href="{escape(n["url"], quote=True)}">기사</a>' if n.get("url") else ""
            lines.append(f"<i>↳ {escape(note)}</i>{link}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)
