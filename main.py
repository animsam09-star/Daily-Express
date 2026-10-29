"""매일 아침 마켓 브리핑 — 수집 → 차트 → 텔레그램 발송.

로컬 점검(발송 없이 결과만 보기):
    python main.py --dry-run
실제 발송(GitHub Actions에서 자동 실행):
    TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python main.py
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import concall
import news
import notify
import profile
import render
import sources
import webgen


# 개요 캐시가 놓이는 곳. 워크플로가 data-us 브랜치에서 받아 두고 다시 올린다.
OVERVIEW_CACHE = os.environ.get("OVERVIEW_CACHE", "data/overviews_us.json")
# 어닝콜 요약 캐시. 콜은 분기에 한 번뿐이라 캐시가 거의 전부를 해결한다.
CALL_CACHE = os.environ.get("CALL_CACHE", "data/concalls_us.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="발송하지 않고 메시지·차트만 만들어 확인")
    # 웹을 먼저 갱신하고 텔레그램을 뒤에 보내기 위한 두 단계 실행.
    # --defer-send 가 본문·차트를 저장해 두면, Cloudflare 배포가 끝난 뒤
    # --send-pending 이 그것만 읽어 보낸다(수집을 두 번 하지 않는다).
    ap.add_argument("--defer-send", action="store_true",
                    help="발송하지 않고 본문·차트를 저장(배포 뒤 --send-pending)")
    ap.add_argument("--send-pending", action="store_true",
                    help="저장해 둔 브리핑을 발송")
    ap.add_argument("--outdir", default="out", help="차트 저장 폴더")
    args = ap.parse_args()

    if args.send_pending:
        token, chat_id = (os.environ.get("TELEGRAM_BOT_TOKEN"),
                          os.environ.get("TELEGRAM_CHAT_ID"))
        if not (token and chat_id):
            print("[발송] 텔레그램 시크릿이 비어 있습니다", file=sys.stderr)
            return 2
        print("[발송] 저장해 둔 브리핑 전송...")
        rc = notify.send_pending(args.outdir, token, chat_id)
        print("    완료.")
        return rc

    t0 = time.time()
    print("[1/3] 데이터 수집...")
    data = sources.collect_all()
    for e in data.get("errors") or []:
        print(f"    ! {e}")

    # 기업 개요를 한글로 다시 쓴다. 전에는 news.translate_profiles 로 매일
    # 번역했는데 두 가지가 아까웠다 — ① 400자로 자른 뒤 번역해서 뒤쪽 사업
    # 내용이 통째로 사라졌고, ② 캐시가 없어 종목 200개를 날마다 다시 돌렸다
    # (같은 회사 설명이 날마다 조금씩 달라지기도 했다).
    # 이제 원문 전체를 재료로 한 번만 쓰고 캐시한다.
    profs = {h["ticker"]: (h.get("profile") or {})
             for hs in (data.get("holdings") or {}).values() for h in hs}
    ko = profile.build(profs, OVERVIEW_CACHE)
    for hs in (data.get("holdings") or {}).values():
        for h in hs:
            text = ko.get(h["ticker"])
            if text and h.get("profile"):
                h["profile"]["summary_ko"] = text

    # 어닝콜 전문 요약. 주도주·관심종목에 더해, 최근 실적을 발표한 종목도
    # 넣는다 — 실적 발표 카드에서 눌러 들어온 종목의 컨콜 탭이 비어 있으면
    # (VLO 가 그랬다) 카드가 가리키는 것과 탭이 담는 것이 어긋난다.
    # 전문이 5만 자라 종목마다 한 번씩 물어야 하므로 그 둘로 끊는다.
    try:
        picks, pnames = set(), {}
        for hs in (data.get("holdings") or {}).values():
            for h in hs:
                pnames[h["ticker"]] = h.get("name") or h["ticker"]
                if h.get("pick"):
                    picks.add(h["ticker"])
        held = {h["ticker"] for hs in (data.get("holdings") or {}).values()
                for h in hs}
        for r in ((data.get("earnings") or {}).get("done") or []):
            if r.get("ticker") in held:
                picks.add(r["ticker"])
        data["calls"] = concall.build(picks, pnames, CALL_CACHE)
    except Exception as e:                     # noqa: BLE001
        print(f"    ! 어닝콜 요약 실패(계속): {type(e).__name__}: {e}")
        data["calls"] = {}

    # 급등락 종목의 관련 뉴스 → Claude 요약 + 섹터별 등락 종합 코멘트.
    # 실패해도 발송은 계속된다. (sources 를 import 하므로 순환을 피해 여기서 붙인다)
    data["notes"], data["sector_notes"] = news.build(
        data.get("holdings") or {},
        sectors=data.get("sectors"),
        macro=news.macro_context(data))

    print("[2/3] 차트 생성...")
    charts = render.build_all(data, args.outdir)
    print(f"    {len(charts)}장: " + ", ".join(os.path.basename(c["path"]) for c in charts))

    # 웹 대시보드(Cloudflare Pages 배포용). 실패해도 발송은 계속된다.
    try:
        webgen.build_us(data, os.path.join("site", "us.html"))
        webgen.write_index("site")
        print("    웹 대시보드: site/us.html")
    except Exception as e:                     # noqa: BLE001
        print(f"    ! 웹 대시보드 생성 실패(발송은 계속): {type(e).__name__}: {e}")

    text = notify.build_message(data)
    print("\n" + "-" * 60 + "\n" + text + "\n" + "-" * 60 + "\n")

    # 진단 한 줄. 로그 맨 끝에 둔다 — 앞쪽에 두면 뉴스·차트 출력에 묻혀
    # 나중에 로그 꼬리만 봐서는 확인할 수 없다(실측으로 계속 못 꺼냈다).
    sn = data.get("sector_notes") or {}
    print(f"[진단] 종목 메모 {len(data.get('notes') or {})}건 / "
          f"섹터 코멘트 {len(sn)}건"
          + (" — " + ", ".join(sorted(sn)) if sn else " — 없음"))
    if news.STATUS:
        print(f"[진단] {news.STATUS}")

    # 선정 내역. 스크리닝(추세·반등·초동)은 웹 전용이라 위 캡션에 안 나오고,
    # 여기 없으면 제대로 뽑혔는지 확인할 길이 아예 없다 — 로그는 꼬리만
    # 꺼낼 수 있고 웹 페이지는 러너 밖에서 열리지 않는다.
    kinds, dead = {}, []
    for rows in (data.get("holdings") or {}).values():
        for h in rows:
            if h.get("pick"):
                kinds.setdefault(h["pick"], []).append(h["ticker"])
            if h.get("price") is None:
                dead.append(h["ticker"])
    # ::notice:: 로 띄우면 작업 로그 전체를 받지 않고 주석 API 로만 꺼낼 수
    # 있다. 로그는 꼬리만 받을 수 있는데 이 줄까지 닿는 데만 230줄이 든다.
    # 연간 실적이 실제로 붙었는지. 분기만 있고 연간이 0이면 수집 경로가
    # 틀린 것인데, 화면을 열어 보기 전에는 모른다.
    hs = [h for rows in (data.get("holdings") or {}).values() for h in rows]
    nq = sum(1 for h in hs if h.get("quarters"))
    na = sum(1 for h in hs if h.get("annuals"))
    print(f"::notice::[진단] 실적 — 분기 {nq}/{len(hs)}종목 · 연간 {na}/{len(hs)}종목")
    print("::notice::[진단] 선정 — " + (" / ".join(
        f"{k} {len(v)}건({', '.join(v[:8])}{'…' if len(v) > 8 else ''})"
        for k, v in sorted(kinds.items())) or "없음"))
    # 실적 발표·컨콜 카드가 실제로 채워졌는지. 캘린더가 막히거나(무료 등급)
    # 티커가 하나도 안 겹치면 카드가 통째로 안 뜨는데, 페이지를 열어 보기
    # 전에는 그게 '발표가 없어서' 인지 '못 받아서' 인지 구분이 안 된다.
    e = data.get("earnings") or {}
    held = {h["ticker"] for h in hs}
    ed = [r for r in (e.get("done") or []) if r["ticker"] in held]
    eu = [r for r in (e.get("upcoming") or []) if r["ticker"] in held]
    print(f"::notice::[진단] 실적 발표 — 들고 있는 종목 중 발표 {len(ed)}건"
          f" · 예정 {len(eu)}건"
          + (f" (최근: {', '.join(r['ticker'] for r in ed[:8])})" if ed else "")
          + ("" if (e.get("done") or e.get("upcoming"))
             else " — 캘린더가 비어 있다"))
    calls = data.get("calls") or {}
    print(f"::notice::[진단] 어닝콜 요약 {len(calls)}건"
          + (f" ({', '.join(sorted(calls)[:10])})" if calls else ""))
    if sources.EARLY_REJECT:
        top = sorted(sources.EARLY_REJECT.items(), key=lambda kv: -kv[1])
        print("::notice::[진단] 초동 탈락 사유 — "
              + ", ".join(f"{k} {v}" for k, v in top))
    print(f"[진단] 시세 못 받은 종목 {len(dead)}건"
          + (": " + ", ".join(sorted(dead)) if dead else ""))

    if args.dry_run:
        print(f"[3/3] --dry-run 이므로 발송 생략. ({time.time() - t0:.1f}s)")
        return 0

    if args.defer_send:
        p = notify.save_pending(args.outdir, text, charts)
        print(f"[3/3] 발송을 미룸 — 웹 배포 뒤에 보낸다({p}). "
              f"({time.time() - t0:.1f}s)")
        return 0

    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", token),
                              ("TELEGRAM_CHAT_ID", chat_id)) if not v]
    if missing:
        # 어느 쪽이 없는지 알려줘야 시크릿을 헤매지 않는다. 값은 절대 찍지 않는다.
        print(f"[3/3] 다음 시크릿이 비어 있습니다: {', '.join(missing)}", file=sys.stderr)
        print("      저장소 Settings → Secrets and variables → Actions 에서 "
              "이름이 정확히 일치하는지 확인하세요.", file=sys.stderr)
        return 2
    print(f"    시크릿 확인: 토큰 {len(token)}자, chat_id {chat_id[:3]}***")

    print("[3/3] 텔레그램 발송...")
    notify.send(token, chat_id, text, charts)
    print(f"    완료. ({time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
