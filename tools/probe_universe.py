"""테마 분류 수정용 실측 프로브 — 러너에서만 돌린다.

확인할 것:
  1) 네이버 업종 전체 목록 — '건설' 테마로 쓸 업종의 정확한 이름
  2) 업종 상세 페이지에서 종목코드와 사명을 함께 긁을 수 있는가
     (지주사를 이름으로 걸러내려면 코드만으론 안 된다)
  3) 문제로 지목된 종목들이 실제로 어느 업종에 있는가
     — 코웨이(전기전자로 잡힘), LG디스플레이(소부장으로 잡힘)
  4) 이름에 '홀딩스/지주'가 든 종목이 테마별로 얼마나 되는가
"""
import re
import sys

sys.path.insert(0, ".")

import kr_universe as ku  # noqa: E402

WATCH = {"021240": "코웨이", "034220": "LG디스플레이", "034730": "SK",
         "267250": "HD현대", "006260": "LS", "078930": "GS",
         "000240": "한국앤컴퍼니", "000150": "두산"}


def probe_upjong_list():
    print("=" * 64)
    print("[1] 네이버 업종 전체 목록")
    ups = re.findall(r'no=(\d+)">([^<]+)</a>', ku._get(ku.GROUP_URL))
    names = [n for _, n in ups]
    print(f"  {len(names)}개")
    for i in range(0, len(names), 6):
        print("   ", " · ".join(names[i:i + 6]))
    print("  건설 관련:", [n for n in names
                          if any(k in n for k in ("건설", "건축", "엔지니어링", "부동산"))])


NAME_RE = re.compile(r'code=(\d{6})">([^<]+)</a>')


def probe_names():
    print("=" * 64)
    print("[2] 업종 상세에서 코드+사명 파싱")
    ups = re.findall(r'no=(\d+)">([^<]+)</a>', ku._get(ku.GROUP_URL))
    found, by_up = {}, {}
    for no, name in ups:
        html = ku._get(ku.DETAIL_URL.format(no=no))
        pairs = dict(NAME_RE.findall(html))
        if pairs:
            by_up[name] = pairs
        found.update(pairs)
    print(f"  업종 {len(by_up)}개 / 종목 {len(found)}개에서 사명 확보")
    sample = list(found.items())[:5]
    print("  샘플:", sample)

    print("-" * 64)
    print("[3] 지목된 종목의 업종")
    for code, label in WATCH.items():
        ups_of = [u for u, pairs in by_up.items() if code in pairs]
        print(f"  {label}({code}): {ups_of}  이름='{found.get(code, '')}'")

    print("-" * 64)
    print("[4] 이름에 '홀딩스/지주'가 든 종목(업종별)")
    hold = re.compile(r"홀딩스|지주")
    for u, pairs in sorted(by_up.items()):
        if u not in ku.UPJONG_THEME:
            continue
        hits = [f"{n}({c})" for c, n in pairs.items() if hold.search(n)]
        if hits:
            print(f"  {u} -> {ku.UPJONG_THEME[u]}: {', '.join(hits[:12])}")


if __name__ == "__main__":
    for fn in (probe_upjong_list, probe_names):
        try:
            fn()
        except Exception as e:                     # noqa: BLE001
            import traceback
            print(f"!! {fn.__name__} 실패: {type(e).__name__}: {e}")
            traceback.print_exc()
