# Fase 1 — Fundação Empresarial ACTIS

## Decisões arquiteturais

- SQLite continua sendo a fonte canônica; o schema Foundation é idempotente e registrado em `storage.init_db()`.
- `organizations` é o tenant; `business_units`, `departments` e `enterprise_workspaces` carregam `organization_id`.
- `users` são identidades globais; `memberships` vinculam usuário, organização e role.
- `roles` são escopadas por organização; `permissions` são catálogo global e `role_permissions` guarda `scope` e condição JSON para evolução futura.
- A coleção legada `company_work_items` e os payloads legados permanecem compatíveis; a migração de dados será tratada no Bloco F.

## Alterações realizadas

### Bloco A — Foundation schema

- Criado `src/meuharness/enterprise.py` com schema e CRUD para Organization, BusinessUnit, Department, Workspace, User, Membership, Role, Permission e RolePermission.
- Registrado `FOUNDATION_SCHEMA` em `src/meuharness/storage.py`.
- Validações impedem relações entre organizações diferentes e mantêm códigos/slugs únicos por escopo.

## Migrations

- Nenhuma migration de dados ainda; apenas DDL idempotente do Bloco A.
- A criação da organização default e o backfill de coleções legadas ficam para o Bloco F.

## Testes

- Smoke SQLite temporário do Bloco A: `BLOCK_A_SMOKE_OK` (schema idempotente, hierarchy CRUD, memberships/roles/permissions e rejeição cross-organization).
- `python3 -m py_compile src/meuharness/enterprise.py src/meuharness/storage.py tests/test_enterprise_foundation.py`: aprovado.
- `pytest` e `ruff` não estão instalados no ambiente atual; execução pendente quando disponíveis.

## Pendências

- Integrar tenant context, autorização centralizada e prevenção de cross-tenant access.
- Implementar RBAC, audit, secrets, migração, Console/API, testes de isolamento e health check nos blocos seguintes.

## Próximo bloco

Bloco B — Tenant isolation: criar contexto de organização atual, autorização centralizada e filtros/validações de `organization_id` sem quebrar APIs legadas.
