# Project Scope — Hardware & Telemetry Copilot

**Last updated:** 2026-06-12

This document defines the tight boundary of what this diagnostic engine can and
cannot analyse.  It exists so that users and contributors know exactly which
aircraft configurations, flight conditions, and failure modes the system is
designed for — and which ones it is not.

---

## 1. What This System Is

A **post-mortem anomaly scanner** for ArduPilot multirotor telemetry logs.  It
compares sensor data against static, profile-driven thresholds to flag hardware
faults, then sends localized context windows to an LLM for root-cause analysis.

It is **not** a real-time monitor, a flight controller, a tuning tool, or a
predictive maintenance system.

---

## 2. Supported Airframes

The system only understands **multirotor** configurations with symmetric
opposing motor pairs.  Every airframe must have a defined `motor_pairs` list in
its propulsion profile — this is what drives the motor-imbalance detector.

| Category | Examples | Motor Count | Motor Pair Scheme |
|----------|----------|-------------|-------------------|
| **Quadcopter** | Quad-X, Quad-+ | 4 | Opposing arms (C1↔C3, C2↔C4) |
| **Hexacopter** | Hexa-X, Hexa-+ | 6 | Opposing arms (C1↔C4, C2↔C5, C3↔C6) |
| **Y6 Coaxial** | 3-arm coaxial | 6 | Coaxial pairs per arm (C1↔C4, etc.) |
| **X8 Flat Octocopter** | 8 independent arms | 8 | Opposing arms (C1↔C5, etc.) |
| **X8 Coaxial Octocopter** | 4-arm coaxial | 8 | Coaxial pairs per arm (C1↔C5, etc.) |

### Explicitly Out of Scope

These airframe types **cannot** be diagnosed by this system.  The motor-pair
assumptions baked into the imbalance and redline detectors do not hold for them:

- **Fixed-wing / Plane** — no motor-pair paradigm; different failure modes
- **Helicopter** — single-rotor dynamics, swashplate, tail rotor
- **Tricopter** — odd motor count; no opposing-pair symmetry
- **VTOL / QuadPlane** — mixed fixed-wing + multirotor flight phases
- **Coaxial with contra-rotating per-arm** (beyond Y6) — no pair definition
- **Rover / Boat / Sub** — entirely different sensor and failure profiles
- **Any airframe with fewer than 4 or more than 8 motors** — untested range

---

## 3. Supported ESC & Motor Protocols

| Protocol | Profiles | PWM Range | Redline Strategy |
|----------|----------|-----------|-----------------|
| **PWM** (standard) | `pwm_standard`, `quad_x` | 1000–2000 | Fixed > redline (1900–1940) |
| **DShot300** | `dshot300` | 1000–2000 | Fixed > redline (1950) |
| **DShot600** | `dshot600`, `x8_flat_octo`, `y6_coaxial` | 1000–2000 | Fixed > redline (1950) |

All profiles assume a **1000–2000 PWM range**.  The minimum (1000 = motor off)
and absolute maximum (2000 = physical limit) are identical across all profiles.

### Explicitly Out of Scope

- **DShot1200 / DShot2400** — no profiles exist
- **Oneshot / Multishot** — not modelled
- **CAN bus ESCs** — no message mapping
- **PWM ranges other than 1000–2000** — requires a new propulsion profile with
  adjusted `pwm.min` and `pwm.absolute_max`

---

## 4. Thrust-to-Weight Awareness

Each propulsion profile now carries **machine-readable TWR and hover-PWM
fields** that the LLM receives in its system prompt:

| Field | Example (racer) | Example (heavy lifter) | Used by |
|-------|----------------|------------------------|---------|
| `thrust_to_weight_ratio` | 8.0 | 3.0 | LLM — contextualizes max-thrust events |
| `hover_pwm_typical` | 1350 | 1650 | LLM — baseline for "normal" PWM range |
| `all_up_weight_g` | 650 | 15000 | LLM — physical context |
| `motor_kv` | 2300 | 120 | LLM — cross-reference with PWM and prop size |
| `propeller_size_inches` | 5.0 | 22.0 | LLM — expected vibration and thrust characteristics |

**The parser still uses a static PWM redline.**  The LLM is given explicit
TWR-aware critical rules that tell it how to interpret the same PWM value
differently depending on the aircraft type:

- A max-thrust event on an **8:1 racer** (hover ~1350, redline 1900) is a
  genuine fault — the motors should never need to approach redline.
- The same event on a **3:1 heavy lifter** (hover ~1650, redline 1950) may be
  normal during aggressive climb — the LLM assesses surrounding PWM, attitude
  change, and current draw before concluding.

**This means the parser is still a blunt instrument** — it flags everything
above the static line.  The LLM is the TWR-aware filter downstream.  This is
an intentional design choice: the parser casts a wide net, and the LLM
decides what to keep.

### What the system still lacks

- **Baseline learning** — the hover PWM is from the profile, not measured from
  the actual flight.  If the profile doesn't match the aircraft, the LLM's
  TWR context will be slightly off.
- **Parser-level TWR adjustment** — the redline is still a fixed number per
  profile.  A future enhancement could derive the redline dynamically from
  TWR and hover PWM.

---

## 5. Battery Chemistry & Voltage

| Chemistry | Profiles | Voltage Range |
|-----------|----------|---------------|
| **LiPo** (3.7V/cell nom, 4.2V/cell max) | 1S–12S (12 profiles) | 3.7V – 50.4V |
| **Li-Ion** (3.6V/cell nom, 4.1V/cell max) | Not yet implemented | — |
| **LiHV** (3.8V/cell nom, 4.35V/cell max) | Not yet implemented | — |

Sag detection uses two methods:
1. **Rolling-median ratio** — voltage drops below 92–95% of local median while
   current spikes above 150–170% of local median (window = 10 rows)
2. **Absolute floor** — voltage below `min_safe_voltage_v` (3.3V/cell) triggers
   unconditionally

The sag ratios are tuned per cell count: lower cell counts get more aggressive
ratios (higher IR losses), higher cell counts get relaxed ratios then
re-tightened past 9S for series IR accumulation.

### Out of Scope

- **Parallel battery configurations** — only single-pack voltage monitoring
- **Battery health / IR estimation** — sag detection is based on voltage dip
  coincidence with current, not a calculated internal resistance
- **Cell-level monitoring** — only pack-level voltage; individual cell voltages
  in MAVLink `BATTERY_STATUS.voltages` are not extracted

---

## 6. Flight Controller Compatibility

| FC Profile | MCU | IMU Count | VCC Range | Vibe Z Limit |
|------------|-----|-----------|-----------|-------------|
| Pixhawk 6C Mini | STM32H743 | 2 (BMI088 + ICM-42688-P) | 4.9–5.3V | 45 m/s² |
| Pixhawk 6X | STM32H753 | 3 (dual ICP-20100) | 4.9–5.3V | 50 m/s² |
| Cube Orange+ | STM32F427 | 3 | 4.8–5.4V | 60 m/s² |
| Cube Blue | STM32F427 | 3 (2× ICM-20689 + BMX055) | 4.7–5.3V | 55 m/s² |
| Holybro Durandal | STM32H743 | 2 | 4.8–5.3V | 45 m/s² |

All profiles assume **ArduPilot Copter** firmware.  The binary parser maps
Dataflash-internal message types (`BAT`, `POWR`, `VIBE`, `ATT`, `RCOU`) for
`.bin` files and MAVLink v2 message types (`SYS_STATUS`, `POWER_STATUS`,
`VIBRATION`, `ATTITUDE`, `NAV_CONTROLLER_OUTPUT`, `SERVO_OUTPUT_RAW`) for
`.tlog` files.

### Out of Scope

- **ArduPlane, ArduRover, ArduSub** — different message sets and failure modes
- **PX4 firmware** — no message mapping exists
- **Betaflight / INAV / KISS** — different logging formats entirely
- **Non-ArduPilot MAVLink dialects** — the parser assumes ArduCopter field names
- **Pre-4.x ArduPilot** — VIBE `Clip` field format changed; the parser has a
  fallback for single-Clip-without-IMU-index but it is untested on old firmware

---

## 7. Flight Style & Flight Phase

| Aspect | System Behaviour | Limitation |
|--------|-----------------|------------|
| **Flight phase** | Not detected | Cannot distinguish takeoff, hover, cruise, punch-out, or landing — all rows treated identically |
| **Tuned vs untuned** | Not distinguished | A well-tuned quad and a wobbly DIY build use the same vibration thresholds |
| **Gentle vs aggressive** | Not distinguished | Voltage sag during a deliberate punch-out is flagged identically to sag during hover |
| **Hover vs forward flight** | Not distinguished | Vibration characteristics differ but thresholds are static |

### Why This Matters

- A voltage sag during a high-throttle punch-out is *expected behaviour* — the
  battery voltage will dip under load.  The system will flag it regardless.
- An untuned aircraft with a high vibration baseline will trigger `VIBE_SPIKE`
  continually — the threshold was designed for a properly tuned craft.
- **The LLM is the filter.**  The parser flags anything above threshold; the
  LLM decides whether it is mechanically significant based on context.

---

## 8. Anomaly Detector Scope Matrix

This table shows which detectors are universal and which are airframe-dependent:

| Detector | Dependencies | Works Across All Supported Airframes? |
|----------|-------------|--------------------------------------|
| VCC Drop / Overvolt | FC profile | Yes — purely electrical |
| Voltage Sag | Battery profile | Yes — purely electrical |
| Max Thrust | PWM redline | **No** — redline is TWR-dependent |
| Motor Imbalance | Motor pairs + high/low thresholds | Yes — but sensitivity varies with airframe |
| Vibration Spike | FC vibration limits | **No** — baseline varies with tune + prop size |
| IMU Clipping | None (any clip > 0) | Yes — clipping is always a fault |
| ATT Desync | 15° divergence (fixed) | **No** — a CineLifter's normal desync under load may exceed a racer's |
| VCC Fluctuation | FC VCC limits | Yes — purely electrical |
| Min Thrust (stuck motor) | PWM minimum (1000) | Yes — detects dead/zero-output motors universally |

---

## 9. Log Format Support

| Format | Extension | Source | Parser | Status |
|--------|-----------|--------|--------|--------|
| CSV (tabular) | `.csv` | Converted export | `pd.read_csv()` | Supported |
| Dataflash binary | `.bin` | Flight controller SD card | `_parse_bin_dataflash()` via pymavlink | Supported |
| MAVLink telemetry | `.tlog` | Ground station recording | `_parse_tlog_mavlink()` via pymavlink | Supported |
| Raw MAVLink stream | `.rlog` | Mission Planner debug | Auto-redirects to matching `.tlog` | Best-effort |

---

## 10. Summary: When Will This System Work?

| Question | Answer |
|----------|--------|
| Will it work on my 5" quad with default profiles? | Probably yes, but max-thrust detection may not trigger on tuned builds |
| Will it work on my heavy X8 lifter? | Yes, but expect max-thrust false positives — the LLM will need to interpret them |
| Will it work on my fixed-wing plane? | **No** — not supported |
| Will it work if I'm running Betaflight? | **No** — ArduPilot only |
| Will it work with Li-Ion packs? | No profile exists yet, but adding one would make it work |
| Will it catch a bent prop shaft? | Yes — vibration spike + motor imbalance correlating |
| Will it catch a dying battery? | Yes — voltage sag + VCC fluctuation |
| Will it catch a desync'ed motor? | Yes — ATT desync + motor imbalance |
| Will it catch an ESC about to fail? | Partially — max thrust + VCC fluctuation may correlate, but ESCs can fail silently |
| Will it distinguish a bad tune from a hardware fault? | **No** — the LLM may infer it, but the parser treats both identically |

### The Golden Rule

> This system is a **threshold-based scanner with LLM interpretation**, not a
> learned model.  It will flag anything that crosses a static line.  Whether
> that flag is meaningful depends on whether the profile thresholds are
> appropriate for your specific aircraft.  When in doubt, **trust the LLM's
> interpretation over the parser's flags** — the LLM has the full hardware
> profile and can reason about context; the parser cannot.

---

## 11. What Would Expand the Scope

These are explicitly **not in scope today** but would meaningfully expand the
system's reach:

1. **Baseline-learning phase** — analyse the first N seconds of steady hover
   (if present) to measure the aircraft's *actual* vibration, PWM, and current
   baselines rather than relying on profile defaults
2. **Parser-level TWR-aware redline** — derive `pwm.redline` dynamically from
   `thrust_to_weight_ratio` and `hover_pwm_typical` instead of using a static
   number per profile
3. **Flight-phase detection** — classify rows into ground, takeoff, hover,
   cruise, aggressive-manoeuvre bins and adjust thresholds per phase
4. **Time-based context windows** — replace ±20 rows with ±2 seconds of
   wall-clock time, making the window size consistent regardless of logging rate
5. **Li-Ion and LiHV battery chemistries** — new power_system profiles
6. **PX4 firmware support** — ulog message mapping
7. **Additional airframe types** — tricopter (requires non-pair-based imbalance
   detector), coaxial >2 motors per arm
