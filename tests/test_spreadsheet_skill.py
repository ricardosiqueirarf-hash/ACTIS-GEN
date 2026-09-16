from __future__ import annotations

import json

from openpyxl import load_workbook

from meuharness.spreadsheet_tools import build_excel_tools
from meuharness.tool_registry import inheritable_tool_ids, skill_instructions_for_tools


def _tools(scopes: set[str]) -> dict[str, object]:
    return {tool.__name__: tool for tool in build_excel_tools(scopes)}


def test_excel_skill_create_edit_inspect_validate(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIS_SPREADSHEET_ROOT", str(tmp_path))
    tools = _tools({"spreadsheet.read", "spreadsheet.write"})

    created = json.loads(
        tools["excel_create_workbook"](
            "financeiro.xlsx",
            json.dumps(
                {
                    "sheets": [
                        {
                            "name": "Vendas",
                            "rows": [
                                ["Produto", "Qtd", "Preço", "Total"],
                                ["Perfil 1036", 3, 100, "=B2*C2"],
                            ],
                            "freeze_panes": "A2",
                            "auto_filter": "A1:D2",
                            "column_widths": {"A": 24, "B": 10, "C": 14, "D": 14},
                            "styles": [
                                {
                                    "range": "A1:D1",
                                    "style": {"bold": True, "fill": "D7FF64"},
                                }
                            ],
                        }
                    ]
                }
            ),
        )
    )
    assert created["ok"] is True
    assert (tmp_path / "financeiro.xlsx").exists()

    edited = json.loads(
        tools["excel_edit_workbook"](
            "financeiro.xlsx",
            json.dumps(
                [
                    {"op": "append_rows", "sheet": "Vendas", "rows": [["Perfil 070", 2, 50, "=B3*C3"]]},
                    {"op": "add_sheet", "name": "Resumo"},
                    {"op": "set", "sheet": "Resumo", "cell": "A1", "value": "Total geral"},
                    {"op": "set", "sheet": "Resumo", "cell": "B1", "value": "=SUM(Vendas!D2:D3)"},
                    {
                        "op": "style_range",
                        "sheet": "Resumo",
                        "range": "A1:B1",
                        "style": {"bold": True, "number_format": "R$ #,##0.00"},
                    },
                ]
            ),
        )
    )
    assert edited["ok"] is True

    inspected = json.loads(
        tools["excel_inspect_workbook"]("financeiro.xlsx", "Resumo", "A1:B1", 20, 10)
    )
    assert inspected["values"] == [["Total geral", "=SUM(Vendas!D2:D3)"]]

    validated = json.loads(tools["excel_validate_workbook"]("financeiro.xlsx"))
    assert validated["formula_count"] == 3

    wb = load_workbook(tmp_path / "financeiro.xlsx", data_only=False)
    assert wb["Vendas"]["D2"].value == "=B2*C2"
    assert wb["Vendas"]["D3"].value == "=B3*C3"
    assert wb["Resumo"]["B1"].value == "=SUM(Vendas!D2:D3)"


def test_excel_skill_is_the_only_inheritable_tool_in_mixed_selection():
    assert inheritable_tool_ids(["harness_files", "excel", "harness_browser"]) == ["skill_excel"]
    instructions = skill_instructions_for_tools(["skill_excel"])
    assert "Excel Spreadsheet Skill" in instructions
    assert "excel_validate_workbook" in instructions


def test_excel_scope_enforcement(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIS_SPREADSHEET_ROOT", str(tmp_path))
    read_only = _tools({"spreadsheet.read"})
    try:
        read_only["excel_create_workbook"]("blocked.xlsx", '{"sheets":[{"name":"A","rows":[]}]}')
    except PermissionError as exc:
        assert "spreadsheet.write" in str(exc)
    else:
        raise AssertionError("write operation should require spreadsheet.write")
