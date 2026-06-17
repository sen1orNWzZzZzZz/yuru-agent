#!/usr/bin/env python3
"""
灵动旅心 V3 - 启动脚本
PlannerAgent + 子Agent 智能旅游规划系统
"""
import sys
import os

# 确保能导入app模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

if __name__ == "__main__":
    print("=" * 60)
    print("  灵动旅心 V3 - 智能旅游多Agent规划系统")
    print("  PlannerAgent + 子Agent架构")
    print("=" * 60)
    print()
    print("启动中...")
    print("- 数据库: 自动初始化 (SQLite)")
    print("- LLM: 从数据库读取配置 (未配置则使用Mock)")
    print("- 天气API: 从数据库读取配置 (未配置则使用Mock)")
    print("- 地图API: 从数据库读取配置 (未配置则使用Mock)")
    print()
    print("服务地址:")
    print("  首页:     http://127.0.0.1:8000/")
    print("  规划页面: http://127.0.0.1:8000/plan")
    print("  管理后台: http://127.0.0.1:8000/admin")
    print("  API文档:  http://127.0.0.1:8000/docs")
    print()
    print("按 Ctrl+C 停止服务")
    print("=" * 60)

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
