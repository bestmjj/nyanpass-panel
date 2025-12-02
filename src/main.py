#!/usr/bin/env python3
"""
Nyanpass Panel 主入口点
"""

from nyanpass_panel.app import NyanpassPanel
import os

if __name__ == '__main__':
    """应用入口点"""
    # 获取配置文件
    config = os.getenv("CONFIG", "config.json")
    panel = NyanpassPanel(config)
    panel.run()