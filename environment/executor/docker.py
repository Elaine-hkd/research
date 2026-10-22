from .bound import BoundExecutor


class DockerExecutor(BoundExecutor):
    """使用所属环境已创建的 Docker 沙箱的绑定执行器。"""
    provider = "docker"
