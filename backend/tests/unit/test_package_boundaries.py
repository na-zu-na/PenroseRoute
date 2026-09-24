from importlib import import_module

import pytest


PACKAGES = [
    "app.schemas",
    "app.db.models",
    "app.db.repositories",
    "app.modules.resources",
    "app.modules.planning",
    "app.modules.operations",
    "app.modules.incidents",
    "app.modules.recovery",
    "app.modules.decisions",
    "app.integrations.optimization",
    "app.integrations.routing",
    "app.integrations.agent",
]


@pytest.mark.parametrize("package", PACKAGES)
def test_architecture_package_is_importable(package: str) -> None:
    assert import_module(package) is not None
