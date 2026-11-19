"""6-K 판별 오탐·누락 본문 확인."""
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nongaap as N  # noqa: E402

PICK = {"DB": None, "AER": None, "XOM": None, "OKLO": None,
        "FMX": ("2025-09-30", "2025-10-28"), "CCJ": ("2025-06-09", "2025-07-31"),
        "GMAB": ("2025-08", "2026-02", "2026-08"), "DNN": ("2025-08", "2025-11", "2026-08"),
        "GFL": ("2026-04-22", "2026-05-01")}


def main():
    ciks = N._ciks()
    since = dt.date.today() - dt.timedelta(days=N.ANNUAL_LOOKBACK_DAYS)
    for t, pref in PICK.items():
        _, _, alt = N._releases(ciks[t], since)
        print(f"\n===== {t}")
        n = 0
        for r in alt:
            if pref and not any(r["filed"].startswith(p) for p in pref):
                continue
            n += 1
            if n > 14:
                break
            try:
                text = N._release_text(ciks[t], r["acc"])
            except Exception as e:                 # noqa: BLE001
                print(r["filed"], "ERR", type(e).__name__)
                continue
            nums = len(N._EARN_NUM.findall(text[:20000]))
            head = re.sub(r"\s+", " ", text[:2500])
            print(f"-- {r['filed']} earn={N._is_earnings(text)} len={len(text)} nums={nums} "
                  f"pre={bool(N._EARN_PRE.search(text[:800]))}")
            print("   ", head[:420])


if __name__ == "__main__":
    main()
