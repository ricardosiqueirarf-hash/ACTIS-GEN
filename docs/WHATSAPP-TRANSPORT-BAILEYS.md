# WhatsApp Transport — Baileys primary + Web fallback

## Estado

Implementado em 2026-09-18 para o agente `whatsapp-colorglass`.

```text
WhatsApp
  ↓
Baileys sidecar (primário)
  ↓
ACTIS WhatsApp gateway
  ↓
roteamento determinístico
  ↓
agente enxuto
  ├─ mensagem simples → send + handoff
  └─ operação → Company/ColorGlass tools
  ↓
ACTIS outbox / idempotência / ACK
  ↓
Baileys
       ↘ se indisponível ANTES de tentar envio
         WhatsApp Web fallback
```

## Princípios

- ACTIS continua sendo fonte de verdade para contatos, mensagens, jobs, outbox, runs e estado empresarial.
- Baileys é apenas transporte: socket, sessão, eventos e envio.
- O sidecar usa `baileys@7.0.0-rc14`, a mesma versão usada pelo pacote WhatsApp do OpenClaw observado na implantação.
- O browser antigo não foi removido. Ele só é usado quando Baileys está indisponível.
- Um erro Baileys após `sendMessage()` é ambíguo: outbox vira `uncertain`; NÃO existe fallback Web.
- Fallback Web só ocorre quando `attempted=false && safe_fallback=true`.
- O ID retornado pelo Baileys é persistido como `transport_ref=baileys:<message_id>`.
- A sessão Baileys fica em `~/.local/share/actis-gen/whatsapp-baileys/auth`.
- Token da API localhost fica em arquivo 0600 e nunca entra no repositório.

## Progressive tools

O atendimento automático escolhe tools deterministicamente antes do LLM.

- `minimal`: apenas `whatsapp_send_message` e `whatsapp_handoff`.
- `operational`: tools de conversa + Company Runtime + ColorGlass Operations.
- orçamento novo/alteração: continua no fluxo determinístico de orçamento antes do LLM.
- shadow benchmark: continua com `disable_tools=True`.

Isso evita carregar ERP/Company tools em cumprimentos e perguntas simples.

## Serviços

- `actis-gen-whatsapp-baileys.service`: transporte primário Node/Baileys.
- `actis-gen-whatsapp-runtime.service`: gateway ACTIS, ingestão, roteamento, agente e outbox.
- browser profile `whatsapp-colorglass`: mantido como fallback.

## Recuperação

1. Se Baileys cair, o gateway detecta health indisponível e usa Web.
2. O sidecar reinicia automaticamente por systemd.
3. Quando volta a `status=open`, o próximo ciclo usa Baileys novamente.
4. Se a sessão for `logged_out`, é necessário novo QR.
5. Nunca force retry de item `uncertain`; confirme o estado real primeiro.

## Evidência de validação

- Baileys pareado e reaberto após restart sem novo QR.
- envio real para o contato operador: ~1,0 s até provider acceptance.
- outbox final: `sent`, 1 tentativa, `transport_ref=baileys:...`.
- sidecar desligado manualmente: `open_session` mudou para `web_fallback`.
- sidecar religado: runtime voltou automaticamente para `transport=baileys`.
- 75 testes focados passaram após a implementação.
- perfil simples expõe 2 tools; perfil operacional expõe 17 no estado atual.
