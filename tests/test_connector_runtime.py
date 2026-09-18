from __future__ import annotations

import json

from meuharness import connector_runtime


class _Response:
    status = 201

    def __init__(self, payload=b'{"id":123}'):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.payload[:size] if size >= 0 else self.payload


def _connector():
    return {
        "id": "erp",
        "name": "ERP",
        "kind": "http",
        "base_url": "https://erp.example/api",
        "auth_type": "bearer_env",
        "credential_ref": "ERP_TOKEN",
        "enabled": True,
        "agent_ids": ["worker"],
