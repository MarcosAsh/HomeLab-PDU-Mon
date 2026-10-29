#!/usr/bin/env python3
"""Look up parts in JLCPCB's assembly library: jlcparts.py KEYWORD..."""
import json, sys, urllib.request

URL = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"

def search(keyword, n=4):
    body = json.dumps({"keyword": keyword, "currentPage": 1, "pageSize": n}).encode()
    req = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        d = json.load(r)
    return ((d.get("data") or {}).get("componentPageInfo") or {}).get("list") or []

for kw in sys.argv[1:]:
    rows = search(kw)
    if not rows:
        print(f"{kw}: none")
    for x in rows:
        print(f"{kw}: {x['componentCode']} {x['componentModelEn']} [{x['componentSpecificationEn']}] "
              f"stock={x['stockCount']} {x['componentLibraryType']}")
