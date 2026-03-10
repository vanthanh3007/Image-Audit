"""Run all pending migrations via Supabase REST API.

Usage: python run_migrations.py
"""
import os
import sys
import requests
from pathlib import Path

# Load .env
sys.path.insert(0, str(Path(__file__).parent))
from config import SUPABASE_URL, SUPABASE_KEY

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run_sql(sql):
    """Execute SQL via Supabase RPC (pg_execute) or direct REST."""
    # Use the Supabase SQL endpoint
    url = f"{SUPABASE_URL}/rest/v1/rpc/exec_sql"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    # This won't work with publishable key - need to use service_role key
    # Instead, just print the SQL for user to copy-paste into SQL Editor
    return None


def main():
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print("No migration files found.")
        return

    print("=" * 60)
    print("IMAGE AUDIT TOOL - Database Migrations")
    print("=" * 60)
    print()
    print("Copy the SQL below and paste into Supabase SQL Editor:")
    print("  https://supabase.com/dashboard → SQL Editor → New Query")
    print()
    print("-" * 60)

    for f in files:
        sql = f.read_text(encoding="utf-8")
        print(f"\n-- Migration: {f.name}")
        print(sql)

    print("-" * 60)
    print("\nPaste ALL the SQL above into Supabase SQL Editor and click RUN.")


if __name__ == "__main__":
    main()
