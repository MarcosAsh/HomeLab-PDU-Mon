#!/usr/bin/env python3
"""JLCPCB assembly files for the PDU board: BOM, CPL and a stock/cost note.

Reads ../pdu.kicad_pcb (run ../gen.py first) and the LCSC codes from gen.py's PARTS table,
checks each part's live stock on jlcpcb.com's parts search, and writes jlc_bom.csv,
jlc_cpl.csv, parts_check.csv and assembly_note.txt. Headless: pcbnew and kicad-cli only.
"""
import csv, os, subprocess, sys
import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gen                                    # noqa: E402  (tables only, main() is not run)
from jlcparts import search                   # noqa: E402

PCB = gen.PCB
OX, OY, H = gen.OX, gen.OY, gen.H

# fitted by the owner after delivery, so not in the JLC BOM
OWNER_FITTED_REFS = {"U1"} | {"J%d" % (100 * n + 1) for n in range(1, gen.NPORTS + 1)}
OWNER_FITTED = [
    "WIZnet W5500-EVB-Pico on 2x 1x20 2.54 mm headers into U1 (RJ45 end at the board's front edge)",
    "6x XPM52C 65 W USB-C PD modules (search 'DC to 65w fast charging USB-C board'), standing on J101-J601:"
    " 90 degree pin header on + (square pad), bent wire on -, as the original Shrike Lab build",
    "M2.5 standoffs at H1, H2 (between the modules), M3 standoffs at H3-H6, 6 mm tall",
]


def main():
    board = pcbnew.LoadBoard(PCB)
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
    groups = {}
    for ref, (spec, x, y, rot, value, desc, lcsc) in gen.PARTS.items():
        if ref.startswith(("H", "TP")) or ref in OWNER_FITTED_REFS or ref in gen.DNP:
            continue
        if not lcsc:
            sys.exit("%s has no LCSC part" % ref)
        groups.setdefault(lcsc, {"refs": [], "comment": "%s %s" % (value, desc)})["refs"].append(ref)

    with open(os.path.join(HERE, "jlc_bom.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for code, g in sorted(groups.items(), key=lambda kv: kv[1]["refs"][0]):
            fpname = fps[g["refs"][0]].GetFPID().GetLibItemName().wx_str()
            w.writerow([g["comment"], ",".join(g["refs"]), fpname, code])

    # CPL: footprint origin (KiCad SMD footprints are centred on the body), board mm, y up
    with open(os.path.join(HERE, "jlc_cpl.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for g in groups.values():
            for ref in g["refs"]:
                fp = fps[ref]
                p = fp.GetPosition()
                x = pcbnew.ToMM(p.x) - OX
                y = OY + H - pcbnew.ToMM(p.y)
                w.writerow([ref, "%.3fmm" % x, "%.3fmm" % y, "Top", "%g" % (fp.GetOrientationDegrees() % 360)])
    subprocess.run(["kicad-cli", "pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "front",
                    "--use-drill-file-origin", "--exclude-dnp", "-o", os.path.join(HERE, "kicad_pos.csv"), PCB],
                   check=True, capture_output=True, env=gen.HEADLESS_ENV)

    rows, cost, extended, smd, tht = [], 0.0, 0, 0, 0
    for code, g in sorted(groups.items(), key=lambda kv: kv[1]["refs"][0]):
        n = len(g["refs"])
        hit = [c for c in search(code, 3) if c["lcsc"] == code]
        c = hit[0] if hit else {}
        price = c.get("price1") or 0.0
        cost += n * price
        kind = {"base": "basic", "expand": "extended"}.get(c.get("type"), c.get("type", "?"))
        extended += kind == "extended"
        is_tht = fps[g["refs"][0]].GetAttributes() & pcbnew.FP_THROUGH_HOLE
        tht += n if is_tht else 0
        smd += 0 if is_tht else n
        rows.append([" ".join(g["refs"]), n, code, c.get("brand", "?"), c.get("mfr", "?"), kind,
                     c.get("stock", "?"), price, "THT" if is_tht else "SMD"])
    with open(os.path.join(HERE, "parts_check.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["designators", "qty per board", "LCSC", "brand", "mfr part", "JLC library", "stock now",
                    "unit price USD (1+)", "mount"])
        w.writerows(rows)
    short = [r for r in rows if isinstance(r[6], int) and r[6] < 5 * r[1]]

    lines = [
        "JLCPCB assembly summary for %s (not a quote; check the live quote before ordering)" % gen.TITLE,
        "",
        "SMD parts per board: %d, THT parts per board: %d, %d LCSC lines (%d extended, ~$3 setup each)"
        % (smd, tht, len(rows), extended),
        "parts per board at 1+ prices: $%.2f" % cost,
        "PCB: 104 x 138.5 mm, 4 layers (In1 solid GND), 1.6 mm, 2 oz outer copper (required for the 3 A port paths)",
        "top side only (no bottom-side parts)",
        "",
        "low stock (less than 5 boards' worth): %s" % (", ".join("%s (%s)" % (r[2], r[0]) for r in short) or "none"),
        "not assembled: R15 (DNP: fit it instead of R14 for 5 V fans), test pads, mounting holes",
        "", "owner-fitted after delivery:"] + ["  " + s for s in OWNER_FITTED] + [
        "",
        "notes:",
        "  CPL is footprint origins in board mm from the bottom-left corner, y up. JLC's rotation",
        "  for SOT-23, TSOT-23-6, SMA/SMB/SMC, SOD-123 and the HTSSOP often needs a 90 or 180 degree",
        "  nudge in the placement preview: check pin 1 of U101-U601, U2-U4, Q1-Q4 and every diode's",
        "  cathode bar before paying.",
        "  Mini-Fit Jr (J1, J2), fan headers (J3, J4) and JST PH (J5-J7) are through-hole: JLC fits them",
        "  by hand, or leave them off the order and solder them yourself.",
    ]
    open(os.path.join(HERE, "assembly_note.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
