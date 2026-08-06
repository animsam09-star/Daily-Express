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

import news
import notify
import render
import sources
import webgen


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
