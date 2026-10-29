#!/usr/bin/env python3
"""HomeLab PDU Mon: 1U 10-inch sheet-metal enclosure (JLCCNC, 5052 aluminium).

Run from the project root:   .venv/bin/python enclosure/enclosure.py
Writes into enclosure/: tray.step lid.step front.step rear.step assembly.step
marking.dxf, renders (PNG) and prints the interference / clearance report.

Coordinates (mm), as in SPEC.md: X right (0 = centre line), Y front to rear
(0 = inner face of the front panel), Z up (0 = top of the floor).
"""
import math
import os
import sys

import ezdxf
import numpy as np
from build123d import (Align, Color, Compound, Cylinder, Box, Face, Location,
                       Plane, Pos, Rot, Solid, Text, Vector, Wire,
                       export_step)
from PIL import Image, ImageDraw

# ----------------------------------------------------------------------------
# PARAMETERS
# ----------------------------------------------------------------------------
OUT = os.path.dirname(os.path.abspath(__file__))

T = 1.2            # sheet thickness: tray, lid, rear
T_FRONT = 2.0      # front panel
R_IN = 1.2         # inside bend radius = t
R_OUT = R_IN + T   # outside bend radius
MIN_FLANGE = 4 * T + R_IN          # JLC: flange >= 4t + r
MIN_HOLE_TO_BEND = 2 * T + R_IN    # JLC: hole/cutout >= 2t + r from bend
RIB_MIN = 1.5 * T                  # vent rib minimum (front: 1.5 * 2.0)
RIB_MIN_FRONT = 1.5 * T_FRONT

INNER_W = 215.6
OUTER_W = INNER_W + 2 * T          # 218.0
XI = INNER_W / 2                   # 107.8 inner wall face
XO = OUTER_W / 2                   # 109.0 outer wall face
Y_REAR_IN = 246.8                  # rear panel inner face
Z_TOP = 42.6                       # top of walls / lid (floor bottom = -1.2)
Z_BOT = -T
BODY_H = Z_TOP - Z_BOT             # 43.8
LID_UNDER = Z_TOP - T              # 41.4 underside of lid plate

PANEL_W, PANEL_H = 254.0, 44.0
EAR_X = 118.25
EAR_SLOT = (9.5, 6.5)              # long (X) x short (Z)
EAR_Z_EDGE = 6.2
Z_MID = (Z_TOP + Z_BOT) / 2        # 20.7, panel and fan centre height

# Mean Well HRP-300
HRP = dict(x0=-105.3, x1=-0.3, y0=43.8, y1=242.8, h=41.0)
HRP_HOLES_X = [HRP["x0"] + 15.0, HRP["x1"] - 15.0]
HRP_HOLES_Y = [HRP["y1"] - 42.5, HRP["y1"] - 42.5 - 120.0]
M4_CLR = 4.5
EARTH_HOLE = (14.0, 42.0, 4.5)     # M4 earth stud on the floor (x, y, dia)

# PCB
PCB = dict(x0=2.0, x1=106.0, y0=245.3 - 138.5, y1=245.3, z0=6.0, t=1.6)
PCB_TOP = PCB["z0"] + PCB["t"]
PCB_HOLES_BOARD = [(18.0, 28.0), (86.0, 28.0), (2.6, 88.6), (101.4, 88.6), (4.0, 134.5), (101.4, 113.4)]
PCB_HEAD_D = 5.5                    # M3 pan head on top of the PCB
M25_HOLES = {(18.0, 28.0), (86.0, 28.0)}   # board holes that are M2.5 (floor hole 2.7, head 4.5)
M25_CLR, M25_HEAD_D = 2.7, 4.5
# Mini-Fit Jr J1/J2 overhang the PCB front edge (board x ranges, board y to 143.4)
CONNECTORS = {"J1": (7.7, 22.6), "J2": (28.9, 43.8)}
CONN_Y_BOARD = 143.4
CONN_H = 13.0                       # above PCB top (assumed reading of '~13')
M3_CLR = 3.2
M3_PANEL = 3.4

# PD modules
N_PORTS = 6
MOD_PITCH = 17.0
MOD_X0 = 9.5                       # board x of module 1 centre
MOD_L, MOD_H, MOD_T = 33.0, 22.0, 12.0
MOD_REAR_GAP = 0.8                 # module body rear face to rear panel inner face
USBC_H_ABOVE_PCB = 13.0            # ASSUMED, see README (fallback value)
USBC_Z = PCB_TOP + USBC_H_ABOVE_PCB
USBC_W, USBC_TH, USBC_L = 8.94, 3.26, 1.8   # receptacle shell, protrusion past module end
RJ_CUT_W, RJ_CUT_H = 14.9, 19.2
USBC_CUT_W, USBC_CUT_H = 15.0, 8.0

# front panel layout
X_RJ45 = -60.0
X_BTN = -30.0
X_IEC = 32.0                        # spec says ~35, moved 3 mm to clear the fan flange
X_FAN = 77.5                        # spec says ~80, moved to clear the wall flange
IEC_CUT = (47.5, 27.5)
BTN_CUT_D = 16.2
FAN_SIZE, FAN_DEPTH, FAN_HOLE_PITCH = 40.0, 20.0, 32.0
FAN_Z = Z_MID
FAN_GRILLE = dict(r_out=18.0, r_hub=7.0, spokes=4, spoke_w=3.0)

# joints
FLANGE_FLAT = 8.6                   # flat length beyond bend tangent for wall flanges
TAB_W = 13.2                        # floor tab width
FRONT_WALL_HOLES_Z = [8.0, 26.0]
FRONT_FLOOR_TABS_X = [-46.0, 0.0]
REAR_FLOOR_TABS_X = [-98.0, -2.0]
REAR_WALL_TAB_Z = (31.0, 41.4)
REAR_WALL_HOLE_Z = 36.0
TAB_HOLE_Z = 6.4                    # floor tab hole height
TAB_TOP_Z = 10.0
FLANGE_HOLE_DX = 101.4              # |X| of wall flange holes
LID_HOLE_Z = 35.0
LID_FLANGE_H = 12.0
LID_SIDE_Y_R = [30.0, 90.0, 150.0, 210.0]
LID_SIDE_Y_L = [14.0, 34.0]         # left side limited to the free front part (HRP nut clearance)
LID_REAR_TABS_X = [30.0, 75.0]
LID_REAR_TAB_W = 20.0
LID_CLR = 0.3

MAX_WIDTH, MAX_HEIGHT = 220.0, 43.8

# ----------------------------------------------------------------------------
# GEOMETRY HELPERS
# ----------------------------------------------------------------------------

def _pt(plane, u, v, w):
    if plane == "XZ":
        return Vector(u, w, v)
    if plane == "XY":
        return Vector(u, v, w)
    if plane == "YZ":
        return Vector(w, u, v)
    raise ValueError(plane)


def prism(pts, plane, a0, a1, fillets=None):
    """Polygon pts=[(u,v)] on plane XZ|XY|YZ, extruded along the third axis a0..a1.
    fillets: {point index: radius} applied as 2D fillets before extrusion."""
    wire = Wire.make_polygon([_pt(plane, u, v, a0) for u, v in pts], close=True)
    face = Face(wire)
    for idx, rad in (fillets or {}).items():
        u, v = pts[idx]
        target = _pt(plane, u, v, a0)
        vert = min(face.vertices(), key=lambda q: (Vector(q.X, q.Y, q.Z) - target).length)
        face = face.fillet_2d(rad, [vert])
    d = {"XZ": Vector(0, a1 - a0, 0), "XY": Vector(0, 0, a1 - a0), "YZ": Vector(a1 - a0, 0, 0)}[plane]
    return Solid.extrude(face, d)


def box(x0, x1, y0, y1, z0, z1):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def cyl(axis, c1, c2, d, a0, a1):
    """Cylinder of diameter d along axis 'X'|'Y'|'Z' from a0 to a1 at the other two coords (c1,c2)."""
    h = a1 - a0
    cy = Cylinder(d / 2, h)
    if axis == "Z":
        return Pos(c1, c2, a0 + h / 2) * cy
    if axis == "Y":   # c1 = X, c2 = Z
        return Pos(c1, a0 + h / 2, c2) * (Rot(X=90) * cy)
    if axis == "X":   # c1 = Y, c2 = Z
        return Pos(a0 + h / 2, c1, c2) * (Rot(Y=90) * cy)
    raise ValueError(axis)


def slot_xz(cx, cz, w, h, y0, y1):
    """Rounded slot (full-radius ends along the long side) in a panel, axis Y."""
    pts = [(cx - w / 2, cz - h / 2), (cx + w / 2, cz - h / 2), (cx + w / 2, cz + h / 2), (cx - w / 2, cz + h / 2)]
    r = min(w, h) / 2 - 1e-3
    return prism(pts, "XZ", y0, y1, {i: r for i in range(4)})


def rect_xz(cx, cz, w, h, y0, y1, r=0.0):
    pts = [(cx - w / 2, cz - h / 2), (cx + w / 2, cz - h / 2), (cx + w / 2, cz + h / 2), (cx - w / 2, cz + h / 2)]
    return prism(pts, "XZ", y0, y1, {i: r for i in range(4)} if r else None)


def hex_xz(cx, cz, flats, y0, y1):
    R = flats / math.sqrt(3)
    pts = [(cx + R * math.sin(math.radians(60 * k)), cz + R * math.cos(math.radians(60 * k))) for k in range(6)]
    return prism(pts, "XZ", y0, y1)


def cut_all(shape, cutters):
    return shape - Compound(children=cutters) if cutters else shape


def bevel_solid(s):
    return s.solids()[0] if len(s.solids()) == 1 else Compound(children=list(s.solids()))


# ----------------------------------------------------------------------------
# PARTS
# ----------------------------------------------------------------------------

def csk(x, y, d, depth=None):
    """Through hole d with a 90 degree countersink from the underside, depth = sheet thickness
    (knife edge at the top of the cone, no land). Underside diameter d + 2 T."""
    depth = depth or T
    cone = Pos(x, y, Z_BOT) * Solid.make_cone(d / 2 + depth + 0.001, d / 2, depth, Plane.XY)
    return cyl("Z", x, y, d, Z_BOT - 1, 1.0) + cone


def build_tray():
    Y1 = Y_REAR_IN
    # U profile (floor + 2 walls) in XZ, extruded along Y
    pts = [(-XO, Z_TOP), (-XO, Z_BOT), (XO, Z_BOT), (XO, Z_TOP), (XI, Z_TOP), (XI, 0.0), (-XI, 0.0), (-XI, Z_TOP)]
    tray = prism(pts, "XZ", 0.0, Y1, {1: R_OUT, 2: R_OUT, 5: R_IN, 6: R_IN})

    adds = []
    cuts = []
    for sgn in (-1, 1):
        # --- front wall flange (inward), Z 2.4..41.4, relief gap to floor bend
        z0, z1 = 2.4, 41.4
        cuts.append(box(min(sgn * XI, sgn * XO) - 0.001, max(sgn * XI, sgn * XO) + 0.001, -0.01, 10.0, z0, z1))
        xe = sgn * (XO - R_OUT - FLANGE_FLAT)
        p = [(sgn * XI, 12.0), (sgn * XO, 12.0), (sgn * XO, 0.0), (xe, 0.0), (xe, T), (sgn * XI, T)]
        adds.append(prism(p, "XY", z0, z1, {2: R_OUT, 5: R_IN}))
        # --- rear wall flange tab (inward), upper part only (module and PCB clearance below)
        z0, z1 = REAR_WALL_TAB_Z
        cuts.append(box(min(sgn * XI, sgn * XO) - 0.001, max(sgn * XI, sgn * XO) + 0.001, Y1 - 10.0, Y1 + 0.01, z0, z1))
        p = [(sgn * XI, Y1 - 12.0), (sgn * XO, Y1 - 12.0), (sgn * XO, Y1), (xe, Y1), (xe, Y1 - T), (sgn * XI, Y1 - T)]
        adds.append(prism(p, "XY", z0, z1, {2: R_OUT, 5: R_IN}))
    # --- floor tabs (upward flanges), front at Y=0, rear at Y=Y1, each with relief notches
    for xc in FRONT_FLOOR_TABS_X:
        cuts.append(box(xc - TAB_W / 2, xc + TAB_W / 2, -0.01, 10.0, Z_BOT - 0.01, 0.01))
        cuts.append(box(xc - TAB_W / 2 - 1.2, xc - TAB_W / 2, -0.01, 2.4, Z_BOT - 0.01, 0.01))
        cuts.append(box(xc + TAB_W / 2, xc + TAB_W / 2 + 1.2, -0.01, 2.4, Z_BOT - 0.01, 0.01))
        p = [(12.0, Z_BOT), (0.0, Z_BOT), (0.0, TAB_TOP_Z), (T, TAB_TOP_Z), (T, 0.0), (12.0, 0.0)]
        adds.append(prism(p, "YZ", xc - TAB_W / 2, xc + TAB_W / 2, {1: R_OUT, 4: R_IN}))
    for xc in REAR_FLOOR_TABS_X:
        cuts.append(box(xc - TAB_W / 2, xc + TAB_W / 2, Y1 - 10.0, Y1 + 0.01, Z_BOT - 0.01, 0.01))
        cuts.append(box(xc - TAB_W / 2 - 1.2, xc - TAB_W / 2, Y1 - 2.4, Y1 + 0.01, Z_BOT - 0.01, 0.01))
        cuts.append(box(xc + TAB_W / 2, xc + TAB_W / 2 + 1.2, Y1 - 2.4, Y1 + 0.01, Z_BOT - 0.01, 0.01))
        p = [(Y1 - 12.0, Z_BOT), (Y1, Z_BOT), (Y1, TAB_TOP_Z), (Y1 - T, TAB_TOP_Z), (Y1 - T, 0.0), (Y1 - 12.0, 0.0)]
        adds.append(prism(p, "YZ", xc - TAB_W / 2, xc + TAB_W / 2, {1: R_OUT, 4: R_IN}))

    tray = tray - Compound(children=cuts)
    for a in adds:
        tray = tray + a

    holes = []
    # panel screws through flanges (axis Y), tab holes
    for sgn in (-1, 1):
        for z in FRONT_WALL_HOLES_Z:
            holes.append(cyl("Y", sgn * FLANGE_HOLE_DX, z, M3_CLR, -1.0, 3.0))
        holes.append(cyl("Y", sgn * FLANGE_HOLE_DX, REAR_WALL_HOLE_Z, M3_CLR, Y1 - 3.0, Y1 + 1.0))
    for xc in FRONT_FLOOR_TABS_X:
        holes.append(cyl("Y", xc, TAB_HOLE_Z, M3_CLR, -1.0, 3.0))
    for xc in REAR_FLOOR_TABS_X:
        holes.append(cyl("Y", xc, TAB_HOLE_Z, M3_CLR, Y1 - 3.0, Y1 + 1.0))
    # lid screws through the walls (axis X)
    for y in LID_SIDE_Y_R:
        holes.append(cyl("X", y, LID_HOLE_Z, M3_PANEL, XI - 1.0, XO + 1.0))
    for y in LID_SIDE_Y_L:
        holes.append(cyl("X", y, LID_HOLE_Z, M3_PANEL, -XO - 1.0, -XI + 1.0))
    # HRP bottom screws (M4, from below) and earth stud
    for x in HRP_HOLES_X:
        for y in HRP_HOLES_Y:
            holes.append(csk(x, y, M4_CLR))
    holes.append(csk(EARTH_HOLE[0], EARTH_HOLE[1], EARTH_HOLE[2]))
    # PCB standoffs (M3 from below into F-F standoffs)
    for bx, by in PCB_HOLES_BOARD:
        if (bx, by) in M25_HOLES:
            holes.append(csk(PCB["x0"] + bx, PCB["y1"] - by, M25_CLR, 1.15))
        else:
            holes.append(csk(PCB["x0"] + bx, PCB["y1"] - by, M3_CLR))
    tray = tray - Compound(children=holes)
    return tray


def build_lid():
    y0, y1 = 0.3, Y_REAR_IN - T - 0.2 - 1.0 - 0.0  # plate end before rear tabs, set below
    xl = XI - LID_CLR                               # 107.5 outer face of lid flanges
    zt = Z_TOP
    ys0 = 2.7                                       # flanges start behind the front wall flange bend (ends Y 2.4)
    ys1 = 244.0                                     # and stop before the rear wall tab bend (starts Y 244.4)
    pts = [(-xl, zt - LID_FLANGE_H), (-xl, zt), (xl, zt), (xl, zt - LID_FLANGE_H),
           (xl - T, zt - LID_FLANGE_H), (xl - T, zt - T), (-xl + T, zt - T), (-xl + T, zt - LID_FLANGE_H)]
    lid = prism(pts, "XZ", ys0, ys1, {1: R_OUT, 2: R_OUT, 5: R_IN, 6: R_IN})
    lid = lid + box(-xl, xl, 0.3, ys0 + 0.01, zt - T, zt)            # front plate strip
    # rear tabs (bent down against the rear panel)
    Yo = Y_REAR_IN - 0.2                                             # outer face of tab 246.6
    for xc in LID_REAR_TABS_X:
        p = [(Yo - 8.0, zt), (Yo, zt), (Yo, zt - LID_FLANGE_H), (Yo - T, zt - LID_FLANGE_H),
             (Yo - T, zt - T), (Yo - 8.0, zt - T)]
        lid = lid + prism(p, "YZ", xc - LID_REAR_TAB_W / 2, xc + LID_REAR_TAB_W / 2, {1: R_OUT, 4: R_IN})
    holes = []
    for y in LID_SIDE_Y_R:
        holes.append(cyl("X", y, LID_HOLE_Z, M3_CLR, xl - 3, xl + 1))
    for y in LID_SIDE_Y_L:
        holes.append(cyl("X", y, LID_HOLE_Z, M3_CLR, -xl - 1, -xl + 3))
    for xc in LID_REAR_TABS_X:
        holes.append(cyl("Y", xc, LID_HOLE_Z, M3_CLR, Yo - 3, Yo + 1))
    return lid - Compound(children=holes)


def front_features():
    """Cutters for the front panel (axis Y, through -2..0)."""
    y0, y1 = -T_FRONT - 1, 1.0
    c = []
    c.append(cyl("Y", X_BTN, FAN_Z, BTN_CUT_D, y0, y1))
    c.append(rect_xz(X_RJ45, Z_MID, RJ_CUT_W, RJ_CUT_H, y0, y1))
    c.append(rect_xz(X_IEC, Z_MID, IEC_CUT[0], IEC_CUT[1], y0, y1))
    # fan grille: annulus with spokes
    g = FAN_GRILLE
    ring = cyl("Y", X_FAN, FAN_Z, 2 * g["r_out"], y0, y1) - cyl("Y", X_FAN, FAN_Z, 2 * g["r_hub"], y0 - 1, y1 + 1)
    spokes = []
    for k in range(g["spokes"]):
        ang = 45 + 90 * k
        b = Pos(X_FAN, 0, FAN_Z) * Rot(Y=-ang) * Pos(0, 0, 0) * Box(2 * g["r_out"] + 2, 10, g["spoke_w"])
        spokes.append(b)
    ring = ring - Compound(children=spokes)
    c.append(ring)
    for dx in (-FAN_HOLE_PITCH / 2, FAN_HOLE_PITCH / 2):
        for dz in (-FAN_HOLE_PITCH / 2, FAN_HOLE_PITCH / 2):
            c.append(cyl("Y", X_FAN + dx, FAN_Z + dz, M3_PANEL, y0, y1))
    # vent slots over the HRP terminal end (4.0 wide, 7.0 pitch -> 3.0 rib = 1.5 x 2.0 mm)
    for x in (-94.0, -87.0, -80.0, -73.0):
        c.append(slot_xz(x, (3.0 + 38.4) / 2, 4.0, 38.4 - 3.0, y0, y1))
    for x in (-13.0, -6.0, 1.0):
        c.append(slot_xz(x, (12.0 + 38.4) / 2, 4.0, 38.4 - 12.0, y0, y1))
    # fastener holes
    for sgn in (-1, 1):
        for z in FRONT_WALL_HOLES_Z:
            c.append(cyl("Y", sgn * FLANGE_HOLE_DX, z, M3_PANEL, y0, y1))
    for xc in FRONT_FLOOR_TABS_X:
        c.append(cyl("Y", xc, TAB_HOLE_Z, M3_PANEL, y0, y1))
    return c


def build_front():
    zc = Z_MID
    p = box(-PANEL_W / 2, PANEL_W / 2, -T_FRONT, 0.0, zc - PANEL_H / 2, zc + PANEL_H / 2)
    cutters = front_features()
    for sgn in (-1, 1):
        for z in (zc - PANEL_H / 2 + EAR_Z_EDGE, zc + PANEL_H / 2 - EAR_Z_EDGE):
            cutters.append(slot_xz(sgn * EAR_X, z, EAR_SLOT[0], EAR_SLOT[1], -T_FRONT - 1, 1.0))
    # round the outside corners of the panel (r = 3)
    p = p.fillet(3.0, [e for e in p.edges() if abs(e.length - T_FRONT) < 1e-6 and abs(abs(e.center().X) - PANEL_W / 2) < 1e-6])
    return p - Compound(children=cutters)


def module_x(i):
    return PCB["x0"] + MOD_X0 + MOD_PITCH * i


def rear_features():
    y0, y1 = Y_REAR_IN - 1, Y_REAR_IN + T + 1
    c = []
    # USB-C cutouts 13 x 7 rounded
    for i in range(N_PORTS):
        c.append(slot_xz(module_x(i), USBC_Z, USBC_CUT_W, USBC_CUT_H, y0, y1))
    # small vents on the PCB side (2.4 x 6, 4.4 pitch)
    x = 8.0
    while x <= 101.0:
        c.append(slot_xz(x, 11.5, 2.4, 6.0, y0, y1))
        x += 4.4
    # hex vents over the HRP fan end
    flats = 7.0
    R = flats / math.sqrt(3)
    rib = 2.0
    px = flats + rib
    py = px * math.sqrt(3) / 2
    xa, xb, za, zb = -88.0, -10.0, 1.0, 40.4
    row = 0
    z = za + R
    while z + R <= zb + 1e-6:
        x = xa + flats / 2 + (px / 2 if row % 2 else 0.0)
        while x + flats / 2 <= xb + 1e-6:
            c.append(hex_xz(x, z, flats, y0, y1))
            x += px
        z += py
        row += 1
    # fastener holes
    for xc in REAR_FLOOR_TABS_X:
        c.append(cyl("Y", xc, TAB_HOLE_Z, M3_PANEL, y0, y1))
    for sgn in (-1, 1):
        c.append(cyl("Y", sgn * FLANGE_HOLE_DX, REAR_WALL_HOLE_Z, M3_PANEL, y0, y1))
    for xc in LID_REAR_TABS_X:
        c.append(cyl("Y", xc, LID_HOLE_Z, M3_PANEL, y0, y1))
    return c


def build_rear():
    p = box(-XO, XO, Y_REAR_IN, Y_REAR_IN + T, Z_BOT, Z_TOP)
    return p - Compound(children=rear_features())


# ----------------------------------------------------------------------------
# PLACEHOLDERS
# ----------------------------------------------------------------------------

def build_placeholders():
    ph = {}
    ph["HRP-300"] = (box(HRP["x0"], HRP["x1"], HRP["y0"], HRP["y1"], 0.0, HRP["h"]), (0.75, 0.75, 0.78))
    ph["PCB"] = (box(PCB["x0"], PCB["x1"], PCB["y0"], PCB["y1"], PCB["z0"], PCB_TOP), (0.1, 0.55, 0.25))
    y_rear = Y_REAR_IN - MOD_REAR_GAP
    for i in range(N_PORTS):
        xc = module_x(i)
        ph[f"PD module {i+1}"] = (box(xc - MOD_T / 2, xc + MOD_T / 2, y_rear - MOD_L, y_rear, PCB_TOP, PCB_TOP + MOD_H), (0.15, 0.35, 0.8))
        ph[f"USB-C {i+1}"] = (box(xc - USBC_W / 2, xc + USBC_W / 2, y_rear, y_rear + MOD_REAR_GAP + T - 0.2 + 0.0, USBC_Z - USBC_TH / 2, USBC_Z + USBC_TH / 2), (0.85, 0.85, 0.85))
    for k, (bx, by) in enumerate(PCB_HOLES_BOARD):   # M3 pan-head screws holding the PCB (top side)
        ph[f"PCB screw head {k+1}"] = (cyl("Z", PCB["x0"] + bx, PCB["y1"] - by, M25_HEAD_D if (bx, by) in M25_HOLES else PCB_HEAD_D, PCB_TOP, PCB_TOP + 2.0), (0.5, 0.5, 0.5))
    for name, (bx0, bx1) in CONNECTORS.items():
        ph[f"{name} Mini-Fit (overhang)"] = (box(PCB["x0"] + bx0, PCB["x0"] + bx1, PCB["y1"] - CONN_Y_BOARD, PCB["y0"], PCB_TOP, PCB_TOP + CONN_H), (0.9, 0.9, 0.85))
    ph["Fan 40x40x20"] = (box(X_FAN - 20, X_FAN + 20, 0.0, FAN_DEPTH, FAN_Z - 20, FAN_Z + 20), (0.2, 0.2, 0.2))
    ph["IEC inlet"] = (box(X_IEC - 23.5, X_IEC + 23.5, -T_FRONT, 33.0, Z_MID - 13.5, Z_MID + 13.5), (0.3, 0.3, 0.35))
    ph["Button 16mm"] = (cyl("Y", X_BTN, FAN_Z, 16.0, -T_FRONT - 2, 25.0), (0.6, 0.6, 0.65))
    ph["RJ45 coupler"] = (box(X_RJ45 - 7.0, X_RJ45 + 7.0, -T_FRONT, 30.0, Z_MID - 9.1, Z_MID + 9.1), (0.4, 0.4, 0.45))
    return ph


# ----------------------------------------------------------------------------
# DXF (marking and flat rear outline)
# ----------------------------------------------------------------------------

def write_marking_dxf(rear):
    """Flat rear panel as seen from OUTSIDE (u = -X, v = Z). Layer CUT: outline and
    holes. Layer MARK: outline-font text, 0.2 mm line (double line = glyph outline)."""
    doc = ezdxf.new("R2010")
    doc.layers.add("CUT", color=1)
    doc.layers.add("MARK", color=5)
    msp = doc.modelspace()

    def poly(edges, layer):
        pts = []
        for e in edges:
            for t in np.linspace(0, 1, 24 if e.geom_type.name != "LINE" else 2):
                q = e.position_at(float(t))
                pts.append((-q.X, q.Z))
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})

    outer = max(rear.faces().filter_by(lambda f: f.normal_at().Y > 0.99), key=lambda f: f.area)
    for w in [outer.outer_wire()] + list(outer.inner_wires()):
        poly(w.edges(), "CUT")

    def text(txt, fs, u, v, centre=False):
        t = Text(txt, fs, align=(Align.CENTER, Align.MIN) if centre else (Align.MIN, Align.MIN))
        t = Pos(u, v, 0) * t
        for f in t.faces():
            for w in [f.outer_wire()] + list(f.inner_wires()):
                pts = []
                for e in w.edges():
                    for s in np.linspace(0, 1, 12):
                        q = e.position_at(float(s))
                        pts.append((q.X, q.Y))
                msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "MARK", "const_width": 0.2})

    for i in range(N_PORTS):
        text(str(i + 1), 4.5, -module_x(i), 27.0, centre=True)
    text("HomeLab PDU Mon", 2.6, -105.0, 4.2)
    text("Marcos Ashton Iglesias 2026-10-28", 2.0, -105.0, 0.9)
    doc.saveas(os.path.join(OUT, "marking.dxf"))


# ----------------------------------------------------------------------------
# CHECKS
# ----------------------------------------------------------------------------

def overlap_volume(a, b):
    ba, bb = a.bounding_box(), b.bounding_box()
    if (ba.max.X <= bb.min.X or bb.max.X <= ba.min.X or ba.max.Y <= bb.min.Y or bb.max.Y <= ba.min.Y
            or ba.max.Z <= bb.min.Z or bb.max.Z <= ba.min.Z):
        return 0.0
    try:
        i = a.intersect(b)
        if i is None:
            return 0.0
        return sum(x.volume for x in i.solids()) if hasattr(i, "solids") else 0.0
    except Exception as e:  # pragma: no cover
        print("   (boolean failed", e, ")")
        return -1.0


def run_checks(parts, ph):
    print("\n=== INTERFERENCE CHECK (volume overlaps > 0.01 mm^3) ===")
    allx = dict((k, v) for k, v in parts.items())
    allx.update({k: v[0] for k, v in ph.items()})
    names = list(allx)
    bad = 0
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            # placeholder vs placeholder only matters for a few combinations, still check all
            v = overlap_volume(allx[a], allx[b])
            if v > 0.01 or v < 0:
                bad += 1
                print(f"  OVERLAP  {a}  x  {b}: {v:.2f} mm^3")
    print(f"  {len(names)} bodies, {bad} overlapping pairs" + ("  -> OK" if bad == 0 else "  -> FIX"))

    print("\n=== CLEARANCES (mm) ===")
    hrp, pcb = ph["HRP-300"][0], ph["PCB"][0]
    metal_rear = parts["rear"]
    d = hrp.distance_to(metal_rear)
    print(f"  HRP fan end to rear panel inner face      : {d:6.2f}   (spec 4.0)")
    for name in ("tray", "lid"):
        print(f"  HRP to {name:<4} (closest point)               : {hrp.distance_to(parts[name]):6.2f}")
    print(f"  IEC body to HRP terminal end (3D)         : {ph['IEC inlet'][0].distance_to(hrp):6.2f}   (Y gap {HRP['y0'] - 33.0:.2f}, X gap {X_IEC - 23.5 - HRP['x1']:.2f})")
    print(f"  Fan to PCB                                : {ph['Fan 40x40x20'][0].distance_to(pcb):6.2f}")
    for j in ("J1", "J2"):
        c = ph[f"{j} Mini-Fit (overhang)"][0]
        print(f"  {j} overhang to fan / IEC / RJ45 / HRP        : {c.distance_to(ph['Fan 40x40x20'][0]):.2f} / {c.distance_to(ph['IEC inlet'][0]):.2f} / {c.distance_to(ph['RJ45 coupler'][0]):.2f} / {c.distance_to(hrp):.2f}")
    print(f"  PCB rear edge to rear panel inner face    : {Y_REAR_IN - PCB['y1']:6.2f}   (spec 1.5)")
    print(f"  PCB rear edge to tray rear floor tab      : {parts['tray'].distance_to(pcb):6.2f}   (tray to PCB, closest)")
    print(f"  HRP top to lid underside                  : {LID_UNDER - HRP['h']:6.2f}")
    print(f"  Fan top to lid underside                  : {LID_UNDER - (FAN_Z + 20):6.2f}   fan bottom to floor: {FAN_Z - 20:.2f}")
    mod_top = PCB_TOP + MOD_H
    print(f"  Module top to lid rear tabs / wall tabs   : {min(LID_UNDER - LID_FLANGE_H + T, REAR_WALL_TAB_Z[0]) - mod_top:6.2f}  (module top Z {mod_top:.1f})")
    print(f"  Module 6 side to right wall inner face    : {XI - (module_x(5) + MOD_T / 2):6.2f}")
    print(f"  USB-C cutout rim to receptacle (X / Z)    : {(USBC_CUT_W - USBC_W) / 2:5.2f} / {(USBC_CUT_H - USBC_TH) / 2:5.2f} each side")
    rib_usb = MOD_PITCH - USBC_CUT_W
    print(f"  USB-C cutout rib between ports            : {rib_usb:6.2f}")
    tb = Compound(children=[parts["tray"], parts["lid"]]).bounding_box()
    width = tb.max.X - tb.min.X
    height = tb.max.Z - tb.min.Z
    full = Compound(children=list(parts.values())).bounding_box()
    print(f"  Body width incl. lid flanges              : {width:6.2f}   (max {MAX_WIDTH})  {'OK' if width <= MAX_WIDTH + 1e-6 else 'FAIL'}")
    print(f"  Body height (tray + lid)                  : {height:6.2f}   (max {MAX_HEIGHT})  {'OK' if height <= MAX_HEIGHT + 1e-6 else 'FAIL'}")
    print(f"  Front panel height / width                : {PANEL_H:.1f} / {PANEL_W:.1f}")
    print(f"  Overall depth incl. front panel           : {full.max.Y - full.min.Y:6.2f}   (spec 250)")
    print(f"  Clearance lid flange to wall (each side)  : {LID_CLR:6.2f}")

    print("\n=== DFM CHECKS (5052, t = %.1f, r = %.1f) ===" % (T, R_IN))
    flange_wall = FLANGE_FLAT
    print(f"  min flange flat length required {MIN_FLANGE:.1f}; wall flanges {flange_wall:.1f}; floor tabs {TAB_TOP_Z - (Z_BOT + R_OUT):.1f}; lid flanges {LID_FLANGE_H - R_OUT:.1f}")
    assert flange_wall >= MIN_FLANGE and TAB_TOP_Z - (Z_BOT + R_OUT) >= MIN_FLANGE and LID_FLANGE_H - R_OUT >= MIN_FLANGE
    h_wall = (XO - R_OUT) - FLANGE_HOLE_DX - M3_CLR / 2
    h_tab = (TAB_HOLE_Z - (Z_BOT + R_OUT)) - M3_CLR / 2
    h_lid = (zt := (Z_TOP - R_OUT)) - LID_HOLE_Z - M3_CLR / 2
    print(f"  hole edge to bend tangent (need >= {MIN_HOLE_TO_BEND:.1f}): wall flange {h_wall:.2f}, floor tab {h_tab:.2f}, lid flange {h_lid:.2f}")
    assert min(h_wall, h_tab, h_lid) >= MIN_HOLE_TO_BEND - 1e-6
    print(f"  min hole diameter {min(M3_CLR, USBC_CUT_H)} >= max(0.5 t, 1) = {max(0.5 * T_FRONT, 1.0)}")
    print(f"  vent ribs: rear hex 2.0 / slots 2.0 (need {RIB_MIN:.1f}); front slots 3.0 (need {RIB_MIN_FRONT:.1f})")
    print(f"  USB-C rib {rib_usb:.1f} (need {RIB_MIN:.1f})")
    return bad


# ----------------------------------------------------------------------------
# RENDER (headless, pure PIL painter's algorithm)
# ----------------------------------------------------------------------------

def tri_mesh(shape, tol=0.25):
    v, t = shape.tessellate(tol, 0.3)
    v = np.array([[p.X, p.Y, p.Z] for p in v])
    t = np.array(t)
    return v[t]   # (n,3,3)


def render(items, name, view, size=(1800, 700), scale=None, light=(0.4, -0.5, 0.8), bg=(245, 245, 245)):
    """items: list of (shape, rgb). view: 'top','front','rear','iso'. Orthographic."""
    if view == "top":      # look down, X right, Y up on screen (front at bottom)
        right, up, fwd = np.array([1, 0, 0.]), np.array([0, 1, 0.]), np.array([0, 0, -1.])
    elif view == "front":  # look toward +Y, X right, Z up
        right, up, fwd = np.array([1, 0, 0.]), np.array([0, 0, 1.]), np.array([0, 1, 0.])
    elif view == "rear":   # look toward -Y from behind, X to the left
        right, up, fwd = np.array([-1, 0, 0.]), np.array([0, 0, 1.]), np.array([0, -1, 0.])
    else:                  # iso from front-left-above... rotate: look at the box from front/right/above
        fwd = np.array([-0.55, 0.7, -0.5]); fwd /= np.linalg.norm(fwd)
        right = np.cross(fwd, [0, 0, 1.]); right /= np.linalg.norm(right)
        up = np.cross(right, fwd)
    L = np.array(light, float); L /= np.linalg.norm(L)
    tris, cols = [], []
    for shape, rgb in items:
        m = tri_mesh(shape)
        tris.append(m)
        cols.extend([rgb] * len(m))
    tris = np.concatenate(tris)
    cols = np.array(cols)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = n / np.where(ln == 0, 1, ln)
    facing = n @ fwd
    n = np.where((facing > 0)[:, None], -n, n)         # orient toward viewer
    shade = 0.45 + 0.55 * np.clip(n @ L, 0, 1)
    depth = (tris.mean(axis=1) @ fwd)
    pu = tris @ right
    pv = tris @ up
    umin, umax, vmin, vmax = pu.min(), pu.max(), pv.min(), pv.max()
    pad = 20
    if scale is None:
        scale = min((size[0] - 2 * pad) / (umax - umin), (size[1] - 2 * pad) / (vmax - vmin))
    W = int((umax - umin) * scale + 2 * pad)
    H = int((vmax - vmin) * scale + 2 * pad)
    sx = (pu - umin) * scale + pad
    sy = H - ((pv - vmin) * scale + pad)
    dz = tris @ fwd                                     # depth per vertex (bigger = farther)
    zbuf = np.full((H, W), np.inf)
    buf = np.zeros((H, W, 3), np.uint8)
    buf[:] = bg
    base = np.array([[int(min(255, c[j] * 255)) for j in range(3)] for c in cols], float)
    for k in range(len(tris)):
        x0, x1, x2 = sx[k]
        y0, y1, y2 = sy[k]
        den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(den) < 1e-9:
            continue
        xa, xb = int(max(0, math.floor(min(x0, x1, x2)))), int(min(W - 1, math.ceil(max(x0, x1, x2))))
        ya, yb = int(max(0, math.floor(min(y0, y1, y2)))), int(min(H - 1, math.ceil(max(y0, y1, y2))))
        if xb < xa or yb < ya:
            continue
        gx, gy = np.meshgrid(np.arange(xa, xb + 1) + 0.5, np.arange(ya, yb + 1) + 0.5)
        l0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / den
        l1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / den
        l2 = 1 - l0 - l1
        m = (l0 >= -0.02) & (l1 >= -0.02) & (l2 >= -0.02)
        if not m.any():
            continue
        z = l0 * dz[k, 0] + l1 * dz[k, 1] + l2 * dz[k, 2]
        zs = zbuf[ya:yb + 1, xa:xb + 1]
        upd = m & (z < zs)
        zs[upd] = z[upd]
        buf[ya:yb + 1, xa:xb + 1][upd] = (base[k] * shade[k]).astype(np.uint8)
    img = Image.fromarray(buf)
    img.save(os.path.join(OUT, name))


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    tray, lid, front, rear = build_tray(), build_lid(), build_front(), build_rear()
    for nm, s in (("tray", tray), ("lid", lid), ("front", front), ("rear", rear)):
        s.label = nm
        export_step(s, os.path.join(OUT, f"{nm}.step"))
        print(f"wrote {nm}.step   volume {s.volume:9.1f} mm^3   solids {len(s.solids())}  valid {s.is_valid}")
    parts = dict(tray=tray, lid=lid, front=front, rear=rear)
    colors = dict(tray=(0.78, 0.8, 0.82), lid=(0.85, 0.86, 0.88), front=(0.08, 0.08, 0.09), rear=(0.1, 0.1, 0.11))
    ph = build_placeholders()

    asm_children = []
    for k, s in parts.items():
        c = s.moved(Location())
        c.label = k
        c.color = Color(*colors[k])
        asm_children.append(c)
    for k, (s, rgb) in ph.items():
        c = s.moved(Location())
        c.label = k
        c.color = Color(*rgb)
        asm_children.append(c)
    asm = Compound(children=asm_children, label="HomeLab PDU Mon assembly")
    export_step(asm, os.path.join(OUT, "assembly.step"))
    print("wrote assembly.step")

    write_marking_dxf(rear)
    print("wrote marking.dxf")

    run_checks(parts, ph)

    # renders
    metal = [(tray, colors["tray"]), (front, (0.25, 0.25, 0.27)), (rear, (0.25, 0.25, 0.27))]
    phl = [(s, rgb) for s, rgb in ph.values()]
    with_lid = metal + [(lid, colors["lid"])] + phl
    no_lid = metal + phl
    render(with_lid, "render_top.png", "top")
    render(no_lid, "render_top_open.png", "top")
    render(with_lid, "render_front.png", "front", size=(1800, 400))
    render(with_lid, "render_rear.png", "rear", size=(1800, 400))
    render(with_lid, "render_iso.png", "iso", size=(1600, 1000))
    render(no_lid, "render_iso_open.png", "iso", size=(1600, 1000))
    print("wrote renders")


if __name__ == "__main__":
    main()
