import json
from pathlib import Path

from meuharness import artifacts, storage
from meuharness.skills import scopes_for_skills, tools_for_skills
from meuharness.spreadsheet_tools import build_excel_tools


def _isolated(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "DB_FILE", data_dir / "actis.db")
    monkeypatch.setenv("ACTIS_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("ACTIS_SPREADSHEET_ROOT", str(tmp_path))


def test_excel_standard_pack_resolves_inherited_tool_and_scopes():
    assert tools_for_skills(["excel.standard-pack"]) == ["excel_toolkit"]
    assert set(scopes_for_skills(["excel.standard-pack"])) == {
        "spreadsheet.read", "spreadsheet.write"
    }


def test_create_edit_inspect_validate_and_register_artifact(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    tools = {tool.__name__: tool for tool in build_excel_tools(
        {"spreadsheet.read", "spreadsheet.write"}, run_id="run-1", agent_id="finance"
    )}
    created = json.loads(tools["excel_create"](
        "financeiro.xlsx",
        json.dumps({
            "sheets": [{
                "name": "Vendas",
                "rows": [["Produto", "Qtd", "Preço", "Total"], ["Perfil", 3, 10, "=B2*C2"]],
                "freeze_panes": "A2",
                "auto_filter": "A1:D2",
                "column_widths": {"A": 24, "D": 16},
                "styles": [{"range": "A1:D1", "style": {"bold": True, "fill": "D9EAD3"}}],
            }]
        }),
    ))
    target = Path(created["path"])
    assert target.is_file()
    assert created["artifact_id"]

    edited = json.loads(tools["excel_edit"](
        str(target),
        json.dumps([
            {"op": "set", "sheet": "Vendas", "cell": "B2", "value": 5},
            {"op": "set", "sheet": "Vendas", "cell": "D2", "value": "=B2*C2"},
            {"op": "add_table", "sheet": "Vendas", "range": "A1:D2", "name": "VendasTable"},
        ]),
    ))
    assert edited["artifact_id"] == created["artifact_id"]

    inspected = json.loads(tools["excel_inspect"](str(target), "Vendas", "A1:D2"))
    assert inspected["values"][1][1] == 5
    assert inspected["values"][1][3] == "=B2*C2"

    validation = json.loads(tools["excel_validate"](str(target)))
    assert validation["formula_count"] == 1
    assert artifacts.list_run_attachments("run-1")[0]["name"] == "financeiro.xlsx"
