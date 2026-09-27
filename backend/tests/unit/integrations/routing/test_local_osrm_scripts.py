"""Behavior at the boundary of the Windows-only local OSRM tools."""

import subprocess
from pathlib import Path

from app.core.config import Settings


ROOT = Path(__file__).resolve().parents[4]


def test_osrm_has_local_default_url(monkeypatch):
    monkeypatch.delenv("OSRM_BASE_URL", raising=False)
    assert Settings(_env_file=None).osrm_base_url == "http://localhost:5000"


def test_start_script_refuses_missing_dataset_from_another_directory(tmp_path):
    script = ROOT / "tools" / "osrm" / "scripts" / "start-osrm.ps1"
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-DataDirectory", str(tmp_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "OSRM dataset not found." in result.stdout + result.stderr
    assert "Run setup-osrm.ps1 first." in result.stdout + result.stderr
