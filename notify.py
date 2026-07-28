"""메시지 조립 + 텔레그램 발송."""
from __future__ import annotations

import datetime as dt
import os

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
        parts = [f"{n} ({idx[n]['chg_pct']:+.2f}%)" for n in ("Dow", "S&P500", "Nasdaq") if n in idx]
        lines.append("□ 미국증시: " + ", ".join(parts))

    sectors = data.get("sectors") or []
    if sectors:
        lines.append("□ 섹터별 등락:")
        for s in sectors:
            lines.append(f"    · {s['name']} {s['chg_pct']:+.2f}%")

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


def _post(token, method, **kw):
    r = requests.post(API.format(token=token, method=method), timeout=TIMEOUT, **kw)
    if not r.ok:
        raise RuntimeError(f"{method} 실패 {r.status_code}: {r.text[:300]}")
    return r.json()


CAPTION_MAX = 1024      # 텔레그램 미디어 캡션 상한


def send(token: str, chat_id: str, text: str, charts: list) -> None:
    """본문 먼저, 그다음 차트를 10장씩 앨범으로.

    charts 는 {"path": ..., "caption": ...} 목록. 캡션이 있으면 사진마다 붙는다
    (섹터 차트의 상위 5개 종목 등락).
    """
    _post(token, "sendMessage",
          data={"chat_id": chat_id, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": True})

    for i in range(0, len(charts), ALBUM_MAX):
        batch = charts[i:i + ALBUM_MAX]
        files, media = {}, []
        for n, item in enumerate(batch):
            path = item["path"] if isinstance(item, dict) else item
            caption = item.get("caption") if isinstance(item, dict) else None
            key = f"photo{n}"
            files[key] = (os.path.basename(path), open(path, "rb"), "image/png")
            entry = {"type": "photo", "media": f"attach://{key}"}
            if caption:
                entry["caption"] = caption[:CAPTION_MAX]
                entry["parse_mode"] = "HTML"
            media.append(entry)
        try:
            import json
            _post(token, "sendMediaGroup",
                  data={"chat_id": chat_id, "media": json.dumps(media)}, files=files)
        finally:
            for _, fh, _ in files.values():
                fh.close()
