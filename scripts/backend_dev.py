"""Owned development server: a private stop file lets Windows drain Uvicorn cleanly."""
import logging
import sys
import threading
import time
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
# Each model call logs its duration, tokens and generation ID at INFO; without this, slow runs leave no breakdown.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
for noisy in ("httpx", "httpcore", "openai"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
server = uvicorn.Server(uvicorn.Config("analyst.api:app", host="127.0.0.1", port=8000))


def shutdown_on_signal():
    while not Path(sys.argv[1]).exists():
        time.sleep(0.1)
    server.should_exit = True


threading.Thread(target=shutdown_on_signal, daemon=True).start()
server.run()
