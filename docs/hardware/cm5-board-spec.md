# Milō — Custom CM5 Carrier Board Spec

> **Status: LIVING DRAFT — nothing is final.** Every decision below is revisable; this
> is a cadrage document, not a frozen contract. Last updated 2026-05-23.
>
> **Owner:** Léo (product/UX, first PCB project). Claude Code = copilot on
> schematic-as-code, BOM, calcs, fab outputs and review — not a substitute for
> human routing of the hard high-speed sections.

## Goal

Replace the current `Raspberry Pi 5 + HiFiBerry AMP4 Pro` stack with a single custom
carrier board for the **Raspberry Pi Compute Module 5 (CM5)** that integrates, on one
board:

- a quality **DAC + class-D amplifier** (drop the HiFiBerry HAT entirely),
- an **RCA line-in** path via an audio **ADC** (new audio source: turntable w/ preamp,
  CD player, TV, etc.),
- **every peripheral Milō already depends on** (screen, rotary, IR, USB/CD, network…).

Reached in stages — start at a **2.0 stereo** board; the CM5/control "brain" block is
reusable, only the power section grows for a later 4.1.

## Why a custom board is what unlocks the line-in

The Pi 5 + AMP4 Pro share the **single I2S bus**, owned by HiFiBerry's closed
device-tree overlay and clock master → there is no room to add a capture ADC. On a
board we design, **we own the I2S bus and its clocking**, so playback (DAC) and capture
(ADC) coexist **full-duplex** on that one bus.

➡️ **Consequence:** the line-in feature only works on the integrated-audio path. Keeping
a HiFiBerry HAT would re-impose the closed overlay + bus-master conflict and kill the
line-in. **Line-in ⇒ no HAT.**

## Locked decisions (revisable)

| Block | Choice | Rationale / consequence |
|---|---|---|
| **Compute** | Raspberry Pi **CM5** (wireless SKU; eMMC or NVMe boot) | It's a Pi 5 → Milō runs unchanged |
| **DAC** | **PCM5122** (Burr-Brown / TI) | Mainline `pcm512x` ASoC driver; same chip as HiFiBerry DAC+ / Allo Boss → low risk. Volume is done in CamillaDSP, not the DAC. |
| **Amplifier** | **TPA3255** (class-D), analog-in | Reference-grade hi-fi class-D; abundant proven designs |
| **Power** | **Single 18 V DC input** (external brick) | → TPA3255 PVDD (~2×40–50 W) + buck 18 V→5 V for the CM5. One connector, one supply. |
| **Line-in** | **Line-level only** → **WM8782** ADC | Simple analog front-end (AC-couple + level scale). Turntable plugs in via its own/external phono preamp. Covers ~90% of cases. |
| **Volume knob** | **Premium detented encoder** (ALPS/Bourns, ~24–30 detents) | Same GPIO quadrature wiring as the KY-040 → **zero Milō code change**; better tactile feel |
| **RTC** | **None** — rely on NTP | Milō is networked; time is set at boot. (An RTC only keeps time while powered off; it does NOT speed boot or power the board.) |
| **Boot storage** | **eMMC** on the CM5 (16–32 GB SKU) | Reliable, no SD corruption — appliance standard; plenty for Milō |
| **Screen** | **Both** DSI 8" + HDMI 7" USB | Keeps Milō's current screen flexibility; cost = extra connectors |
| **Clocking** | **Single-domain, dedicated low-jitter oscillator** | CamillaDSP outputs a FIXED rate to the DAC → the DAC only ever sees one rate → one oscillator suffices. Dual-domain (+2nd osc +clock mux +switching logic) buys nothing here and the mux can add jitter. Spend the budget on one excellent oscillator wired straight to the DAC. |
| **Oscillator** | **Crystek CCHD-957** — **24.576 MHz** (48k family, tentative; confirm at Phase 0) | Audiophile reference, easy to hand-solder + heat-tolerant + reliable supply (Mouser/Digikey). The measurable gap vs NDK NZ2520SDA / Accusilicon AS318B is inaudible here → buildability wins for a 1st board. |
| **Clock topology** | **PCM5122 masters** the I2S bus; CM5 + WM8782 are **slaves** | "DAC owns its master clock" = lowest jitter. **Gated on verifying the CM5/RP1 can run I2S in slave mode (Phase 0); fallback = a reclocker (Allo Kali-style) between CM5 and DAC.** |

## Block diagram

```
   18V DC ──┬──────────────────────► PVDD ──► [ TPA3255 ] ──► Speakers (2.0)
            └─► buck 18V→5V ──► [ CM5 ] (+3V3 / 1V8 logic)
                                  │
                                  │   ONE I2S bus, full-duplex
              ┌───────────────────┼──────────────────────────────┐
         DOUT │ (playback)         │ BCLK + LRCLK          MCLK    │
              ▼                    │                               │
        [ PCM5122 DAC ] ──analog──► analog input of TPA3255        │
              ▲ I2C (config)       │                               │
              │                    │                               │
         DIN  ▲ (capture)          │                               │
        [ WM8782 ADC ] ◄── RCA in (line-level)                     │
                                                                   │
        [ low-jitter audio master clock ] ──► MCLK to DAC + ADC ───┘
```

## Audio + clock chain

- **I2S pins (CM5, standard):** BCLK `GPIO18`, LRCLK `GPIO19`, DIN `GPIO20` (capture
  from ADC), DOUT `GPIO21` (playback to DAC).
- **I2C (`GPIO2/3`):** configures the PCM5122 (the `pcm512x` driver needs it). The
  WM8782 has no control bus (hardware-mode pins).
- **Master clock = the audiophile-critical subsystem.** A **single** dedicated
  low-jitter oscillator feeds **MCLK to both** the PCM5122 and the WM8782
  (**single-domain**, confirmed). Rationale: CamillaDSP outputs a *fixed*
  rate to the DAC → the DAC only ever sees one rate → one oscillator suffices.
  Dual-domain would add a 2nd oscillator + clock mux + glitch-free switching logic for
  **no benefit here** (and a cheap mux adds jitter). Part: **Crystek CCHD-957**
  (24.576 MHz / 48k, tentative). Topology: **PCM5122 masters** the bus, CM5 + WM8782
  slave — *verify RP1 I2S slave mode in Phase 0; fallback = a Kali-style reclocker.*
- Full-duplex runs **playback and capture at the same sample rate** (48k) — a non-issue
  for "play music + capture a line input".

## Software impact (small, by design)

1. **Drivers are free:** `pcm512x` + `wm8782` are both mainline ASoC.
2. **One custom DT overlay:** a `simple-audio-card` overlay binding PCM5122 (playback,
   device 0) + WM8782 (capture, device 1) as a single full-duplex card.
3. **One registry entry:** add the new card to `backend/hardware/registry.py` (maps card
   IDs → overlays), so Milō's existing card-selection logic picks it up.
4. **New "Line-In" source** (later): family A (external control, no rich metadata) — an
   `alsaloop` from the WM8782 capture device into the CamillaDSP input, plus UI to
   select it. Could split into "Aux" vs "Phono" labels.

## Hardware completeness checklist (carrier must carry all of these)

Derived from Milō's actual hardware footprint — each line is something the appliance
uses today.

- [ ] **CM5 connectors** (2× Hirose; copy RPi CM5 IO Board reference for high-speed)
- [ ] **Audio:** PCM5122 + WM8782 on the I2S bus + master clock; RCA line-in jacks;
      speaker output terminals; TPA3255 + output LC filter
- [ ] **Screen:** DSI connector (Waveshare 8" `vc4-kms-dsi-waveshare-panel`) **and/or**
      HDMI + USB (Waveshare 7" USB — touch + brightness over USB HID)
- [ ] **Rotary encoder:** `GPIO22` (CLK), `GPIO27` (DT), `GPIO23` (SW) + pull-ups
- [ ] **IR receiver** (TSOP4838): `GPIO17` (`gpio-ir` overlay) + pull-up
- [ ] **USB ×2+:** CD drive (`/dev/sr0`, Apple SuperDrive needs `sg_raw` unlock udev
      rule) + margin
- [ ] **Ethernet:** CM5 PHY → magnetics + RJ45 (mDNS `milo.local` depends on it)
- [ ] **WiFi/BT:** wireless CM5 SKU + antenna (U.FL or onboard) — BT remote + BT audio
- [ ] **Cooling:** fan PWM header (Milō drives 50→80 °C tiers via RP1)
- [ ] **Power:** 18 V DC input; PVDD rail to amp; buck 18 V→5 V (≥5 A) to CM5;
      `usb_max_current_enable`
- [ ] **Boot:** eMMC SKU **or** NVMe M.2 / microSD

## Hard parts / open questions

1. **Clocking** — single-domain + **CCHD-957** (24.576 MHz tentative) +
   **PCM5122-as-master** topology all chosen. Remaining to validate on hardware:
   **verify RP1 I2S slave mode** (Phase 0; fallback = Kali-style reclocker); confirm the
   system sample rate; MCLK fan-out / jitter-aware layout. *Highest-priority subsystem.*
2. **Power & thermal** of the TPA3255 — PVDD decoupling, output filter, ground/EMI
   discipline, heatsinking at 18 V.
3. **Analog front-end** of the line-in — input impedance, level scaling to WM8782 range,
   ground-loop / hum rejection on the RCA path.
4. **Staging** — does a first EVT board keep a HiFiBerry HAT (faster bring-up, **no
   line-in**) before the integrated-audio board? Trade-off: speed vs the line-in goal.

## Validation before any PCB (Phase 0)

De-risk on a **CM5 dev kit + official CM5 IO Board** before committing to a layout —
this is hands-on and cheap, and resolves the open hardware questions.

**Dev-kit BOM (mostly reuses hardware already owned):**

*Buy (~€110–120):*
- **CM5 `CM5108032`** — Wireless / **8 GB RAM** / **32 GB eMMC** (matches the locked
  eMMC-boot decision; 8 GB = comfortable headroom for the Chromium kiosk + longevity)
- **Official CM5 IO Board** — exposes 40-pin HAT header, 2× DSI/CSI, M.2 NVMe, USB, GbE,
  4-pin PWM fan
- **Official 27 W USB-C PD PSU** — the IO board's J11 input is **USB-C 5 V/5 A via PD**
  (*not* a 12 V barrel jack)
- **Official CM5 Cooler** — active heatsink+fan → IO board's PWM connector (CM5 needs
  active cooling under load)

*Reuse (already owned):*
- **HiFiBerry AMP4 Pro** on the 40-pin header → validates the audio stack AND, being a
  clock-master board, gives strong evidence the CM5/RP1 handles externally-clocked I2S
  (proxy for the PCM5122-as-master topology)
- DSI 8" / HDMI 7" screen · USB CD drive · KY-040 rotary · TSOP4838 IR · speakers

The eMMC SKU means **no separate boot media** — flash the image to eMMC via `rpiboot`.

**Validation checklist:**

- [ ] Milō's full software stack runs on CM5 (it's a Pi 5 → expected trivial).
- [ ] **RP1 I2S slave-mode works** — gates the PCM5122-as-master clock topology.
- [ ] Screen overlays (DSI 8" + HDMI 7" USB) behave on CM5.
- [ ] CD drive (`/dev/sr0` + SuperDrive `sg_raw` unlock) over CM5 USB.
- [ ] Rotary (`GPIO22/27/23`) + IR (`GPIO17`) mapping unchanged on CM5.
- [ ] Measure the real source-rate mix → fix the oscillator frequency (44.1k vs 48k).

## Out of scope (for now)

- Integrated **phono preamp** (line-only chosen; revisit if turntable-without-preamp
  becomes a requirement).
- **4.1 / multichannel** power section (CM5/control block stays reusable).
- **CE / EMC** product certification (a later phase).

## Reference designs to study before routing

- **CM5 carrier + class-D stereo amp** — raspipcb showcase (closest to this board).
- **IanCanada**, **StationPi CM5** — audiophile CM5 DAC/streamer clocking examples.
- **Shawn Hymel / Digikey** — CM4/CM5 carrier board in KiCad (starting template).
- **TI TPA3255EVM**, **Infineon MERUS** — amplifier reference designs.
- **HiFiBerry DAC+ / Allo Boss** — PCM5122 reference implementations.
