import os
from pathlib import Path
from dotenv import load_dotenv

# Ensure .env is loaded from project root regardless of cwd
load_dotenv(Path(__file__).parent / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

SIZE_THRESHOLD_KB = 1000
DIMENSION_THRESHOLD_PX = 3000

# Scan limit: max scans per domain per day (0 = unlimited)
SCAN_LIMIT_PER_DAY = int(os.getenv("SCAN_LIMIT_PER_DAY", "0"))
