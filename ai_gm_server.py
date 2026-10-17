#!/usr/bin/env python3
"""
ai_gm_server.py - AI GM Bridge サーバー起動スクリプト

実装は gm_server パッケージに機能単位で分割されています。
  python3 ai_gm_server.py
"""

import asyncio
import os
import sys

# `python3 ai_gm_server.py` をどこから実行しても gm_server / tools を import できるようにする
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gm_server.app import main  # noqa: E402

if __name__ == "__main__":
    asyncio.run(main())
