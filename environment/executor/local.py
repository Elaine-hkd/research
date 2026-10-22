from .bound import BoundExecutor


class LocalExecutor(BoundExecutor):
    """使用所属环境本地进程会话的绑定执行器。"""
    provider = "local"
