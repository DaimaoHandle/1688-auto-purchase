"""
管理服务器入口。

用法：python3 server/server_main.py
默认监听 0.0.0.0:1688
"""
import sys
import os
import logging

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn
from server.app import create_app

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "1688"))

if __name__ == "__main__":
    app = create_app()
    print(f"\n{'='*50}")
    print(f"  1688 采购管理系统")
    print(f"  http://{HOST}:{PORT}")
    print(f"{'='*50}\n")
    uvicorn.run(app, host=HOST, port=PORT)
