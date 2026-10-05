# 根级 conftest：确保以仓库根目录为导入根（app 包可被测试导入）。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
