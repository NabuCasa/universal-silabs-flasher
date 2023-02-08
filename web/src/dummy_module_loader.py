import sys
import unittest.mock


class DummyFinder:
    """
    Combined module loader and finder that recursively returns Mock objects.
    """

    def __init__(self, name):
        self.name = name

    def find_module(self, fullname, path=None):
        if fullname.startswith(self.name):
            return self

    def load_module(self, fullname):
        return sys.modules.setdefault(fullname, unittest.mock.MagicMock(__path__=[]))


sys.meta_path.append(DummyFinder(__name__))
