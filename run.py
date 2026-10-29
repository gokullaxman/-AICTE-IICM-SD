import os
import sys
import threading
import time
import webbrowser
import uvicorn

def open_dashboard():
    time.sleep(1.5)
    try:
        webbrowser.open("http://localhost:8000")
    except Exception:
        pass

def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.join(root_dir, "backend")

    print("=" * 75)
    print("  AICTE Integrated Institution Complaint Monitoring & Status Dashboard")
    print("  Version: 3.0.0 (Production Build)")
    print("=" * 75)
    print("  • Web Dashboard:     http://localhost:8000")
    print("  • Alternate Port:    http://localhost:8080")
    print("  • FastAPI Docs:      http://localhost:8000/docs")
    print("  • System Health:     http://localhost:8000/health")
    print("=" * 75)
    print("Opening browser at http://localhost:8000 ...")
    print("Press Ctrl+C to terminate the application.")
    print("=" * 75)

    threading.Thread(target=open_dashboard, daemon=True).start()
    uvicorn.run("app.main:app", app_dir=backend_dir, host="0.0.0.0", port=8000, reload=False)

if __name__ == "__main__":
    main()

