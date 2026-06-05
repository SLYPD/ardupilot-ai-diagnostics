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
