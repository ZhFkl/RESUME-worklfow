"""兼容旧终端命令；实现位于 backend/agent.py。"""
import runpy

if __name__ == "__main__":
    runpy.run_module("backend.agent", run_name="__main__")
