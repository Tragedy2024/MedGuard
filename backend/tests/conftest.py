"""pytest 公共配置：把项目根加入 sys.path，测试无需安装包即可运行。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)