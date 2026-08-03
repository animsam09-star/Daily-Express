"""유니버스 확장용 실측 프로브 — GitHub Actions 러너에서만 돌린다.

개발 컨테이너는 야후·SSGA 로 나가는 길이 프록시에서 막혀 있어(403),
'후보를 어디까지 넓힐 수 있는지'를 기억이 아니라 실측으로 확인하려면
러너에서 한 번 찍어봐야 한다.

1차 프로브에서 확인한 것:
  - 팔란티어는 XLK 안에 있지만 섹터 내 시총 12/76위 — 상위 5 규칙에 잘린다
  - 앱러빈 8/24(XLC), 비스트라 8/34(XLU) 도 같은 이유로 잘린다
  - 블룸에너지는 11개 섹터 ETF 어디에도 없다(시총 64B 인데 S&P500 미편입)
  - quote 응답에 fiftyTwoWeekChangePercent / twoHundredDayAverageChangePercent /
    fiftyDayAverageChangePercent / averageDailyVolume3Month 가 전부 온다(한국 포함)

2차 프로브(이 파일)에서 확인할 것:
  - S&P500 밖 대형주를 담는 SSGA 파일이 있는가(중형·소형·전체시장)
  - 그 파일에 GICS 섹터 열이 있어 11개 섹터로 매핑할 수 있는가
"""
import io
import sys

sys.path.insert(0, ".")

import sources  # noqa: E402

WATCH = {"BE": "블룸에너지", "PLTR": "팔란티어", "APP": "앱러빈", "VST": "비스트라"}

# 같은 SSGA 주소 규칙을 쓰는 광범위 ETF 후보.
# SPMD=S&P400 중형, SPSM=S&P600 소형, SPTM=S&P1500 전체, SPY=S&P500,
# MDY=S&P400(구형), TOTL 은 채권이라 제외.
BROAD = ["sptm", "spmd", "spsm", "mdy", "spy"]


def _rows(etf):
    import openpyxl
    raw = sources._get(sources.SSGA_HOLDINGS.format(etf=etf)).content
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        return [list(r) for r in wb.active.iter_rows(values_only=True)]
    finally:
        wb.close()


def probe_broad():
    print("=" * 60)
    print("[1] S&P500 밖 종목을 담는 SSGA 파일 탐색")
    for etf in BROAD:
        try:
            rows = _rows(etf)
        except Exception as e:                     # noqa: BLE001
            print(f"  {etf.upper()}: 실패 {type(e).__name__}: {e}")
            continue
        hi = next((i for i, r in enumerate(rows)
                   if r and any(str(c).strip() == "Ticker" for c in r if c)), None)
        if hi is None:
            print(f"  {etf.upper()}: Ticker 헤더 없음")
            continue
        hdr = [str(c).strip() if c else "" for c in rows[hi]]
        ti = hdr.index("Ticker")
        body = [r for r in rows[hi + 1:] if r and r[ti]]
        tick = {str(r[ti]).strip().upper() for r in body}
        hit = [f"{t}({n})" for t, n in WATCH.items() if t in tick]
        print(f"  {etf.upper()}: {len(body)}종목 | 열={hdr}")
        print(f"      감시종목: {', '.join(hit) if hit else '없음'}")
        # 감시종목이 있으면 그 행을 통째로 보여준다(섹터 열 값 확인용)
        si = hdr.index("Sector") if "Sector" in hdr else None
        for r in body:
            t = str(r[ti]).strip().upper()
            if t in WATCH:
                print(f"      → {t}: 섹터={r[si] if si is not None else '(열 없음)'}")
        if si is not None:
            secs = {}
            for r in body:
                secs[str(r[si]).strip()] = secs.get(str(r[si]).strip(), 0) + 1
            print(f"      섹터 값: {sorted(secs.items(), key=lambda kv: -kv[1])}")


def probe_sector_file_columns():
    print("=" * 60)
    print("[2] 섹터 ETF 파일에도 섹터 열이 있나(있으면 매핑 기준을 통일할 수 있다)")
    rows = _rows("xlk")
    hi = next((i for i, r in enumerate(rows)
               if r and any(str(c).strip() == "Ticker" for c in r if c)), None)
    print("  XLK 열:", [str(c).strip() if c else "" for c in rows[hi]])


if __name__ == "__main__":
    for fn in (probe_broad, probe_sector_file_columns):
        try:
            fn()
        except Exception as e:                     # noqa: BLE001
            print(f"!! {fn.__name__} 실패: {type(e).__name__}: {e}")
