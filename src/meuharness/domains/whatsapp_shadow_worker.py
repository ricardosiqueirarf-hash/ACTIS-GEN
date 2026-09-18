"""Out-of-band WhatsApp shadow benchmark worker. Never participates in production delivery."""
from __future__ import annotations

import argparse
import json
import logging
import time

from meuharness.domains.whatsapp_channel_gateway import process_shadow_once

LOGGER = logging.getLogger("meuharness.whatsapp_shadow_worker")


def run(agent_id: str, interval: float = 0.5) -> None:
    while True:
        try:
            result = process_shadow_once(agent_id)
            if result.get("processed"):
                LOGGER.info("WhatsApp shadow out-of-band [%s]: %s", agent_id, json.dumps(result, ensure_ascii=False))
                if not result.get("ok"):
                    time.sleep(max(1.0, interval))
            else:
                time.sleep(interval)
        except Exception as exc:  # noqa: BLE001 - benchmark worker must never affect production
            LOGGER.exception("WhatsApp shadow worker failure [%s]: %s", agent_id, exc)
            time.sleep(max(1.0, interval))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(args.agent_id, max(0.1, args.interval))


if __name__ == "__main__":
    main()
