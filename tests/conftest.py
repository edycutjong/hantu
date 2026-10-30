import os
import plistlib
import time

import pytest

NOW = 1_800_000_000.0  # fixed clock so ages are deterministic


def write_plist(directory, name, data, age_days=0):
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    with open(p, "wb") as f:
        plistlib.dump(data, f)
    t = NOW - age_days * 86400
    os.utime(p, (t, t))
    return str(p)


def make_exe(path, text="#!/bin/sh\nexit 0\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def agents_dir(tmp_path):
    return tmp_path / "LaunchAgents"


@pytest.fixture
def empty_index():
    from hantu.scan import AppIndex
    return AppIndex()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The suite must never touch the network: any urlopen is a test bug."""
    import urllib.request

    def boom(*a, **k):
        raise AssertionError("test tried to use the network")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
