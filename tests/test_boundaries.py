import ast
from pathlib import Path


def test_core_and_public_api_have_no_provider_or_framework_imports():
    root = Path(__file__).resolve().parents[1] / "src" / "meuharness"
    for path in [*root.joinpath("core").glob("*.py"), root / "__init__.py"]:
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            assert not any(
                part in name.split(".")
                for name in names
                for part in (
                    "agent_framework",
                    "agent_framework_openai",
                    "openai",
                    "providers",
                    "domains",
                )
            ), path


def test_maf_imports_stay_in_adapter():
    root = Path(__file__).resolve().parents[1] / "src" / "meuharness"
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "agent_framework"
            ):
                assert "adapters/maf" in path.as_posix(), path
