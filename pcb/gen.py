#!/usr/bin/env python3
"""HomeLab PDU Mon v1: board generator.

Fork of Shrike Lab HomeLab-PDU-V1 (CERN-OHL-S-2.0): 6 USB-C PD module ports off a Mean Well
HRP-300-24, each behind a TPS16630 eFuse and an INA237 monitor, with a W5500-EVB-Pico for
Prometheus metrics and remote power-cycling. See ../SPEC.md for the design.

Builds pdu.kicad_pcb from the PARTS and NETS tables below, autoroutes it with freerouting, pours
GND, then writes DRC, Gerbers, drill, BOM, netlist and renders. A net change is one edit and a
rerun. Coordinates are board mm, origin at the rear-left corner (rear edge at the top), y towards
the front of the enclosure.

  python3 gen.py              # full run (routes again only when placement or nets changed)
  python3 gen.py --reroute    # force a new freerouting pass
  python3 gen.py --place-only # placement plot only (place.png)
"""

import csv, hashlib, json, os, re, subprocess, sys, zipfile, shutil, math
import pcbnew
from pcbnew import FromMM, VECTOR2I

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "pdu"
PCB = os.path.join(HERE, NAME + ".kicad_pcb")
FREEROUTING = os.path.expanduser("~/.local/share/protocol-emulator/pcb/tools/freerouting.jar")
FPLIB = "/usr/share/kicad/footprints"
# never open a window: no display for anything this script launches
HEADLESS_ENV = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}

W, H = 104.0, 138.5          # board size (SPEC.md: enclosure X 2..106, Y 245.3..106.8)
OX, OY = 50.0, 40.0          # board origin on the KiCad page
CORNER_R = 2.0
TITLE = "HomeLab PDU Mon v1"
FORK_LINE = "fork of Shrike Lab HomeLab-PDU-V1, CERN-OHL-S-2.0"
OWNER_LINE = "Marcos Ashton Iglesias  2026-10-28"

NPORTS = 6
PITCH = 17.0                 # PD module pitch, as the original
PORT_X0 = 9.5                # port 1 module centre
MOD_Y = 22.6                 # module pins, from the rear edge (original: 22.6)
MOD_PIN_DX = 4.35            # + pin at centre - 4.35, - pin at centre + 4.35
BUS_Y, BUS_W = 82.5, 6.0     # +24V_SW bus, top copper
BUS_X0, BUS_X1 = 6.5, 97.5   # clear of the mid mounting holes


def port_cx(i):
    return PORT_X0 + PITCH * i


# ---------------------------------------------------------------- parts
# ref: (footprint, x, y, rot, value, description, LCSC part)
# footprint is "Lib:Name" from the KiCad library, an alias below, or "custom:<builder>".
ALIASES = {
    "R0603": "Resistor_SMD:R_0603_1608Metric",
    "C0603": "Capacitor_SMD:C_0603_1608Metric",
    "R1206": "Resistor_SMD:R_1206_3216Metric",
    "C1206": "Capacitor_SMD:C_1206_3216Metric",
    "F1206": "Fuse:Fuse_1206_3216Metric",
    "CP10": "Capacitor_SMD:CP_Elec_10x10.5",
    "SOT23": "Package_TO_SOT_SMD:SOT-23",
    "TP": "TestPoint:TestPoint_Pad_D1.0mm",
    "M3": "MountingHole:MountingHole_3.2mm_M3",
    "PH2": "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical",
    "MINIFIT": "Connector_Molex:Molex_Mini-Fit_Jr_5569-06A2_2x03_P4.20mm_Horizontal",
    "FAN": "Connector:FanPinHeader_1x04_P2.54mm_Vertical",
    "INA": "Package_SO:VSSOP-10_3x3mm_P0.5mm",
    "BUCK": "Package_TO_SOT_SMD:TSOT-23-6",
    "L5040": "Inductor_SMD:L_Sunlord_SWPA5040S",
}
PARTS = {}
NETS = {}
NO_CONNECT = []
DNP = set()


def part(ref, spec, x, y, rot, value, desc, lcsc=""):
    if ref in PARTS:
        raise SystemExit("duplicate ref " + ref)
    PARTS[ref] = (spec, x, y, rot, value, desc, lcsc)


def connect(net, *pads):
    NETS.setdefault(net, []).extend(pads)


# ---- PD port channels: one function, six copies
# INA237 address straps (A1, A0), GND or VS only (straps to SDA/SCL would cross the bus), so two
# I2C buses: A = I2C1 on GP2/GP3 (input monitor, ports 1-3), B = I2C0 on GP4/GP5 (ports 4-6).
ADDR = {0: ("GND", "GND"), 1: ("GND", "+3V3"), 2: ("+3V3", "GND"), 3: ("+3V3", "+3V3"),
        4: ("GND", "GND"), 5: ("GND", "+3V3"), 6: ("+3V3", "GND")}
I2C_BUS = {n: ("SDA", "SCL") if n <= 3 else ("SDA_B", "SCL_B") for n in range(0, 7)}
ADDR_HEX = {n: 0x40 + 4 * (ADDR[n][0] != "GND") + (ADDR[n][1] != "GND") for n in ADDR}
# Pico physical pin per port: ports 1-3 come in from the left, the farthest one to the deepest pin
PORT_GPIO = {1: 11, 2: 10, 3: 9, 4: 12, 5: 14, 6: 15}   # GP8, GP7, GP6, GP9, GP10, GP11


def port(n):
    """Port n (1..6): fuse, TPS16630 eFuse, 10 mOhm shunt + INA237, TVS and bulk, PD module pads.
    Column centred on the module, rear (module) to front (bus): TVS, bulk cap, shunt + INA,
    eFuse and its resistors, fuse."""
    cx = port_cx(n - 1)
    b = 100 * n
    P = "P%d_" % n
    # module pads (+ is pad 1)
    part("J%d" % (b + 1), "custom:pdmodule", cx, MOD_Y, 0, "PD%d" % n,
         "XPM52C 65 W USB-C PD module, owner-fitted on 2 pins")
    connect(P + "OUT", "J%d.1" % (b + 1))
    connect("GND", "J%d.2" % (b + 1))
    # output protection and bulk: + of the cap faces the module, the 3 A path runs up the
    # right of the column from the shunt
    part("D%d" % (b + 1), "Diode_SMD:D_SMB", cx, 28.2, 0, "SMBJ26A", "TVS 26 V, port output", "C123820")
    connect(P + "OUT", "D%d.1" % (b + 1)); connect("GND", "D%d.2" % (b + 1))
    part("C%d" % (b + 4), "CP10", cx, 37.6, 270, "470u", "470 uF 35 V, port output", "C976030")
    connect(P + "OUT", "C%d.1" % (b + 4)); connect("GND", "C%d.2" % (b + 4))
    part("C%d" % (b + 5), "C1206", cx + 3.2, 46.0, 0, "1u", "1 uF 50 V X7R, port output", "C1848")
    connect(P + "OUT", "C%d.2" % (b + 5)); connect("GND", "C%d.1" % (b + 5))
    # shunt and monitor
    part("R%d" % (b + 6), "R1206", cx + 7.0, 50.2, 90, "10m", "10 mOhm 1 % 0.5 W shunt", "C105362")
    connect(P + "EF", "R%d.1" % (b + 6)); connect(P + "OUT", "R%d.2" % (b + 6))
    # rotated 90: IN+ IN- VBUS GND VS along the top (rear) edge, left to right, facing the shunt
    part("U%d" % (b + 2), "INA", cx + 3.4, 54.4, 90, "INA237",
         "INA237 power monitor, I2C %s 0x%02X" % ("A" if n <= 3 else "B", ADDR_HEX[n]),
         "C2864837")
    a1, a0 = ADDR[n]
    u = "U%d." % (b + 2)
    connect(a1, u + "1"); connect(a0, u + "2")
    connect("ALERT_N", u + "3"); connect(I2C_BUS[n][0], u + "4"); connect(I2C_BUS[n][1], u + "5")
    connect("+3V3", u + "6"); connect("GND", u + "7")
    connect(P + "OUT", u + "8", u + "9"); connect(P + "EF", u + "10")
    part("C%d" % (b + 3), "C0603", cx + 7.6, 58.5, 0, "100n", "100 nF, INA237 VS", "C14663")
    connect("+3V3", "C%d.1" % (b + 3)); connect("GND", "C%d.2" % (b + 3))
    # eFuse, rotated 90: IN pins face the fuse (front), OUT pins face the shunt (rear)
    part("U%d" % (b + 1), "custom:htssop20", cx - 1.4, 65.0, 90, "TPS16630", "TPS16630 60 V 6 A eFuse",
         "C1849461")
    u = "U%d." % (b + 1)
    connect(P + "IN", u + "1", u + "2", u + "3", u + "4", u + "5", u + "6")   # 4, 5 are NC, tied to IN
    connect(P + "UVLO", u + "7"); connect(P + "OVP", u + "8")
    connect("GND", u + "9", u + "21")
    # MODE (12) open: latch off on overcurrent. The MCU resets a tripped port by toggling SHDN.
    connect(P + "DVDT", u + "10"); connect(P + "ILIM", u + "11"); connect(P + "SHDN", u + "13")
    connect("FLT_N", u + "15")
    connect(P + "EF", u + "18", u + "19", u + "20")
    NO_CONNECT.extend([u + "12", u + "14", u + "16", u + "17"])
    part("C%d" % (b + 1), "C0603", cx - 6.0, 65.0, 90, "100n", "100 nF 50 V, eFuse IN", "C14663")
    connect(P + "IN", "C%d.2" % (b + 1)); connect("GND", "C%d.1" % (b + 1))
    part("R%d" % (b + 1), "R0603", cx + 4.0, 62.6, 0, "4k53", "R_ILIM 4.53 k 1 %: 3.97 A", "C25971")
    connect(P + "ILIM", "R%d.1" % (b + 1)); connect("GND", "R%d.2" % (b + 1))
    part("C%d" % (b + 2), "C0603", cx + 2.0, 70.6, 90, "56n", "C_dVdT 56 nF: 28 ms ramp", "C282072")
    connect(P + "DVDT", "C%d.1" % (b + 2)); connect("GND", "C%d.2" % (b + 2))
    # UVLO / OVP divider: IN - 442k - UVLO - 8.06k - OVP - 20k - GND (20.1 V / 28.2 V)
    part("R%d" % (b + 2), "R0603", cx - 2.0, 73.4, 0, "442k", "UVLO/OVP divider top, 1 %", "C23175")
    connect(P + "IN", "R%d.1" % (b + 2)); connect(P + "UVLO", "R%d.2" % (b + 2))
    part("R%d" % (b + 3), "R0603", cx + 0.2, 70.6, 90, "8k06", "UVLO/OVP divider middle, 1 %", "C23112")
    connect(P + "UVLO", "R%d.1" % (b + 3)); connect(P + "OVP", "R%d.2" % (b + 3))
    part("R%d" % (b + 4), "R0603", cx + 1.4, 74.2, 0, "20k", "UVLO/OVP divider bottom, 1 %", "C4184")
    connect(P + "OVP", "R%d.1" % (b + 4)); connect("GND", "R%d.2" % (b + 4))
    # MCU high = port off; MCU dead or booting = SHDN floats high = port on
    part("Q%d" % (b + 1), "SOT23", cx + 5.6, 67.2, 0, "2N7002", "port off switch on SHDN", "C8545")
    connect(P + "OFF", "Q%d.1" % (b + 1)); connect("GND", "Q%d.2" % (b + 1)); connect(P + "SHDN", "Q%d.3" % (b + 1))
    part("R%d" % (b + 5), "R0603", cx + 5.6, 71.4, 90, "100k", "port off gate pull-down", "C25803")
    connect(P + "OFF", "R%d.2" % (b + 5)); connect("GND", "R%d.1" % (b + 5))
    connect(P + "OFF", "U1.%d" % PORT_GPIO[n])
    # fuse from the bus (pad 1 faces the bus)
    part("F%d" % (b + 1), "F1206", cx - 4.0, 77.0, 90, "5A", "fuse 5 A 1206 fast", "C57525")
    connect("+24V_SW", "F%d.1" % (b + 1)); connect(P + "IN", "F%d.2" % (b + 1))


for _n in range(1, NPORTS + 1):
    port(_n)

# ---- 24 V input, input monitor, master switch (original circuit, front-left)
part("J1", "MINIFIT", 19.4, 129.0, 180, "24V IN", "Mini-Fit Jr 2x3 right angle, from HRP-300 +V/-V", "C114098")
connect("GND", "J1.1", "J1.2", "J1.3"); connect("+24V_RAW", "J1.4", "J1.5", "J1.6")
part("F1", "Fuse:Fuse_Littelfuse-NANO2-451_453", 15.2, 118.0, 0, "15A", "fuse 15 A 2410 (Littelfuse 451)", "C44480")
connect("+24V_RAW", "F1.1"); connect("+24V_F", "F1.2")
part("C1", "CP10", 8.0, 109.6, 180, "470u", "470 uF 35 V, input", "C976030")
connect("+24V_F", "C1.1"); connect("GND", "C1.2")
part("C2", "CP10", 8.0, 97.8, 180, "470u", "470 uF 35 V, input", "C976030")
connect("+24V_F", "C2.1"); connect("GND", "C2.2")
part("D1", "Diode_SMD:D_SMC", 21.06, 109.2, 0, "SMCJ26A", "TVS 26 V, input", "C152085")
connect("+24V_F", "D1.1"); connect("GND", "D1.2")
part("C3", "C1206", 21.4, 120.0, 0, "1u", "1 uF 50 V X7R, input", "C1848")
connect("+24V_F", "C3.2"); connect("GND", "C3.1")
part("R1", "R1206", 10.0, 89.0, 0, "4k7", "bleeder 4.7 k 1206", "C17936")
connect("+24V_F", "R1.2"); connect("GND", "R1.1")
part("R2", "Resistor_SMD:R_2512_6332Metric", 27.4, 114.8, 180, "2m", "2 mOhm 1 % 2512 input shunt", "C160590")
connect("+24V_F", "R2.2"); connect("+24V", "R2.1")
part("U2", "INA", 34.0, 116.6, 90, "INA237", "INA237 input monitor, I2C 0x40", "C2864837")
connect("GND", "U2.1", "U2.2"); connect("ALERT_N", "U2.3"); connect("SDA", "U2.4"); connect("SCL", "U2.5")
connect("+3V3", "U2.6"); connect("GND", "U2.7"); connect("+24V", "U2.8", "U2.9"); connect("+24V_F", "U2.10")
part("C5", "C0603", 36.8, 113.6, 90, "100n", "100 nF, INA237 VS", "C14663")
connect("+3V3", "C5.1"); connect("GND", "C5.2")
part("Q1", "Package_TO_SOT_SMD:TO-263-2", 27.0, 95.6, 90, "NCEP40PT15D", "P-FET 40 V, master switch", "C918237")
connect("Q1_G", "Q1.1"); connect("+24V_SW", "Q1.2"); connect("+24V", "Q1.3")
# gate network as the original: BTN -47k- -100R- G, G -100k- S, 12 V zener G-S, 220n BTN-S
part("D2", "Diode_SMD:D_SOD-123", 34.0, 102.6, 90, "12V", "zener 12 V, gate clamp (K to source)", "C173429")
connect("+24V", "D2.1"); connect("Q1_G", "D2.2")
part("R5", "R0603", 36.2, 102.6, 270, "100k", "gate to source", "C25803")
connect("Q1_G", "R5.1"); connect("+24V", "R5.2")
part("R4", "R0603", 37.8, 102.6, 270, "100R", "gate series", "C22775")
connect("Q1_G", "R4.1"); connect("Q1_GR", "R4.2")
part("R3", "R0603", 39.4, 107.0, 270, "47k", "button to gate", "C25819")
connect("Q1_GR", "R3.1"); connect("BTN", "R3.2")
part("C6", "C0603", 36.2, 107.0, 90, "220n", "220 nF 50 V, soft switch", "C64705")
connect("+24V", "C6.1"); connect("BTN", "C6.2")
part("J2", "MINIFIT", 40.6, 129.0, 180, "BUTTON", "Mini-Fit Jr 2x3 right angle, latching button (original pinout)",
     "C114098")
connect("+24V", "J2.1"); connect("GND", "J2.3"); connect("BTN", "J2.5", "J2.6")
NO_CONNECT.extend(["J2.2", "J2.4"])
# button state for the MCU: +24V_SW / 11
part("R6", "R0603", 77.6, 87.8, 90, "100k", "24V_SW sense divider top", "C25803")
connect("+24V_SW", "R6.2"); connect("SW_SENSE", "R6.1")
part("R7", "R0603", 77.6, 90.8, 90, "10k", "24V_SW sense divider bottom", "C25804")
connect("SW_SENSE", "R7.2"); connect("GND", "R7.1")
part("C7", "C0603", 77.6, 107.4, 90, "100n", "24V_SW sense filter, at the Pico pin", "C14663")
connect("SW_SENSE", "C7.2"); connect("GND", "C7.1")

# ---- MCU: W5500-EVB-Pico (Pico pinout; GP16..21 belong to the W5500), RJ45 end at the front edge
PICO_X, PICO_Y = 57.0, 88.6
part("U1", "Module:RaspberryPi_Pico_Common_THT", PICO_X, PICO_Y, 0, "W5500-EVB-Pico",
     "WIZnet W5500-EVB-Pico on 2x 1x20 8.5 mm female headers, owner-fitted")
connect("GND", *["U1.%d" % p for p in (3, 8, 13, 18, 23, 28, 33, 38)])
connect("SDA", "U1.4"); connect("SCL", "U1.5"); connect("SDA_B", "U1.6"); connect("SCL_B", "U1.7")
connect("FAN2_TACH", "U1.1"); connect("LED_CTL", "U1.2")
connect("FLT_N", "U1.16"); connect("ALERT_N", "U1.17")
connect("FAN1_PWMC", "U1.19"); connect("FAN2_PWMC", "U1.20")
connect("FAN1_TACH", "U1.29"); connect("SW_SENSE", "U1.31"); connect("DCOK", "U1.32"); connect("NTC", "U1.34")
connect("ADC_VREF", "U1.35"); connect("+3V3", "U1.36"); connect("VSYS", "U1.39")
NO_CONNECT.extend(["U1.%d" % p for p in (21, 22, 24, 25, 26, 27, 30, 37, 40)])
part("C19", "C1206", 71.6, 89.4, 0, "10u", "10 uF 25 V, VSYS (under the module)", "C13585")
connect("VSYS", "C19.2"); connect("GND", "C19.1")
part("C20", "C1206", 64.2, 113.0, 180, "10u", "10 uF 25 V, 3V3 (under the module, on the bar)", "C13585")
connect("+3V3", "C20.2"); connect("GND", "C20.1")
part("D4", "Diode_SMD:D_SMA", 72.0, 94.4, 270, "SS14", "Schottky +5V to VSYS (USB on the Pico stays safe)", "C2480")
connect("VSYS", "D4.1"); connect("+5V", "D4.2")
# shared pull-ups, under the module beside their Pico pins (pad 1 signal, pad 2 the 3V3 bar)
PU_X, PU_BAR = 59.4, 62.0


def pico_pin_y(pin):
    return PICO_Y + 2.54 * (pin - 1)


part("R27", "R0603", PU_X, pico_pin_y(4), 0, "4k7", "SDA pull-up", "C23162")
connect("SDA", "R27.1"); connect("+3V3", "R27.2")
part("R28", "R0603", PU_X, pico_pin_y(5), 0, "4k7", "SCL pull-up", "C23162")
connect("SCL", "R28.1"); connect("+3V3", "R28.2")
part("R29", "R0603", PU_X, pico_pin_y(16), 0, "10k", "FLT_N pull-up", "C25804")
connect("FLT_N", "R29.1"); connect("+3V3", "R29.2")
part("R30", "R0603", PU_X, pico_pin_y(17), 0, "10k", "ALERT_N pull-up", "C25804")
connect("ALERT_N", "R30.1"); connect("+3V3", "R30.2")
part("R31", "R0603", PU_X, pico_pin_y(6), 0, "4k7", "SDA_B pull-up", "C23162")
connect("SDA_B", "R31.1"); connect("+3V3", "R31.2")
part("R32", "R0603", PU_X, pico_pin_y(7), 0, "4k7", "SCL_B pull-up", "C23162")
connect("SCL_B", "R32.1"); connect("+3V3", "R32.2")

# ---- aux bucks from +24V (unswitched): 5 V for the Pico, 12 V for the fans (front-right)
def buck(tag, u, x, y, vout, l_val, l_lcsc, rtop, rtop_lcsc, cout_desc, refs):
    """LMR16006X: CB 1, GND 2, FB 3 on the left, SHDN 4, VIN 5, SW 6 on the right. Block about
    22 x 11 mm around the chip. refs: L, D, Cin, Cin2, Cboot, Rtop, Rbot, Cout1, Cout2 or None, Rshdn."""
    L, D, Ci, Ci2, Cb, Rt, Rb, Co1, Co2, Rsh = refs
    net = "+%s" % vout
    part(u, "BUCK", x, y, 0, "LMR16006X", "LMR16006X 60 V 0.6 A buck, %s" % vout, "C87080")
    connect("+24V", u + ".5"); connect("GND", u + ".2"); connect(tag + "_SW", u + ".6")
    connect(tag + "_CB", u + ".1"); connect(tag + "_FB", u + ".3")
    NO_CONNECT.append(u + ".4")      # SHDN floats to enable (internal pull-up, datasheet 8.3.4)
    part(Rt, "R0603", x - 3.2, y + 0.6, 90, rtop, "FB top", rtop_lcsc)
    connect(net, Rt + ".1"); connect(tag + "_FB", Rt + ".2")
    part(Rb, "R0603", x - 4.8, y + 0.6, 90, "10k", "FB bottom", "C25804")
    connect(tag + "_FB", Rb + ".2"); connect("GND", Rb + ".1")
    part(Cb, "C0603", x + 0.6, y - 2.8, 0, "100n", "boot cap", "C14663")
    connect(tag + "_CB", Cb + ".1"); connect(tag + "_SW", Cb + ".2")
    part(Ci, "C1206", x + 0.6, y + 3.2, 0, "2u2", "2.2 uF 50 V X7R, buck input", "C50254")
    connect("GND", Ci + ".1"); connect("+24V", Ci + ".2")
    part(Ci2, "C0603", x + 0.6, y + 5.4, 0, "100n", "100 nF 50 V, buck input", "C14663")
    connect("GND", Ci2 + ".1"); connect("+24V", Ci2 + ".2")
    part(D, "Diode_SMD:D_SMA", x + 9.0, y + 3.2, 0, "SS14", "Schottky 40 V 1 A, buck catch", "C2480")
    connect(tag + "_SW", D + ".1"); connect("GND", D + ".2")
    part(L, "L5040", x + 8.0, y - 1.8, 0, l_val, "%s shielded 5040, buck" % l_val, l_lcsc)
    connect(tag + "_SW", L + ".1"); connect(net, L + ".2")
    part(Co1, "C1206", x + 12.6, y - 2.0, 90, "22u", cout_desc, "C12891")
    connect(net, Co1 + ".2"); connect("GND", Co1 + ".1")
    if Co2:
        part(Co2, "C1206", x + 15.2, y - 2.0, 90, "22u", cout_desc, "C12891")
        connect(net, Co2 + ".2"); connect("GND", Co2 + ".1")


buck("B5", "U3", 84.0, 99.6, "5V", "33u", "C86618", "56k", "C23206", "22 uF 25 V X5R, 5 V out",
     ["L1", "D3", "C8", "C9", "C10", "R9", "R10", "C11", "C12", "R8"])
buck("B12", "U4", 84.0, 110.4, "12V", "47u", "C86617", "147k", "C22878", "22 uF 25 V X5R, 12 V out",
     ["L2", "D5", "C13", "C14", "C15", "R11", "R12", "C16", None, "R13"])
# fan supply: R14 fitted = 12 V fans, swap to R15 for 5 V fans
part("R14", "R0603", 95.6, 117.6, 0, "0R", "fan supply 12 V (fit this or R15)", "C21189")
connect("+12V", "R14.2"); connect("VFAN", "R14.1")
part("R15", "R0603", 101.4, 103.6, 90, "0R", "fan supply 5 V (DNP by default)", "C21189")
connect("+5V", "R15.2"); connect("VFAN", "R15.1")
DNP.add("R15")


# ---- fans: 4-pin PWM headers (GND, VFAN, tach, PWM), tach pulled to 3V3, PWM open drain
def fan(k, j, x, y, q, r_pd, r_pu, r_s, tach_net):
    pwm = "FAN%d_PWM" % k
    part(j, "FAN", x, y, 0, "FAN%d" % k, "4-pin PWM fan header (Molex 47053)", "C240840")
    connect("GND", j + ".1"); connect("VFAN", j + ".2"); connect("FAN%d_TACHR" % k, j + ".3"); connect(pwm, j + ".4")
    part(q, "SOT23", x + 5.2, y - 5.6, 0, "2N7002", "fan PWM open drain", "C8545")
    connect("FAN%d_PWMC" % k, q + ".1"); connect("GND", q + ".2"); connect(pwm, q + ".3")
    part(r_pd, "R0603", x + 2.2, y - 5.6, 90, "100k", "PWM gate pull-down (MCU off = full speed)", "C25803")
    connect("FAN%d_PWMC" % k, r_pd + ".2"); connect("GND", r_pd + ".1")
    part(r_pu, "R0603", x + 0.6, y - 5.6, 90, "10k", "tach pull-up", "C25804")
    connect("FAN%d_TACHR" % k, r_pu + ".1"); connect("+3V3", r_pu + ".2")
    part(r_s, "R0603", x - 1.0, y - 5.6, 90, "1k", "tach series", "C21190")
    connect("FAN%d_TACHR" % k, r_s + ".1"); connect(tach_net, r_s + ".2")


fan(1, "J3", 80.6, 134.6, "Q2", "R16", "R17", "R18", "FAN1_TACH")
fan(2, "J4", 92.6, 134.6, "Q3", "R19", "R20", "R21", "FAN2_TACH")

# ---- small connectors: HRP DC-OK, PSU NTC, front status LED (between the button and the Pico)
part("J5", "PH2", 46.6, 122.0, 0, "DC-OK", "JST PH 2-pin: HRP CN100 pin 3 DC-OK, pin 5 GND", "C5251182")
connect("DCOK_IN", "J5.1"); connect("GND", "J5.2")
part("R22", "R0603", 45.4, 117.6, 0, "15k", "DC-OK divider top (5.6 V -> 3.2 V)", "C22809")
connect("DCOK_IN", "R22.1"); connect("DCOK", "R22.2")
part("R23", "R0603", 48.8, 117.6, 0, "20k", "DC-OK divider bottom", "C4184")
connect("DCOK", "R23.1"); connect("GND", "R23.2")
part("J6", "PH2", 46.6, 113.0, 0, "NTC", "JST PH 2-pin: 10 k NTC on the PSU case", "C5251182")
connect("NTC", "J6.1"); connect("GND", "J6.2")
part("R24", "R0603", 45.4, 108.6, 0, "10k", "NTC pull-up to ADC_VREF", "C25804")
connect("ADC_VREF", "R24.1"); connect("NTC", "R24.2")
part("C18", "C0603", 48.8, 108.6, 0, "100n", "NTC filter", "C14663")
connect("NTC", "C18.1"); connect("GND", "C18.2")
part("J7", "PH2", 46.6, 104.0, 0, "LED", "JST PH 2-pin: front status LED (1 anode, 2 cathode)", "C5251182")
connect("LED_A", "J7.1"); connect("LED_K", "J7.2")
part("R25", "R0603", 45.6, 99.8, 0, "220R", "LED current, from +5V", "C22962")
connect("+5V", "R25.1"); connect("LED_A", "R25.2")
part("Q4", "SOT23", 50.6, 99.8, 0, "2N7002", "LED low side", "C8545")
connect("LED_CTL", "Q4.1"); connect("GND", "Q4.2"); connect("LED_K", "Q4.3")
part("R26", "R0603", 52.4, 103.2, 90, "100k", "LED gate pull-down", "C25803")
connect("LED_CTL", "R26.2"); connect("GND", "R26.1")

# ---- test points and mounting holes
for i, (net, x, y) in enumerate([("GND", 2.4, 121.0), ("+5V", 96.6, 92.6), ("+3V3", PU_BAR, 132.0),
                                  ("+12V", 91.4, 117.6), ("+24V_SW", 45.0, 92.0)], start=1):
    part("TP%d" % i, "TP", x, y, 0, net, "test pad " + net)
    connect(net, "TP%d.1" % i)
HOLES = [(18.0, 28.0), (86.0, 28.0), (2.6, 88.6), (101.4, 88.6), (4.0, 134.5), (101.4, 113.4)]
for i, (x, y) in enumerate(HOLES, start=1):
    if i <= 2:
        part("H%d" % i, "MountingHole:MountingHole_2.7mm_M2.5", x, y, 0, "M2.5", "M2.5 standoff hole (between modules)")
    else:
        part("H%d" % i, "M3", x, y, 0, "M3", "M3 standoff hole")

# Pico pins with nothing on them (W5500 bus, RUN, 3V3_EN, VBUS) are listed in NO_CONNECT above.
# Ports carry 3 A, the input path 12.5 A: those are hand tracks below, the rest is autorouted on a 4-layer board (In1 solid GND).
# The port nets' 3 A copper is all hand tracks, so what is left for the router there (INA sense
# pins, eFuse P_IN, small caps) is signal width.
POWER_NETS = {"GND": 0.5, "+3V3": 0.4, "+5V": 0.5, "VSYS": 0.5, "+12V": 0.5, "VFAN": 0.4,
              "+24V": 0.5}

# ---------------------------------------------------------------- silk labels
PORT_ADDR_TEXT = {n: "%s 0x%02X" % ("A" if n <= 3 else "B", ADDR_HEX[n]) for n in range(0, NPORTS + 1)}


# ---------------------------------------------------------------- helpers
def P(x, y):
    return VECTOR2I(FromMM(OX + x), FromMM(OY + y))


def seg(parent, layer, a, b, width=0.15):
    s = pcbnew.PCB_SHAPE(parent, pcbnew.SHAPE_T_SEGMENT)
    s.SetLayer(layer)
    s.SetStart(a)
    s.SetEnd(b)
    s.SetWidth(FromMM(width))
    parent.Add(s)
    return s


def rect(parent, layer, x0, y0, x1, y1, width=0.15, local=False):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    f = (lambda x, y: VECTOR2I(FromMM(x), FromMM(y))) if local else P
    for a, b in zip(pts, pts[1:]):
        seg(parent, layer, f(*a), f(*b), width)


def text(board, s, x, y, size=1.0, layer=pcbnew.F_SilkS, rot=0, just="C", thick=0.15, bold=False):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetLayer(layer)
    t.SetTextSize(VECTOR2I(FromMM(size), FromMM(size)))
    t.SetTextThickness(FromMM(thick if not bold else max(thick, size * 0.2)))
    t.SetTextAngleDegrees(rot)
    t.SetHorizJustify({"L": pcbnew.GR_TEXT_H_ALIGN_LEFT, "R": pcbnew.GR_TEXT_H_ALIGN_RIGHT,
                       "C": pcbnew.GR_TEXT_H_ALIGN_CENTER}[just])
    if layer == pcbnew.B_SilkS:
        t.SetMirrored(True)
    t.SetPosition(P(x, y))
    board.Add(t)
    return t


def add_pad(fp, num, x, y, drill=1.0, size=1.7, square=False):
    pad = pcbnew.PAD(fp)
    pad.SetNumber(str(num))
    pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
    pad.SetShape(pcbnew.PAD_SHAPE_RECT if square else pcbnew.PAD_SHAPE_CIRCLE)
    pad.SetSize(VECTOR2I(FromMM(size), FromMM(size)))
    pad.SetDrillSize(VECTOR2I(FromMM(drill), FromMM(drill)))
    pad.SetLayerSet(pcbnew.PAD.PTHMask())
    pad.SetPosition(VECTOR2I(FromMM(x), FromMM(y)))
    fp.Add(pad)
    return pad


def fp_text(fp, s, x, y, size=1.0, layer=pcbnew.F_SilkS, rot=0, just="C"):
    t = pcbnew.PCB_TEXT(fp)
    t.SetText(s)
    t.SetLayer(layer)
    t.SetTextSize(VECTOR2I(FromMM(size), FromMM(size)))
    t.SetTextThickness(FromMM(0.15))
    t.SetTextAngleDegrees(rot)
    t.SetHorizJustify({"L": pcbnew.GR_TEXT_H_ALIGN_LEFT, "R": pcbnew.GR_TEXT_H_ALIGN_RIGHT,
                       "C": pcbnew.GR_TEXT_H_ALIGN_CENTER}[just])
    t.SetPosition(VECTOR2I(FromMM(x), FromMM(y)))
    fp.Add(t)


def build_pdmodule(board):
    """Standing XPM52C PD module, as the original: + on a 90 degree pin header, - on a bent wire,
    8.7 mm apart across the module. Origin between the pins. The module body (fab/courtyard)
    runs from the pins to just past the rear edge, where its USB-C sits."""
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("pdu", "PD_Module_XPM52C_Standing"))
    fp.SetAttributes(pcbnew.FP_THROUGH_HOLE)
    add_pad(fp, 1, -MOD_PIN_DX, 0, drill=1.0, size=2.0, square=True)
    add_pad(fp, 2, MOD_PIN_DX, 0, drill=1.0, size=2.0)
    fp_text(fp, "+", -MOD_PIN_DX - 2.0, 0.0, 1.2, just="R")
    fp_text(fp, "-", MOD_PIN_DX + 2.0, 0.0, 1.2, just="L")
    rect(fp, pcbnew.F_Fab, -6.0, -MOD_Y - 2.0, 6.0, 1.6, 0.1, local=True)
    rect(fp, pcbnew.F_CrtYd, -6.2, -MOD_Y - 2.2, 6.2, 1.8, 0.05, local=True)
    return fp


def build_htssop20(board):
    """TI PWP0020T (TPS16630PWP): KiCad's HTSSOP-20 with the datasheet thermal pad, 2.96 x 2.96,
    and nine 0.3 mm vias in it to the bottom GND pour."""
    fp = pcbnew.FootprintLoad(os.path.join(FPLIB, "Package_SO.pretty"), "HTSSOP-20-1EP_4.4x6.5mm_P0.65mm_EP2.74x3.86mm")
    fp.SetFPID(pcbnew.LIB_ID("pdu", "HTSSOP-20-1EP_4.4x6.5mm_P0.65mm_EP2.96x2.96mm_ThermalVias"))
    for pad in list(fp.Pads()):
        if pad.GetNumber() in ("", "21"):
            fp.Remove(pad)
    ep = pcbnew.PAD(fp)
    ep.SetNumber("21")
    ep.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    ep.SetShape(pcbnew.PAD_SHAPE_RECT)
    ep.SetSize(VECTOR2I(FromMM(2.96), FromMM(2.96)))
    ep.SetLayerSet(pcbnew.PAD.SMDMask())
    ep.SetPosition(VECTOR2I(0, 0))
    fp.Add(ep)
    for i in (-1, 0, 1):
        for j in (-1, 0, 1):
            v = add_pad(fp, 21, i * 0.95, j * 0.95, drill=0.3, size=0.8)
            v.SetLayerSet(pcbnew.LSET.AllCuMask())
    return fp


def load_fp(spec):
    lib, name = spec.split(":")
    fp = pcbnew.FootprintLoad(os.path.join(FPLIB, lib + ".pretty"), name)
    if fp is None:
        raise SystemExit("footprint not found: " + spec)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


CUSTOM = {"custom:pdmodule": build_pdmodule, "custom:htssop20": build_htssop20}

# ---------------------------------------------------------------- build
DRU = """(version 1)
(rule "thermal spokes"
  (condition "A.Type == 'Pad'")
  (constraint min_resolved_spokes 1))
(rule "eFuse thermal vias"
  (condition "A.Type == 'Pad' && A.Pad_Number == '21' && B.Type == 'Pad' && B.Pad_Number == '21'")
  (constraint hole_to_hole (min 0.3mm)))
"""


def build():
    open(os.path.join(HERE, NAME + ".kicad_dru"), "w").write(DRU)
    board = pcbnew.NewBoard(PCB)
    board.SetCopperLayerCount(4)
    board.SetLayerType(pcbnew.In1_Cu, pcbnew.LT_POWER)
    ds = board.GetDesignSettings()
    ds.SetBoardThickness(FromMM(1.6))
    ds.SetAuxOrigin(P(0, H))   # bottom-left corner: placement files read in board mm, y up
    # JLCPCB, 2 oz outer: 0.15 mm track and space, PTH annular ring 0.254 mm or more
    ds.m_TrackMinWidth = FromMM(0.15)
    ds.m_MinClearance = FromMM(0.15)
    ds.m_ViasMinSize = FromMM(0.8)
    ds.m_ViasMinAnnularWidth = FromMM(0.2)    # vias are 0.25; JST PH pads are 0.225
    ds.m_MinThroughDrill = FromMM(0.3)
    ds.m_HoleClearance = FromMM(0.25)
    ds.m_HoleToHoleMin = FromMM(0.5)
    ds.m_CopperEdgeClearance = FromMM(0.4)
    ds.m_SilkClearance = FromMM(0.0)
    ns = ds.m_NetSettings
    d = ns.GetDefaultNetclass()
    d.SetTrackWidth(FromMM(0.25)); d.SetClearance(FromMM(0.2))
    d.SetViaDiameter(FromMM(0.8)); d.SetViaDrill(FromMM(0.3))
    for name, width in sorted(set((("Power%g" % w).replace(".", "_"), w) for w in POWER_NETS.values())):
        nc = pcbnew.NETCLASS(name)
        nc.SetTrackWidth(FromMM(width)); nc.SetClearance(FromMM(0.2))
        nc.SetViaDiameter(FromMM(0.8)); nc.SetViaDrill(FromMM(0.3))
        nc.SetPriority(0)
        ns.SetNetclass(name, nc)
    for net, width in POWER_NETS.items():
        ns.SetNetclassPatternAssignment(net, ("Power%g" % width).replace(".", "_"))

    # outline with rounded corners
    r = CORNER_R
    seg(board, pcbnew.Edge_Cuts, P(r, 0), P(W - r, 0), 0.1)
    seg(board, pcbnew.Edge_Cuts, P(W, r), P(W, H - r), 0.1)
    seg(board, pcbnew.Edge_Cuts, P(W - r, H), P(r, H), 0.1)
    seg(board, pcbnew.Edge_Cuts, P(0, H - r), P(0, r), 0.1)
    k = r * (1 - math.sqrt(0.5))
    for a_, m_, e_ in [((0, r), (k, k), (r, 0)), ((W - r, 0), (W - k, k), (W, r)),
                       ((W, H - r), (W - k, H - k), (W - r, H)), ((r, H), (k, H - k), (0, H - r))]:
        a = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_ARC)
        a.SetLayer(pcbnew.Edge_Cuts)
        a.SetWidth(FromMM(0.1))
        a.SetArcGeometry(P(*a_), P(*m_), P(*e_))
        board.Add(a)

    nets = {}
    for name in NETS:
        n = pcbnew.NETINFO_ITEM(board, name)
        board.Add(n)
        nets[name] = n

    fps = {}
    for ref, (spec, x, y, rot, value, _desc, _pn) in PARTS.items():
        spec = ALIASES.get(spec, spec)
        fp = CUSTOM[spec](board) if spec in CUSTOM else load_fp(spec)
        if ref == "U1":
            for z in list(fp.Zones()):
                fp.Remove(z)
            for item in list(fp.GraphicalItems()):          # on 8.5 mm sockets, parts fit under it
                if item.GetLayer() == pcbnew.F_CrtYd:
                    fp.Remove(item)
        fp.SetReference(ref)
        fp.SetValue(value)
        fp.Value().SetVisible(False)
        fp.Value().SetLayer(pcbnew.F_Fab)
        if ref in DNP:
            fp.SetDNP(True)
            fp.SetExcludedFromBOM(True)
        board.Add(fp)
        fp.SetPosition(P(x, y))
        fp.SetOrientationDegrees(rot)
        fps[ref] = fp
        fp.Reference().SetTextSize(VECTOR2I(FromMM(0.8), FromMM(0.8)))
        fp.Reference().SetTextThickness(FromMM(0.12))

    padmap = {}
    for ref, fp in fps.items():
        for pad in fp.Pads():
            if pad.GetNumber():
                padmap.setdefault("%s.%s" % (ref, pad.GetNumber()), []).append(pad)
    used = set()
    for name, pads in NETS.items():
        for pp in pads:
            if pp not in padmap:
                raise SystemExit("no pad %s (net %s)" % (pp, name))
            if pp in used:
                raise SystemExit("pad %s in two nets" % pp)
            used.add(pp)
            for pad in padmap[pp]:
                pad.SetNet(nets[name])
    for pp in padmap:
        if pp not in used and pp not in NO_CONNECT:
            raise SystemExit("pad %s neither connected nor listed in NO_CONNECT" % pp)

    for net, layer, width, pts in hand_tracks(fps):
        if net is None:
            net = next(n for n, pads in NETS.items() if any(pad_xy(fps[p.split(".")[0]], p.split(".")[1]) == pts[0]
                                                              for p in pads if p.split(".")[0] in fps))
        for a, b in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(P(*a)); t.SetEnd(P(*b))
            t.SetWidth(FromMM(width))
            t.SetLayer(board.GetLayerID(layer))
            t.SetNet(nets[net])
            t.SetLocked(True)
            board.Add(t)
    for net, (x, y) in VIAS:
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(P(x, y))
        v.SetWidth(FromMM(0.8)); v.SetDrill(FromMM(0.3))
        v.SetNet(nets[net])
        v.SetLocked(True)
        board.Add(v)

    silk(board, fps)
    write_libs(board, fps)
    return board, fps, nets


def pad_xy(fp, num):
    for pad in fp.Pads():
        if pad.GetNumber() == str(num):
            p = pad.GetPosition()
            return pcbnew.ToMM(p.x) - OX, pcbnew.ToMM(p.y) - OY
    raise KeyError(num)


VIAS = []   # (net, (x, y)) hand vias, filled in by hand_tracks()

# Shared signal bus, hand-routed like a backplane. B.Cu rails behind the +24V_SW bus carry ALERT,
# SDA, SCL and FLT across all six columns (SDA/SCL split between bus A, ports 1-3, and bus B,
# ports 4-6); a 3V3 rail on In2 runs just below the INAs. Trunks on In2 pass under the +24V_SW bus
# to the Pico pins, nested so that none cross: the trunk nearest the pins ends first.
RAIL_33 = 58.6
BUS_RAIL = {"ALERT_N": 75.4, "SDA": 76.3, "SDA_B": 76.3, "SCL": 77.2, "SCL_B": 77.2, "FLT_N": 78.1}
RAIL_X0, RAIL_X1 = 10.5, 100.5
# net: (trunk x, Pico physical pin); FLT's trunk starts at port 4's FLT tap via (x = cx + 0.6)
TRUNKS = {"SDA": (58.14, 4), "SCL": (58.88, 5), "SDA_B": (59.62, 6), "SCL_B": (60.36, 7),
          "FLT_N": (port_cx(3) + 0.6, 16), "ALERT_N": (61.86, 17)}
TRUNK_33 = (70.6, 36)


def off_tracks(n, cx, roff):
    """Port n's off line. In2 from the column down past the bus to y 88, then B.Cu to the Pico.
    Ports 1-3 enter the left pin row from the left; 4-6 run under the module and enter it from the
    right, crossing the right pin row between pins."""
    net = "P%d_OFF" % n
    v1 = (cx + 6.8, 71.4)
    px_, py_ = pico_xy(PORT_GPIO[n])
    T = [(net, "F.Cu", 0.25, [roff, v1])]
    if n == 6:                       # mounting hole H4 sits in line: jog left after the rail vias
        v2 = (97.6, 86.8)
        T.append((net, "In2.Cu", 0.25, [v1, (v1[0], 84.0), v2]))
    elif n <= 3:                     # under the bus, then across on In2 to a strip left of the Pico
        y, x = {1: (88.4, 52.5), 2: (87.6, 53.4), 3: (86.8, 54.3)}[n]
        v2 = (x, y)
        T.append((net, "In2.Cu", 0.25, [v1, (v1[0], y), v2]))
    else:
        v2 = (v1[0], 88.0)
        T.append((net, "In2.Cu", 0.25, [v1, v2]))
    VIAS.extend([(net, v1), (net, v2)])
    if n <= 3:
        pts = [v2, (v2[0], py_), (px_, py_)]
    elif n == 4:
        pts = [v2, (v2[0], py_), (px_, py_)]
    else:
        cross = py_ - 1.27                            # between two pins of the right row
        pts = [v2, (v2[0], cross), (70.0 + (n - 5), cross), (70.0 + (n - 5), py_), (px_, py_)]
    T.append((net, "B.Cu", 0.25, pts))
    return T


def input_monitor_tracks(T, px):
    """U2, the 24 V input INA237, rotated so IN+ IN- VBUS GND VS face the shunt R2. IN-/VBUS tap the
    +24V copper, IN+ reaches +24V_F through In2. A1/A0 to GND. ALERT, SDA, SCL drop to vias and run
    on In2 to the Pico, ALERT between the two pin rows of J2."""
    p = {k: px("U2", k) for k in range(1, 11)}
    T.append(("+24V", "F.Cu", 0.25, [p[8], p[9], (p[9][0], 111.4), (31.0, 111.4)]))
    vin = (32.4, 112.6)
    T.append(("+24V_F", "F.Cu", 0.25, [p[10], (p[10][0], 113.2), vin]))
    T.append(("+24V_F", "In2.Cu", 0.25, [vin, (18.4, 111.5)]))
    VIAS.extend([("+24V_F", vin), ("+24V_F", (18.4, 111.5))])     # on the +24V_F bar
    vg = (35.3, 112.4)
    T.append(("GND", "F.Cu", 0.25, [p[7], (p[7][0], 112.9), vg]))
    VIAS.append(("GND", vg))
    c5 = px("C5", 1)
    T.append(("+3V3", "F.Cu", 0.25, [p[6], (c5[0], p[6][1]), c5]))
    T.append(("GND", "F.Cu", 0.25, [px("C5", 2), (px("C5", 2)[0], vg[1]), vg]))
    vg2 = (31.9, 118.9)
    T.append(("GND", "F.Cu", 0.25, [p[2], p[1], vg2]))
    VIAS.append(("GND", vg2))
    # straight drops off the 0.5 mm pad row to staggered vias: SCL first and right, SDA lower, ALERT left
    va, vs, vc = (33.0, 120.4), (35.9, 120.7), (37.3, 119.75)
    T.append(("ALERT_N", "F.Cu", 0.25, [p[3], (p[3][0], 120.0), va]))
    T.append(("SDA", "F.Cu", 0.25, [p[4], (p[4][0], vs[1]), vs]))
    T.append(("SCL", "F.Cu", 0.25, [p[5], (p[5][0], vc[1]), vc]))
    VIAS.extend([("ALERT_N", va), ("SDA", vs), ("SCL", vc)])
    a17, s4, c5_ = pico_xy(17), pico_xy(4), pico_xy(5)
    # ALERT passes between J2's pins 6 and 5, then between its two pin rows
    T.append(("ALERT_N", "In2.Cu", 0.25, [va, (34.3, 121.5), (34.3, 126.25), (54.5, 126.25), (54.5, a17[1]), a17]))
    T.append(("SDA", "In2.Cu", 0.25, [vs, (vs[0], 117.0), (43.5, 109.4), (43.5, 98.0), (55.0, s4[1]), s4]))
    T.append(("SCL", "In2.Cu", 0.25, [vc, (44.4, 112.65), (44.4, 99.5), (55.0, c5_[1]), c5_]))


def front_tracks(T, px):
    """Master switch gate network (original circuit), button, J2's +24V, the input cap C3, the input
    monitor's 3V3, the pull-ups under the module and the VSYS diode and cap."""
    g = px("Q1", 1)
    d2a, d2k = px("D2", 2), px("D2", 1)
    T.append(("Q1_G", "F.Cu", 0.25, [(g[0], g[1] - 2.05), (g[0], 100.6), (d2a[0], 100.6), d2a]))
    # R4 and R5 rotated so their gate pads share the rear row with D2's anode
    T.append(("Q1_G", "F.Cu", 0.25, [d2a, (35.2, d2a[1] + 0.25), (35.2, px("R5", 1)[1]), px("R5", 1), px("R4", 1)]))
    T.append(("+24V", "F.Cu", 0.4, [px("R5", 2), (px("R5", 2)[0], d2k[1]), d2k, (31.0, d2k[1])]))
    T.append(("+24V", "F.Cu", 0.4, [px("C6", 1), (px("C6", 1)[0], 108.6), (31.0, 108.6)]))
    r4g, r3g = px("R4", 2), px("R3", 1)
    T.append(("Q1_GR", "F.Cu", 0.25, [r4g, (38.8, r4g[1] + 0.5), (38.8, r3g[1] - 1.2), (r3g[0], r3g[1] - 0.6), r3g]))
    c6b, r3b = px("C6", 2), px("R3", 2)
    T.append(("BTN", "F.Cu", 0.25, [c6b, (37.2, c6b[1]), (37.2, 108.0), (r3b[0], 108.0), r3b]))
    vb = (40.6, 108.4)
    T.append(("BTN", "F.Cu", 0.25, [r3b, vb]))
    VIAS.append(("BTN", vb))
    j5, j6 = px("J2", 5), px("J2", 6)
    T.append(("BTN", "B.Cu", 0.4, [vb, (40.6, 120.5), (37.5, 122.0), j5]))
    T.append(("BTN", "F.Cu", 0.4, [j6, j5]))
    j1 = px("J2", 1)
    T.append(("+24V", "F.Cu", 0.4, [j1, (j1[0], 126.25), (26.5, 126.25), (26.5, 117.0), (29.54, 117.0), (29.54, 116.3)]))
    c3f, c3g = px("C3", 2), px("C3", 1)
    T.append(("+24V_F", "F.Cu", 0.5, [c3f, (c3f[0], 116.3)]))
    VIAS.append(("GND", (c3g[0], 121.0)))
    T.append(("GND", "F.Cu", 0.4, [c3g, (c3g[0], 121.0)]))
    # U2's 3V3 along a free channel above J6 to the pull-up bar under the module
    c5 = px("C5", 1)
    T.append(("+3V3", "F.Cu", 0.3, [c5, (38.4, c5[1]), (38.4, 110.1), (52.5, 110.1), (53.0, 110.23), (PU_BAR, 110.23)]))
    ys = [pico_pin_y(p) for p in (4, 5, 6, 7, 16, 17)]
    T.append(("+3V3", "F.Cu", 0.4, [(PU_BAR, min(ys) - 1.0), (PU_BAR, max(ys) + 3.0)]))
    p36 = pico_xy(36)
    T.append(("+3V3", "F.Cu", 0.4, [(PU_BAR, p36[1]), p36]))
    for ref, pin in (("R27", 4), ("R28", 5), ("R29", 16), ("R30", 17), ("R31", 6), ("R32", 7)):
        T.append((None, "F.Cu", 0.25, [px(ref, 1), pico_xy(pin)]))
        T.append(("+3V3", "F.Cu", 0.25, [px(ref, 2), (PU_BAR, px(ref, 2)[1])]))
    T.append(("+3V3", "F.Cu", 0.25, [px("TP3", 1), (PU_BAR, px("TP3", 1)[1])]))
    # C20 on the 3V3 bar, its GND to a via
    T.append(("+3V3", "F.Cu", 0.4, [px("C20", 2), (PU_BAR, px("C20", 2)[1])]))
    T.append(("GND", "F.Cu", 0.4, [px("C20", 1), (px("C20", 1)[0], 114.6)]))
    VIAS.append(("GND", (px("C20", 1)[0], 114.6)))
    # +24V_SW sense: divider right behind the bus, filter at the Pico pin, a lane outside the right pin row
    r6s, r6b, r7s, r7g = px("R6", 1), px("R6", 2), px("R7", 2), px("R7", 1)
    T.append(("+24V_SW", "F.Cu", 0.3, [r6b, (r6b[0], BUS_Y + BUS_W / 2 - 0.3)]))
    p31 = pico_xy(31)
    T.append(("SW_SENSE", "F.Cu", 0.25, [r6s, r7s]))
    T.append(("SW_SENSE", "F.Cu", 0.25, [r7s, (76.6, r7s[1] + 0.6), (76.6, p31[1]), p31]))
    T.append(("SW_SENSE", "F.Cu", 0.25, [px("C7", 2), (76.6, px("C7", 2)[1])]))
    vg7 = (78.9, r7g[1])
    T.append(("GND", "F.Cu", 0.3, [r7g, vg7]))
    VIAS.append(("GND", vg7))
    vg8 = (78.9, px("C7", 1)[1])
    T.append(("GND", "F.Cu", 0.3, [px("C7", 1), vg8]))
    VIAS.append(("GND", vg8))
    # +24V (unswitched) to the two bucks: In2 along the front, between the Pico's pins and between
    # the fan headers, then up to buck 12 V's input caps
    j1 = px("J2", 1)
    vb24 = (90.4, 116.6)
    lane = (pico_pin_y(17) + pico_pin_y(18)) / 2        # between two Pico pins, clear of the fan pegs
    T.append(("+24V", "In2.Cu", 0.5, [(j1[0], lane), (vb24[0], lane), vb24]))
    VIAS.append(("+24V", vb24))
    ci2 = px("C14", 2)
    T.append(("+24V", "F.Cu", 0.5, [vb24, (86.2, vb24[1]), ci2]))
    p39 = pico_xy(39)
    T.append(("VSYS", "F.Cu", 0.5, [px("D4", 1), p39]))
    T.append(("VSYS", "F.Cu", 0.5, [px("C19", 2), p39]))
    vg = (69.0, px("C19", 1)[1])
    T.append(("GND", "F.Cu", 0.5, [px("C19", 1), vg]))
    VIAS.append(("GND", vg))


CI = {"U3": "C8", "U4": "C13"}
CI2 = {"U3": "C9", "U4": "C14"}
CB = {"U3": "C10", "U4": "C15"}


def buck_tracks(T, px):
    """Each buck's switch node: SW pin, boot cap, inductor and catch diode."""
    for tag, u, L, D, Cb in (("B5", "U3", "L1", "D3", "C10"), ("B12", "U4", "L2", "D5", "C15")):
        sw, l1, d1, cb = px(u, 6), px(L, 1), px(D, 1), px(Cb, 2)
        net = tag + "_SW"
        T.append((net, "F.Cu", 0.6, [sw, (sw[0] + 1.2, sw[1]), (l1[0], sw[1]), l1]))
        T.append((net, "F.Cu", 0.6, [l1, (l1[0], l1[1] + 2.1), (d1[0], d1[1] - 0.9), d1]))
        T.append((net, "F.Cu", 0.3, [cb, (cb[0], sw[1]), sw]))
        # GND pin out between the FB divider and the CB/FB pins to its own via
        gp = px(u, 2)
        vg = (gp[0] - 1.26, gp[1] - 1.4)
        T.append(("GND", "F.Cu", 0.25, [gp, (gp[0] - 0.96, gp[1]), vg]))
        VIAS.append(("GND", vg))
        # input: VIN pin, 2.2 uF, 100 nF and the SHDN pull-up's top, all +24V
        vin, ci, ci2 = px(u, 5), px(CI[u], 2), px(CI2[u], 2)
        T.append(("+24V", "F.Cu", 0.3, [vin, (ci[0] + 0.45, vin[1]), (ci[0] + 0.45, ci[1]), ci, ci2]))
        cb1, cbp = px(CB[u], 1), px(u, 1)
        T.append((tag + "_CB", "F.Cu", 0.25, [cb1, (cb1[0], cb1[1] + 0.5), (cbp[0] + 0.34, cbp[1] - 0.65), cbp]))
    # buck 5 V's input from buck 12 V's, on In2 (the front +24V feed lands at vb24)
    vb5 = (88.6, 105.4)
    T.append(("+24V", "In2.Cu", 0.5, [(90.4, 116.6), vb5]))
    VIAS.append(("+24V", vb5))
    T.append(("+24V", "F.Cu", 0.4, [vb5, (86.2, vb5[1]), px("C9", 2)]))


def pico_xy(pin):
    return (PICO_X, PICO_Y + 2.54 * (pin - 1)) if pin <= 20 else (PICO_X + 17.78, PICO_Y + 2.54 * (40 - pin))


def bus_tracks(T):
    for net, y in BUS_RAIL.items():
        tx = TRUNKS[net][0]
        x0, x1 = {"SDA": (RAIL_X0, tx), "SCL": (RAIL_X0, tx), "SDA_B": (tx, RAIL_X1),
                  "SCL_B": (tx, RAIL_X1)}.get(net, (RAIL_X0, RAIL_X1))
        T.append((net, "B.Cu", 0.25, [(x0, y), (x1, y)]))
    for net, (tx, pin) in TRUNKS.items():
        y0 = BUS_RAIL[net]
        if net != "FLT_N":
            VIAS.append((net, (tx, y0)))
        px_, py_ = pico_xy(pin)
        T.append((net, "In2.Cu", 0.25, [(tx, y0), (tx, py_), (px_, py_)]))
    T.append(("+3V3", "In2.Cu", 0.4, [(RAIL_X0, RAIL_33), (RAIL_X1, RAIL_33)]))
    tx, pin = TRUNK_33
    px_, py_ = pico_xy(pin)
    T.append(("+3V3", "In2.Cu", 0.4, [(tx, RAIL_33), (tx, py_), (px_, py_)]))


def hand_tracks(fps):
    """High-current copper drawn before autorouting (locked): the 12.5 A input path, the
    +24V_SW bus across the board and each port's 3 A path bus -> fuse -> eFuse -> shunt -> module."""
    T = []
    del VIAS[:]
    px = lambda ref, n: pad_xy(fps[ref], n)
    # bus, and the master FET's drain tab onto it
    T.append(("+24V_SW", "F.Cu", BUS_W, [(BUS_X0, BUS_Y), (BUS_X1, BUS_Y)]))
    tab = px("Q1", 2)
    T.append(("+24V_SW", "F.Cu", 6.0, [(tab[0], BUS_Y), (tab[0], tab[1])]))
    # J1 +24V_RAW pins -> F1 -> +24V_F bar (caps, TVS, bleeder) -> shunt -> Q1 source
    j4, j6 = px("J1", 4), px("J1", 6)
    f1a, f1b = px("F1", 1), px("F1", 2)
    T.append(("+24V_RAW", "F.Cu", 3.0, [j6, j4]))
    T.append(("+24V_RAW", "F.Cu", 2.5, [(f1a[0], j6[1]), f1a]))
    bx = f1b[0]
    T.append(("+24V_F", "F.Cu", 3.0, [f1b, (bx, px("C2", 1)[1])]))
    for ref in ("C1", "C2"):
        T.append(("+24V_F", "F.Cu", 2.0, [(bx, px(ref, 1)[1]), px(ref, 1)]))
    T.append(("+24V_F", "F.Cu", 2.0, [(bx, px("D1", 1)[1]), px("D1", 1)]))
    r2f, r2s = px("R2", 2), px("R2", 1)
    T.append(("+24V_F", "F.Cu", 3.0, [(bx, r2f[1]), r2f]))
    qs = px("Q1", 3)
    T.append(("+24V", "F.Cu", 3.0, [r2s, (qs[0], r2s[1]), qs]))
    bus_tracks(T)
    input_monitor_tracks(T, px)
    front_tracks(T, px)
    buck_tracks(T, px)
    for n in range(1, NPORTS + 1):
        b = 100 * n
        cx = port_cx(n - 1)
        f_bus, f_in = px("F%d" % (b + 1), 1), px("F%d" % (b + 1), 2)
        T.append(("+24V_SW", "F.Cu", 1.5, [(f_bus[0], BUS_Y), f_bus]))
        u = "U%d" % (b + 1)
        # IN pins 1-3 joined along the pad row, fed from the fuse below
        i1, i6 = px(u, 1), px(u, 6)
        T.append(("P%d_IN" % n, "F.Cu", 0.4, [i1, i6]))
        T.append(("P%d_IN" % n, "F.Cu", 1.2, [f_in, (i1[0], f_in[1] - 1.2), (i1[0], i1[1] + 1.2)]))
        T.append(("P%d_IN" % n, "F.Cu", 0.6, [(i1[0], i1[1] + 1.2), i1]))
        # OUT pins 18-20 joined, then up to the shunt
        o20, o18 = px(u, 20), px(u, 18)
        rs1, rs2 = px("R%d" % (b + 6), 1), px("R%d" % (b + 6), 2)
        # eFuse OUT -> shunt on B.Cu, so the column stays open on F.Cu for the INA and FLT signals:
        # OUT pins up to four vias at y 60.2, B.Cu across and up the right edge, four vias, F.Cu to the shunt
        T.append(("P%d_EF" % n, "F.Cu", 0.4, [o20, o18]))
        ef_l = [(cx - 5.6 + 1.0 * i, 60.2) for i in range(4)]
        ef_r = [(rs1[0], 54.0 + 1.0 * i) for i in range(4)]
        T.append(("P%d_EF" % n, "F.Cu", 1.0, [((o20[0] + o18[0]) / 2, o20[1]), ((o20[0] + o18[0]) / 2, 60.2)]))
        T.append(("P%d_EF" % n, "F.Cu", 0.8, [ef_l[0], ef_l[-1]]))
        T.append(("P%d_EF" % n, "B.Cu", 1.2, [ef_l[0], ef_l[-1], (ef_l[-1][0], ef_r[0][1]), ef_r[0], ef_r[-1]]))
        T.append(("P%d_EF" % n, "F.Cu", 1.2, [rs1, ef_r[-1]]))
        VIAS.extend(("P%d_EF" % n, p) for p in ef_l + ef_r)
        # INA237 sense: IN- and VBUS (pins 9, 8) straight up to the shunt's OUT pad, IN+ (pin 10)
        # through a via and In2 to the EF track, GND (pin 7) to its own via
        ina = "U%d" % (b + 2)
        p10, p9, p8, p7 = px(ina, 10), px(ina, 9), px(ina, 8), px(ina, 7)
        top = p8[1] - 0.73
        T.append(("P%d_OUT" % n, "F.Cu", 0.25, [p9, p8]))
        T.append(("P%d_OUT" % n, "F.Cu", 0.25, [p8, (p8[0], rs2[1]), rs2]))
        VIAS.append(("P%d_EF" % n, (p10[0], top - 1.6)))
        T.append(("P%d_EF" % n, "F.Cu", 0.25, [p10, (p10[0], top - 1.6)]))
        T.append(("P%d_EF" % n, "In2.Cu", 0.25, [(p10[0], top - 1.6), (p10[0] - 0.9, top - 1.6),
                                                  (p10[0] - 0.9, 54.0), (rs1[0], 54.0)]))
        VIAS.append(("GND", (p7[0] + 0.4, top - 1.2)))
        T.append(("GND", "F.Cu", 0.25, [p7, (p7[0], top - 0.4), (p7[0] + 0.4, top - 1.2)]))
        # ---- signal taps (see BUS_* below): VS and its cap onto the In2 3V3 rail, address straps,
        # ALERT / SDA / SCL down the right-hand corridor to B.Cu rails, FLT through In2 to its rail
        p6, p5, p4, p3, p2, p1 = (px(ina, k) for k in (6, 5, 4, 3, 2, 1))
        v33 = (cx + 5.6, RAIL_33)
        T.append(("+3V3", "F.Cu", 0.25, [p6, (v33[0], p6[1]), v33]))
        VIAS.append(("+3V3", v33))
        c3a, c3b = px("C%d" % (b + 3), 1), px("C%d" % (b + 3), 2)
        T.append(("+3V3", "F.Cu", 0.25, [c3a, (c3a[0], v33[1]), v33]))
        VIAS.append(("GND", (cx + 8.4, 57.3)))
        T.append(("GND", "F.Cu", 0.25, [c3b, (cx + 8.4, 57.3)]))
        bot = p1[1] + 0.73
        # A1 heads left under the pad row then down, A0 drops a little further and angles to its via
        for pin, vx, strap, path in ((p1, cx + 1.55, ADDR[n][0], lambda vx, vy: [(p1[0], bot + 0.3), (vx, bot + 0.3)]),
                                     (p2, cx + 2.6, ADDR[n][1], lambda vx, vy: [(p2[0], bot + 0.75)])):
            vy = RAIL_33 if strap == "+3V3" else RAIL_33 + 1.1
            T.append((strap, "F.Cu", 0.25, [pin] + path(vx, vy) + [(vx, vy)]))
            VIAS.append((strap, (vx, vy)))
        sda, scl = I2C_BUS[n]
        for pin, net, turn, corr, stair_y, vx in ((p5, scl, 59.7, 8.8, 73.9, 5.6), (p4, sda, 60.15, 8.35, 73.45, 4.6),
                                                  (p3, "ALERT_N", 60.6, 7.9, 73.0, 3.6)):
            rail = BUS_RAIL[net]
            T.append((net, "F.Cu", 0.25, [pin, (pin[0], turn), (cx + corr, turn), (cx + corr, stair_y),
                                          (cx + vx, stair_y), (cx + vx, rail)]))
            VIAS.append((net, (cx + vx, rail)))
        f15 = px(u, 15)
        fv1, fv2 = (cx - 1.0, 60.4), (cx + 0.6, BUS_RAIL["FLT_N"])
        T.append(("FLT_N", "F.Cu", 0.25, [f15, fv1]))
        T.append(("FLT_N", "In2.Cu", 0.25, [fv1, (fv2[0], fv1[1]), fv2]))
        VIAS.extend([("FLT_N", fv1), ("FLT_N", fv2)])
        # port off: gate pull-down pad -> via -> In2 under the +24V_SW bus -> via -> B.Cu to its Pico pin
        roff = px("R%d" % (b + 5), 2)
        T.extend(off_tracks(n, cx, roff))
        # small fixed stubs: 1 uF output cap onto the OUT track, divider top onto the IN track
        c5 = px("C%d" % (b + 5), 2)
        T.append(("P%d_OUT" % n, "F.Cu", 0.5, [c5, (rs2[0], c5[1])]))
        r2 = px("R%d" % (b + 2), 1)
        T.append(("P%d_IN" % n, "F.Cu", 0.3, [r2, (i1[0], r2[1])]))
        # shunt -> bulk cap -> TVS -> module + pin
        c_pos, d_k, m_pos = px("C%d" % (b + 4), 1), px("D%d" % (b + 1), 1), px("J%d" % (b + 1), 1)
        T.append(("P%d_OUT" % n, "F.Cu", 1.5, [rs2, (rs2[0], c_pos[1]), c_pos]))
        T.append(("P%d_OUT" % n, "F.Cu", 1.5, [c_pos, (d_k[0], c_pos[1]), d_k, m_pos]))
    return T


def write_libs(board, fps):
    """Project fp-lib-table: KiCad's stock libraries plus pdu.pretty for the custom footprints."""
    libdir = os.path.join(HERE, "pdu.pretty")
    os.makedirs(libdir, exist_ok=True)
    for ref in ("J101", "U101"):
        fp = pcbnew.FOOTPRINT(fps[ref])
        fp.SetPosition(VECTOR2I(0, 0))
        fp.SetOrientationDegrees(0)
        fp.SetReference("REF**")
        for pad in fp.Pads():
            pad.SetNetCode(0)
        pcbnew.PCB_IO_KICAD_SEXPR().FootprintSave(libdir, fp)
    libs = sorted({ALIASES.get(p[0], p[0]).split(":")[0] for p in PARTS.values()} - {"custom"})
    rows = ['  (lib (name "pdu")(type "KiCad")(uri "${KIPRJMOD}/pdu.pretty")(options "")(descr "PD module pads, TPS16630 HTSSOP"))']
    rows += ['  (lib (name "%s")(type "KiCad")(uri "%s/%s.pretty")(options "")(descr ""))' % (l, FPLIB, l) for l in libs]
    open(os.path.join(HERE, "fp-lib-table"), "w").write("(fp_lib_table\n  (version 7)\n" + "\n".join(rows) + "\n)\n")


def place_refs(fps):
    """Reference text: centred on small passives, hidden on holes, test pads and modules."""
    for ref, fp in fps.items():
        if ref.startswith(("H", "TP")):
            fp.Reference().SetVisible(False)
            continue
        if ref.startswith(("R", "C", "F", "D", "L")) and len(list(fp.Pads())) == 2:
            (x1, y1), (x2, y2) = pad_xy(fp, 1), pad_xy(fp, 2)
            fp.Reference().SetPosition(P((x1 + x2) / 2, (y1 + y2) / 2))
            fp.Reference().SetTextAngleDegrees(90 if abs(x1 - x2) < 0.01 else 0)
            fp.Reference().SetTextSize(VECTOR2I(FromMM(0.6), FromMM(0.6)))
            fp.Reference().SetTextThickness(FromMM(0.1))
            fp.Reference().SetLayer(pcbnew.F_Fab)


def silk(board, fps):
    place_refs(fps)
    S = pcbnew.F_SilkS
    for n in range(1, NPORTS + 1):
        cx = port_cx(n - 1)
        text(board, str(n), cx, MOD_Y + 3.0, 2.0, bold=True)
        text(board, PORT_ADDR_TEXT[n], cx - 3.6, 56.0, 0.8)
    text(board, PORT_ADDR_TEXT[0], 38.8, 117.6, 0.8, just="L")
    text(board, "24V IN", 15.2, 134.0, 1.0, bold=True)
    text(board, "BUTTON", 36.4, 134.0, 1.0, bold=True)
    text(board, "FAN1", 84.4, 137.4, 0.9)
    text(board, "FAN2", 96.4, 137.4, 0.9)
    text(board, "R14 12V / R15 5V fan", 102.9, 106.0, 0.8, rot=90)
    text(board, "DC-OK", 50.2, 124.6, 0.8, just="L")
    text(board, "NTC", 50.2, 115.6, 0.8, just="L")
    text(board, "LED", 50.2, 106.6, 0.8, just="L")
    text(board, "W5500-EVB-Pico, RJ45 to the front", PICO_X + 8.9, 137.6, 0.8)
    # on the solder mask over the bus
    text(board, TITLE + "    " + OWNER_LINE, W / 2, BUS_Y - 1.1, 1.2, bold=True)
    text(board, FORK_LINE, W / 2, BUS_Y + 1.3, 1.0)
    cat(board)


# Surprised cat face, the same drawing as the protocol-emulator bench carrier (mm, origin top
# left of the head box 15 x 9.2; whiskers reach 1.6 mm past each side).
CAT_HEAD = [(1.2, 7.2), (2.4, 8.3), (4.2, 8.85), (7.5, 9.05), (10.8, 8.85), (12.6, 8.3), (13.8, 7.2),
            (14.4, 5.6), (14.3, 4.0), (13.6, 2.6), (12.6, 0.25), (10.2, 1.85), (7.5, 1.55), (4.8, 1.85),
            (2.4, 0.25), (1.4, 2.6), (0.7, 4.0), (0.6, 5.6), (1.2, 7.2)]
CAT_FILLED = [[(2.65, 1.05), (4.05, 2.15), (2.15, 2.75)],        # inner ears
              [(12.35, 1.05), (10.95, 2.15), (12.85, 2.75)],
              [(6.95, 5.7), (8.05, 5.7), (7.5, 6.3)]]            # nose
CAT_EYES = [(4.9, 4.3), (10.1, 4.3)]                             # wide open: r 1.5, pupil r 0.6
CAT_MOUTH = (7.5, 7.4)                                           # the "o", r 0.55
CAT_WHISKERS = [((3.4, 6.2), (-1.5, 5.5)), ((3.4, 6.75), (-1.6, 6.85)), ((3.4, 7.3), (-1.3, 8.1)),
                ((11.6, 6.2), (16.5, 5.5)), ((11.6, 6.75), (16.6, 6.85)), ((11.6, 7.3), (16.3, 8.1))]
CAT_AT = (84.0, 118.4)
CAT_KEEPOUT = (-2.0, -0.5, 17.0, 9.6)   # box (x0, y0, x1, y1) kept free of stitching vias


def cat(board):
    ox, oy = CAT_AT
    S = pcbnew.F_SilkS

    def circle(cx, cy, r, filled, width=0.25):
        c = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_CIRCLE)
        c.SetLayer(S)
        c.SetCenter(P(ox + cx, oy + cy))
        c.SetEnd(P(ox + cx + r, oy + cy))
        c.SetWidth(FromMM(width))
        c.SetFilled(filled)
        board.Add(c)

    for a, b in zip(CAT_HEAD, CAT_HEAD[1:]):
        seg(board, S, P(ox + a[0], oy + a[1]), P(ox + b[0], oy + b[1]), 0.3)
    for pts in CAT_FILLED:
        sh = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_POLY)
        sh.SetLayer(S)
        sh.SetPolyPoints([P(ox + x, oy + y) for x, y in pts])
        sh.SetFilled(True)
        sh.SetWidth(FromMM(0.15))
        board.Add(sh)
    for ex, ey in CAT_EYES:
        circle(ex, ey, 1.5, False)
        circle(ex, ey, 0.6, True, 0.15)
    circle(CAT_MOUTH[0], CAT_MOUTH[1], 0.55, False)
    for a, b in CAT_WHISKERS:
        seg(board, S, P(ox + a[0], oy + a[1]), P(ox + b[0], oy + b[1]), 0.2)


# ---------------------------------------------------------------- routing
def placement_hash():
    h = hashlib.sha256()
    geo = {r: (ALIASES.get(p[0], p[0]), p[1], p[2], p[3]) for r, p in PARTS.items()}
    h.update(json.dumps([geo, NETS, POWER_NETS, NO_CONNECT, BUS_X0, BUS_X1, BUS_Y, BUS_W], sort_keys=True).encode())
    h.update(open(__file__, "rb").read().split(b"def hand_tracks")[1].split(b"def write_libs")[0])
    return h.hexdigest()[:16]


PASSES = int(sys.argv[sys.argv.index("--passes") + 1]) if "--passes" in sys.argv else 1


def route(force=False):
    dsn = os.path.join(HERE, NAME + ".dsn")
    ses = os.path.join(HERE, NAME + ".ses")
    stamp = os.path.join(HERE, NAME + ".ses.stamp")
    hsh = placement_hash()
    if not force and os.path.exists(ses) and os.path.exists(stamp) and open(stamp).read() == hsh:
        print("routing: reusing", ses)
        return ses
    board = pcbnew.LoadBoard(PCB)
    router_planes(board)
    if not pcbnew.ExportSpecctraDSN(board, dsn):
        raise SystemExit("DSN export failed")
    if os.path.exists(ses):
        os.remove(ses)
    # freerouting is not deterministic: run a few in parallel, keep the one with fewest unrouted
    best = None
    for rnd in range(1):
        procs = []
        for i in range(1):
            out = os.path.join(HERE, "route_try%d.ses" % i)
            log = os.path.join(HERE, "route_try%d.log" % i)
            for f in (out, log):
                if os.path.exists(f):
                    os.remove(f)
            cmd = ["java", "-Djava.awt.headless=true", "-jar", FREEROUTING, "--gui.enabled=false",
                   "-de", dsn, "-do", out, "--router.autorouter.max_passes=%d" % PASSES, "-mt", "4",
                   "--router.automatic_neckdown=true", "--router.copper_to_edge_clearance_um=500",
                   "--router.hole_clearance_um=300"]
            procs.append((subprocess.Popen(cmd, cwd=HERE, env=HEADLESS_ENV, stdin=subprocess.DEVNULL,
                                           stdout=open(log, "w"), stderr=subprocess.STDOUT), out, log))
        for p, out, log in procs:
            p.wait()
            txt = open(log).read()
            m = re.findall(r"final score: [\d.]+ \((\d+) unrouted", txt)
            if m and os.path.exists(out):
                left = int(m[-1])
                print("routing attempt: %d unrouted" % left)
                if best is None or left < best[0]:
                    best = (left, out, log)
        if best and best[0] == 0:
            break
    if best is None:
        raise SystemExit("freerouting wrote no session; see route_try*.log")
    shutil.copy(best[1], ses)
    shutil.copy(best[2], os.path.join(HERE, "freerouting.log"))
    for i in range(4):
        for f in ("route_try%d.ses" % i, "route_try%d.log" % i):
            if os.path.exists(os.path.join(HERE, f)):
                os.remove(os.path.join(HERE, f))
    print("routing: kept attempt with %d unrouted" % best[0])
    open(stamp, "w").write(hsh)
    return ses


def rect_outline(x0, y0, x1, y1):
    """Zone outline. ZONE.SetOutline keeps the pointer, so Python must not free it."""
    ps = pcbnew.SHAPE_POLY_SET()
    ps.NewOutline()
    for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
        ps.Append(FromMM(OX + x), FromMM(OY + y))
    ps.thisown = False
    return ps


def router_planes(board):
    """For freerouting only (not saved): In1.Cu is a solid GND plane, so GND pads fan out to
    vias and signals have F.Cu, In2.Cu and B.Cu to themselves."""
    gnd = board.FindNet("GND")
    m = 0.5
    z = pcbnew.ZONE(board)
    z.SetLayer(pcbnew.In1_Cu)
    z.SetNet(gnd)
    z.SetOutline(rect_outline(m, m, W - m, H - m))
    z.SetLocalClearance(FromMM(0.3))
    z.SetMinThickness(FromMM(0.25))
    board.Add(z)
    fill(board)


# ---------------------------------------------------------------- finishing
def drop_dangling_vias(board):
    """freerouting sometimes leaves a via with a track on one side only."""
    tracks = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]
    for v in [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]:
        p = v.GetPosition()
        ends = sum((t.GetStart() == p) + (t.GetEnd() == p) for t in tracks)
        if ends < 2 and v.GetNetname() != "GND" and not v.IsLocked():   # hand vias sit mid-rail
            board.Remove(v)


def add_zones(board, nets):
    gnd = nets["GND"]
    zones = []
    m = 0.5
    for layer in (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(gnd)
        z.SetOutline(rect_outline(m, m, W - m, H - m))
        z.SetLocalClearance(FromMM(0.3))
        z.SetMinThickness(FromMM(0.25))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        z.SetThermalReliefGap(FromMM(0.4))
        z.SetThermalReliefSpokeWidth(FromMM(0.6))
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        z.SetZoneName("GND_" + board.GetLayerName(layer).replace(".Cu", ""))
        if layer == pcbnew.In1_Cu:
            z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)   # the plane: solid to every GND pad and via
        board.Add(z)
        zones.append(z)
    return zones


def fill(board):
    board.BuildConnectivity()
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())


def stitch(board, nets, pitch=3.0, via=0.8, drill=0.3, clear=0.35):
    """GND vias on a grid wherever nothing else is near and both pours have copper."""
    gnd = nets["GND"]
    zones = list(board.Zones())
    acc = FromMM(via / 2 + clear)
    others = [t for t in board.GetTracks()
              if t.GetNetCode() != gnd.GetNetCode() or t.Type() == pcbnew.PCB_VIA_T]
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    n = 0
    y = 2.0
    while y < H - 1.5:
        x = 2.0
        while x < W - 1.5:
            pt = P(x, y)
            bx, by = CAT_AT
            kx0, ky0, kx1, ky1 = CAT_KEEPOUT
            if bx + kx0 - 1 < x < bx + kx1 + 1 and by + ky0 - 1 < y < by + ky1 + 1:
                x += pitch
                continue
            ok = all(z.HitTestFilledArea(z.GetLayer(), pt, FromMM(via / 2 + 0.1)) for z in zones
                     if z.GetLayer() in (pcbnew.F_Cu, pcbnew.B_Cu))
            ok = ok and not any(t.HitTest(pt, acc) for t in others)
            ok = ok and not any(p.HitTest(pt, FromMM(via / 2 + 0.5)) or
                                (p.GetDrillSize().x and
                                 (p.GetPosition() - pt).EuclideanNorm() < p.GetDrillSize().x / 2 + FromMM(via / 2 + 0.6))
                                for p in pads)
            if ok:
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pt)
                v.SetWidth(FromMM(via))
                v.SetDrill(FromMM(drill))
                v.SetNet(gnd)
                board.Add(v)
                n += 1
            x += pitch
        y += pitch
    return n


def island_vias(board, nets, via=0.8, drill=0.3, clear=0.35):
    """A top-pour GND island that only touches SMD pads (no via, no through-hole pad) is not
    connected to the planes. Drop one via inside each such island, wherever the clearance allows."""
    gnd = nets["GND"]
    board.BuildConnectivity()
    zone = [z for z in board.Zones() if z.GetLayer() == pcbnew.F_Cu and z.GetNetCode() == gnd.GetNetCode()][0]
    polys = zone.GetFilledPolysList(pcbnew.F_Cu)
    vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T and t.GetNetCode() == gnd.GetNetCode()]
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    tht = [p for p in pads if p.GetNetCode() == gnd.GetNetCode() and p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH]
    others = [t for t in board.GetTracks() if t.GetNetCode() != gnd.GetNetCode() or t.Type() == pcbnew.PCB_VIA_T]
    acc = FromMM(via / 2 + clear)
    added = 0
    for i in range(polys.OutlineCount()):
        if any(polys.Contains(v.GetPosition(), i) for v in vias) or any(polys.Contains(p.GetPosition(), i) for p in tht):
            continue
        bb = polys.Outline(i).BBox()
        x0, x1 = pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetRight())
        y0, y1 = pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetBottom())
        best = None
        y = y0
        while y <= y1 and best is None:
            x = x0
            while x <= x1:
                pt = VECTOR2I(FromMM(x), FromMM(y))
                if (polys.Contains(pt, i) and zone.HitTestFilledArea(pcbnew.F_Cu, pt, FromMM(via / 2 + 0.05))
                        and not any(t.HitTest(pt, acc) for t in others)
                        and not any(p.HitTest(pt, FromMM(via / 2 + 0.4)) for p in pads)):
                    best = pt
                    break
                x += 0.2
            y += 0.2
        if best is not None:
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(best)
            v.SetWidth(FromMM(via)); v.SetDrill(FromMM(drill))
            v.SetNet(gnd)
            board.Add(v)
            added += 1
    return added


def run(cmd):
    print("$", " ".join(cmd))
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, env=HEADLESS_ENV, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
    return r


def outputs(board):
    out = os.path.join(HERE, "out")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    # netlist table from the board itself
    with open(os.path.join(HERE, "netlist.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["net", "pads"])
        bynet = {}
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if pad.GetNumber():
                    nn = pad.GetNetname() or "(unconnected)"
                    bynet.setdefault(nn, set()).add("%s.%s" % (fp.GetReference(), pad.GetNumber()))

        def k(p):
            r, n = p.split(".")
            a, b = re.match(r"([A-Z]+)(\d+)", r).groups()
            return (a, int(b), int(n))
        for nn in sorted(bynet):
            w.writerow([nn, " ".join(sorted(bynet[nn], key=k))])
    # BOM
    groups = {}
    for ref, (spec, x, y, rot, value, desc, pn) in PARTS.items():
        if ref.startswith("H"):
            continue
        spec = ALIASES.get(spec, spec)
        key = (value, desc, pn, spec, ref in DNP)
        groups.setdefault(key, []).append(ref)
    with open(os.path.join(HERE, "bom.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["qty", "refs", "value", "description", "LCSC", "footprint", "fit"])
        for (value, desc, pn, spec, dnp), refs in sorted(groups.items(), key=lambda kv: kv[1][0]):
            w.writerow([0 if dnp else len(refs), " ".join(refs), value, desc, pn,
                        spec.replace("custom:", "pdu:"), "DNP" if dnp else "fit"])

    pcb = PCB
    gdir = os.path.join(out, "gerbers")
    os.makedirs(gdir)
    run(["kicad-cli", "pcb", "export", "gerbers", "-o", gdir + "/", "--layers",
         "F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts",
         "--subtract-soldermask", pcb])
    run(["kicad-cli", "pcb", "export", "drill", "-o", gdir + "/", "--format", "excellon",
         "--excellon-separate-th", "--generate-map", "--map-format", "gerberx2", pcb])
    zp = os.path.join(HERE, NAME + "_jlcpcb_gerbers.zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in sorted(os.listdir(gdir)):
            z.write(os.path.join(gdir, fn), fn)
    run(["kicad-cli", "pcb", "export", "step", "--subst-models", "-o", os.path.join(out, NAME + ".step"), pcb])
    rdir = os.path.join(HERE, "renders")
    shutil.rmtree(rdir, ignore_errors=True)
    os.makedirs(rdir)
    for side in ("top", "bottom"):
        run(["kicad-cli", "pcb", "render", "--side", side, "--width", "2400", "--height", "3000",
             "--quality", "high", "-o", os.path.join(rdir, "render_%s.png" % side), pcb])
    for name, layers in [("F_Cu", "F.Cu,Edge.Cuts"), ("B_Cu", "B.Cu,Edge.Cuts"),
                         ("F_Silk", "F.Silkscreen,Edge.Cuts"), ("F_Cu_Silk", "F.Cu,F.Silkscreen,Edge.Cuts"),
                         ("Fab", "F.Fab,F.Silkscreen,Edge.Cuts")]:
        pdf = os.path.join(rdir, name + ".pdf")
        run(["kicad-cli", "pcb", "export", "pdf", "--layers", layers, "--mode-single", "-o", pdf, pcb])
        if os.path.exists(pdf):
            pdf_png(pdf, os.path.join(rdir, name + ".png"))
            os.remove(pdf)


def pdf_png(pdf, png, dpi=200):
    """Rasterise a KiCad A4 plot and crop it to the board."""
    from PIL import Image
    base = png[:-4]
    run(["pdftoppm", "-png", "-r", str(dpi), "-singlefile", pdf, base])
    im = Image.open(png)
    k = dpi / 25.4
    im.crop((int((OX - 2) * k), int((OY - 2) * k), int((OX + W + 2) * k), int((OY + H + 2) * k))).save(png)


def drc():
    rep = os.path.join(HERE, "drc.json")
    run(["kicad-cli", "pcb", "drc", "--format", "json", "--severity-all", "--units", "mm",
         "-o", rep, PCB])
    run(["kicad-cli", "pcb", "drc", "--format", "report", "--severity-all", "--units", "mm",
         "-o", os.path.join(HERE, "drc.rpt"), PCB])
    j = json.load(open(rep))
    errs = [v for v in j.get("violations", []) if v["severity"] == "error"]
    warns = [v for v in j.get("violations", []) if v["severity"] == "warning"]
    unc = j.get("unconnected_items", [])
    print("DRC: %d errors, %d warnings, %d unconnected" % (len(errs), len(warns), len(unc)))
    from collections import Counter
    print(" errors:", Counter(v["type"] for v in errs))
    print(" warnings:", Counter(v["type"] for v in warns))
    return errs, warns, unc


def main():
    force = "--reroute" in sys.argv
    board, fps, nets = build()
    pcbnew.SaveBoard(PCB, board)
    if "--place-only" in sys.argv:
        pdf = os.path.join(HERE, "place.pdf")
        run(["kicad-cli", "pcb", "export", "pdf", "--layers", "F.Cu,F.Silkscreen,F.Fab,F.Courtyard,Edge.Cuts",
             "--mode-single", "-o", pdf, PCB])
        pdf_png(pdf, os.path.join(HERE, "place.png"), dpi=300)
        os.remove(pdf)
        return
    # freerouting is not deterministic: route again (up to 4 tries) until DRC finds nothing unconnected
    for attempt in range(4):
        if attempt:
            board, fps, nets = build()
            pcbnew.SaveBoard(PCB, board)
        ses = route(force or attempt > 0)
        board = pcbnew.LoadBoard(PCB)
        if not pcbnew.ImportSpecctraSES(board, ses):
            raise SystemExit("SES import failed")
        nets = {n: board.FindNet(n) for n in NETS}
        drop_dangling_vias(board)
        add_zones(board, nets)
        fill(board)
        print("stitching vias:", stitch(board, nets))
        fill(board)
        print("island vias:", island_vias(board, nets))
        fill(board)
        pcbnew.SaveBoard(PCB, board)
        errs, warns, unc = drc()
        if not errs and not unc:
            break
        print("route try %d left %d unconnected, %d errors" % (attempt + 1, len(unc), len(errs)))
    if "--no-out" not in sys.argv:
        outputs(pcbnew.LoadBoard(PCB))


if __name__ == "__main__":
    main()
