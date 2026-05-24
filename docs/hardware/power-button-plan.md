# Power Button (J2) — Implementation Plan

> **For when the button arrives.** Spec: [power-button.md](power-button.md). Steps use
> checkboxes for tracking. This is a **hardware** plan: most "tests" are physical
> observations + on-Pi commands, not pytest. Do the phases **in order** — Phase 1 (the
> switch) must work before touching the LED.
>
> **Hardware:** Raspberry Pi 5 + HiFiBerry AMP4 Pro, **powered by one DC supply into the
> amp** which feeds the Pi over the 40-pin header (no USB-C on the Pi).
>
> Created 2026-05-24.

**Goal:** A panel-mount *Self-reset* button on the Pi 5 `J2` header that cleanly shuts
down + wakes Milō, with a white LED lit only while the Pi runs.

**Architecture:** Switch contacts → `J2` (native Pi 5 power-button behaviour, no
software). LED `+` on the always-on 5 V rail, LED `−` returned through a GPIO driven low
while the Pi runs (off in low-power), so the light tracks Pi on/off.

**Order:** Phase 0 prep → Phase 1 switch (MVP, must pass) → Phase 2 find GPIO → Phase 3
LED → Phase 4 optional power/true-off → Phase 5 document & commit.

---

## ⚠️ Safety rule (applies to every wiring step)

**Always unplug the amp's DC supply before touching `J2`, the header, or any wire.**
The amp keeps 5 V live even when the Pi is shut down — "shut down" is **not** safe to
wire on. Wire only with the DC barrel physically unplugged.

---

## Phase 0 — Prep (no hardware action)

- [ ] **Step 0.1: Re-read the spec.** Open [power-button.md](power-button.md) and confirm
  the bought button is **Self-reset + 6 V + white LED, screw terminals**. If it's
  Latching, stop — wrong part (see spec).

- [ ] **Step 0.2: Identify the button's 4 terminals.** Illuminated buttons have a
  **switch pair** (the 7 A-rated contacts) and an **LED pair** (often marked `+`/`−` or
  with a small lamp symbol). Note which two are the switch and which two are the LED.
  If unmarked: the LED pair is usually the smaller/separate terminals; confirm later with
  a 5 V test in Phase 3.

- [ ] **Step 0.3: Locate `J2` on the Pi 5.** It's a 2-pad header **on the Pi board**,
  next to the round RTC-battery connector, at the board edge — *not* on the 40-pin header
  the amp sits on. Confirm you can physically reach it with the amp stacked (it sits near
  the USB-C corner). If a 2-pin JST lead came with similar kits, great; otherwise you'll
  solder/clip two wires to the pads.

---

## Phase 1 — The switch (MVP: clean shutdown + wake)

This phase has **no repo or config change** — it's pure wiring + verification.

- [ ] **Step 1.1: Power down for wiring.** Clean-shutdown first, then unplug the amp DC:

  Run on the Pi: `sudo shutdown now`
  Wait for it to fully power off, **then unplug the amp's DC supply.**

- [ ] **Step 1.2: Wire the switch pair → `J2`.** Connect the **two switch terminals**
  (the 7 A pair) to the two `J2` pads. **Polarity does not matter** for a dry contact.
  Keep the LED pair disconnected for now.

- [ ] **Step 1.3: Reassemble and power on.** Plug the amp DC back in. The Pi should boot
  automatically (first power-up always boots). Wait for Milō to come up.

- [ ] **Step 1.4: Verify clean SHUTDOWN via the button.** Give the button **one short
  tap** (do **not** hold — holding ~5 s forces a hard cut). Expected: Milō UI goes down,
  Pi powers off within a few seconds.

- [ ] **Step 1.5: Verify the shutdown was clean.** Tap the button again to boot, then
  run:

  ```bash
  journalctl -b -1 -n 30 --no-pager
  ```
  Expected: the *previous* boot ends with an orderly shutdown sequence (`Stopping…`,
  `Unmounted…`, `Reached target … Power-Off`), **not** a truncated log. (The journal is
  persistent on this system, so `-b -1` works.)

- [ ] **Step 1.6: Verify WAKE.** From the powered-off state, one short tap → Pi boots →
  Milō reachable. ✅ If 1.4–1.6 pass, the core feature works. The LED is optional polish.

> **If the button does nothing while off (no wake):** the Pi may not be entering the
> wake-capable low-power state. Go to **Step 4.1** (set `POWER_OFF_ON_HALT`) and retry,
> then come back.

---

## Phase 2 — Find a free, reachable GPIO for the LED

- [ ] **Step 2.1: List GPIO usage.** On the Pi:

  ```bash
  pinout 2>/dev/null | head -40 ; echo "---" ; raspi-gpio get 26
  ```
  Reserved, do **not** use: `GPIO2/3` (I2C/amp), `GPIO18/19/20/21` (I2S), `GPIO17` (IR),
  `GPIO22/27/23` (rotary), `GPIO0/1` (HAT EEPROM). Tentative pick: **`GPIO26` (pin 37)**.
  Expected from `raspi-gpio get 26`: `func=INPUT` (i.e. unused/free), not `func=ALTx`.

- [ ] **Step 2.2: Confirm physical reachability.** The amp covers the 40-pin header.
  Check whether **pin 37 (GPIO26)**, a **5 V pin (2 or 4)**, and a **GND pin (e.g. 6, 9,
  39)** are tappable on your stacked build (passthrough header, exposed castellations, or
  the amp's own 5 V/GND points for the supply side). 5 V and GND can also be taken from
  the amp's power terminals; **only the GPIO must come from the header.**

  **Decision gate:**
  - GPIO pin reachable → continue to Phase 3 (tracks on/off).
  - GPIO pin NOT reachable → fall back: wire LED to the amp's 5 V + GND for a simple
    always-on "powered" glow, OR defer the LED. Record the choice in the spec and skip to
    Phase 5.

---

## Phase 3 — Wire the LED so it tracks Milō on/off

- [ ] **Step 3.1: Power down for wiring.** `sudo shutdown now`, wait, **unplug amp DC.**

- [ ] **Step 3.2: Wire the LED.**
  - LED `+` → a **5 V** source (header pin 2/4, or the amp's 5 V).
  - LED `−` → **GPIO26 (pin 37)**.
  - GND of the supply common as needed.

  > Current note: these 6 V illuminated buttons have a built-in resistor; at 5 V they
  > typically draw ~8–12 mA — within a Pi GPIO's ~16 mA sink limit, so a **direct GPIO
  > connection is fine.** If the button's datasheet states >16 mA, use the transistor
  > fallback: `LED− → NPN collector`, `emitter → GND`, `GPIO26 → 1 kΩ → base`.

- [ ] **Step 3.3: Add the config block on the Pi.** Edit `/boot/firmware/config.txt`
  (needs root) and append:

  ```ini
  # BEGIN MILO POWER LED
  gpio=26=op,dl
  # END MILO POWER LED
  ```
  This sets GPIO26 as an output driven **low** at boot, so it sinks the LED while running.
  (`milo-apply-hardware` leaves unknown blocks untouched, so this survives normal
  operation — but **not** a full reinstall; see Step 5.2 for the durable option.)

- [ ] **Step 3.4: Reboot and verify the LED.** Plug amp DC back in, let it boot.
  - Expected: LED **on** a few seconds into boot (when firmware applies `gpio=`).
  - Tap the button → Pi shuts down → LED **off**. Tap → boots → LED **on**.
  - If the LED never lights: swap the two LED wires (LED polarity) and retry.

- [ ] **Step 3.5: Confirm the amp still works.** Play audio in Milō — confirm GPIO26 use
  didn't disturb the amp (it shouldn't; GPIO26 is unrelated to the I2S/I2C the amp uses).

---

## Phase 4 — Master switch (latching) + standby tuning

- [ ] **Step 4.1 (optional): Cut the Pi's shut-down draw.** Default Pi 5 soft-off ≈ 1.3 W;
  `POWER_OFF_ON_HALT=1` drops it to ~0.01 W.

  ```bash
  sudo rpi-eeprom-config --edit
  # set / add:  POWER_OFF_ON_HALT=1
  sudo reboot
  ```
  **Verify after reboot:** (a) the amp still plays, (b) the button still **wakes** the Pi
  from off, (c) optional: confirm lower draw with a wall meter. **If the amp misbehaves or
  wake breaks, revert to `POWER_OFF_ON_HALT=0`** — RPi ships `0` for HAT compatibility, so
  this combo (amp = HAT, back-feeding 5 V) is exactly the case that needs checking.

- [ ] **Step 4.2: Fit the latching master switch (no LED) on the +18 V feed.** Power down
  first (`sudo shutdown now`, wait, **unplug the amp DC**). On the **+18 V DC cable**
  between the power brick and the AMP4 Pro, interrupt the **positive (+) conductor** and
  wire its two cut ends to the latching button's **two switch terminals** (the 7 A
  screw-terminal pair — ideal here). Leave the negative/ground continuous. **No LED
  wiring** — the button's pressed/released position is the indicator.
  - 7 A is comfortably rated for an 18 V AMP4 Pro feed.
  - **Verify:** after a clean shutdown, latching OFF = whole unit dark. Latching ON = amp
    powers, Pi **auto-boots**, Milō starts. Confirm a J2 tap does **nothing** while the
    latching is OFF (expected — no master power, no Pi).

- [ ] **Step 4.3: Internalise the operating rules** (also in the spec): J2 only works with
  the latching ON; for full-off always **clean-shutdown via J2 first, then** flip the
  latching; flipping the latching ON auto-boots everything. Never flip the latching while
  the Pi runs.

---

## Phase 5 — Document & commit

- [ ] **Step 5.1: Finalise the spec.** Update [power-button.md](power-button.md) with the
  real values: GPIO pin actually used, measured/assumed LED current + whether a transistor
  was needed, and the `POWER_OFF_ON_HALT` decision. Remove the `[verify on Pi]` markers
  that are now resolved.

- [ ] **Step 5.2 (optional, durable): Integrate the LED GPIO into hardware management.**
  If this should survive a full reinstall, add the `gpio=26=op,dl` line as a managed block
  in `rootfs/usr/local/bin/milo-apply-hardware` (a `# BEGIN/END MILO POWER LED` block,
  mirroring the existing `MILO IR` block) driven by a `hardware.json` flag. Skip if this
  stays a one-off on your personal unit.

- [ ] **Step 5.3: Commit.**

  ```bash
  git add docs/hardware/power-button.md docs/hardware/power-button-plan.md
  # plus rootfs/usr/local/bin/milo-apply-hardware if Step 5.2 was done
  git commit -m "docs(hardware): finalise power button (J2) wiring + LED"
  ```

---

## References

- Pi 5 power button / `J2`: <https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#power-button>
- HiFiBerry power-button guidance (GPIO3 conflict): <https://www.hifiberry.com/blog/powering-up-down-your-pi-with-a-button/>
- Pi 5 shutdown power + `POWER_OFF_ON_HALT`: <https://www.jeffgeerling.com/blog/2023/reducing-raspberry-pi-5s-power-consumption-140x/>
