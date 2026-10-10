"""兼容旧终端命令；实现位于 backend/account_admin.py。"""
import runpy

if __name__ == "__main__":
    runpy.run_module("backend.account_admin", run_name="__main__")
