"""Print the conservative Serial budget for representative configurations."""
from rideguard_sim.protocol import budget_bytes_per_second

LIMIT = 921600 / 10 * 0.8

print(f"80% of 921600 baud (8N1) = {LIMIT:.0f} byte/s\n")
for period in (65, 100, 250):
    for max_targets in (1, 2, 4):
        for speed in (0.5, 1, 2):
            used = budget_bytes_per_second(period, max_targets, speed)
            verdict = "OK" if used <= LIMIT else "OVER"
            print(f"period={period:4}ms targets={max_targets} speed={speed}x "
                  f"-> {used:8.0f} B/s  {verdict}")
    print()
