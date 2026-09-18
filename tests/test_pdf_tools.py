import json

from meuharness import pdf_tools


def _tools(scopes=("pdf.read", "pdf.write")):
    return {tool.__name__: tool for tool in pdf_tools.build_pdf_tools(set(scopes))}


def test_pdf_create_inspect_edit_and_render(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_tools, "HOME", tmp_path.resolve())
    tools = _tools()
    original = tmp_path / "original.pdf"
    created = json.loads(tools["pdf_create"](str(original), "# Título\n\nConteúdo **forte**.", "Teste"))
    assert created["ok"] is True
    inspected = json.loads(tools["pdf_inspect"](str(original), 4))
    assert inspected["pages"] == 1
    assert "Título" in inspected["preview"][0]["text"]

    edited = tmp_path / "edited.pdf"
    result = json.loads(tools["pdf_edit"](
        str(original), str(edited),
        '[{"type":"insert_text","page":1,"x":72,"y":180,"text":"EDITADO"}]',
    ))
    assert result["ok"] is True
    inspected_edit = json.loads(tools["pdf_inspect"](str(edited), 4))
    assert "EDITADO" in inspected_edit["preview"][0]["text"]

    rendered = json.loads(tools["pdf_render"](str(edited), str(tmp_path / "render"), "1", 96))
    assert len(rendered["files"]) == 1
    assert (tmp_path / "render" / "page-001.png").is_file()


def test_pdf_tool_scopes_are_granular(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_tools, "HOME", tmp_path.resolve())
    assert {tool.__name__ for tool in _tools(("pdf.read",)).values()} == {"pdf_inspect"}
    assert {tool.__name__ for tool in _tools(("pdf.write",)).values()} == {"pdf_create"}


def test_pdf_create_registers_artifact_for_run(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_tools, "HOME", tmp_path.resolve())
    seen = {}

    def fake_register(path, *, run_id, agent_id=None, mime_type=None, **kwargs):
        seen.update(path=str(path), run_id=run_id, agent_id=agent_id, mime_type=mime_type)
        return {"id": "artifact-pdf"}

    monkeypatch.setattr(pdf_tools, "register_artifact", fake_register)
    tools = {tool.__name__: tool for tool in pdf_tools.build_pdf_tools(
        {"pdf.write"}, run_id="run-pdf", agent_id="pdf-agent"
    )}
    target = tmp_path / "artifact.pdf"
    result = json.loads(tools["pdf_create"](str(target), "# Visible title\n\nBody", "Metadata title"))

    assert result["artifact_id"] == "artifact-pdf"
    assert seen == {
        "path": str(target), "run_id": "run-pdf", "agent_id": "pdf-agent", "mime_type": "application/pdf"
    }
