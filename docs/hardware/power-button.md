# Milō — Physical Power Button (clean shutdown + wake)

> **Status: BUILD GUIDE — pending hardware.** The button has not been wired yet.
> Steps marked **[verify on Pi]** must be confirmed on the actual board (the LED
> wiring depends on electrical values we measure once the button is in hand).
> Last updated 2026-05-24.
>
> **Hardware target:** Raspberry Pi 5 + HiFiBerry AMP4 Pro, where the **amp's DC
> supply powers the Pi over the 40-pin header** (no separate USB-C on the Pi).

## Goal

A single panel-mount button that:

- on a short press while running → **cleanly shuts down** Milō + the Pi (services
  stopped properly, no SD-card corruption), then the Pi enters its low-power state;
- on a press while the Pi is asleep → **boots** the Pi, and Milō autostarts;
- has a **white LED** that is lit only while the Pi is running, off once shut down.

## Part to buy

16 mm panel-mount illuminated push button, screw terminals, in this exact variant:

| Spec axis | Choose | Why |
|---|---|---|
| Type ("Norme") | **Self-reset** (momentary, spring-return) | The Pi 5's `J2` power-button input reacts to a *press event*, like a PC power button. A **latching** switch holds a level and desynchronises from the real on/off state — wrong tool. |
| LED voltage ("Tension") | **6 V** | The Pi only exposes 3.3 V / 5 V. A 6 V LED lights from the 5 V rail (slightly dim, fine for an indicator). 12–24 V needs a separate supply; 110–230 V is mains — never connect to a Pi. |
| LED colour | White | Cosmetic. White Vf ≈ 3.0–3.2 V, lights fine at 5 V. |

> The **latching** 7 A button (bought first) **is** part of the design — as the **master
> power switch**, wired inline on the AMP4 Pro's **+18 V DC feed**, with **no LED** (its
> pressed/released position already shows on/off). See *Master switch & daily use* below.

## Why J2, and not the alternatives

- **`J2` (chosen):** the Pi 5's 2-pad external-power-button header. A momentary
  normally-open switch on `J2` behaves identically to the on-board power button:
  short press = clean shutdown, press-when-off = boot. **No `dtoverlay`, no daemon.**
- **GPIO3 / pin 5 (rejected):** the classic "wake" pin. It is the **I2C SCL line the
  HiFiBerry amp uses to configure its codec** — HiFiBerry explicitly warns against it.
  Holding it down (which a button does) breaks the amp's control bus.
- **`gpio-shutdown` overlay (not needed):** would only duplicate what `J2` already does
  natively on the Pi 5, and the wake half still requires `J2`/power-button hardware.

## Wiring — the switch (the on/off function)

`J2` is a 2-pin header on the **Pi 5 board itself** (near the round RTC-battery
connector, at the board edge) — *not* on the 40-pin header, so it stays reachable under
the stacked amp.

```
   Self-reset button (switch terminals, 2x)
        ┌───────────┐
        │  o     o  │   ── wire ──►  J2 pad 1
        │  SWITCH   │   ── wire ──►  J2 pad 2
        └───────────┘
```

- Use the **two switch terminals** (the 7 A-rated pair). Polarity is irrelevant for a
  dry contact.
- That's the entire on/off function. **[verify on Pi]** tap-test below.

## Wiring — the LED (so it tracks Milō on/off)

**The problem:** the amp keeps feeding **5 V to the header even while the Pi sleeps**,
so an LED wired plainly across 5 V → GND would stay glowing after shutdown.

**The fix:** keep the LED's `+` on the always-on 5 V, but route its `−` (ground return)
**through a GPIO that only conducts while the Pi is running.** A `config.txt` directive
sets that GPIO as an output driven **low** at boot; in the Pi's low-power state the GPIO
pad is unpowered (high-impedance), so no current flows and the LED is dark.

```
   5V (header)  ──►  LED +  (anode)
                      LED −  (cathode)  ──►  GPIO26 (pin 37)   [tentative — verify free+reachable]

   Pi running   →  GPIO26 driven LOW (0 V)  →  current sinks  →  LED ON
   Pi asleep    →  RP1 unpowered, GPIO26 hi-Z →  no path       →  LED OFF
```

Config line (added inside a dedicated managed block, applied at wiring time — **not yet
committed**, since the pin is finalised against the real board):

```ini
# BEGIN MILO POWER LED
gpio=26=op,dl
# END MILO POWER LED
```

**[verify on Pi] — three checks before trusting this:**

1. **Pin is free + reachable.** Avoid all pins already in use: I2C `GPIO2/3`, I2S
   `GPIO18/19/20/21`, IR `GPIO17`, rotary `GPIO22/27/23`, HAT EEPROM `GPIO0/1`.
   `GPIO26` (pin 37) is the tentative pick; confirm the AMP4 Pro doesn't use it and that
   pin 37 + a 5 V pin + a GND pin are physically tappable on the stacked build.
2. **LED current ≤ ~16 mA at 5 V.** A Pi GPIO should sink no more than ~16 mA. Measure
   the button's LED current. If it's higher, use the transistor fallback below.
3. **LED on/off follows boot/shutdown** in the tap-test.

**Transistor fallback (if the LED is too thirsty for a direct GPIO sink):** drive a small
NPN (e.g. 2N3904/BC547) instead — `LED− → collector`, `emitter → GND`, `GPIO26 → base`
via a ~1 kΩ resistor. The GPIO then carries ~1 mA; the transistor sinks the LED. Same
`config.txt` line.

> Nuance: the LED tracks **"Pi powered on"** (firmware applies `gpio=` a few seconds into
> boot), which for Milō ≈ "Milō running" since the services autostart. Tying it to the
> `milo-backend` *service* specifically would need a small systemd unit toggling the GPIO
> — out of scope unless the few-second boot gap matters.

## Making it work / verification on the Pi

1. **Power-off behaviour [verify on Pi].** Confirm `sudo poweroff` leaves the Pi in the
   low-power state where a `J2` press boots it (Pi 5 default). If a press does **not**
   wake it, set `POWER_OFF_ON_HALT=1` in the bootloader config (`rpi-eeprom-config`) and
   retest. Reason: this combo back-feeds 5 V from the amp; the wake path relies on the
   PMIC staying in standby, which it does as long as the amp is on.
2. **Clean-shutdown behaviour.** Short press while running → Milō UI goes down → the Pi
   powers off within a few seconds. Check `journalctl -b -1` after the next boot shows a
   **clean** shutdown (the journal is persistent — see project notes), not a hard cut.
3. **Wake.** Press while asleep → Pi boots → Milō reachable.
4. **LED.** Lit while running, dark after shutdown (per the LED checks above).

## Master switch & daily use (two-button design)

The agreed setup mirrors a desktop PC: a clean OS button + a hard power switch.

| PC equivalent | Milō part | Placement |
|---|---|---|
| Front power button (ACPI) | **Self-reset + white LED** → `J2` | Back of the case — daily on/off |
| PSU rear `I/O` switch | **Latching, no LED** → inline on the **+18 V DC** amp feed | Hidden under the case — true off |

**Operating rules:**

- The self-reset (`J2`) only works while the latching is **ON** — no master power, no Pi
  (exactly like a PC's front button does nothing if the PSU rear switch is off).
- **Full power-down:** tap the self-reset for a clean shutdown, wait until the Pi is off,
  **then** flip the latching OFF. Never flip the latching while the Pi runs — that's a
  hard cut (corruption risk).
- **Power-up from full-off:** flip the latching ON → the amp powers up → the Pi boots
  automatically → Milō starts (no need to touch the self-reset).
- The latching is on the **low-voltage DC** side, so the mains brick keeps a sub-watt
  standby. For literal zero, switch the wall socket instead — don't hand-wire mains.

## Future — CM5 carrier board

The custom board ([cm5-board-spec.md](cm5-board-spec.md)) should integrate this natively
(momentary → CM5 power-enable + driven LED) and can sequence "clean shutdown then cut the
amp rail" in hardware, collapsing both buttons into one smart control.

## References

- Raspberry Pi 5 power button + `J2`:
  <https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#power-button>
- HiFiBerry — power button guidance (GPIO3 conflict warning):
  <https://www.hifiberry.com/blog/powering-up-down-your-pi-with-a-button/>
