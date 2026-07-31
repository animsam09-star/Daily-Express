"""메시지 조립 + 텔레그램 발송."""
from __future__ import annotations

import datetime as dt
import json
import os
import time

import requests

API = "https://api.telegram.org/bot{token}/{method}"
ALBUM_MAX = 10          # 텔레그램 sendMediaGroup 상한
TIMEOUT = 90


def build_message(data) -> str:
    """보내주신 샘플과 같은 '□ 항목: 값(변동)' 형식."""
    today = dt.date.today()
    lines = [f"<b>📊 마켓 브리핑  {today:%Y.%m.%d} ({'월화수목금토일'[today.weekday()]})</b>", ""]

    idx = data.get("indices") or {}
    if idx:
        parts = [f"{n} ({idx[n]['chg_pct']:+.1f}%)" for n in ("Dow", "S&P500", "Nasdaq") if n in idx]
        lines.append("□ 미국증시: " + ", ".join(parts))

    sectors = data.get("sectors") or []
    if sectors:
        lines.append("□ 섹터별 등락:")
        for s in sectors:
            lines.append(f"    · {s['name']} {s['chg_pct']:+.1f}%")

    now = data.get("ust_now") or {}
    if now:
        parts = [
            f"{now[s]['label']} {now[s]['yield']:.3f}%({now[s]['chg_bp']:+.1f}bp)"
            for s in ("US2Y", "US10Y", "US30Y") if s in now
        ]
        lines.append("□ 미국국채: " + ", ".join(parts))

    fx = data.get("fx")
    if fx:
        lines.append(f"□ 원/달러 환율 {fx['last']:,.1f}원({fx['chg']:+.1f}원)")

    dom = data.get("domestic") or {}
    corp, spread = dom.get("corp_aa3y"), dom.get("spread")
    if corp and corp.get("last") is not None:
        lines.append(f"□ 회사 AA- 3년 {corp['last']:.2f}%({corp['chg']:+.0f}bp)")
    if spread and spread.get("last") is not None:
        lines.append(f"□ 회사 AA- 3년 Spread {spread['last']:.1f}bp({spread['chg']:+.0f}bp)")

    lines.append("")
    lines.append("<i>미국 지표는 전일 종가, 국내 금리는 전영업일 기준입니다.</i>")
    lines.append("<i>국내 금리는 공시 소수 2자리 기준이라 bp 변동에 ±1bp 오차가 있습니다.</i>")

    errors = data.get("errors") or []
    if errors:
        lines.append("")
        lines.append("⚠️ <b>일부 항목 수집 실패</b>")
        for e in errors:
            lines.append(f"    · {e}")

    return "\n".join(lines)


SEND_GAP = 1.2          # 메시지 사이 간격(초). 같은 방에 초당 1건이 안전선이다
MAX_RETRY = 4


def _post(token, method, files=None, **kw):
    """텔레그램 호출. 429(속도 제한)와 5xx 는 쉬었다 다시 보낸다.

    22개 메시지를 연달아 던지면 429 가 난다. 그때 텔레그램이 알려주는
    retry_after 만큼 기다렸다 재시도해야 한다. 파일은 한 번 읽으면 커서가
    끝에 가 있으므로 재시도할 때마다 처음으로 되돌린다.
    """
    last = None
    for attempt in range(MAX_RETRY):
        if files:
            for f in files.values():
                try:
                    f[1].seek(0)
                except (AttributeError, OSError):
                    pass
        r = requests.post(API.format(token=token, method=method),
                          files=files, timeout=TIMEOUT, **kw)
        if r.ok:
            return r.json()

        last = f"{method} 실패 {r.status_code}: {r.text[:300]}"
        if r.status_code == 429:
            try:
                wait = float(r.json()["parameters"]["retry_after"])
            except Exception:                  # noqa: BLE001
                wait = 5.0
            print(f"    [telegram] 속도 제한 — {wait:.0f}초 대기 후 재시도")
            time.sleep(wait + 0.5)
            continue
        if 500 <= r.status_code < 600:
            time.sleep(2 * (attempt + 1))
            continue
        break                                   # 400 등은 재시도해도 같다
    raise RuntimeError(last or f"{method} 실패")


# 캡션 길이는 여기서 자르지 않는다. 텔레그램 상한(1024자)은 태그를 뺀
# '보이는 글자수' 기준이고 render.holdings_caption 이 그 기준으로 1000자
# 이내를 보장한다. 태그 포함 원시 문자열을 1024자에서 자르면 <b> 같은
# 태그가 중간에서 잘려 텔레그램이 메시지 전체를 400 으로 거부한다
# (실제로 캡션이 길어진 뒤 섹터 7장이 전부 그렇게 떨어졌다).


def _send_photo(token, chat_id, path, caption=None):
    """사진 한 장. 캡션이 사진 바로 아래 붙어 한 묶음으로 읽힌다."""
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
        data["parse_mode"] = "HTML"
    with open(path, "rb") as fh:
        _post(token, "sendPhoto",
              data=data, files={"photo": (os.path.basename(path), fh, "image/png")})


def _send_album(token, chat_id, batch):
    """사진 여러 장을 한 앨범으로. 텔레그램 앨범은 2~10장만 허용한다."""
    if len(batch) == 1:                     # 1장짜리 앨범은 API 가 거부한다
        _send_photo(token, chat_id, batch[0]["path"], batch[0].get("caption"))
        return

    files, media = {}, []
    for n, item in enumerate(batch):
        key = f"photo{n}"
        files[key] = (os.path.basename(item["path"]),
                      open(item["path"], "rb"), "image/png")
        entry = {"type": "photo", "media": f"attach://{key}"}
        if item.get("caption"):
            entry["caption"] = item["caption"]
            entry["parse_mode"] = "HTML"
        media.append(entry)
    try:
        _post(token, "sendMediaGroup",
              data={"chat_id": chat_id, "media": json.dumps(media)}, files=files)
    finally:
        for _, fh, _ in files.values():
            fh.close()


def send(token: str, chat_id: str, text: str, charts: list) -> None:
    """본문 → 지표 차트(앨범) → 섹터 차트(한 장씩).

    charts 는 {"path", "caption", "solo"} 목록.

    solo=True 인 항목은 앨범에 묶지 않고 개별 전송한다. 앨범(미디어 그룹)은
    사진별 캡션을 앨범 화면에 표시하지 않아서, 캡션이 본문인 섹터 차트
    (상위 5종목 등락 + 관련 이슈)가 사진을 눌러야만 보이기 때문이다.
    """
    _post(token, "sendMessage",
          data={"chat_id": chat_id, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": True})

    grouped = [c for c in charts if not c.get("solo")]
    solos = [c for c in charts if c.get("solo")]

    # 하나가 실패해도 나머지는 계속 보낸다. 예전에는 첫 실패에서 전체가 멈춰
    # 절반만 도착했다. 실패는 모아 두었다가 마지막에 알린다.
    failed = []

    def attempt(label, fn, *a):
        try:
            fn(*a)
        except Exception as e:                 # noqa: BLE001
            print(f"    [telegram] {label} 실패: {type(e).__name__}: {e}")
            failed.append(label)
        time.sleep(SEND_GAP)

    for i in range(0, len(grouped), ALBUM_MAX):
        batch = grouped[i:i + ALBUM_MAX]
        attempt(f"앨범 {i // ALBUM_MAX + 1}", _send_album, token, chat_id, batch)

    for c in solos:
        name = os.path.splitext(os.path.basename(c["path"]))[0]
        attempt(name, _send_photo, token, chat_id, c["path"], c.get("caption"))

    if failed:
        raise RuntimeError(f"{len(failed)}건 전송 실패: {', '.join(failed)}")
