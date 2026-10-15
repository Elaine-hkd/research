from .bound import BoundExecutor


class LocalExecutor(BoundExecutor):
    """Bound executor using the owner's local process session."""
    provider = "local"
