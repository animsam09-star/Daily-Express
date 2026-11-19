"""SPOT 4분기 실적 6-K 가 왜 빠지는지."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nongaap as N  # noqa: E402

ciks = N._ciks()
import datetime as dt  # noqa: E402
_, _, alt = N._releases(ciks["SPOT"], dt.date(2024, 1, 1))
for r in alt:
    if not r["filed"].startswith(("2026-02-10", "2025-02-04")):
        continue
    base = N.ARCHIVE.format(cik=ciks["SPOT"], acc=r["acc"].replace("-", ""))
    for f in N._sec_get(base + "index.json").json()["directory"]["item"]:
        if not f["name"].endswith(".htm"):
            continue
        t = N._clean(N._sec_get(base + f["name"]).text)
        neg = N._EARN_NEG.search(t[:600])
        pos = [rx.search(t[:N.EARN_HEAD]) for rx in N._EARN_POS]
        print(r["filed"], f["name"], len(t), "earn=", N._is_earnings(t),
              "neg=", neg and neg.group(0), "pos=", [m.group(0)[:60] for m in pos if m])
        print("   ", re.sub(r"\s+", " ", t[:700]))
