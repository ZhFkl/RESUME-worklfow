"""兼容旧终端命令；实现位于 backend/task_metrics.py。"""
import runpy

if __name__ == "__main__":
    runpy.run_module("backend.task_metrics", run_name="__main__")
