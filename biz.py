"""티커 -> 한두 단어 사업 설명. 'NEM' 만 봐서는 금 광산인지 알 수 없다.

두 갈래로 만든다.
1) BIZ 사전 — S&P 섹터별 상위권 종목을 손으로 적었다. 야후가 주는 산업 분류는
   Tesla 를 'Auto Manufacturers'(자동차 제조)라고만 해서 '전기차'라는 핵심을
   놓친다. 실제로 브리핑에 나오는 종목은 손으로 적는 편이 정확하다.
2) 야후 산업 분류 폴백 — 섹터 구성이 바뀌어 사전에 없는 종목이 올라올 때
   빈칸으로 두지 않기 위한 안전망이다.

한국 종목(6자리 숫자 코드)은 사명 자체가 사업을 말해주므로 대상이 아니다.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from sources import TIMEOUT, _crumb_session

BIZ = {
    # 기술
    "AAPL": "스마트폰", "NVDA": "AI 반도체", "MSFT": "클라우드·SW",
    "AVGO": "통신 반도체", "MU": "메모리 반도체", "ORCL": "기업용 DB",
    "CRM": "고객관리 SW", "AMD": "CPU·GPU", "CSCO": "네트워크 장비",
    "ADBE": "콘텐츠 SW", "PLTR": "데이터 분석", "INTC": "반도체 제조",
    "TXN": "아날로그 반도체", "QCOM": "모바일 반도체", "AMAT": "반도체 장비",
    "LRCX": "반도체 장비", "KLAC": "반도체 검사장비", "ACN": "IT 컨설팅",
    "IBM": "기업 IT", "NOW": "업무 자동화 SW", "INTU": "세무·회계 SW",
    "PANW": "보안 SW", "CRWD": "보안 SW", "FTNT": "보안 SW",
    "ANET": "데이터센터 스위치", "DELL": "서버·PC", "HPQ": "PC·프린터",
    "ADI": "아날로그 반도체", "SNPS": "반도체 설계SW", "CDNS": "반도체 설계SW",
    "MCHP": "마이크로컨트롤러", "ON": "전력 반도체", "NXPI": "차량용 반도체",
    "GLW": "광섬유·유리", "APH": "커넥터", "TEL": "커넥터", "MSI": "무선통신 장비",
    # 커뮤니케이션
    "GOOGL": "검색·광고", "GOOG": "검색·광고", "META": "소셜미디어",
    "NFLX": "스트리밍", "VZ": "통신", "TMUS": "통신", "T": "통신",
    "CMCSA": "케이블·방송", "DIS": "미디어·테마파크", "EA": "게임",
    "TTWO": "게임", "WBD": "미디어", "CHTR": "케이블", "LYV": "공연 기획",
    "OMC": "광고 대행", "IPG": "광고 대행", "FOXA": "방송", "NWSA": "신문·미디어",
    "MTCH": "데이팅 앱", "PINS": "소셜미디어",
    # 경기소비재
    "AMZN": "전자상거래", "TSLA": "전기차", "HD": "건축자재 유통",
    "MCD": "패스트푸드", "TJX": "의류 할인점", "LOW": "건축자재 유통",
    "BKNG": "여행 예약", "NKE": "스포츠 의류", "SBUX": "커피 체인",
    "CMG": "멕시칸 외식", "ORLY": "자동차부품 유통", "AZO": "자동차부품 유통",
    "MAR": "호텔", "HLT": "호텔", "GM": "자동차", "F": "자동차",
    "ABNB": "숙박 중개", "DHI": "주택 건설", "LEN": "주택 건설",
    "ROST": "의류 할인점", "YUM": "프랜차이즈 외식", "DASH": "음식 배달",
    "RCL": "크루즈", "CCL": "크루즈", "EBAY": "온라인 장터", "LULU": "요가복",
    "DECK": "신발", "GRMN": "GPS 기기", "APTV": "자동차 부품", "LVS": "카지노",
    # 필수소비재
    "WMT": "대형 마트", "COST": "창고형 할인점", "KO": "음료", "PG": "생활용품",
    "PM": "담배", "PEP": "음료·스낵", "MO": "담배", "MDLZ": "과자",
    "CL": "생활용품", "KMB": "위생용품", "GIS": "시리얼·식품", "KHC": "가공식품",
    "STZ": "주류", "SYY": "식자재 유통", "KR": "식료품 체인", "KDP": "음료",
    "HSY": "초콜릿", "MNST": "에너지드링크", "CHD": "생활용품", "TSN": "육류",
    "ADM": "곡물 가공", "TAP": "맥주", "CAG": "가공식품", "DG": "저가 잡화점",
    "DLTR": "저가 잡화점", "EL": "화장품", "CLX": "세제",
    # 에너지
    "XOM": "종합 석유", "CVX": "종합 석유", "COP": "원유·가스 생산",
    "MPC": "정유", "VLO": "정유", "PSX": "정유", "EOG": "원유·가스 생산",
    "SLB": "유전 서비스", "OXY": "원유·가스 생산", "WMB": "가스 파이프라인",
    "KMI": "가스 파이프라인", "OKE": "가스 파이프라인", "HES": "원유 탐사",
    "BKR": "유전 장비", "HAL": "유전 서비스", "DVN": "셰일 개발",
    "FANG": "셰일 개발", "TRGP": "가스 처리", "EQT": "천연가스",
    "CTRA": "원유·가스 생산", "APA": "원유 탐사",
    # 금융
    "BRK-B": "복합 지주", "JPM": "은행", "V": "카드 결제망", "MA": "카드 결제망",
    "BAC": "은행", "WFC": "은행", "GS": "투자은행", "MS": "투자은행",
    "SPGI": "신용평가", "AXP": "신용카드", "C": "은행", "BLK": "자산운용",
    "SCHW": "증권", "CB": "손해보험", "PGR": "자동차 보험", "MMC": "보험 중개",
    "AON": "보험 중개", "AJG": "보험 중개", "CME": "선물 거래소", "ICE": "거래소",
    "NDAQ": "거래소", "MCO": "신용평가", "PYPL": "온라인 결제", "USB": "은행",
    "PNC": "은행", "TFC": "은행", "COF": "신용카드", "DFS": "신용카드",
    "AIG": "손해보험", "MET": "생명보험", "PRU": "생명보험", "AFL": "보험",
    "TRV": "손해보험", "ALL": "손해보험", "HIG": "손해보험", "KKR": "사모펀드",
    "BX": "사모펀드", "FI": "결제 처리", "GPN": "결제 처리", "BK": "수탁은행",
    "STT": "수탁은행",
    # 헬스케어
    "LLY": "비만·당뇨약", "JNJ": "제약·의료기기", "ABBV": "면역 치료제",
    "UNH": "건강보험", "MRK": "항암제", "ABT": "의료기기", "TMO": "실험 장비",
    "AMGN": "바이오 신약", "PFE": "제약", "ISRG": "수술 로봇", "DHR": "진단 장비",
    "BSX": "의료기기", "SYK": "정형외과 기기", "VRTX": "희귀질환 치료제",
    "MDT": "의료기기", "GILD": "항바이러스제", "CI": "건강보험", "ELV": "건강보험",
    "CVS": "약국·보험", "MCK": "의약품 유통", "COR": "의약품 유통",
    "REGN": "항체 신약", "ZTS": "동물 의약품", "BDX": "의료 소모품",
    "HCA": "병원 운영", "BMY": "제약", "EW": "심장 판막", "A": "분석 장비",
    "IQV": "임상시험 대행", "GEHC": "의료 영상", "MRNA": "mRNA 백신",
    "BIIB": "뇌질환 치료제",
    # 산업재
    "GE": "항공 엔진", "CAT": "건설 장비", "RTX": "방산·항공부품",
    "GEV": "발전 설비", "UNP": "철도", "HON": "산업 복합", "BA": "항공기",
    "LMT": "방산", "DE": "농기계", "ADP": "급여 대행", "ETN": "전력 관리",
    "UPS": "특송 물류", "NOC": "방산", "GD": "방산·조선", "CSX": "철도",
    "NSC": "철도", "WM": "폐기물 처리", "RSG": "폐기물 처리",
    "EMR": "산업 자동화", "ROK": "산업 자동화", "ITW": "산업 부품",
    "PH": "유압 부품", "TT": "공조 설비", "CARR": "공조 설비",
    "JCI": "건물 설비", "MMM": "산업 소재", "FDX": "특송 물류",
    "LHX": "방산 통신", "PCAR": "대형 트럭", "CMI": "디젤 엔진",
    "PWR": "전력망 시공", "URI": "장비 렌털", "AME": "계측 기기",
    "OTIS": "엘리베이터", "DAL": "항공사", "UAL": "항공사", "LUV": "항공사",
    "VRSK": "데이터 분석", "FAST": "산업부품 유통", "GWW": "산업자재 유통",
    "HWM": "항공 부품", "TDG": "항공 부품", "AXON": "테이저·보디캠",
    "IR": "압축기", "DOV": "산업 기계", "EFX": "신용 정보",
    # 소재
    "LIN": "산업용 가스", "APD": "산업용 가스", "NEM": "금 광산",
    "FCX": "구리 광산", "SHW": "페인트", "PPG": "페인트", "ECL": "위생·수처리",
    "DOW": "기초 화학", "DD": "특수 소재", "LYB": "석유화학", "EMN": "특수 화학",
    "CE": "화학 소재", "NUE": "철강", "STLD": "철강", "VMC": "골재·시멘트",
    "MLM": "골재·시멘트", "CTVA": "종자·농약", "IFF": "향료·소재",
    "PKG": "종이 포장", "IP": "종이 포장", "SW": "종이 포장", "AMCR": "포장재",
    "BALL": "알루미늄 캔", "AVY": "라벨·접착제", "ALB": "리튬", "MOS": "비료",
    "CF": "질소 비료",
    # 부동산(리츠)
    "WELL": "시니어주거 리츠", "PLD": "물류창고 리츠", "EQIX": "데이터센터 리츠",
    "DLR": "데이터센터 리츠", "SPG": "쇼핑몰 리츠", "AMT": "통신탑 리츠",
    "CCI": "통신탑 리츠", "SBAC": "통신탑 리츠", "PSA": "셀프 스토리지",
    "EXR": "셀프 스토리지", "O": "소매임대 리츠", "CBRE": "부동산 중개",
    "VICI": "카지노 부동산", "AVB": "아파트 리츠", "EQR": "아파트 리츠",
    "MAA": "아파트 리츠", "UDR": "아파트 리츠", "CPT": "아파트 리츠",
    "ESS": "아파트 리츠", "INVH": "임대 주택", "VTR": "헬스케어 리츠",
    "DOC": "의료시설 리츠", "ARE": "연구시설 리츠", "BXP": "오피스 리츠",
    "HST": "호텔 리츠", "KIM": "상가 리츠", "WY": "목재 리츠",
    "IRM": "문서 보관",
    # 유틸리티
    "NEE": "전력·신재생", "SO": "전력", "DUK": "전력", "CEG": "원자력 발전",
    "AEP": "전력", "D": "전력·가스", "SRE": "전력·가스", "EXC": "전력 배전",
    "XEL": "전력", "PCG": "전력·가스", "ED": "전력·가스", "VST": "발전 사업",
    "NRG": "발전·소매", "WEC": "전력·가스", "PEG": "전력·가스", "EIX": "전력",
    "AEE": "전력·가스", "DTE": "전력·가스", "ETR": "전력", "FE": "전력",
    "PPL": "전력", "CMS": "전력·가스", "CNP": "전력·가스", "LNT": "전력",
    "EVRG": "전력", "ATO": "가스 공급", "NI": "가스 공급", "AWK": "상수도",
}

# 야후 산업 분류 -> 한국어. 사전에 없는 종목의 안전망이라 넓고 얕게 적는다.
INDUSTRY_KO = {
    "Aerospace & Defense": "방산·항공", "Agricultural Inputs": "비료·농자재",
    "Airlines": "항공사", "Apparel Manufacturing": "의류 제조",
    "Apparel Retail": "의류 소매", "Asset Management": "자산운용",
    "Auto Manufacturers": "자동차", "Auto Parts": "자동차 부품",
    "Banks - Diversified": "은행", "Banks - Regional": "지역 은행",
    "Beverages - Non-Alcoholic": "음료", "Beverages - Wineries & Distilleries": "주류",
    "Biotechnology": "바이오 신약", "Building Products & Equipment": "건축 자재",
    "Capital Markets": "증권·자본시장", "Chemicals": "화학",
    "Communication Equipment": "통신 장비", "Computer Hardware": "컴퓨터 하드웨어",
    "Confectioners": "제과", "Conglomerates": "복합 기업",
    "Consumer Electronics": "가전·전자", "Copper": "구리 광산",
    "Credit Services": "카드·여신", "Discount Stores": "할인점",
    "Drug Manufacturers - General": "제약",
    "Drug Manufacturers - Specialty & Generic": "제네릭 의약품",
    "Electrical Equipment & Parts": "전기 장비",
    "Electronic Components": "전자 부품",
    "Engineering & Construction": "건설·엔지니어링",
    "Entertainment": "미디어·엔터", "Farm & Heavy Construction Machinery": "중장비",
    "Financial Data & Stock Exchanges": "거래소·금융정보",
    "Food Distribution": "식자재 유통", "Footwear & Accessories": "신발·잡화",
    "Gold": "금 광산", "Grocery Stores": "식료품 체인",
    "Healthcare Plans": "건강보험", "Home Improvement Retail": "건축자재 유통",
    "Household & Personal Products": "생활용품",
    "Industrial Distribution": "산업자재 유통",
    "Information Technology Services": "IT 서비스",
    "Insurance - Diversified": "보험", "Insurance - Life": "생명보험",
    "Insurance - Property & Casualty": "손해보험",
    "Insurance Brokers": "보험 중개", "Integrated Freight & Logistics": "물류",
    "Internet Content & Information": "인터넷 콘텐츠",
    "Internet Retail": "온라인 소매", "Lodging": "호텔",
    "Medical Devices": "의료기기", "Medical Distribution": "의약품 유통",
    "Medical Instruments & Supplies": "의료 기구",
    "Oil & Gas Equipment & Services": "유전 서비스",
    "Oil & Gas Integrated": "종합 석유",
    "Oil & Gas Midstream": "가스 파이프라인",
    "Oil & Gas Refining & Marketing": "정유",
    "Oil & Gas E&P": "원유·가스 생산", "Packaged Foods": "가공식품",
    "Packaging & Containers": "포장재", "Paper & Paper Products": "제지",
    "Pollution & Treatment Controls": "환경 설비",
    "REIT - Diversified": "복합 리츠", "REIT - Healthcare Facilities": "헬스케어 리츠",
    "REIT - Hotel & Motel": "호텔 리츠", "REIT - Industrial": "물류창고 리츠",
    "REIT - Office": "오피스 리츠", "REIT - Residential": "주거 리츠",
    "REIT - Retail": "상가 리츠", "REIT - Specialty": "특수 리츠",
    "Railroads": "철도", "Real Estate Services": "부동산 서비스",
    "Rental & Leasing Services": "렌털·리스",
    "Residential Construction": "주택 건설", "Restaurants": "외식",
    "Scientific & Technical Instruments": "계측 기기",
    "Semiconductor Equipment & Materials": "반도체 장비·소재",
    "Semiconductors": "반도체", "Software - Application": "응용 SW",
    "Software - Infrastructure": "인프라 SW",
    "Specialty Chemicals": "특수 화학", "Specialty Industrial Machinery": "산업 기계",
    "Specialty Retail": "전문 소매", "Steel": "철강",
    "Telecom Services": "통신", "Tobacco": "담배",
    "Travel Services": "여행 서비스", "Trucking": "화물 운송",
    "Utilities - Diversified": "전력·가스",
    "Utilities - Regulated Electric": "전력",
    "Utilities - Regulated Gas": "가스 공급",
    "Utilities - Regulated Water": "상수도",
    "Utilities - Independent Power Producers": "발전 사업",
    "Waste Management": "폐기물 처리",
}

PROFILE_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
US_TICKER = re.compile(r"^[A-Z][A-Z.\-]*$")     # 한국 종목(005930)은 제외
_cache: dict[str, str] = {}


def _industry(session, crumb, ticker):
    try:
        r = session.get(PROFILE_URL.format(sym=ticker),
                        params={"modules": "assetProfile", "crumb": crumb},
                        timeout=TIMEOUT)
        r.raise_for_status()
        res = r.json()["quoteSummary"]["result"]
        return (res[0]["assetProfile"].get("industry") or "") if res else ""
    except Exception:                          # noqa: BLE001
        return ""


def describe(tickers) -> dict[str, str]:
    """{티커: 사업 설명}. 사전에 없으면 야후 산업 분류로 메운다."""
    out, unknown = {}, []
    for t in tickers:
        if t in BIZ:
            out[t] = BIZ[t]
        elif t in _cache:
            out[t] = _cache[t]
        elif US_TICKER.match(t or ""):
            unknown.append(t)

    if not unknown:
        return out
    session, crumb = _crumb_session()
    if not session:
        return out
    with ThreadPoolExecutor(max_workers=6) as ex:
        got = list(ex.map(lambda t: _industry(session, crumb, t), unknown))
    for t, ind in zip(unknown, got):
        # 매핑에 없는 산업명은 영문 그대로라도 쓴다. 빈칸보다 낫다.
        label = INDUSTRY_KO.get(ind, ind)
        _cache[t] = label
        if label:
            out[t] = label
    if unknown:
        named = sum(1 for t in unknown if out.get(t))
        print(f"    [biz] 사전에 없는 {len(unknown)}종목 중 {named}건 야후 산업분류로 보완")
    return out
