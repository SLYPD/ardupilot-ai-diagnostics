"""Generate a mock ArduPilot telemetry CSV with seeded anomalies for pipeline testing."""
import csv

ROWS = 100
HEADER = [
    "TimeS",
    "BAT.Volt", "BAT.Curr", "BAT.Enrg",
    "VCC",
    "VIBE.VibeX", "VIBE.VibeY", "VIBE.VibeZ",
    "VIBE.Clip0", "VIBE.Clip1", "VIBE.Clip2",
    "ATT.DesRoll", "ATT.Roll", "ATT.DesPitch", "ATT.Pitch",
    "RCOU.C1", "RCOU.C2", "RCOU.C3", "RCOU.C4", "RCOU.C5", "RCOU.C6",
    "RCOU.C7", "RCOU.C8",
]

# Normal hover values
NORMAL = {
    # BAT.Enrg accumulates consumed energy in mAh. At 15 A average:
    # 15 A * (t s / 3600 s/h) * 1000 mAh/Ah = t * 4.167 mAh/s ≈ t * 4.17
    "BAT.Volt": 22.5, "BAT.Curr": 15.0, "BAT.Enrg": lambda t: round(t * 4.167, 1),
    "VCC": 5.05,
    "VIBE.VibeX": 5.0, "VIBE.VibeY": 4.0, "VIBE.VibeZ": 8.0,
    "VIBE.Clip0": 0, "VIBE.Clip1": 0, "VIBE.Clip2": 0,
    "ATT.DesRoll": 0.0, "ATT.Roll": 0.2, "ATT.DesPitch": 0.0, "ATT.Pitch": -0.1,
    "RCOU.C1": 1550, "RCOU.C2": 1540, "RCOU.C3": 1560,
    "RCOU.C4": 1530, "RCOU.C5": 1550, "RCOU.C6": 1540,
    "RCOU.C7": 1550, "RCOU.C8": 1540,
}

# Anomaly definitions: {row_number: {column_override, ...}}
ANOMALIES = {
    # Row 20-21: VCC sag to 4.6V (below 4.9 min) → VCC_DROP
    20: {"VCC": 4.6},
    21: {"VCC": 4.55},
    # Row 40-42: C4 at 1970 (>1940 redline), C1 at 1150 (<1300 low)
    #            → MAX_THRUST_RCOU.C4 + IMBALANCE_RCOU.C1_vs_RCOU.C4
    #            3 rows required for persistence filter
    40: {"RCOU.C4": 1970, "RCOU.C1": 1150},
    41: {"RCOU.C4": 1985, "RCOU.C1": 1130},
    42: {"RCOU.C4": 1960, "RCOU.C1": 1100},
    # Row 52: Motor C3 stuck at min (1000) → MIN_THRUST_RCOU.C3
    52: {"RCOU.C3": 1000},
    # Row 60-61: VibeZ spike (>45), Clip0 fires (>0) → VIBE_Z_SPIKE + IMU_CLIP_VIBE.Clip0
    60: {"VIBE.VibeZ": 50.0, "VIBE.Clip0": 3},
    61: {"VIBE.VibeZ": 55.0, "VIBE.Clip0": 5},
    # Row 70: ATT desync — Roll at 20° while desired is 0° → ATT_DESYNC_ROLL
    70: {"ATT.DesRoll": 0.0, "ATT.Roll": 20.0},
    # Row 80-81: Voltage sag (volt < median*0.95=21.37, curr > median*1.5=22.5)
    #            → VOLTAGE_SAG
    80: {"BAT.Volt": 19.0, "BAT.Curr": 50.0},
    81: {"BAT.Volt": 18.5, "BAT.Curr": 55.0},
}

rows_written = 0
with open("flight_log_01.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=HEADER)
    w.writeheader()
    for i in range(ROWS):
        t = round(i * 0.1, 1)
        row = {"TimeS": t}
        for col in HEADER[1:]:
            val = NORMAL[col]
            if callable(val):
                val = val(t)
            row[col] = val
        # Apply anomaly overrides
        if i in ANOMALIES:
            row.update(ANOMALIES[i])
        w.writerow(row)
        rows_written += 1

print(f"Generated flight_log_01.csv: {rows_written} rows, {len(HEADER)} columns")
print(f"Seeded anomaly rows: {sorted(ANOMALIES.keys())}")

# Show the key anomaly rows
print("\n--- Anomaly rows preview ---")
for r in sorted(ANOMALIES.keys()):
    print(f"Row {r}: {ANOMALIES[r]}")
