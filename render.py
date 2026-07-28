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


def line_chart(path, title, series, *, value_fmt="{:,.2f}", unit="",
               change=None, change_fmt="{:+.2f}", change_unit=""):
    """2개년 종가 + 이동평균선 1장."""
    if not series or len(series) < 2:
        return None
    dates = [d for d, _ in series]
    vals = [v for _, v in series]

    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=140)
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
    ax.legend(loc="upper left", fontsize=8, frameon=False, ncol=4)

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
                f"{v:+.2f}%", va="center", ha="left" if v >= 0 else "right",
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
                change=d["chg_pct"], change_unit="%")

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
            unit="원", change=fx["chg"], change_unit="원")

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
    for s in sectors:                          # 당일 등락률 높은 순
        if s.get("series"):
            add(line_chart, os.path.join(outdir, f"sector_{s['symbol']}.png"),
                f"{s['name']} ({s['symbol']}) 2년 추이", s["series"],
                unit="", change=s["chg_pct"], change_unit="%",
                caption=holdings_caption(s, holdings.get(s["symbol"]), notes),
                solo=True)
    return charts


def _cap_fmt(v, cur="달러"):
    """시가총액을 한국식 단위로. 4759668391936 -> 4.76조달러 / 71.7조원"""
    if not v:
        return ""
    if v >= 1e14:
        return f"{v / 1e12:,.0f}조{cur}"
    if v >= 1e12:
        return f"{v / 1e12:.2f}조{cur}"
    return f"{v / 1e8:,.0f}억{cur}"


CAPTION_LIMIT = 1000        # 텔레그램 상한 1024 에 여유를 둔다
NOTE_MAX = 45               # 뉴스 한 줄 최대 글자수
MAX_NOTES = 3               # 섹터당 뉴스 최대 개수(등락 큰 종목 우선)
TAG_RE = re.compile(r"<[^>]+>")


def _visible_len(html_text: str) -> int:
    """텔레그램 캡션 상한은 '엔티티 파싱 후' 글자수 기준이다.

    즉 <b> 같은 태그와 <a href="..."> 의 URL 은 세지 않고, 링크는 보이는
    글자('기사')만 센다. HTML 원문 길이로 재면 크게 과대평가돼서
    실제로는 들어갈 뉴스가 통째로 잘려나간다.
    """
    return len(TAG_RE.sub("", html_text))


def holdings_caption(sector, holdings, notes=None, cur="달러", px="${:,.2f}"):
    """섹터 차트에 붙일 상위 5종목: 주가·시가총액·등락 + 종목별 뉴스.

    텔레그램 HTML 에는 글자 크기 지정이 없다. 쓸 수 있는 강약은 굵게/보통/기울임뿐이라
    종목명·등락은 <b>, 뉴스는 보통 글씨로 두어 위계를 만든다.
    사명에 '&' 가 들어가는 종목(AT&T 등)이 있어 이스케이프는 필수다.
    """
    if not holdings:
        return None
    notes = notes or {}

    # 뉴스는 섹터당 MAX_NOTES 개까지만. 등락이 큰 종목이 그날의 주요 뉴스다.
    ranked = sorted(holdings, key=lambda h: abs(h.get("chg_pct") or 0), reverse=True)
    picked = {h["ticker"]: notes[h["ticker"]]
              for h in ranked if h["ticker"] in notes}
    picked = dict(list(picked.items())[:MAX_NOTES])

    # 그래도 상한을 넘기면 잘라내지 않고 단계적으로 줄인다.
    # 태그 중간에서 잘리면 텔레그램이 메시지 전체를 거부하기 때문이다.
    for note_cap in (NOTE_MAX, 30, 22):
        cap = _build_caption(sector, holdings, picked, note_cap, cur, px)
        if _visible_len(cap) <= CAPTION_LIMIT:
            return cap
    for keep in (2, 1):
        subset = dict(list(picked.items())[:keep])
        cap = _build_caption(sector, holdings, subset, 22, cur, px)
        if _visible_len(cap) <= CAPTION_LIMIT:
            return cap
    return _build_caption(sector, holdings, {}, NOTE_MAX, cur, px)


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


def _spans(returns):
    """1M/3M/6M/12M 를 자리맞춤해 한 줄로. 종목 간 세로로 눈이 맞는다."""
    r = returns or {}
    out = [f"{lab} {r[k]:+5.1f}" for k, lab in
           (("m1", "1M"), ("m3", "3M"), ("m6", "6M"), ("m12", "12M"))
           if r.get(k) is not None]
    return "  ".join(out)


def _build_caption(sector, holdings, notes, note_cap=NOTE_MAX, cur="달러", px="${:,.2f}"):
    # 섹터 헤더: 이름·당일 등락(굵게) + 기간 수익률
    chg = sector.get("chg_pct")
    sec_label = (sector["name"] if sector["name"] == sector["symbol"]
                 else f"{sector['name']} ({sector['symbol']})")
    blocks = [f"{_arrow(chg)} <b>{escape(sec_label)}  {chg:+.2f}%</b>"]
    sec_spans = _spans(sector.get("returns"))
    if sec_spans:
        blocks[0] += f"\n<code>{sec_spans}</code>"

    for h in holdings:
        c = h.get("chg_pct")
        # 1행: 방향 표시 + 티커 + 당일 등락 (굵게 — 가장 먼저 읽히는 줄)
        label = f"{_short_name(h['name'])} ({h['ticker']})"
        head = (f"{_arrow(c)} <b>{escape(label)}  {c:+.2f}%</b>" if c is not None
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
