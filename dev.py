import subprocess
import sys

backend = subprocess.Popen(
    [
        sys.executable,
        "-m",
        "uvicorn",
        "src.api.app:app",
        "--port",
        "8000",
        "--reload",
    ]
)

frontend = subprocess.Popen(
    ["npm", "run", "dev"],
    cwd="frontend",
    shell=True,
)

try:
    backend.wait()
    frontend.wait()
except KeyboardInterrupt:
    backend.terminate()
    frontend.terminate()
