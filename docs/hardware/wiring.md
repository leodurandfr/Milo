# Milō — Physical Wiring Reference

> Every physical connection on a Milō unit (Raspberry Pi 5 + HiFiBerry AMP4 Pro).
> Section 1 is the at-a-glance map; the following sections give the exact wiring per
> component. Last updated 2026-06-05.

## 1. GPIO 40-pin header — overview

Pin 1 is top-left, USB / Ethernet at the bottom. Unlabelled pins are free.

```
           IR VCC ─►[ 1] 3V3 ┃ 5V  [ 2]◄─ LED + (anode)
   AMP4 HAT (I2C) ─►[ 3] G2  ┃ 5V  [ 4]◄─ Screen 5V
   AMP4 HAT (I2C) ─►[ 5] G3  ┃ GND [ 6]◄─ IR GND
                    [ 7] G4  ┃ G14 [ 8]
       Screen GND ─►[ 9] GND ┃ G15 [10]
          IR DATA ─►[11] G17 ┃ G18 [12]◄─ AMP4 HAT (I2S)
        Rotary DT ─►[13] G27 ┃ GND [14]◄─ Rotary GND
       Rotary CLK ─►[15] G22 ┃ G23 [16]◄─ Rotary SW (click)
         Rotary + ─►[17] 3V3 ┃ G24 [18]
                    [19] G10 ┃ GND [20]
                    [21] G9  ┃ G25 [22]
                    [23] G11 ┃ G8  [24]
                    [25] GND ┃ G7  [26]
AMP4 HAT (EEPROM) ─►[27] G0  ┃ G1  [28]◄─ AMP4 HAT (EEPROM)
                    [29] G5  ┃ GND [30]
                    [31] G6  ┃ G12 [32]
                    [33] G13 ┃ GND [34]
   AMP4 HAT (I2S) ─►[35] G19 ┃ G16 [36]
  LED − (cathode) ─►[37] G26 ┃ G20 [38]◄─ AMP4 HAT (I2S)
                    [39] GND ┃ G21 [40]◄─ AMP4 HAT (I2S)
```

> **AMP4 HAT** pins are taken by the stacked HiFiBerry board — do not connect anything to them.

---

## 2. Rotary encoder — KY-040 (volume knob)

| Encoder pin | Connect to | Header pin |
|-------------|------------|:----------:|
| CLK         | GPIO22     | 15 |
| DT          | GPIO27     | 13 |
| SW (click)  | GPIO23     | 16 |
| +           | 3V3        | 17 |
| GND         | GND        | 14 |

Inputs use internal pull-ups — no external resistors.

## 3. IR receiver — TSOP4838 (Apple Remote)

| Receiver pin | Connect to | Header pin |
|--------------|------------|:----------:|
| OUT / DATA   | GPIO17     | 11 |
| GND          | GND        | 6  |
| VCC          | 3V3        | 1  |

## 4. Software power button (clean shutdown + wake)

A **momentary / self-reset** button with a white LED. Short press = clean shutdown;
press while asleep = boot. Applying power does **not** auto-boot — you press the button
to start, like a PC (configured by the installer).

- **Button** (2 terminals) → **J2 pads 1 & 2**. J2 is a small 2-pin header **on the Pi 5
  board itself** (next to the round RTC-battery connector) — *not* the 40-pin header.
  Polarity doesn't matter.
- **LED +** (anode) → **5V** (header pin 2)
- **LED −** (cathode) → **GPIO26** (header pin 37)

```
   5V (pin 2) ──►|►|──► GPIO26 (pin 37)
                  LED
   running → GPIO26 LOW → LED ON     |     halted → GPIO26 hi-Z → LED OFF
```

The installer configures this automatically: the status LED (GPIO26, lit while running,
off once halted) **and** "wait for the power button on power-up" (applying power keeps
Milō off until you press the button). If the LED draws > ~16 mA, drive it through an NPN
transistor.

## 5. Hard power switch (master cut)

A **latching** switch, **no LED**, wired on the amp's DC input. It fully cuts power.

- Cut the **+ wire** of the **+18 V DC** going into the AMP4 Pro barrel jack and put the
  latching switch **inline on that + wire**. The − wire goes straight to the amp.

```
   DC brick (+) ──► [ latching switch ] ──► AMP4 Pro DC +
   DC brick (−) ─────────────────────────► AMP4 Pro DC −
```

**Order of use:** shut down with the software button (section 4) first, wait until the Pi
is off, *then* flip this latching switch off. Never flip it while Milō is running.

## 6. Screen — Waveshare 8" DSI

- **Image + touch:** FFC **ribbon** → the Pi's **DSI** connector (not GPIO).
- **Power:** **5V** wire → **5V** (header pin 4) · **GND** wire → **GND** (header pin 9).

## 7. CD drive

External **USB** CD drive → any Pi USB port. Detected as `/dev/sr0`.

## 8. Network

**RJ45 Ethernet** → the Pi's Ethernet port (or Wi-Fi). Used for multiroom and Mac/ROC
streaming.

## 9. Audio HAT, speakers & main power

- **HiFiBerry AMP4 Pro** stacked on the **full 40-pin header**. It powers the Pi
  through the header.
- **Speakers** → the amp's screw terminals.
- **Main power:** DC brick → AMP4 Pro barrel jack (through the hard switch, section 5).
