"""代码移动后仍统一从项目根目录读取规则、模板和配置。"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
