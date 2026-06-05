# HARDWARE SPECIFICATION (GROUND TRUTH)
**System:** High-Performance Hexacopter  
**Flight Controller:** Holybro Pixhawk 6C Mini  
**Firmware:** ArduPilot (Copter)  

## 1. FLIGHT CONTROLLER & I/O
* **FCU:** Pixhawk 6C Mini (STM32H743)
* **IMU Configuration:** * IMU1: Bosch BMI088 (Isolated)
    * IMU2: InvenSense ICM-42688-P
* **Barometer:** ICP-20100
* **Telemetry Link:** SiK Telemetry Radio [Insert Frequency: e.g., 433MHz / 915MHz]

## 2. AIRFRAME & PROPULSION
* **Frame Type:** Hexacopter (6 Motors)
* **Motor Layout:** Standard ArduPilot Hexa-X Configuration
* **Motors:** [Insert Brand/Model, e.g., T-Motor F90 1300KV]
* **Propellers:** [Insert Size/Pitch, e.g., 7x4 inch Carbon Fiber]
* **Electronic Speed Controllers (ESC):** [Insert Protocol: e.g., DShot600, PWM] at [Insert Amp Rating: e.g., 45A] continuous.
* **CRITICAL DIAGNOSTIC RULE - THRUST EVALUATION:** All motor thrust calculations, limits, and sync evaluations MUST refer to the **maximum physical output capability** of the motors, NEVER the take-off/hover throttle point.

## 3. POWER SYSTEM (BATTERY & PMU)
* **Battery Chemistry:** [e.g., LiPo / Li-Ion]
* **Cell Count (S):** [e.g., 6S]
* **Nominal Voltage:** [e.g., 22.2V]
* **Maximum Fully Charged Voltage:** [e.g., 25.2V]
* **Absolute Minimum Safe Voltage (Sag Limit):** [e.g., 19.8V or 3.3V per cell]
* **Power Module:** Holybro PM06 (Standard Pixhawk 6C Mini combo)

## 4. SENSOR BASELINES & THRESHOLDS
*(Note to LLM: Any telemetry data exceeding these thresholds should be flagged as a primary anomaly.)*
* **Vibration (VIBE):** X/Y axes should remain below 30m/s/s. Z axis should remain below 45m/s/s.
* **Clipping:** IMU clipping (clip0, clip1, clip2) greater than 0 indicates mechanical hard-mounting issues.
* **VCC (Board Voltage):** Must remain strictly between 4.9V and 5.3V. Fluctuations greater than 0.15V indicate power module failure.