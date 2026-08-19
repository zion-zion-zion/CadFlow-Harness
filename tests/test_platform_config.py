import tomllib
from pathlib import Path
from typing import Any

from packaging.markers import Marker


ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_ENVIRONMENTS = {
    "sys_platform == 'linux' and platform_machine == 'x86_64' and python_version == '3.12'",
    "sys_platform == 'darwin' and platform_machine == 'arm64' and python_version == '3.13'",
}


def _project_config() -> dict[str, Any]:
    with (ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)


def _matching_cadflow_sources(environment: dict[str, str]) -> list[dict[str, str]]:
    sources = _project_config()["tool"]["uv"]["sources"]["cadflow"]
    return [
        source
        for source in sources
        if Marker(source["marker"]).evaluate(environment=environment)
    ]


def test_uv_resolution_is_limited_to_supported_environments() -> None:
    environments = _project_config()["tool"]["uv"]["environments"]

    assert set(environments) == SUPPORTED_ENVIRONMENTS


def test_cadflow_wheel_matches_each_supported_platform() -> None:
    supported = [
        (
            {
                "sys_platform": "linux",
                "platform_machine": "x86_64",
                "python_version": "3.12",
            },
            "cadflow-0.1.0-cp312-cp312-linux_x86_64.whl",
        ),
        (
            {
                "sys_platform": "darwin",
                "platform_machine": "arm64",
                "python_version": "3.13",
            },
            "cadflow-0.1.0-cp313-cp313-macosx_26_0_arm64.whl",
        ),
    ]

    for environment, filename in supported:
        matches = _matching_cadflow_sources(environment)
        assert [Path(source["path"]).name for source in matches] == [filename]


def test_cadflow_wheel_rejects_unsupported_platform_python_pairs() -> None:
    unsupported = [
        {
            "sys_platform": "linux",
            "platform_machine": "x86_64",
            "python_version": "3.13",
        },
        {
            "sys_platform": "darwin",
            "platform_machine": "x86_64",
            "python_version": "3.13",
        },
    ]

    for environment in unsupported:
        assert _matching_cadflow_sources(environment) == []


def test_run_script_uses_portable_port_detection() -> None:
    script = (ROOT / "run.sh").read_text()

    assert "/dev/tcp" not in script
    assert "socket.create_connection" in script
