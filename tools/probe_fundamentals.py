"""기업 개요·분기 실적 파서 검증용 임시 프로브 — 러너에서만 돌린다.

개발 컨테이너는 야후·네이버가 프록시에서 막혀 있어(403) 실제 응답으로
파서를 돌려볼 수 없다. 여기서 구현체(sources.fetch_company_info,
kr_sources.fetch_company_info)를 그대로 호출해 결과를 눈으로 확인한다.

2차 확인 대상: 네이버 표의 추정치 열 제외와 FnGuide 개요 재시도가 실제로
먹히는지(1차에서 4종목 중 3종목의 개요가 비었다).

앞선 프로브에서 확인한 소스:
  [미국] quoteSummary?modules=assetProfile 로 기업 개요(영문),
         fundamentals-timeseries 로 매출·영업이익·순이익·EPS 5분기.
  [한국] 종목 페이지의 '기업실적분석' 표(분기 6열), 개요는 FnGuide cmp_comment.
"""
import sys

sys.path.insert(0, ".")

import kr_sources  # noqa: E402
import sources  # noqa: E402


def _fmt(v):
    if v is None:
        return "–"
    return f"{v/1e8:,.0f}억" if abs(v) < 1e12 else f"{v/1e12:,.2f}조"


def probe_us():
    print("=" * 64)
    print("[미국] fetch_company_info")
    info = sources.fetch_company_info(["AAPL", "NVDA", "BE", "PLTR"])
    for t, v in info.items():
        p, qs = v.get("profile") or {}, v.get("quarters") or []
        print(f"  {t}: 개요 {len(p.get('summary',''))}자 / {p.get('industry')} "
              f"/ 직원 {p.get('employees')}")
        print(f"    {p.get('summary','')[:150]}")
        for q in qs:
            print(f"    {q['date']}  매출 {_fmt(q['revenue'])}  "
                  f"영업익 {_fmt(q['op'])}  순익 {_fmt(q['net'])}  EPS {q.get('eps')}")


def probe_kr():
    print("=" * 64)
    print("[한국] fetch_company_info")
    info = kr_sources.fetch_company_info(["005930", "000660", "042660", "112040"])
    for c, v in info.items():
        p, qs = v.get("profile") or {}, v.get("quarters") or []
        print(f"  {c} {v.get('name')}: 개요 {len(p.get('summary',''))}자")
        print(f"    {p.get('summary','')[:150]}")
        for q in qs:
            print(f"    {q['date']}  매출 {_fmt(q.get('revenue'))}  "
                  f"영업익 {_fmt(q.get('op'))}  순익 {_fmt(q.get('net'))}")
        if not qs:
            print("    !! 분기 실적 파싱 실패")


if __name__ == "__main__":
    for fn in (probe_us, probe_kr):
        try:
            fn()
        except Exception as e:                     # noqa: BLE001
            import traceback
            print(f"!! {fn.__name__} 실패: {type(e).__name__}: {e}")
            traceback.print_exc()
