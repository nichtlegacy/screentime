#!/usr/bin/env python3
"""
Apple Screen Time Exporter - Main Pipeline

1. Collects data from Mac (knowledgeC.db) and iPhone (Biome)
2. Exports to Home Assistant (and InfluxDB)
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SRC_DIR = SCRIPT_DIR / "src"


def run_script(name: str, display_name: str) -> bool:
    """Runs a Python script and returns success status."""
    script = SRC_DIR / name
    if not script.exists():
        print(f"[ERROR] Script not found: {script}")
        return False

    print(f"\n{'='*60}")
    print(f">>> {display_name}")
    print('='*60)

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=SCRIPT_DIR
    )

    return result.returncode == 0


def main():
    print(f"╔{'═'*62}╗")
    print(f"║  Apple Screen Time Exporter - {datetime.now().strftime('%Y-%m-%d %H:%M')}       ║")
    print(f"╚{'═'*62}╝")

    # Step 1: Collect
    collect_ok = run_script("collector.py", "Collecting Screen Time Data")
    if not collect_ok:
        print("\n[WARN] Collection had issues, trying export anyway...")

    # Step 2: Export
    export_ok = run_script("exporter.py", "Exporting to Home Assistant")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY:")
    print(f"  Collect:  {'✓' if collect_ok else '✗'}")
    print(f"  Export:   {'✓' if export_ok else '✗'}")
    print('='*60)

    return 0 if (collect_ok and export_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
