"""초동 선정 조건을 합성 캔들로 시험한다.  python3 tools/test_early.py

실전 데이터로는 확인이 비싸다 — 한 번 돌리는 데 10분이고 로그 회수도
꼬리만 된다. 조건이 네 겹이라 어느 하나가 과하게 거르면 카드가 비는데,
그걸 실행해 보고서야 아는 일을 세 번 반복했다. 여기서 먼저 잡는다.

픽스처가 '엉뚱한 조건'에서 걸려도 테스트는 통과해 버리므로(실측: 돌파
조건을 보려던 케이스가 등락률 상한에서 걸리고 있었다) 탈락 사유까지 본다.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sources as S                                        # noqa: E402


def series(n=300, *, quiet=True, vol_surge=True, rise=0.20,
           early_cross=True, dip=True):
    """합성 캔들: 횡보 → 눌림(50일선 아래) → 마지막 20봉 거래량 동반 돌파."""
    out, price = [], 100.0
    d0 = dt.date(2025, 1, 1)
    for i in range(n):
        last20, base = i >= n - 20, (n - 80) <= i < (n - 20)
        if last20:
            price *= 1 + rise / 20
        elif not early_cross and i >= n - 140:
            price *= 1.0012                    # 한참 전부터 완만히 올라 있었다
        elif base and dip:
            price *= 0.9985                    # 눌림 — 50일선 아래로 내려간다
        else:
            price *= 1.0 + (0.0005 if i % 2 else -0.0005)
        w = price * ((0.004 if quiet else 0.06) if base else 0.01)
        v = 1_000_000 * ((2.5 if vol_surge else 1.0) if last20 else 1.0)
        out.append((d0 + dt.timedelta(days=i), price, price + w, price - w, price, v))
    return out


def check(label, want_pass, m3=None, **kw):
    S.EARLY_REJECT.clear()
    o = series(**kw)
    r3 = m3 if m3 is not None else (o[-1][4] / o[-92][4] - 1) * 100
    sc = S._early_score({"m3": r3}, o)
    why = "통과" if sc is not None else next(iter(S.EARLY_REJECT), "?")
    ok = (sc is not None) == want_pass
    print(f"{'OK  ' if ok else '실패 '}{label:22} 3M {r3:+6.1f}%  ->  {why}")
    return ok


def main():
    r = [
        # 조건별로 하나씩 깨뜨려 본다. 탈락 사유가 의도한 조건이어야 한다.
        check("정상 초동", True),
        check("거래량 안 붙음", False, vol_surge=False),
        check("돌파 직전 시끄러움", False, quiet=False),
        check("한참 전에 올라섰다", False, early_cross=False, dip=False),
        # 등락률 문턱은 값만 바꿔 직접 본다 — 캔들을 비틀면 다른 조건과 얽힌다.
        check("바닥권(하한 밖)", False, m3=-25.0),
        check("바닥 다지는 중", True, m3=-9.0),
        check("아직 안 갔다", True, m3=39.0),
        check("너무 갔다(상한 밖)", False, m3=41.0),
    ]
    r.append(S._early_score({"m3": 20.0}, series()[:100]) is None)
    print(f"{'OK  ' if r[-1] else '실패 '}시계열 1년 미만")
    r.append(S._early_score({}, series()) is None)
    print(f"{'OK  ' if r[-1] else '실패 '}3개월 등락률 없음")
    print(f"\n{sum(r)}/{len(r)} 통과")
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
