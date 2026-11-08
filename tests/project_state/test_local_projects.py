from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from orbit.project_state.local_projects import (
    InvalidProjectConfigurationError,
    InvalidProjectIdError,
    InvalidProjectRootError,
    LocalProjectBinding,
    LocalProjectResolver,
    UnknownProjectError,
)


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    return path.resolve()


def test_resolves_configured_root(tmp_path):
    root = make_repo(tmp_path / "repo")
    binding = LocalProjectResolver({"alpha": root}).resolve("alpha")
    assert binding == LocalProjectBinding("alpha", root)


def test_symlinked_root_is_canonicalized(tmp_path):
    root = make_repo(tmp_path / "repo")
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    assert LocalProjectResolver({"alpha": str(link)}).resolve("alpha").repository_root == root


@pytest.mark.parametrize("bad", ["", "  ", " alpha", "alpha ", "a/b", "../x", "a\x00b", "-x", None, 3])
def test_rejects_blank_or_malformed_ids(tmp_path, bad):
    resolver = LocalProjectResolver({"alpha": make_repo(tmp_path / "repo")})
    with pytest.raises(InvalidProjectIdError):
        resolver.resolve(bad)


def test_rejects_unknown_id_and_is_case_sensitive(tmp_path):
    resolver = LocalProjectResolver({"alpha": make_repo(tmp_path / "repo")})
    for name in ("beta", "Alpha"):
        with pytest.raises(UnknownProjectError):
            resolver.resolve(name)


def test_does_not_infer_from_cwd_or_basename(tmp_path, monkeypatch):
    root = make_repo(tmp_path / "repo")
    monkeypatch.chdir(root)
    with pytest.raises(UnknownProjectError):
        LocalProjectResolver({"alpha": root}).resolve("repo")


def test_configuration_rejections(tmp_path):
    root = make_repo(tmp_path / "repo")
    for roots in (
        {"": root},
        {"bad id": root},
        {"alpha": "relative/path"},
        {"alpha": f"{root}/../repo"},
        {"alpha": ""},
        {"alpha": root, "beta": str(root) + "/"},
    ):
        with pytest.raises(InvalidProjectConfigurationError):
            LocalProjectResolver(roots)
    with pytest.raises(InvalidProjectConfigurationError):
        LocalProjectResolver(["alpha"])  # type: ignore[arg-type]


def test_from_json(tmp_path):
    root = make_repo(tmp_path / "repo")
    resolver = LocalProjectResolver.from_json(json.dumps({"alpha": str(root)}))
    assert resolver.resolve("alpha").project_id == "alpha"
    duplicate = '{"alpha": ' + json.dumps(str(root)) + ', "alpha": ' + json.dumps(str(root)) + '}'
    for text in ("not json", "[]", duplicate):
        with pytest.raises(InvalidProjectConfigurationError):
            LocalProjectResolver.from_json(text)


def test_missing_root_and_file_root(tmp_path):
    file_path = tmp_path / "file"
    file_path.write_text("x")
    resolver = LocalProjectResolver({"gone": tmp_path / "gone", "file": file_path})
    for name in ("gone", "file"):
        with pytest.raises(InvalidProjectRootError):
            resolver.resolve(name)


def test_rejects_non_git_directory_and_subdirectory(tmp_path):
    root = make_repo(tmp_path / "repo")
    sub = root / "pkg"
    sub.mkdir()
    plain = tmp_path / "plain"
    plain.mkdir()
    resolver = LocalProjectResolver({"sub": sub, "plain": plain})
    for name in ("sub", "plain"):
        with pytest.raises(InvalidProjectRootError):
            resolver.resolve(name)


def test_git_environment_overrides_cannot_redirect(tmp_path, monkeypatch):
    other = make_repo(tmp_path / "other")
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    with pytest.raises(InvalidProjectRootError):
        LocalProjectResolver({"plain": plain}).resolve("plain")
