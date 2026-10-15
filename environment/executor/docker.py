from .bound import BoundExecutor


class DockerExecutor(BoundExecutor):
    """Bound executor using the owner's provisioned Docker sandbox."""
    provider = "docker"
