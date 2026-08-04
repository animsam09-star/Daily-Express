"""한국 테마 구성종목을 손으로 적지 않고 데이터에서 만든다.

두 갈래를 쓴다.
1) 네이버 업종 분류(전 종목 4,036개 / 79개 업종) — 업종으로 깔끔히 떨어지는 테마
2) 테마 ETF 구성종목 — 업종에 존재하지 않는 테마(2차전지·로봇·신재생)

업종 분류의 한계가 이 구조의 이유다. GICS 세분류라 '반도체와반도체장비' 170종목에
삼성전자와 소부장이 한 덩어리로 들어가고, 2차전지·로봇·신재생은 업종 자체가 없다.
반대로 ETF 는 테마를 정확히 반영하지만 네이버가 상위 10종목까지만 공개해서
테마당 ETF 를 여러 개 묶어 합집합을 쓴다.

시가총액 하한(기본 1,000억)으로 잡주를 걸러낸다.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

from sources import TIMEOUT, UA, VERIFY

if not VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MIN_CAP = 1_000e8               # 시가총액 하한: 1,000억원
NAVER_HDR = {**UA, "Referer": "https://finance.naver.com/"}
GROUP_URL = "https://finance.naver.com/sise/sise_group.naver?type=upjong"
DETAIL_URL = "https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no={no}"
ITEM_URL = "https://finance.naver.com/item/main.naver?code={code}"
ETF_CODE_RE = r"[0-9A-Z]{6}"

# 업종 -> 테마. 여기 없는 업종은 어느 테마에도 들어가지 않는다.
UPJONG_THEME = {
    "반도체와반도체장비": "반도체 소부장",     # 대형주는 아래 SEMI_LARGE 로 빼낸다
    "디스플레이장비및부품": "반도체 소부장",
    # 패널 제조사(LG디스플레이)는 소부장이 아니다 — 장비·부품을 납품받는 쪽이다
    "디스플레이패널": "전기전자",
    "제약": "바이오·제약", "생물공학": "바이오·제약",
    "생명과학도구및서비스": "바이오·제약", "건강관리장비및용품": "바이오·제약",
    "건강관리장비와용품": "바이오·제약",
    "자동차": "자동차", "자동차부품": "자동차",
    "은행": "금융", "증권": "금융", "손해보험": "금융", "생명보험": "금융",
    "기타금융": "금융", "카드": "금융", "다각화된금융서비스": "금융",
    "화장품": "화장품",
    "섬유,의류,신발,호화품": "의류·유통", "백화점과일반상점": "의류·유통",
    "전문소매": "의류·유통", "판매업체": "의류·유통",
    "인터넷과카탈로그소매": "의류·유통", "식품과기본식료품소매": "의류·유통",
    # 정유·화학과 철강·비철은 사업이 전혀 달라 한 테마로 묶으면 지수가 흐려진다
    "석유와가스": "정유·화학", "화학": "정유·화학",
    "철강": "철강·비철", "비철금속": "철강·비철",
    "포장재": "철강·비철", "종이와목재": "철강·비철",
    "조선": "조선·기계", "기계": "조선·기계",
    "게임엔터테인먼트": "인터넷·게임", "IT서비스": "인터넷·게임",
    "소프트웨어": "인터넷·게임", "양방향미디어와서비스": "인터넷·게임",
    "우주항공과국방": "방산·항공",           # 우주는 아래 SPACE 로 빼낸다
    "전자장비와기기": "전기전자", "전자제품": "전기전자",
    "핸드셋": "전기전자", "컴퓨터와주변기기": "전기전자",
    "통신장비": "전기전자", "전기제품": "전기전자",
    # '가정용기기와용품'(코웨이·쿠쿠)은 뺐다. 정수기·비데 렌탈은 소비 경기를
    # 타지 IT 하드웨어 수요를 타지 않아, 전기전자 지수에 넣으면 흐려진다.
    "건설": "건설", "건축자재": "건설", "건축제품": "건설",
    # 전력기기(변압기·차단기 — HD현대일렉트릭·LS ELECTRIC 등)는 IT 하드웨어와
    # 등락 동인이 전혀 다르다(전력망 투자 vs IT 수요). 전기전자에서 분리한다.
    "전기장비": "전력기기",
}


# 사람이 고정하는 분류 — 데이터(업종·ETF)가 뭐라 하든 여기가 이긴다.
# 국내 테마 ETF 는 대형주를 섞어 담아서 종목이 엉뚱한 테마로 끌려간다
# (신재생 ETF 가 두산에너빌리티를 담아 두산이 신재생 상위로 잡혔다).
# 값이 None 이면 어느 테마에도 넣지 않는다(지주회사 등).
PINNED_THEME = {
    "034020": "전력기기",   # 두산에너빌리티 — 원전·발전설비. 신재생 ETF 비중 탓에 신재생으로 끌려감
    "000150": None,          # 두산(지주) — 지주사는 특정 테마 지수를 흐린다
    "402340": "반도체",     # SK스퀘어 — 업종상 반도체와반도체장비라 소부장으로 잡히지만
                             # 실질은 SK하이닉스 지분 프록시. 대형 반도체로 묶는다
    "007660": "전기전자",   # 이수페타시스 — AI 가속기용 기판(MLB). 소부장이 아니라 전자부품
    "005935": "반도체",     # 삼성전자우 — 우선주 일괄 제외의 예외. 고정 분류는 필터를 통과한다
    "010060": "신재생",     # OCI홀딩스 — 태양광 폴리실리콘. 업종이 '화학'이라
                             # 정유·화학이 먼저 가져갔고 지주사 필터에도 걸렸다
    "096770": "정유·화학",  # SK이노베이션 — SK온(배터리) 때문에 2차전지 ETF 에
                             # 담겨 그쪽으로 끌려간다. 본업은 정유다
    "103140": "철강·비철",  # 풍산 — 탄약 때문에 방산 ETF 에 담기지만 매출 대부분은
                             # 신동(구리 압연)이다
}

# 지주회사는 사업회사가 따로 상장돼 있으면 지수를 흐린다 — SK 와 SK이노베이션이
# 정유·화학에 나란히 서면 지수가 지주 주가에 끌려간다. 이름으로 걸러내되,
# 금융지주는 지주 자체가 대표주(신한지주·하나금융지주)라 손대지 않는다.
HOLDING_RE = re.compile(r"홀딩스|지주")
# 이름에 표시가 없는 순수 지주회사. 업종과 코드는 실측으로 확인했다.
HOLDING_CODES = {
    "034730": "SK",             # 석유와가스 — 사업회사 SK이노베이션이 따로 있다
    "078930": "GS",             # 석유와가스 — GS리테일·GS건설
    "267250": "HD현대",         # 조선 — HD현대중공업·HD한국조선해양
    "006260": "LS",             # 전기장비 — LS ELECTRIC
    "000240": "한국앤컴퍼니",   # 자동차부품 — 한국타이어앤테크놀로지
}
# 지주 형태지만 사업회사가 비상장이라 이 종목이 유일한 창구인 경우는 남긴다
KEEP_HOLDINGS = {"005490"}      # POSCO홀딩스 — 포스코는 비상장
NO_HOLDING_THEMES = {"금융"}    # 금융지주가 곧 대표주다

# 코드 -> 사명. 업종 상세 페이지에 이미 들어 있어 따로 받지 않는다.
NAMES: dict[str, str] = {}


# '반도체와반도체장비' 안에서 메모리 대형 2사. 나머지(파운드리·장비·소재)는
# 전부 소부장으로 남는다.
SEMI_LARGE = {"005930": "삼성전자", "000660": "SK하이닉스"}

# '우주항공과국방' 안에서 위성·발사체. 나머지는 방산·항공에 남는다.
# 한국항공우주(047810)는 이름 때문에 우주로 넣었었지만 매출은 군용기·기체
# 구조물이다 — 방산에 둔다.
SPACE = {"099320", "451760", "462350", "211270", "189300"}

# 업종에 없는 테마는 해당 테마 ETF 의 구성종목으로 만든다(네이버는 상위 10종목 공개).
ETF_WEIGHTS: dict[str, dict[str, float]] = {}   # 테마 -> {종목: ETF 구성비중}

# 한 종목은 한 테마에만 넣는다. 위에 있는 테마가 먼저 가져간다.
# 대형주를 많이 담는 ETF 테마(로봇·신재생)를 아래에 둬야 현대차가 자동차에,
# LG전자가 전기전자에 남는다. 반대로 전지 밸류체인은 정유·화학보다 2차전지
# 정체성이 뚜렷해 위에 뒀다.
THEME_PRIORITY = [
    # 본업이 뚜렷한 테마를 먼저 — 여기서 대형주가 제 자리를 찾는다
    "반도체", "2차전지", "자동차", "인터넷·게임", "바이오·제약", "화장품",
    # 로봇은 조선·기계/전기전자보다 위여야 순수 로봇주가 살아남는다
    # (로보티즈·레인보우로보틱스의 업종은 '기계', 에스피지는 '전기전자')
    "로봇",
    "전력기기", "전기전자", "조선·기계", "건설",
    # 방산이 우주보다 위여야 한화에어로·LIG·현대로템이 방산에 남는다
    "방산·항공", "우주",
    "반도체 소부장", "금융", "의류·유통", "정유·화학", "철강·비철",
    "신재생",
]

# 네이버 ETF 목록에서 이름으로 실제 코드를 확인해 넣었다. 기억으로 적었던
# 코드는 로봇 ETF 자리에 바이오 ETF 가 들어가 있었다(알테오젠·셀트리온이 로봇
# 테마로 잡혔다). 코드는 숫자 6자리가 아닌 것도 있다(0148J0).
THEME_ETFS = {
    "2차전지": ["305720", "305540", "364980", "462010", "461950"],
    "로봇": ["445290", "0148J0", "469070", "0177X0"],
    "신재생": ["385510", "377990", "367770", "457990"],
    "우주": ["421320", "463250", "0207G0"],
    "방산·항공": ["449450", "0080G0", "490480"],
}


def _dec(r):
    for enc in ("euc-kr", "utf-8"):
        try:
            return r.content.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def _get(url):
    return _dec(requests.get(url, headers=NAVER_HDR, verify=VERIFY, timeout=TIMEOUT))


ITEM_RE = re.compile(r'code=(\d{6})">([^<]+)</a>')


def upjong_members() -> dict[str, list[str]]:
    """{업종명: [종목코드]}. 전 종목을 훑으며 사명(NAMES)도 함께 채운다."""
    ups = re.findall(r'no=(\d+)">([^<]+)</a>', _get(GROUP_URL))
    wanted = [(no, name) for no, name in ups if name in UPJONG_THEME]

    def one(no):
        # 같은 종목 링크가 행마다 두 번 나와 중복이 생긴다
        pairs = ITEM_RE.findall(_get(DETAIL_URL.format(no=no)))
        NAMES.update({c: n.strip() for c, n in pairs})
        return list(dict.fromkeys(c for c, _ in pairs))

    with ThreadPoolExecutor(max_workers=10) as ex:
        codes = list(ex.map(one, [no for no, _ in wanted]))
    return {name: c for (_, name), c in zip(wanted, codes)}


ETF_TABLE_MARK = "구성종목(구성자산)"
ETF_ROW_RE = re.compile(
    r'<td class="ctg">\s*<a href="/item/main\.naver\?code=([0-9A-Z]{6})">([^<]+)</a>.*?'
    r'<td class="per">\s*([\d.]+)%', re.S)


def etf_members(etf_code: str) -> list[tuple[str, float]]:
    """ETF 구성종목과 구성비중(상위 10). [(종목코드, 비중%), ...]

    페이지 전체의 링크를 긁으면 '인기종목'·'동일업종' 같은 다른 영역까지
    들어온다(로봇 ETF 에 셀트리온·한미약품이 섞여 나왔다). 구성종목 표
    안쪽만 파싱한다.
    """
    text = _get(ITEM_URL.format(code=etf_code))
    i = text.find(ETF_TABLE_MARK)
    if i < 0:
        return []
    end = text.find("</table>", i)
    seg = text[i:end if end > 0 else i + 20000]
    out = []
    for code, _name, pct in ETF_ROW_RE.findall(seg):
        if code == etf_code:
            continue
        NAMES.setdefault(code, _name.strip())
        try:
            out.append((code, float(pct)))
        except ValueError:
            continue
    return out


def build_pools() -> dict[str, list[str]]:
    """테마 -> 종목코드 목록. 시가총액 필터는 호출한 쪽에서 적용한다."""
    pools: dict[str, list[str]] = {}

    for upjong, codes in upjong_members().items():
        theme = UPJONG_THEME[upjong]
        pools.setdefault(theme, [])
        for c in codes:
            if theme == "반도체 소부장" and c in SEMI_LARGE:
                pools.setdefault("반도체", []).append(c)
            elif theme == "방산·항공" and c in SPACE:
                pools.setdefault("우주", []).append(c)
            else:
                pools[theme].append(c)

    # 업종에 없는 테마: ETF 구성종목 합집합.
    # 국내 테마 ETF 는 삼성전자 같은 대형주를 상당 비중 담는다. 시가총액으로
    # 줄을 세우면 로봇 테마가 삼성전자 지수가 되어버리므로, ETF 가 실제로 부여한
    # 구성비중을 순위와 가중치로 쓴다.
    with ThreadPoolExecutor(max_workers=8) as ex:
        flat = [(t, e) for t, es in THEME_ETFS.items() for e in es]
        weights: dict[str, dict[str, float]] = {}
        for (theme, _), rows in zip(flat, ex.map(etf_members, [e for _, e in flat])):
            w = weights.setdefault(theme, {})
            for code, pct in rows:
                w[code] = max(w.get(code, 0.0), pct)   # 여러 ETF 에 겹치면 최대치
    # ETF 로 정의되는 테마는 업종에서 온 종목을 섞지 않고 ETF 구성만 쓴다.
    # 섞으면 가중치 기준이 둘(시총/구성비중)이 되어 지수가 흐려진다.
    for theme, w in weights.items():
        pools[theme] = sorted(w, key=lambda c: w[c], reverse=True)
        ETF_WEIGHTS[theme] = w

    # 대형 반도체가 업종 목록에 없을 수도 있어 확실히 채워 넣는다
    pools.setdefault("반도체", []).extend(SEMI_LARGE)

    # 고정 분류를 마지막에 적용 — 업종·ETF 어느 경로로 들어왔든 여기로 옮긴다.
    # 옛 테마의 ETF 가중치는 지운다(남겨두면 weight() 가 옛 기준으로 계산).
    for code, theme in PINNED_THEME.items():
        for t in list(pools):
            pools[t] = [c for c in pools[t] if c != code]
        for t, w in ETF_WEIGHTS.items():
            if t != theme:
                w.pop(code, None)
        if theme:
            pools.setdefault(theme, []).append(code)
            # ETF 로 정의된 테마는 구성비중이 곧 순위다. ETF 가 담지 않은 종목을
            # 사람이 넣으면 비중이 0 이 되어 맨 뒤로 밀리므로(OCI홀딩스가
            # 신재생 표에서 사라졌다) 그 테마 비중의 중간값을 준다.
            w = ETF_WEIGHTS.get(theme)
            if w and code not in w:
                w[code] = sorted(w.values())[len(w) // 2]

    # 한 종목은 한 테마에만. 우선순위가 높은 테마가 먼저 가져간다.
    claimed: set[str] = set()

    # 위성·발사체는 우주가 먼저 가져간다. 방산을 우주보다 위에 두지 않으면
    # 한화에어로·LIG 가 우주로 빨려가고, 아래에 두면 한국항공우주가 방산으로
    # 가버려 우주에 소재주만 남는다. 명시 목록에만 예외를 준다.
    if "우주" in pools:
        head = [c for c in SPACE if c in pools["우주"]]
        pools["우주"] = head + [c for c in pools["우주"] if c not in head]
        claimed.update(head)
    space_head = set(pools.get("우주", [])[:len(SPACE)]) & SPACE
    for theme in THEME_PRIORITY + [t for t in pools if t not in THEME_PRIORITY]:
        if theme not in pools:
            continue
        keep = space_head if theme == "우주" else set()
        pools[theme] = [c for c in pools[theme] if c not in claimed or c in keep]
        claimed.update(pools[theme])

    # 우선주 제외. 보통주는 코드가 0 으로 끝나고 우선주는 5·7 로 끝난다
    # (삼성전자우 005935 가 '반도체 소부장' 1위로 올라왔다).
    # 단, 고정 분류(PINNED_THEME)로 명시된 우선주는 예외 — 삼성전자우는
    # 반도체 테마에 일부러 넣는다.
    pinned_keep = {c for c, t in PINNED_THEME.items() if t}

    def keep(theme, code):
        if code in pinned_keep or code in KEEP_HOLDINGS:
            return True
        if not (code.endswith("0")):
            return False
        if theme in NO_HOLDING_THEMES:
            return True
        return not (code in HOLDING_CODES or HOLDING_RE.search(NAMES.get(code, "")))

    return {t: [c for c in dict.fromkeys(cs) if keep(t, c)]
            for t, cs in pools.items() if cs}
