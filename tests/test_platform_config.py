import os
import subprocess
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


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents)
    path.chmod(0o755)


def _run_setup_check(
    tmp_path: Path,
    *,
    platform: str,
    architecture: str,
    node_version: str = "22.12.0",
    glibc_version: str = "2.31",
    macos_version: str = "12.0",
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    _write_executable(
        bin_dir / "uname",
        "#!/bin/sh\ncase \"$1\" in\n"
        f"  -s) echo {platform} ;;\n"
        f"  -m) echo {architecture} ;;\n"
        "esac\n",
    )
    _write_executable(
        bin_dir / "getconf",
        f"#!/bin/sh\necho 'glibc {glibc_version}'\n",
    )
    _write_executable(
        bin_dir / "sw_vers",
        f"#!/bin/sh\necho '{macos_version}'\n",
    )
    _write_executable(
        bin_dir / "node",
        f"#!/bin/sh\n[ \"$1\" = \"-p\" ] && echo '{node_version}' || echo 'v{node_version}'\n",
    )
    _write_executable(bin_dir / "npm", "#!/bin/sh\necho '11.0.0'\n")
    _write_executable(bin_dir / "uv", "#!/bin/sh\necho 'uv 0.8.0'\n")

    environment = os.environ.copy()
    environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
    return subprocess.run(
        ["bash", str(ROOT / "setup.sh"), "--check"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )


def test_setup_script_selects_supported_platform_environment(tmp_path: Path) -> None:
    linux = _run_setup_check(
        tmp_path / "linux",
        platform="Linux",
        architecture="x86_64",
    )
    macos = _run_setup_check(
        tmp_path / "macos",
        platform="Darwin",
        architecture="arm64",
    )

    assert linux.returncode == 0, linux.stderr
    assert "using Python 3.12" in linux.stdout
    assert macos.returncode == 0, macos.stderr
    assert "using Python 3.13" in macos.stdout


def test_setup_script_rejects_unsupported_platform(tmp_path: Path) -> None:
    result = _run_setup_check(
        tmp_path,
        platform="Linux",
        architecture="aarch64",
    )

    assert result.returncode == 1
    assert "unsupported platform Linux/aarch64" in result.stderr


def test_setup_script_rejects_unsupported_system_versions(tmp_path: Path) -> None:
    linux = _run_setup_check(
        tmp_path / "linux",
        platform="Linux",
        architecture="x86_64",
        glibc_version="2.30",
    )
    macos = _run_setup_check(
        tmp_path / "macos",
        platform="Darwin",
        architecture="arm64",
        macos_version="11.9",
    )

    assert linux.returncode == 1
    assert "glibc 2.30 is unsupported" in linux.stderr
    assert macos.returncode == 1
    assert "macOS 11.9 is unsupported" in macos.stderr


def test_setup_script_rejects_incompatible_node_version(tmp_path: Path) -> None:
    result = _run_setup_check(
        tmp_path,
        platform="Linux",
        architecture="x86_64",
        node_version="20.18.0",
    )

    assert result.returncode == 1
    assert "Node.js ^20.19 or >=22.12 is required" in result.stderr
