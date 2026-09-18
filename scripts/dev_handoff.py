#!/usr/bin/env python3
"""Create and maintain recoverable ACTIS development handoffs."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "docs" / "dev"
ACTIVE = DEV / "active"
COMPLETED = DEV / "completed"
STATE = ROOT / ".ACTIS_DEV_STATE.json"
STATUS = DEV / "STATUS.md"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip()


def task_path(task_id: str, base: Path = ACTIVE) -> Path:
    return base / f"{task_id}.md"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def metadata(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in read_text(path).splitlines()[:14]:
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        data[key.strip()] = value.strip()
    return data


def get_section(path: Path, name: str) -> str:
    lines = read_text(path).splitlines()
    header = f"## {name}"
    if header not in lines:
        return "não informado"
    start = lines.index(header) + 1
    values: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.strip():
            values.append(line.strip())
    return " ".join(values) or "não informado"


def replace_header(path: Path, key: str, value: str) -> None:
    prefix = f"{key}: "
    lines = read_text(path).splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{prefix}{value}"
            replaced = True
            break
    if not replaced:
        raise ValueError(f"Cabeçalho ausente em {path}: {key}")
    write_text(path, "\n".join(lines))


def replace_section(path: Path, name: str, body: str) -> None:
    lines = read_text(path).splitlines()
    header = f"## {name}"
    if header not in lines:
        raise ValueError(f"Seção ausente em {path}: {name}")
    start = lines.index(header) + 1
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    replacement = [header, "", body.strip(), ""]
    write_text(path, "\n".join(lines[: start - 1] + replacement + lines[end:]))


def append_checkpoint(path: Path, message: str) -> None:
    old = get_section(path, "Checkpoints de continuidade")
    entry = f"- {now()} — {message}"
    body = entry if old == "não informado" else f"{old}\n{entry}"
    replace_section(path, "Checkpoints de continuidade", body)


def create_task(args: argparse.Namespace) -> None:
    path = task_path(args.id)
    if path.exists():
        raise SystemExit(f"Tarefa já existe: {path}")
    dirty = "sim" if git("status", "--porcelain") else "não"
    content = f"""# Tarefa: {args.title}

ID: {args.id}
Status: IN_PROGRESS
Agente: {args.agent}
Iniciado em: {now()}
Última atualização: {now()}

## Objetivo

{args.objective}

## Escopo

- {args.scope}

## Fora do escopo

- definir explicitamente conforme necessário

## Estado inicial do repositório

- Branch: `{git('branch', '--show-current')}`
- HEAD inicial: `{git('rev-parse', 'HEAD')}`
- Working tree já estava sujo: `{dirty}`
- Alterações preexistentes relevantes: conferir `git status`; não assumir propriedade.

## PRONTO

- [ ] nenhum item concluído ainda
"""
    content += """
## EM PROGRESSO

- [ ] primeira etapa da implementação

## FALTA

- [ ] listar trabalho restante

## Arquivos tocados

### Modificados
- nenhum ainda

### Criados
- nenhum ainda

### Removidos
- nenhum

### Parciais / não finalizados
- nenhum ainda

## Decisões técnicas

- nenhuma ainda

## Testes executados

- [ ] nenhum ainda
"""
    content += """
## Estado do runtime

- Seguro continuar com runtime atual: `sim|não`
- Serviços reiniciados: nenhum
- Observações: preencher quando relevante

## Riscos / bloqueios

- nenhum conhecido

## Rollback / recovery

- definir antes de mudança de alto risco

## Próximo passo exato

Registrar o primeiro arquivo/função/comando a alterar e o resultado esperado.

## Commits

- ainda não commitado

## Checkpoints de continuidade

- tarefa criada
"""
    write_text(path, content)
    sync_indexes()
    print(path)


def task_summary(path: Path) -> dict[str, str]:
    info = metadata(path)
    first = read_text(path).splitlines()[0]
    return {
        "id": info.get("ID", path.stem),
        "title": first.removeprefix("# Tarefa: "),
        "status": info.get("Status", "UNKNOWN"),
        "agent": info.get("Agente", "unknown"),
        "updated": info.get("Última atualização", "unknown"),
        "next": get_section(path, "Próximo passo exato"),
    }


def sync_indexes() -> None:
    tasks = [task_summary(path) for path in sorted(ACTIVE.glob("DEV-*.md"))]
    state = {
        "updated_at": now(),
        "active_tasks": tasks,
        "source": "docs/dev/active",
    }
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# ACTIS GEN — Development Status",
        "",
        f"Atualizado: {state['updated_at']}",
        "",
        "Gerado de `docs/dev/active/`. O working tree local continua sendo a fonte de verdade.",
        "",
        "## Tarefas ativas",
        "",
        "| ID | Tarefa | Estado | Agente | Próximo passo |",
        "|---|---|---|---|---|",
    ]
    if not tasks:
        lines.append("| — | Nenhuma tarefa ativa registrada | — | — | — |")
    for item in tasks:
        next_step = item["next"].replace("\n", " ").replace("|", "\\|")
        lines.append(
            f"| {item['id']} | {item['title']} | {item['status']} | {item['agent']} | {next_step} |"
        )
    lines += [
        "",
        "## Como retomar",
        "",
        "1. Abra a tarefa correspondente em `docs/dev/active/`.",
        "2. Reconcilie o handoff com `git diff` e o runtime atual.",
        "3. Continue pelo campo `Próximo passo exato`.",
        "",
    ]
    write_text(STATUS, "\n".join(lines))


def checkpoint(args: argparse.Namespace) -> None:
    path = task_path(args.id)
    if not path.exists():
        raise SystemExit(f"Tarefa ativa não encontrada: {args.id}")
    replace_header(path, "Última atualização", now())
    if args.status:
        replace_header(path, "Status", args.status)
    if args.next:
        replace_section(path, "Próximo passo exato", args.next)
    append_checkpoint(path, args.message)
    sync_indexes()
    print(path)


def has_open_work(path: Path) -> bool:
    for name in ("EM PROGRESSO", "FALTA"):
        if "- [ ]" in get_section(path, name):
            return True
    return False


def finish(args: argparse.Namespace) -> None:
    path = task_path(args.id)
    if not path.exists():
        raise SystemExit(f"Tarefa ativa não encontrada: {args.id}")
    if has_open_work(path) and not args.force:
        raise SystemExit(
            "Ainda existem itens abertos em EM PROGRESSO/FALTA. "
            "Conclua-os ou use --force com justificativa registrada."
        )
    replace_header(path, "Status", "DONE")
    replace_header(path, "Última atualização", now())
    replace_section(path, "Próximo passo exato", "nenhum — tarefa concluída")
    append_checkpoint(path, args.message or "tarefa concluída")
    destination = task_path(args.id, COMPLETED)
    path.replace(destination)
    sync_indexes()
    print(destination)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="criar nova tarefa recuperável")
    create.add_argument("--id", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--agent", required=True)
    create.add_argument("--objective", required=True)
    create.add_argument("--scope", required=True)
    create.set_defaults(func=create_task)

    sync = sub.add_parser("sync", help="regenerar índices de continuidade")
    sync.set_defaults(func=lambda _args: sync_indexes())

    cp = sub.add_parser("checkpoint", help="registrar checkpoint de continuidade")
    cp.add_argument("id")
    cp.add_argument("message")
    cp.add_argument("--next")
    cp.add_argument("--status", choices=["IN_PROGRESS", "BLOCKED"])
    cp.set_defaults(func=checkpoint)

    done = sub.add_parser("finish", help="finalizar e arquivar tarefa")
    done.add_argument("id")
    done.add_argument("--message")
    done.add_argument("--force", action="store_true")
    done.set_defaults(func=finish)

    return parser


def main() -> None:
    ACTIVE.mkdir(parents=True, exist_ok=True)
    COMPLETED.mkdir(parents=True, exist_ok=True)
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
