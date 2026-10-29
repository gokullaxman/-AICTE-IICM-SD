#!/usr/bin/env python3
"""
CLI Utility to import AICTE Approved Institutions from an official AICTE EOA CSV dump.
Usage:
    python3 scripts/import_aicte_institutions.py data/aicte_approved_institutions_public.csv
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.core.database import db

def main():
    db.initialize_and_seed()
    csv_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "../data/aicte_approved_institutions_public.csv")
    imported = db.import_institutions_from_csv(csv_path)
    total = db.fetch_one("SELECT COUNT(*) AS cnt FROM institutions")["cnt"]
    print(f"Import complete: {imported} new institution(s) added. Total AICTE institutions in DB: {total}")

if __name__ == "__main__":
    main()
