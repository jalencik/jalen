"""Read package dependencies from the same list used by pip setup."""
from pathlib import Path


def read_dependencies(path: Path) -> list[str]:
    dependencies = []
    for line in path.read_text(encoding="utf-8").splitlines():
        requirement = line.split("#", 1)[0].strip()
        if requirement and not requirement.startswith("-"):
            dependencies.append(requirement)
    return dependencies


if __name__ == "__main__":
    from setuptools import setup

    setup(install_requires=read_dependencies(Path(__file__).with_name("requirements.txt")))
