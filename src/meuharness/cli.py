"""Command-line interface for interacting with meuharness."""

from __future__ import annotations

import argparse
import asyncio
import sys

from meuharness import HarnessError, HarnessRequest
from meuharness.bootstrap import create_harness
from meuharness.providers.nine_router import NineRouterSettings


async def run_chat(args: argparse.Namespace) -> int:
    try:
        settings = NineRouterSettings.from_env(
            args.env_file,
            model=args.model,
            timeout_s=args.timeout,
        )
        harness = create_harness(settings)
    except HarnessError as exc:
        print(f"Erro de configuração: {exc}")
        return 1

    history: list[dict[str, str]] = []
    print(f"ACTIS GEN | modelo: {settings.model}")
    print("Comandos: /model <id>, /models, /clear, /exit.\n")
    while True:
        try:
            prompt = input("você> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not prompt:
            continue
        if prompt == "/exit":
            return 0
        if prompt == "/clear":
            history.clear()
            print("Histórico limpo.\n")
            continue
        if prompt == "/models":
            from meuharness.providers.nine_router import NineRouterGateway
            models = await NineRouterGateway(settings).list_models()
            for model_id in models:
                print(model_id)
            print()
            continue
        if prompt.startswith("/model "):
            new_model = prompt.split(maxsplit=1)[1].strip()
            settings = NineRouterSettings.from_env(args.env_file, model=new_model, timeout_s=args.timeout)
            harness = create_harness(settings)
            print(f"Modelo alterado para: {settings.model}\n")
            continue

        context = {"conversation": history[-12:]} if history else {}
        try:
            result = await harness.run(
                HarnessRequest(
                    prompt=prompt,
                    context=context,
                    instructions=(
                        "Converse naturalmente em português. Use o histórico fornecido "
                        "apenas como contexto da conversa atual."
                    ),
                    timeout_s=args.timeout or settings.timeout_s,
                    max_output_tokens=args.max_output_tokens,
                )
            )
        except HarnessError as exc:
            print(f"modelo> [erro: {exc.code}] {exc}\n")
            continue

        print(f"modelo> {result.text}\n")
        history.extend([
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": result.text},
        ])


def main() -> None:
    parser = argparse.ArgumentParser(prog="meuharness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    chat = subparsers.add_parser("chat", help="Converse com o modelo via meuharness")
    chat.add_argument("--env-file", help="Arquivo .env explícito")
    chat.add_argument("--model", help="ID de modelo explícito")
    chat.add_argument("--timeout", type=float, help="Timeout por resposta")
    chat.add_argument("--max-output-tokens", type=int, default=600)

    args = parser.parse_args()
    try:
        if args.command == "chat":
            sys.exit(asyncio.run(run_chat(args)))
    except KeyboardInterrupt:
        print()
        sys.exit(130)


if __name__ == "__main__":
    main()
