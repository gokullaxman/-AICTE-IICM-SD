import os
import sys

# Ensure backend directory is first in sys.path so 'app.*' imports resolve cleanly
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, "..", "backend"))
root_dir = os.path.abspath(os.path.join(current_dir, ".."))

if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# Ensure VERCEL indicator is set for serverless path adaptation
os.environ.setdefault("VERCEL", "1")

# Import the database adapter and FastAPI app
from app.core.database import db
from app.main import app

# Initialize and seed schema on cold start
try:
    db.initialize_and_seed()
except Exception as e:
    # Log warning for debugging without preventing module loading
    print(f"[Vercel Initialization Notice] Database setup: {e}")
