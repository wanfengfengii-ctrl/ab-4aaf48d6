#!/usr/bin/env bash
# verify 一次性服务入口：任一步失败立即以非零码退出。
# 1) 镜像内项目构建（setuptools 构建并安装本项目）
# 2) 代码测试（pytest：重组/状态机单测 + 真实 socket 集成测试）
# 3) 真实 TCP 写入与 HTTP 查询冒烟（对正在运行的 gateway 容器）
set -euo pipefail

echo "== [verify 1/3] in-image project build =="
python3 -m pip install --no-cache-dir --no-deps --no-build-isolation .
python3 -c "import app; print('app version:', app.__version__)"

echo "== [verify 2/3] code tests =="
python3 -m pytest -q

echo "== [verify 3/3] live TCP write + HTTP query smoke =="
python3 scripts/smoke.py

echo "== [verify] ALL STAGES PASSED =="
