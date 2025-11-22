import os
import sys
from flask import Flask
from app.utils.accessibility import fetch_elevator_status

# Mock app context if needed, or just test the utility directly
print("Fetching elevator status...")
outages = fetch_elevator_status()

print(f"Found outages for {len(outages)} stations.")
for station, details in list(outages.items())[:5]:
    print(f"\nStation: {station}")
    for d in details:
        print(f"  - {d['type']} ({d['equipment']}): {d['reason']}")
