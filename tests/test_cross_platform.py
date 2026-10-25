"""Platform-independent validation of Windows known folders and MIDI diagnostics."""
from pathlib import Path
from mixassist.fl.paths import documents_candidates, fl_settings_candidates, find_fl_subdir
from mixassist.doctor import diagnose


def test_windows_redirected_documents(tmp_path):
    redirected = tmp_path / "OneDrive" / "Documents"
    redirected.mkdir(parents=True)
    env = {"USERPROFILE": str(tmp_path), "OneDrive": str(tmp_path / "OneDrive")}
    settings = redirected / "Image-Line" / "FL Studio" / "Settings"
    scripts = settings / "Piano roll scripts"
    scripts.mkdir(parents=True)
    assert documents_candidates("win32", env, tmp_path, registry_personal=None)[0] == redirected
    assert find_fl_subdir("Piano roll scripts", platform="win32", environ=env, home=tmp_path,
                          registry_personal=None) == scripts


def test_windows_known_folder_registry_expansion(tmp_path):
    env = {"USERPROFILE": str(tmp_path)}
    result = documents_candidates("win32", env, tmp_path,
                                  registry_personal=r"%USERPROFILE%\\RedirectedDocs")
    assert result[0] == tmp_path / "RedirectedDocs"


def test_macos_documents_folder(tmp_path):
    docs = tmp_path / "Documents"
    assert documents_candidates("darwin", {}, tmp_path) == [docs]
    assert fl_settings_candidates("darwin", {}, tmp_path)[0] == (
        docs / "Image-Line" / "FL Studio" / "Settings")


def test_doctor_missing_ports(monkeypatch, tmp_path):
    monkeypatch.setattr("mixassist.doctor.find_fl_subdir", lambda *args, **kwargs: None)
    monkeypatch.setattr("mixassist.doctor.fl_settings_candidates", lambda: [])
    result = diagnose(port_reader=lambda: {"inputs": [], "outputs": []})
    assert result["bridge"] == "not checked"
    assert any("Missing MIDI ports" in hint for hint in result["instructions"])


def test_doctor_handshake(monkeypatch):
    monkeypatch.setattr("mixassist.doctor.find_fl_subdir", lambda *args, **kwargs: None)
    monkeypatch.setattr("mixassist.doctor.fl_settings_candidates", lambda: [])
    ports = lambda: {"outputs": ["IAC Driver MA to FL"], "inputs": ["MA from FL 1"]}
    data = diagnose(check=True, port_reader=ports, check_fn=lambda: {"api": 35, "port": 10})
    assert data["bridge"] == "connected"
