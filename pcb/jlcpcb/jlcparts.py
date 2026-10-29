#!/usr/bin/env python3
"""Query JLCPCB's public parts search (the jlcpcb.com/parts backend). Prints code, type, stock, part, package."""
import json, sys, urllib.request

URL = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"


def search(keyword, n=8):
    req = urllib.request.Request(URL, json.dumps({"keyword": keyword, "currentPage": 1, "pageSize": n}).encode(),
                                 {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    import time
    for attempt in range(5):
        try:
            d = json.load(urllib.request.urlopen(req, timeout=30))
            break
        except Exception:
            if attempt == 4:
                raise
            time.sleep(3 * (attempt + 1))
    out = []
    for c in (d.get("data") or {}).get("componentPageInfo", {}).get("list") or []:
        out.append({"lcsc": c.get("componentCode"), "type": c.get("componentLibraryType"),
                    "stock": c.get("stockCount"), "mfr": c.get("componentModelEn"),
                    "brand": c.get("componentBrandEn"), "pkg": c.get("componentSpecificationEn"),
                    "desc": (c.get("describe") or "")[:110],
                    "price1": (c.get("componentPrices") or [{}])[0].get("productPrice")})
    return out


if __name__ == "__main__":
    for kw in sys.argv[1:]:
        print("==", kw)
        for c in search(kw):
            print("  %(lcsc)s %(type)s stock=%(stock)s $%(price1)s | %(brand)s %(mfr)s | %(pkg)s | %(desc)s" % c)
