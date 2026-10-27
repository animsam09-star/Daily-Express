# -*- coding: utf-8 -*-
"""실적 캘린더 수집을 시험한다 — 창을 끊어 받는 부분과 발표·예정 분류.

이 자리에서 세 번 틀렸다. ① 실제치가 없으면 지난 날짜도 '예정' 에 뒀고,
② 4주를 한 번에 달라고 해서 응답이 1500행에서 잘렸으며(앞쪽이 통째로
날아가 발표가 35건에서 8건이 됐다), ③ 그 사실을 로그로 알 수 없었다.
바깥 호출은 하지 않는다 — requests 를 가짜로 바꿔 끼운다.
"""
import datetime as dt
import sys

sys.path.insert(0, ".")
import sources                                     # noqa: E402

TODAY = dt.date.today()


class FakeResp:
    status_code = 200

    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return {"earningsCalendar": self._rows}


def run(rows_for, tickers=("AAA", "BBB", "CCC")):
    """rows_for(from, to) -> 그 창에서 돌려줄 행들. 실제 호출 창도 모아 준다."""
    calls = []

    def fake_get(url, **kw):
        p = kw.get("params") or {}
        calls.append((p.get("from"), p.get("to")))
        return FakeResp(rows_for(p.get("from"), p.get("to")))

    saved_get, saved_key = sources.requests.get, sources._finnhub_key
    sources.requests.get = fake_get
    sources._finnhub_key = lambda: "x"
    try:
        out = sources.fetch_earnings(list(tickers))
    finally:
        sources.requests.get = saved_get
        sources._finnhub_key = saved_key
    return out, calls


def check(name, got, want):
    ok = got == want
    print(("OK  " if ok else "FAIL") + f"  {name}")
    if not ok:
        print(f"      got={got!r}\n      want={want!r}")
    return ok


def main():
    ok = True
    d = lambda n: (TODAY + dt.timedelta(days=n)).isoformat()   # noqa: E731

    # 1) 창이 요청 구간을 빈틈·겹침 없이 덮는가
    _, calls = run(lambda f, t: [])
    starts = [c[0] for c in calls]
    ends = [c[1] for c in calls]
    ok &= check("첫 창은 EARN_BACK 일 전부터", starts[0], d(-sources.EARN_BACK))
    ok &= check("마지막 창은 EARN_AHEAD 일 뒤까지", ends[-1], d(sources.EARN_AHEAD))
    gaps = [(ends[i], starts[i + 1]) for i in range(len(calls) - 1)
            if dt.date.fromisoformat(starts[i + 1])
            != dt.date.fromisoformat(ends[i]) + dt.timedelta(days=1)]
    ok &= check("창 사이에 빈틈·겹침이 없다", gaps, [])

    # 2) 한 창이 상한에 닿아도 나머지 창은 계속 받는다 + 중복은 합친다
    def rows_for(f, t):
        if f == d(-sources.EARN_BACK):             # 가장 오래된 창
            return [{"symbol": "AAA", "date": f, "epsActual": 1.0,
                     "epsEstimate": 0.9, "revenueActual": 10, "revenueEstimate": 9}]
        if t == d(sources.EARN_AHEAD):             # 가장 나중 창
            return [{"symbol": "BBB", "date": d(sources.EARN_AHEAD)},
                    # 같은 행이 두 창에 걸쳐 와도 한 번만 센다
                    {"symbol": "AAA", "date": d(-sources.EARN_BACK),
                     "epsActual": 1.0, "epsEstimate": 0.9}]
        return []

    out, _ = run(rows_for)
    ok &= check("오래된 창의 발표가 살아남는다",
                [r["ticker"] for r in out["done"]], ["AAA"])
    ok &= check("앞으로의 발표는 예정으로",
                [r["ticker"] for r in out["upcoming"]], ["BBB"])

    # 3) 실제치가 없어도 날짜가 지났으면 '발표'
    def rows2(f, t):
        out_ = []
        if f <= d(-2) <= t:
            out_.append({"symbol": "AAA", "date": d(-2)})        # 지난 날짜·수치 없음
        if f <= d(0) <= t:
            out_.append({"symbol": "BBB", "date": d(0)})         # 오늘·수치 없음
        if f <= d(3) <= t:
            out_.append({"symbol": "CCC", "date": d(3)})         # 미래
        return out_

    out, _ = run(rows2)
    ok &= check("지난 날짜는 수치가 없어도 발표",
                [r["ticker"] for r in out["done"]], ["AAA"])
    ok &= check("오늘·미래는 예정",
                sorted(r["ticker"] for r in out["upcoming"]), ["BBB", "CCC"])

    # 4) 우리 종목이 아니면 안 담는다
    out, _ = run(lambda f, t: ([{"symbol": "ZZZ", "date": d(-1)}]
                               if f == d(-sources.EARN_BACK) else []))
    ok &= check("우리 종목만 추린다", (out["done"], out["upcoming"]), ([], []))

    print("\n통과" if ok else "\n실패")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
