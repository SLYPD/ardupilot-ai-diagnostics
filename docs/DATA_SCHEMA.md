# ARDUPILOT DATA SCHEMA
The provided telemetry data utilizes standard ArduPilot (Copter) logging variables. Use this dictionary to map data tags to physical events.

## 1. POWER & BATTERY (BAT)
* `BAT.Volt`: Main battery rail voltage. Monitored for sudden sags under high load.
* `BAT.Curr`: Total current draw in Amperes.
* `BAT.Enrg`: Total energy consumed (mAh).

## 2. MOTOR OUTPUTS (RCOU)
* `RCOU.C1` through `RCOU.C6`: PWM output to motors 1 through 6 on the hexacopter.
* **Scale:** Typically 1000 (Off/Min) to 2000 (Absolute Max Thrust). 
* **Imbalance Detection:** If one motor (e.g., C4) is sustained at 1940 while the opposite motor (e.g., C1) is at 1300, it indicates the flight controller is fighting a physical or aerodynamic failure.

## 3. VIBRATION & IMU (VIBE)
* `VIBE.VibeX`, `VIBE.VibeY`, `VIBE.VibeZ`: Measured vibration levels on the X, Y, and Z axes.
* `VIBE.Clip0`, `Clip1`, `Clip2`: IMU clipping events. Any increment above 0 indicates severe mechanical shock or sensor saturation.

## 4. ATTITUDE & TARGETS (ATT)
* `ATT.DesRoll` vs `ATT.Roll`: Commanded roll vs. actual roll.
* `ATT.DesPitch` vs `ATT.Pitch`: Commanded pitch vs. actual pitch.
* **Desync Detection:** A large, sustained divergence between 'Desired' and 'Actual' indicates the drone lacks the mechanical authority to execute the command.

## 5. BINARY LOG FIELD MAPPINGS

The parser maps two distinct log formats to the unified schema above:

### .bin Dataflash (onboard flash log)
Uses ArduPilot-internal Dataflash message types.  Field names come from the
FMT definitions embedded in each log and may vary by firmware version.

| Dataflash Msg | Source Fields | Internal Column | Notes |
|---|---|---|---|
| `BAT` | `Volt`, `Curr`, `EnrgTot` | `BAT.Volt`, `BAT.Curr`, `BAT.Enrg` | `EnrgTot` renamed |
| `POWR` | `Vcc` | `VCC` | |
| `VIBE` | `VibeX/Y/Z`, `Clip`, `IMU` | `VIBE.VibeX/Y/Z`, `VIBE.Clip0/1/2` | Single `Clip` field demuxed to Clip0/1/2 by IMU index |
| `ATT` | `DesRoll`, `Roll`, `DesPitch`, `Pitch` | `ATT.DesRoll`, `ATT.Roll`, `ATT.DesPitch`, `ATT.Pitch` | Direct mapping |
| `RCOU` | `C1`–`C14` | `RCOU.C1`–`RCOU.C14` | Direct mapping |

Timestamps: `TimeUS` (microseconds since boot) → `TimeS` (seconds).

### .tlog MAVLink telemetry (ground station recording)
Uses standard MAVLink v2 message types with unit conversions.

| MAVLink Msg | Source Fields | Internal Column | Unit Conversion |
|---|---|---|---|
| `SYS_STATUS` | `voltage_battery`, `current_battery` | `BAT.Volt`, `BAT.Curr` | mV→V, cA→A |
| `POWER_STATUS` | `Vcc` | `VCC` | mV→V |
| `VIBRATION` | `vibration_x/y/z`, `clipping_0/1/2` | `VIBE.VibeX/Y/Z`, `VIBE.Clip0/1/2` | None |
| `ATTITUDE` | `roll`, `pitch` | `ATT.Roll`, `ATT.Pitch` | rad→deg |
| `NAV_CONTROLLER_OUTPUT` | `nav_roll`, `nav_pitch` | `ATT.DesRoll`, `ATT.DesPitch` | None (already deg) |
| `SERVO_OUTPUT_RAW` | `servo1_raw`–`servo14_raw` | `RCOU.C1`–`RCOU.C14` | None |

Timestamps: `_timestamp` (Unix epoch seconds) → `TimeS`.

### .rlog telemetry (Mission Planner raw stream)
.rlog files are the raw MAVLink stream captured by Mission Planner alongside
.tlog files.  They contain the same MAVLink data but with interleaved ASCII
debug text that makes them unparseable by pymavlink.  **The parser
auto-redirects to the matching .tlog file** when one exists in the same
directory.  If no .tlog is available, it falls back to a best-effort parse of
the .rlog directly.
