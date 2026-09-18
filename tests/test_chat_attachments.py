import base64

import pymupdf

from meuharness import chat_attachments


def _pdf_data_url(text="PDF INPUT TESTE"):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    raw = doc.tobytes()
    doc.close()
    return "data:application/pdf;base64," + base64.b64encode(raw).decode()


def test_pdf_chat_input_is_persisted_and_extractable(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_attachments, "DATA_DIR", tmp_path)
    attachments = chat_attachments.normalize_chat_attachments(
        [{"name": "pedido.pdf", "media_type": "application/pdf", "data_url": _pdf_data_url()}],
        "conversation-1",
    )
    item = attachments[0]
    assert item["type"] == "input_file"
    assert item["media_type"] == "application/pdf"
    assert item["pages"] == 1
    assert tmp_path in __import__("pathlib").Path(item["local_path"]).parents
    context = chat_attachments.pdf_attachment_context(attachments)
    assert "PDF INPUT TESTE" in context
    assert "página 1" in context


def test_pdf_reference_survives_in_conversation_history(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_attachments, "DATA_DIR", tmp_path)
    item = chat_attachments.normalize_chat_attachments(
        [{"name": "manual.pdf", "media_type": "application/pdf", "data_url": _pdf_data_url("MANUAL ACTIS")}],
        "conversation-2",
    )[0]
    content = chat_attachments.history_content_with_attachment_refs(
        {"content": "Leia isto", "attachments": [item]}
    )
    assert "manual.pdf" in content
    assert item["local_path"] in content
    assert "MANUAL ACTIS" in content
