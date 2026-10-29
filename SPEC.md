# HomeLab PDU Mon

Fork of [Shrike Lab HomeLab-PDU-V1](https://github.com/Shrike-Lab/HomeLab-PDU-V1) (CERN-OHL-S-2.0).
Same idea (Mean Well HRP-300-24 plus XPM52C 65 W USB-C PD modules in a 1U 10" box), with
6 ports instead of 5 and per-port monitoring and switching.

Marcos Ashton Iglesias, 2026-10-28.

## What changes from the original

- 6 PD ports (was 5), same module mounting: modules stand upright, 2 pins per module,
  pins 8.7 mm apart across the module, 17 mm module pitch, pins 22.6 mm in from the rear edge.
- Per port: fuse, TPS16630 eFuse (current limit, inrush, UVLO/OVP, on/off), INA237 monitor
  (V, I, P, energy, die temperature), TVS and bulk caps as before.
- One INA237 on the 24 V input.
- W5500-EVB-Pico (RP2040 + wired Ethernet) for Prometheus metrics and remote power-cycling.
- 2 x 4-pin PWM fan headers with tach (Noctua 40 mm), 12 V or 5 V by solder jumper.
- HRP-300 DC-OK input and an NTC input for the PSU case.
- Ports default ON if the MCU is dead or rebooting (TPS16630 SHDN floats high).
- Enclosure is all sheet metal (JLCCNC, 5052 aluminium), not part-printed.

## Mechanical frame

Coordinates in mm. Viewed from the front: X to the right, Y from front to rear, Z up.
X = 0 is the enclosure centre line. Y = 0 is the inner face of the front panel.
Z = 0 is the top of the floor.

| Item | Value |
|---|---|
| Rack panel (ears) | 254.0 wide, 44.0 high (1U = 44.45), 2.0 thick |
| Rack holes | slots 6.5 x 9.5 (horizontal), centres at X = +-118.25 (236.5 c-c, slots cover 234 to 239), 6.2 from top and bottom edge |
| Body outside width | 218.0 max incl. lid flanges 220.0 (10" rail opening is 222.25) |
| Body outside height | 43.8 max |
| Body depth | 250 overall incl. front panel |
| Sheet | 5052, 1.2 for tray, lid and rear, 2.0 for front panel |
| Inner width | 215.6 (X -107.8 to +107.8) |
| Rear panel inner face | Y = 246.8 |

### Mean Well HRP-300-24 (datasheet in datasheets/HRP-300-SPEC.pdf)

- 199 L x 105 W x 41 H. Airflow from the terminal end to the fan end.
- Placed X -105.3 to -0.3, Y 43.8 to 242.8 (terminal end at the front, fan end 4 mm from the rear panel), on the floor.
- Bottom holes 4 x M4, max screw depth 5: 120 apart along the length, the first 42.5 from the fan end
  (so 36.5 from the terminal end), 75 apart across, 15 in from each long side.
- Terminals on the front end: 1 AC/L, 2 AC/N, 3 FG, 4-5 -V, 6-7 +V, CN100 (DF11-8) next to pin 7.
  Pin 1 is on the left wall side (X ~ -100), CN100 on the PCB side.

### PCB

- 104.0 x 138.5, 4 layer (F signal/power, In1 solid GND, In2 signal, B power/signal), 1.6 mm, 2 oz outer.
- Vias 0.8 / 0.3 everywhere (0.25 annular ring, JLC's 2 oz rule), tracks and spaces 0.15 minimum.
- Placed X +2.0 to +106.0, Y 106.8 to 245.3 (rear edge 1.5 from the rear panel).
- Sits on 6 standoffs, 6.0 tall (board bottom at Z = 6.0): M2.5 at the two rear holes (4.5 mm heads fit
  the 5 mm gap between module bases), M3 elsewhere.
- Board coordinates (KiCad): origin at the rear-left corner seen from above with the rear edge at the
  top, x to the right (same as enclosure X), y towards the front. Enclosure X = 2.0 + x,
  enclosure Y = 245.3 - y.
- Mounting holes (board coords): M2.5 (18, 28), (86, 28) between modules 1/2 and 5/6;
  M3 (2.6, 88.6), (101.4, 88.6), (4, 134.5), (101.4, 113.4).
- J1 and J2 (Mini-Fit Jr, right angle) overhang the front edge by 4.9 mm, board x 7.7-22.6 and 28.9-43.8.
- PD module pin pairs at y = 22.6, pair centres x = 9.5 + 17 i (i = 0..5), pins at centre -4.35 (+) and +4.35 (-).
  Modules stand above the rear strip y 0..~34, USB-C overhangs the rear edge. Rear panel cutouts:
  one per module, centred on the module centre x, 15 wide x 8 tall, rounded, centre Z 20.6. USB-C height: take it from
  the Shrike design (their PCB tray, jumper guide and rear panel in ~/dev/hw/HomeLab-PDU-V1/ASSEMBLY/CAD)
  and our board height. Make the cutout tolerant of the module being +-2 mm off in X.

### Panels

Front, left to right (X):
- Vent slots over the HRP terminal end, left part
- RJ45 panel-mount coupler (keystone-size cutout 14.9 x 19.2) at X ~ -60, Z centre ~ 21
- Latching power button 16 mm (cutout 16.2 dia) at X ~ -30
- IEC C14 inlet with fuse and switch ("3-in-1", snap-in cutout 47.5 x 27.5) at X +32
- 40 mm fan at X +77.5, mounted to the inside of the front panel (holes 32 c-c, M3), grille cutout,
  fan depth 20 (NF-A4x20), blowing rearwards

Rear:
- HRP side (X < 0): vent pattern over the HRP fan end
- PCB side (X > 0): 6 USB-C cutouts (above) plus small vents
- Laser-marked port numbers 1-6 above the USB-C cutouts (port 1 at the lowest X)

## Electrical

### Power path

HRP +V/-V -> J1 Molex Mini-Fit Jr 2x3 (as original: pins 1-3 GND, 4-6 +24V) -> F1 15 A 2410 ->
SMCJ26A, 1000 uF + 470 uF + 1 uF -> input shunt 2 mOhm (INA237 #0) -> Q1 NCEP40PT15D P-FET driven by the
latching button on J2 (same circuit and pinout as original) -> +24V_SW bus.

Each port i (1..6): +24V_SW -> F 5 A 1206 -> TPS16630 (IN to OUT) -> shunt 10 mOhm 1206 (INA237 #i) ->
SMBJ26A, 470 uF, 1 uF, 100 nF -> module + pin. Module - pin to GND.

TPS16630 values (VIN 24 V):
- R_ILIM 4.53 k: I_OL = 18 / R_ILIM(k) = 3.97 A (65 W module draws ~2.9 A at 24 V), fast trip 2 x I_OL
- UVLO/OVP divider R1 442 k, R2 8.06 k, R3 20 k: UVLO 20.1 V, OVP 28.2 V
- C_dVdT 56 nF: ramp 28 ms, inrush ~0.5 A into ~600 uF
- MODE open: latch off on overcurrent; the MCU resets a tripped port by toggling SHDN
- NC pins 4 and 5 tied to IN so one track along the pin row reaches P_IN (pin 6)
- SHDN: open (internal 2.7 V) = on. 2N7002 drain on SHDN, gate from MCU with 100 k pull-down. MCU high = port off.
- FLT: open drain, all 6 wired together to one MCU pin with 10 k pull-up to 3V3
- PGOOD, IMON unused (test points)

INA237 (I2C, VS 3V3): ADCRANGE 1 (+-40.96 mV) on ports, 0 on the input.
Address straps are GND or VS only (a strap to SDA or SCL would have to cross the bus), so two buses:
- bus A, I2C1 on GP2/GP3: input 0x40, port 1 0x41, port 2 0x44, port 3 0x45
- bus B, I2C0 on GP4/GP5: port 4 0x40, port 5 0x41, port 6 0x44
All ALERT pins wired together to one MCU pin (open drain, 10 k).

### Aux power

- +5V: LMR16006X buck from +24V (unswitched), 0.6 A, SHDN left floating (internal pull-up = on), feeds the Pico VSYS through a Schottky
  (so plugging USB into the Pico is safe).
- +12V_FAN: second LMR16006X from +24V. Fan supply solder jumper: 12 V (default) or 5 V.
- 3V3 from the Pico 3V3(OUT) for INAs, pull-ups, logic.

### MCU (W5500-EVB-Pico, Pico pinout, GP16-21 used by the W5500)

The module sits on 2 x 1x20 8.5 mm female headers. The six I2C/ALERT/FLT pull-ups, C19, C20 and D4 are
under it, beside the pins they serve.

| GPIO | Use |
|---|---|
| GP2 | I2C1 SDA (bus A) |
| GP3 | I2C1 SCL (bus A) |
| GP4 | I2C0 SDA (bus B) |
| GP5 | I2C0 SCL (bus B) |
| GP8, GP7, GP6, GP9, GP10, GP11 | Port 1, 2, 3, 4, 5, 6 off (high = off) |
| GP12 | eFuse FLT (all, active low) |
| GP13 | INA ALERT (all, active low) |
| GP14 | Fan 1 PWM (via 2N7002 open drain, inverted) |
| GP15 | Fan 2 PWM |
| GP22 | Fan 1 tach |
| GP0 | Fan 2 tach |
| GP27 | HRP DC-OK (divider 15 k top / 20 k bottom: 5.6 V max -> 3.2 V) |
| GP28 | NTC 10 k (B3950) to GND, 10 k pull-up to ADC_VREF |
| GP26 | +24V_SW sense (divider 100 k / 10 k), so the MCU sees the button state |
| GP1 | Status LED (front panel, via 2N7002) |

### Connectors

| Ref | Part | Use |
|---|---|---|
| J1 | Molex 39-30-1060 Mini-Fit Jr 2x3 | 24 V in from HRP |
| J2 | Molex 39-30-1060 | Latching button (original pinout) |
| J3, J4 | Molex 47053-1000 4-pin fan | Fan 1, fan 2 |
| J5 | JST PH 2-pin | HRP DC-OK (pin 1 DC-OK, pin 2 GND) from CN100 pins 3, 5 |
| J6 | JST PH 2-pin | NTC |
| J7 | JST PH 2-pin | Status LED |
| P1..P6 + / - | 1.0 mm drill pads | PD modules |

### Silkscreen

- Title "HomeLab PDU Mon v1", "fork of Shrike Lab HomeLab-PDU-V1, CERN-OHL-S-2.0"
- "Marcos Ashton Iglesias  2026-10-28" and the shocked cat from ~/dev/protocol-emulator-bench (same size)
- Port numbers 1-6, + and - at each module, I2C addresses, fan voltage jumper labels
