from pathlib import Path

from meuharness import artifacts, storage


def _isolated_db(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "DB_FILE", data_dir / "actis.db")
    monkeypatch.setenv("ACTIS_ARTIFACT_ROOT", str(tmp_path))


def test_register_download_attachment_and_bubble(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    file_path = tmp_path / "relatorio.xlsx"
    file_path.write_bytes(b"xlsx-test")

    created = artifacts.register_artifact(
        file_path,
        run_id="child-run",
        agent_id="excel-agent",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert created["name"] == "relatorio.xlsx"
    assert created["size"] == len(b"xlsx-test")

    attachment = artifacts.as_attachment(created)
    assert attachment["type"] == "file"
    assert attachment["artifact_id"] == created["id"]
    assert attachment["download_url"].endswith("/download")

    metadata, resolved = artifacts.artifact_file(created["id"])
    assert metadata["id"] == created["id"]
    assert resolved == file_path.resolve()

    bubbled = artifacts.bubble_run_artifacts("child-run", "parent-run", "general")
    assert len(bubbled) == 1
    parent = artifacts.list_run_attachments("parent-run")
    assert parent[0]["name"] == "relatorio.xlsx"
