"""분류 수정 결과 확인용 임시 프로브 — 러너에서만 돌린다.

앞선 프로브로 확인한 사실:
  - 건설 관련 업종은 '건설'·'건축자재'·'건축제품'(부동산은 별개)
  - 코웨이는 '가정용기기와용품', LG디스플레이는 '디스플레이패널'
  - SK·GS는 '석유와가스', HD현대는 '조선', LS는 '전기장비',
    한국앤컴퍼니는 '자동차부품', 두산은 '복합기업'(매핑에 없어 이미 제외)
  - 업종 상세 페이지에서 종목 4,029개의 사명을 함께 받을 수 있다

이 프로브는 고친 분류가 실제로 어떻게 떨어지는지 본다.
"""
import sys

sys.path.insert(0, ".")

import kr_universe as ku  # noqa: E402

WATCH = ["코웨이", "LG디스플레이", "SK", "SK이노베이션", "풍산", "풍산홀딩스",
         "한국항공우주", "POSCO홀딩스", "신한지주", "아모레퍼시픽",
         "아모레퍼시픽홀딩스", "삼성물산", "현대건설", "GS", "HD현대"]


def main():
    pools = ku.build_pools()
    name = lambda c: ku.NAMES.get(c, c)
    where = {}
    for theme, codes in pools.items():
        for c in codes:
            where[name(c)] = theme

    print("=" * 64)
    print("[1] 지목된 종목이 어느 테마로 갔나")
    for w in WATCH:
        print(f"  {w:16} -> {where.get(w, '(제외됨)')}")

    print("=" * 64)
    print("[2] 테마별 종목 수")
    for t, cs in sorted(pools.items(), key=lambda kv: -len(kv[1])):
        print(f"  {t:14} {len(cs):4}종목")

    print("=" * 64)
    print("[3] 건설 테마 구성(일부)")
    print("  " + ", ".join(name(c) for c in pools.get("건설", [])[:30]))

    print("=" * 64)
    print("[4] 남아 있는 '홀딩스/지주' 종목 — 금융과 예외만 있어야 한다")
    for t, cs in sorted(pools.items()):
        hits = [name(c) for c in cs if ku.HOLDING_RE.search(name(c))]
        if hits:
            print(f"  {t}: {', '.join(hits)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        import traceback
        print(f"!! 실패: {type(e).__name__}: {e}")
        traceback.print_exc()
