# Copyright (C) 2026 CARROLAGGI Xavier
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
"""
Dependency maintenance tools (phase 2 ter, docs/maintenance-dependances.md):
tools/dependencies/ (inventory, lock from a pip report) and the build-time
check of the analyzer image (presidio/analyzer-build/check_lock.py). None of
them is in an image: they are loaded from the repository, mounted read-only
by app/run-tests.sh (OBFUSK8_REPO). No network: the PyPI and OSV calls of
outdated.py are replaced by canned answers.
"""

import importlib.util
import io
import json
import os
import runpy
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SHA = "a" * 64


def _load(relative: str) -> ModuleType:
    path = Path(os.environ.get("OBFUSK8_REPO", "")) / relative
    assert path.is_file(), "OBFUSK8_REPO must point to the repository (see app/run-tests.sh)"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lock_from_report = _load("tools/dependencies/lock_from_report.py")
outdated = _load("tools/dependencies/outdated.py")
check_lock = _load("presidio/analyzer-build/check_lock.py")


def _item(name: str, version: str, url: str, sha: str | None = _SHA) -> dict[str, Any]:
    hashes = {"sha256": sha} if sha else {}
    return {
        "metadata": {"name": name, "version": version},
        "download_info": {"url": url, "archive_info": {"hashes": hashes}},
    }


# --- lock_from_report.py ----------------------------------------------------


def test_lock_trie_par_nom_avec_empreinte() -> None:
    report = {
        "install": [
            _item("Werkzeug", "3.1.9", "https://files/werkzeug-3.1.9-py3-none-any.whl"),
            _item("anyio", "4.15.1", "https://files/anyio-4.15.1-py3-none-any.whl"),
        ]
    }
    assert lock_from_report.lock_lines(report) == [
        f"anyio==4.15.1 \\\n    --hash=sha256:{_SHA}",
        f"Werkzeug==3.1.9 \\\n    --hash=sha256:{_SHA}",
    ]


@pytest.mark.parametrize(
    ("url", "sha"),
    [("https://files/pkg-1.0.tar.gz", _SHA), ("https://files/pkg-1.0-py3-none-any.whl", None)],
)
def test_lock_refuse_source_ou_empreinte_inconnue(url: str, sha: str | None) -> None:
    with pytest.raises(SystemExit, match="not a wheel with a known SHA-256"):
        lock_from_report.lock_lines({"install": [_item("pkg", "1.0", url, sha)]})


def test_lock_point_d_entree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"install": [_item("anyio", "4.15.1", "https://f/anyio-4.15.1-py3-none-any.whl")]}))
    monkeypatch.setattr(sys, "argv", ["lock_from_report.py", str(report)])
    runpy.run_path(str(lock_from_report.__file__), run_name="__main__")
    assert capsys.readouterr().out == f"anyio==4.15.1 \\\n    --hash=sha256:{_SHA}\n"


# --- outdated.py --------------------------------------------------------------


def _pypi(releases: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {"releases": releases}


def _file(date: str, yanked: bool = False) -> dict[str, Any]:
    return {"upload_time": f"{date}T00:00:00", "yanked": yanked}


def test_derniere_version_ignore_preversions_retirees_et_invalides(monkeypatch: pytest.MonkeyPatch) -> None:
    releases = {
        "1.0": [_file("2026-01-01")],
        "1.2": [_file("2026-03-01"), _file("2026-03-02")],
        "1.3": [_file("2026-04-01", yanked=True)],
        "2.0rc1": [_file("2026-05-01")],
        "2.0.dev1": [_file("2026-05-02")],
        "1.4": [],
        "not-a-version": [_file("2026-06-01")],
        "0.9": [_file("2025-01-01")],
    }
    monkeypatch.setattr(outdated, "_get", lambda url, payload=None: _pypi(releases))
    version, date = outdated.latest_release("pkg")
    assert (str(version), date) == ("1.2", "2026-03-01")


def test_requete_https_json(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: int) -> io.BytesIO:
        sent.update(url=request.full_url, body=request.data, timeout=timeout)
        return io.BytesIO(b'{"vulns": []}')

    monkeypatch.setattr(outdated.urllib.request, "urlopen", fake_urlopen)
    assert outdated._get("https://api.osv.dev/v1/query", {"version": "1.0"}) == {"vulns": []}
    assert sent == {"url": "https://api.osv.dev/v1/query", "body": b'{"version": "1.0"}', "timeout": 30}


def test_schema_autre_que_https_refuse() -> None:
    with pytest.raises(ValueError, match="refused URL scheme"):
        outdated._get("file:///etc/passwd")


def test_derniere_version_absente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outdated, "_get", lambda url, payload=None: _pypi({"1.0rc1": [_file("2026-01-01")]}))
    with pytest.raises(LookupError):
        outdated.latest_release("pkg")


def test_inventaire_ligne_par_paquet(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_get(url: str, payload: dict[str, Any] | None = None) -> Any:
        if url.endswith("/query"):
            assert payload is not None
            return {"vulns": [{"id": "GHSA-test"}]} if payload["version"] == "3.1.8" else {}
        if "/broken/" in url:
            raise OSError("unreachable")
        return _pypi({"3.1.8": [_file("2026-04-02")], "3.1.9": [_file("2026-09-27")]})

    monkeypatch.setattr(outdated, "_get", fake_get)
    monkeypatch.setattr(sys, "stdin", io.StringIO("ana werkzeug 3.1.8\n\napp werkzeug 3.1.9\nana broken 1.0\n"))
    assert outdated.main() == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "ana\twerkzeug\t3.1.8\t3.1.9\t2026-09-27\tOUTDATED\tGHSA-test"
    assert lines[1] == "app\twerkzeug\t3.1.9\t3.1.9\t2026-09-27\t\t-"
    assert lines[2].startswith("ana\tbroken\t1.0\tERROR")


def test_inventaire_point_d_entree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(outdated.__file__), run_name="__main__")
    assert exit_info.value.code == 0


# --- check_lock.py -------------------------------------------------------------

_LOCK = f"""# comment
alpha==1.0 \\
    --hash=sha256:{_SHA}
Beta_Pkg==2.0 \\
    --hash=sha256:{_SHA}
# model
fr_core_news_md @ https://github.com/x/releases/download/fr_core_news_md-3.8.0/fr_core_news_md-3.8.0-py3-none-any.whl \\
    --hash=sha256:{"b" * 64}
"""


def test_verrou_analyse() -> None:
    versions, direct = check_lock._declared(_LOCK)
    assert versions == {"alpha": "1.0", "beta-pkg": "2.0", "fr-core-news-md": "3.8.0"}
    assert direct == {"fr-core-news-md": "b" * 64}


@pytest.mark.parametrize(
    ("lock", "message"),
    [("alpha==1.0\n", "entry without --hash"), (f"alpha>=1.0 --hash=sha256:{_SHA}\n", "unparsed entry")],
)
def test_verrou_entree_invalide(lock: str, message: str) -> None:
    with pytest.raises(SystemExit, match=message):
        check_lock._declared(lock)


class _Dist:
    def __init__(self, name: str, version: str, direct_url: str | None = None) -> None:
        self.metadata = {"Name": name}
        self.version = version
        self._direct_url = direct_url

    def read_text(self, filename: str) -> str | None:
        return self._direct_url if filename == "direct_url.json" else None


def _run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], dists: list[_Dist]
) -> tuple[int, str]:
    lock = tmp_path / "requirements.lock"
    lock.write_text(_LOCK, encoding="utf-8")
    by_name = {check_lock._canonical(d.metadata["Name"]): d for d in dists}
    monkeypatch.setattr(check_lock.importlib.metadata, "distributions", lambda: dists)
    monkeypatch.setattr(check_lock.importlib.metadata, "distribution", lambda n: by_name[check_lock._canonical(n)])
    code = check_lock.main(str(lock))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def _model(sha: str) -> _Dist:
    return _Dist("fr_core_news_md", "3.8.0", f'{{"archive_info": {{"hashes": {{"sha256": "{sha}"}}}}}}')


def test_environnement_conforme(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dists = [_Dist("alpha", "1.0"), _Dist("beta-pkg", "2.0"), _model("b" * 64)]
    code, output = _run(monkeypatch, tmp_path, capsys, dists)
    assert code == 0
    assert "3 distributions match" in output


def test_environnement_non_conforme(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dists = [_Dist("alpha", "1.1"), _Dist("extra", "0.1"), _model("c" * 64)]
    code, output = _run(monkeypatch, tmp_path, capsys, dists)
    assert code == 1
    assert "not declared: extra==0.1" in output
    assert "not installed: beta-pkg==2.0" in output
    assert "version: alpha installed 1.1, declared 1.0" in output
    assert "sha256: fr-core-news-md installed from another artifact" in output


def test_modele_sans_direct_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dists = [_Dist("alpha", "1.0"), _Dist("beta-pkg", "2.0"), _Dist("fr_core_news_md", "3.8.0")]
    code, output = _run(monkeypatch, tmp_path, capsys, dists)
    assert code == 1
    assert "sha256: fr-core-news-md" in output
