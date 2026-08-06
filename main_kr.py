"""한국 증시 마감 브리핑 — 수집 → 차트 → 텔레그램 발송.

미국판(main.py)과 같은 양식이되 섹터·종목·통화가 국내 기준이다.
장 마감(15:30) 직후에는 종가가 아직 정리되지 않아 15:45 에 보낸다.

로컬 점검:  SSL_VERIFY=0 python main_kr.py --dry-run --outdir out_kr
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

import kr_sources as kr
import news
import notify
import render
import sources
import webgen

CUR = "원"
PX = "{:,.0f}원"


def _fmt_flow(v):
    """억원 단위 값을 조/억원으로."""
    return f"{v / 1e4:+.2f}조" if abs(v) >= 1e4 else f"{v:+,.0f}억"


def build_message(data) -> str:
    today = dt.date.today()
    lines = [f"<b>📉 코스피 마감  {today:%Y.%m.%d} "
             f"({'월화수목금토일'[today.weekday()]})</b>", ""]

    idx = data.get("indices") or {}
    for name in ("코스피", "코스닥"):
        d = idx.get(name)
        if d:
            lines.append(f"□ {name} {d['last']:,.2f} ({d['chg_pct']:+.1f}%)")

    secs = data.get("sectors") or []
    if secs:
        lines.append("□ 섹터별 등락:")
        for s in secs:
            lines.append(f"    · {s['name']} {s['chg_pct']:+.1f}%")

    fx = data.get("fx")
    if fx:
        lines.append(f"□ 원/달러 환율 {fx['last']:,.1f}원({fx['chg']:+.1f}원)")

    dom = data.get("domestic") or {}
    govt, corp = dom.get("govt_3y"), dom.get("corp_aa3y")
    if govt and govt.get("last") is not None:
        lines.append(f"□ 국고채 3년 {govt['last']:.2f}%({govt['chg']:+.0f}bp)")
    if corp and corp.get("last") is not None:
        lines.append(f"□ 회사채 AA- 3년 {corp['last']:.2f}%({corp['chg']:+.0f}bp)")

    flows = data.get("flows") or {}
    for mkt in ("코스피", "코스닥"):
        f = flows.get(mkt)
        if not f:
            continue
        parts = [f"{who} {_fmt_flow(f['today'][who])}(YTD {_fmt_flow(f['ytd'][who])})"
                 for who in ("외국인", "기관", "개인")]
        lines.append(f"□ {mkt} 수급: " + ", ".join(parts))
        # 외국인은 기간 누적을 따로 한 줄 더 — 당일만으로는 추세가 안 보인다
        w = f.get("windows") or {}
        cums = [f"{k} {_fmt_flow(w[k]['외국인'])}" for k, _ in kr.FLOW_WINDOWS
                if w.get(k) is not None]
        if cums:
            lines.append("    · 외국인 누적: " + ", ".join(cums))

    lines.append("")
    lines.append("<i>지수·종목은 당일 종가, 국내 금리는 전영업일 기준입니다.</i>")

    errors = data.get("errors") or []
    if errors:
        lines.append("")
        lines.append("⚠️ <b>일부 항목 수집 실패</b>")
        lines.extend(f"    · {e}" for e in errors)
    return "\n".join(lines)


def collect():
    data, errors = {}, []

    def run(key, fn):
        try:
            data[key] = fn()
        except Exception as e:                 # noqa: BLE001
            errors.append(f"{key}: {type(e).__name__}: {e}")
            data[key] = None

    run("indices", kr.fetch_indices)
    run("flows", kr.fetch_flows)
    run("fx", sources.fetch_fx)                # 원/달러는 미국판과 같은 소스
    run("domestic", sources.fetch_domestic)    # 국고채·회사채도 동일

    try:
        secs, holdings = kr.fetch_sectors_and_holdings()
        # 코스닥 종목은 코스닥 지수 기준으로 상대수익률을 낸다
        kr.attach_benchmarks(secs, holdings, data.get("indices") or {})
        data["sectors"], data["holdings"] = secs, holdings
    except Exception as e:                     # noqa: BLE001
        errors.append(f"sectors: {type(e).__name__}: {e}")
        data["sectors"], data["holdings"] = [], {}

    data["errors"] = errors
    return data


def build_charts(data, outdir):
    os.makedirs(outdir, exist_ok=True)
    render.setup_font()
    charts = []

    def add(path, caption=None, solo=False):
        if path:
            charts.append({"path": path, "caption": caption, "solo": solo})

    idx = data.get("indices") or {}
    for name in ("코스피", "코스닥"):
        d = idx.get(name)
        if d:
            add(render.line_chart(os.path.join(outdir, f"{name}.png"),
                                  f"{name} 2년 추이", d["series"],
                                  change=d["chg_pct"], change_unit="%",
                                  ohlc=d.get("ohlc")))

    fx = data.get("fx")
    if fx:
        add(render.line_chart(os.path.join(outdir, "USDKRW.png"),
                              "원/달러 환율 2년 추이", fx["series"],
                              unit="원", change=fx["chg"], change_unit="원",
                              ohlc=fx.get("ohlc")))

    dom = data.get("domestic") or {}
    for key, title in (("govt_3y", "국고채 3년"), ("corp_aa3y", "회사채 AA- 3년"),
                       ("spread", "회사채 AA- 3년 스프레드")):
        d = dom.get(key)
        if d and d.get("last") is not None:
            unit = "bp" if key == "spread" else "%"
            add(render.line_chart(os.path.join(outdir, f"{key}.png"),
                                  f"{title} 2년 추이", d["series"],
                                  value_fmt="{:,.2f}", unit=unit,
                                  change=d["chg"], change_fmt="{:+.0f}", change_unit="bp"))

    flows = data.get("flows") or {}
    foreign = {mkt: (flows.get(mkt) or {}).get("foreign_cum")
               for mkt in ("코스피", "코스닥")}
    foreign = {k: v for k, v in foreign.items() if v}
    if foreign:
        add(render.flow_chart(os.path.join(outdir, "foreign_flows.png"), foreign))

    secs = data.get("sectors") or []
    holdings = data.get("holdings") or {}
    notes = data.get("notes") or {}
    if secs:
        add(render.sector_chart(os.path.join(outdir, "sectors_daily.png"), secs,
                                title="한국증시 섹터별 등락"))

    for s in secs:
        series = kr.sector_series(s["symbol"], s.get("members") or holdings.get(s["symbol"]))
        if not series:
            continue
        s["web_series"] = series               # 웹 대시보드가 재사용한다
        cap = render.holdings_caption(s, holdings.get(s["symbol"]), notes,
                                      cur=CUR, px=PX,
                                      bench=(idx.get("코스피") or {}).get("returns"))
        add(render.line_chart(
            os.path.join(outdir, f"sector_{s['symbol']}.png"),
            f"{s['name']} 2년 추이 — {s.get('member_count', 0)}종목 시총가중 (2년 전 = 100)", series,
            value_fmt="{:,.1f}", change=s["chg_pct"], change_unit="%",
            bench_series=(idx.get("코스피") or {}).get("series"),
            bench_label="코스피"),
            caption=cap, solo=True)
    return charts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    # 웹을 먼저 갱신하고 텔레그램을 뒤에 보내기 위한 두 단계 실행.
    # --defer-send 가 본문·차트를 저장해 두면, Cloudflare 배포가 끝난 뒤
    # --send-pending 이 그것만 읽어 보낸다(수집을 두 번 하지 않는다).
    ap.add_argument("--defer-send", action="store_true")
    ap.add_argument("--send-pending", action="store_true")
    ap.add_argument("--outdir", default="out_kr")
    args = ap.parse_args()

    if args.send_pending:
        token, chat_id = (os.environ.get("TELEGRAM_BOT_TOKEN"),
                          os.environ.get("TELEGRAM_CHAT_ID"))
        if not (token and chat_id):
            print("[발송] 텔레그램 시크릿이 비어 있습니다", file=sys.stderr)
            return 2
        print("[발송] 저장해 둔 브리핑 전송...")
        rc = notify.send_pending(args.outdir, token, chat_id)
        print("    완료.")
        return rc

    t0 = time.time()
    print("[1/3] 데이터 수집...")
    data = collect()
    for e in data.get("errors") or []:
        print(f"    ! {e}")
    data["notes"] = news.build_kr(data.get("holdings") or {})

    print("[2/3] 차트 생성...")
    charts = build_charts(data, args.outdir)
    print(f"    {len(charts)}장: " + ", ".join(os.path.basename(c["path"]) for c in charts))

    # 웹 대시보드(Cloudflare Pages 배포용). 섹터 시계열(web_series)을
    # build_charts 가 계산해 두므로 반드시 그 뒤에 만든다. 실패해도 발송은 계속.
    try:
        webgen.build_kr(data, os.path.join("site", "kr.html"))
        webgen.write_index("site")
        print("    웹 대시보드: site/kr.html")
    except Exception as e:                     # noqa: BLE001
        print(f"    ! 웹 대시보드 생성 실패(발송은 계속): {type(e).__name__}: {e}")

    text = build_message(data)
    print("\n" + "-" * 60 + "\n" + text + "\n" + "-" * 60 + "\n")

    if args.dry_run:
        print(f"[3/3] --dry-run 이므로 발송 생략. ({time.time() - t0:.1f}s)")
        return 0

    if args.defer_send:
        p = notify.save_pending(args.outdir, text, charts)
        print(f"[3/3] 발송을 미룸 — 웹 배포 뒤에 보낸다({p}). "
              f"({time.time() - t0:.1f}s)")
        return 0

    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", token),
                              ("TELEGRAM_CHAT_ID", chat_id)) if not v]
    if missing:
        print(f"[3/3] 다음 시크릿이 비어 있습니다: {', '.join(missing)}", file=sys.stderr)
        return 2

    print("[3/3] 텔레그램 발송...")
    notify.send(token, chat_id, text, charts)
    print(f"    완료. ({time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
