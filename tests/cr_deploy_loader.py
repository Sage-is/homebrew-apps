"""Import the `cr-deploy` script (it has no .py suffix) as a module for tests."""

import importlib.machinery
import importlib.util
from pathlib import Path

PATH = str(Path(__file__).resolve().parents[1] / "cr-deploy")
loader = importlib.machinery.SourceFileLoader("cr_deploy", PATH)
spec = importlib.util.spec_from_loader("cr_deploy", loader)
cr_deploy = importlib.util.module_from_spec(spec)
loader.exec_module(cr_deploy)


class FakeCaptain:
    """Answers GETs from a script: each path maps to a list of replies, the last repeats."""

    name = "fake"

    def __init__(self, replies: dict[str, list[dict]]):
        self.replies, self.posts = replies, []

    def get(self, path: str) -> dict:
        queue = self.replies[path]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def post(self, path: str, data: dict) -> dict:
        self.posts.append((path, data))
        return {}
