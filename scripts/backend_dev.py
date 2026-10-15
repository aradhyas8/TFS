"""Owned development server: a private stop file lets Windows drain Uvicorn cleanly."""
import sys
import threading
import time
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
server = uvicorn.Server(uvicorn.Config("analyst.api:app", host="127.0.0.1", port=8000))


def shutdown_on_signal():
    while not Path(sys.argv[1]).exists():
        time.sleep(0.1)
    server.should_exit = True


threading.Thread(target=shutdown_on_signal, daemon=True).start()
server.run()
