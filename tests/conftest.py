def pytest_addoption(parser):
    group = parser.getgroup("distributions")
    group.addoption("--dist", help="Directory of built wheels and sdists")
    group.addoption("--package", help="Check only this distribution")
    group.addoption(
        "--published-dependencies",
        action="store_true",
        help="Resolve companion dependencies from PyPI instead of candidates",
    )


def pytest_ignore_collect(collection_path, config):
    if collection_path.name == "distributions" and not config.getoption("--dist"):
        return True
