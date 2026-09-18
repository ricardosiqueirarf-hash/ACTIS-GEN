"""Deterministic PDF runtime tools for ACTIS GEN agents."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Annotated, Any

from meuharness.artifacts import register_artifact

HOME = Path.home().resolve()
PDF_MIME = "application/pdf"


def _safe_path(value: str, *, must_exist: bool = False) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        path = HOME / path
    path = path.resolve()
    if not path.is_relative_to(HOME):
        raise ValueError("PDFs só podem ser lidos/escritos dentro do diretório do usuário.")
    if must_exist and not path.is_file():
        raise ValueError(f"Arquivo não encontrado: {path}")
    return path


def _require_pdf(path: Path) -> None:
    if path.suffix.lower() != ".pdf":
        raise ValueError("O caminho precisa terminar em .pdf")


def _pdf_libs():
    try:
        import pymupdf as fitz
        from reportlab.lib.pagesizes import A4, LETTER
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:
        raise RuntimeError("Runtime PDF indisponível. Instale as dependências do projeto ACTIS GEN.") from exc
    return fitz, A4, LETTER, getSampleStyleSheet, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer


def _inline_markup(text: str) -> str:
    value = html.escape(text.strip())
    value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"(?<!\*)\*(.+?)\*(?!\*)", r"<i>\1</i>", value)
    value = re.sub(r"`(.+?)`", r"<font name='Courier'>\1</font>", value)
    return value


def _markdown_story(markdown: str, styles: Any, Paragraph: Any, Spacer: Any,
                    ListFlowable: Any, ListItem: Any) -> list[Any]:
    story: list[Any] = []
    bullets: list[Any] = []

    def flush_bullets() -> None:
        nonlocal bullets
        if bullets:
            story.append(ListFlowable(bullets, bulletType="bullet", leftIndent=18))
            story.append(Spacer(1, 7))
            bullets = []

    for raw in str(markdown or "").splitlines():
        line = raw.strip()
        if not line:
            flush_bullets()
            story.append(Spacer(1, 6))
            continue
        if line.startswith("### "):
            flush_bullets(); story.append(Paragraph(_inline_markup(line[4:]), styles["Heading3"])); continue
        if line.startswith("## "):
            flush_bullets(); story.append(Paragraph(_inline_markup(line[3:]), styles["Heading2"])); continue
        if line.startswith("# "):
            flush_bullets(); story.append(Paragraph(_inline_markup(line[2:]), styles["Heading1"])); continue
        if re.match(r"^[-*]\s+", line):
            item = re.sub(r"^[-*]\s+", "", line)
            bullets.append(ListItem(Paragraph(_inline_markup(item), styles["BodyText"])))
            continue
        flush_bullets()
        story.append(Paragraph(_inline_markup(line), styles["BodyText"]))
        story.append(Spacer(1, 4))
    flush_bullets()
    return story


def _json_list(raw: str, label: str) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} precisa ser JSON válido.") from exc
    if not isinstance(value, list):
        raise TypeError(f"{label} precisa ser uma lista JSON.")
    return value


def build_pdf_tools(
    scopes: set[str] | None = None, *, run_id: str | None = None, agent_id: str | None = None
) -> list[object]:
    """Build native PDF tools constrained by pdf.read / pdf.write scopes."""
    allowed = set(scopes or {"pdf.read", "pdf.write"})
    tools: list[object] = []

    if "pdf.read" in allowed:
        def pdf_inspect(
            path: Annotated[str, "PDF dentro do diretório do usuário"],
            max_pages: Annotated[int, "Máximo de páginas com preview textual"] = 12,
        ) -> str:
            """Inspecione metadata, páginas, dimensões e preview textual de um PDF."""
            fitz, *_ = _pdf_libs()
            source = _safe_path(path, must_exist=True); _require_pdf(source)
            with fitz.open(source) as doc:
                pages = []
                for index, page in enumerate(doc):
                    if index >= max(1, min(int(max_pages), 50)):
                        break
                    text = " ".join(page.get_text("text").split())[:1200]
                    pages.append({"page": index + 1, "width": page.rect.width, "height": page.rect.height, "text": text})
                return json.dumps({"path": str(source), "pages": len(doc), "metadata": doc.metadata, "preview": pages}, ensure_ascii=False)
        tools.append(pdf_inspect)

    if "pdf.write" in allowed:
        def pdf_create(
            output_path: Annotated[str, "Novo caminho .pdf dentro do diretório do usuário"],
            markdown: Annotated[str, "Conteúdo em Markdown simples"],
            title: Annotated[str, "Título nos metadados do PDF"] = "",
            page_size: Annotated[str, "A4 ou LETTER"] = "A4",
        ) -> str:
            """Crie um PDF novo a partir de Markdown simples com headings, listas e ênfase."""
            _, A4, LETTER, get_styles, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer = _pdf_libs()
            output = _safe_path(output_path); _require_pdf(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            styles = get_styles()
            styles["BodyText"].leading = 15
            size = LETTER if str(page_size).upper() == "LETTER" else A4
            doc = SimpleDocTemplate(str(output), pagesize=size, title=str(title or ""),
                                    rightMargin=42, leftMargin=42, topMargin=46, bottomMargin=46)
            story = _markdown_story(markdown, styles, Paragraph, Spacer, ListFlowable, ListItem)
            if not story:
                story = [Paragraph(" ", styles["BodyText"])]
            doc.build(story)
            artifact = register_artifact(output, run_id=run_id, agent_id=agent_id, mime_type=PDF_MIME) if run_id else None
            return json.dumps({
                "ok": True, "path": str(output), "bytes": output.stat().st_size,
                "artifact_id": artifact.get("id") if artifact else None,
            }, ensure_ascii=False)
        tools.append(pdf_create)

    if {"pdf.read", "pdf.write"} <= allowed:
        def pdf_merge(
            output_path: Annotated[str, "Novo PDF de saída"],
            input_paths_json: Annotated[str, "Lista JSON de PDFs na ordem desejada"],
        ) -> str:
            """Una vários PDFs em um novo arquivo, preservando a ordem informada."""
            fitz, *_ = _pdf_libs()
            output = _safe_path(output_path); _require_pdf(output)
            inputs = [_safe_path(str(x), must_exist=True) for x in _json_list(input_paths_json, "input_paths_json")]
            if not inputs:
                raise ValueError("Informe pelo menos um PDF de entrada.")
            merged = fitz.open()
            try:
                for source in inputs:
                    _require_pdf(source)
                    with fitz.open(source) as current:
                        merged.insert_pdf(current)
                output.parent.mkdir(parents=True, exist_ok=True)
                merged.save(output)
            finally:
                merged.close()
            artifact = register_artifact(output, run_id=run_id, agent_id=agent_id, mime_type=PDF_MIME) if run_id else None
            return json.dumps({
                "ok": True, "path": str(output), "inputs": [str(x) for x in inputs],
                "artifact_id": artifact.get("id") if artifact else None,
            }, ensure_ascii=False)
        tools.append(pdf_merge)

        def pdf_edit(
            source_path: Annotated[str, "PDF existente"],
            output_path: Annotated[str, "Novo PDF de saída; nunca sobrescreva o original"],
            operations_json: Annotated[str, "Lista JSON de operações de edição"],
        ) -> str:
            """Edite PDF com replace_text, insert_text, delete_pages, rotate_pages ou metadata."""
            fitz, *_ = _pdf_libs()
            source = _safe_path(source_path, must_exist=True); _require_pdf(source)
            output = _safe_path(output_path); _require_pdf(output)
            if output == source:
                raise ValueError("Nunca sobrescreva o PDF original; use outro output_path.")
            operations = _json_list(operations_json, "operations_json")
            with fitz.open(source) as doc:
                for op in operations:
                    if not isinstance(op, dict):
                        raise TypeError("Cada operação precisa ser um objeto JSON.")
                    kind = str(op.get("type") or "").strip().lower()
                    if kind == "replace_text":
                        pages = [int(op["page"]) - 1] if op.get("page") else list(range(len(doc)))
                        needle, replacement = str(op.get("find") or ""), str(op.get("replace") or "")
                        if not needle:
                            raise ValueError("replace_text exige find.")
                        for page_index in pages:
                            page = doc[page_index]
                            rects = page.search_for(needle)
                            for rect in rects:
                                page.add_redact_annot(rect, fill=(1, 1, 1))
                            if rects:
                                page.apply_redactions()
                                for rect in rects:
                                    page.insert_textbox(rect, replacement, fontsize=float(op.get("font_size") or 10), fontname="helv")
                    elif kind == "insert_text":
                        page = doc[int(op.get("page") or 1) - 1]
                        point = fitz.Point(float(op.get("x") or 36), float(op.get("y") or 36))
                        page.insert_text(point, str(op.get("text") or ""), fontsize=float(op.get("font_size") or 11), fontname="helv")
                    elif kind == "delete_pages":
                        pages = sorted({int(x) - 1 for x in op.get("pages", [])}, reverse=True)
                        for page_index in pages:
                            doc.delete_page(page_index)
                    elif kind == "rotate_pages":
                        for page_number in op.get("pages", []):
                            page = doc[int(page_number) - 1]
                            page.set_rotation((page.rotation + int(op.get("degrees") or 90)) % 360)
                    elif kind == "metadata":
                        meta = dict(doc.metadata or {})
                        meta.update({str(k): str(v) for k, v in dict(op.get("values") or {}).items()})
                        doc.set_metadata(meta)
                    else:
                        raise ValueError(f"Operação PDF desconhecida: {kind}")
                output.parent.mkdir(parents=True, exist_ok=True)
                doc.save(output, garbage=4, deflate=True)
            artifact = register_artifact(output, run_id=run_id, agent_id=agent_id, mime_type=PDF_MIME) if run_id else None
            return json.dumps({
                "ok": True, "source": str(source), "path": str(output), "operations": len(operations),
                "artifact_id": artifact.get("id") if artifact else None,
            }, ensure_ascii=False)
        tools.append(pdf_edit)

        def pdf_render(
            source_path: Annotated[str, "PDF existente"],
            output_dir: Annotated[str, "Diretório para PNGs renderizados"],
            pages_csv: Annotated[str, "Páginas 1-based separadas por vírgula; vazio = todas"] = "",
            dpi: Annotated[int, "DPI de renderização"] = 144,
        ) -> str:
            """Renderize páginas do PDF em PNG para verificação visual antes de declarar conclusão."""
            fitz, *_ = _pdf_libs()
            source = _safe_path(source_path, must_exist=True); _require_pdf(source)
            out_dir = _safe_path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            requested = [int(x.strip()) - 1 for x in pages_csv.split(",") if x.strip()]
            files: list[str] = []
            with fitz.open(source) as doc:
                indexes = requested or list(range(len(doc)))
                for index in indexes:
                    if index < 0 or index >= len(doc):
                        raise ValueError(f"Página fora do intervalo: {index + 1}")
                    target = out_dir / f"page-{index + 1:03d}.png"
                    doc[index].get_pixmap(dpi=max(72, min(int(dpi), 300)), alpha=False).save(target)
                    files.append(str(target))
            return json.dumps({"ok": True, "source": str(source), "files": files}, ensure_ascii=False)
        tools.append(pdf_render)

    return tools
