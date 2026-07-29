from pathlib import Path


DOCKERFILE = Path(__file__).parents[1] / "Dockerfile"


def test_runtime_image_removes_global_python_installers() -> None:
    dockerfile = DOCKERFILE.read_text()

    assert "/usr/local/lib/python3.11/site-packages/*" in dockerfile
    assert "/usr/local/lib/python3.11/ensurepip" in dockerfile
    assert "/usr/local/bin/pip*" in dockerfile
