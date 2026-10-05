"""容器健康检查：HTTP /healthz 可达即健康。"""

import os
import sys
import urllib.request

port = os.environ.get("HTTP_PORT", "8080")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
