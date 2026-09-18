# WhatsApp Channel Gateway — OpenClaw-inspired shadow path

## Objetivo

Comparar um segundo agente WhatsApp com o agente de produção da ColorGlass sem duplicar sessão, transporte ou envio externo.

## Arquitetura

```text
WhatsApp Web
  -> whatsapp-colorglass (transport owner)
  -> whatsapp_shadow.ingest_messages()
       -> production enqueue_inbound()
       -> whatsapp_channel_gateway.route_inbound_message()
            -> whatsapp-colorglass-shadow
               session_key = agent + account + peer
               disable_tools=True
               candidate response persisted
```

## Regras

- O agente de produção continua sendo o único dono do browser/sessão/outbox.
- O roteamento shadow acontece antes do LLM.
- O fan-out é idempotente por binding + source_message_id.
- O shadow nunca recebe whatsapp_contact, whatsapp_delivery_key ou tools.
- O shadow não pertence a empresa, não tem skills, tools, permissions ou memória global.
- O histórico do shadow é isolado por `agent_id + transport_agent_id + peer_kind + peer_id`.
- Respostas shadow são apenas candidatas e ficam em `whatsapp_channel_jobs.response`.
- Feature flag: `ACTIS_WHATSAPP_OPENCLAW_SHADOW_ENABLED`.
- Binding ativo: `whatsapp-colorglass -> whatsapp-colorglass-shadow`, direct, peer `*`.
- Desligar a flag ou desabilitar o binding interrompe o experimento sem afetar produção.

## Estado atual

- Agente shadow: `whatsapp-colorglass-shadow`
- Modelo: `gh/gpt-4.1`
- Transporte: compartilhado somente na entrada; outbound exclusivo da produção.
- Smoke real local: resposta candidata gerada em ~2 s, sem envio externo.
- Suíte focada: 62 testes passaram após hardening final.

## Caminho futuro

Para comparar modelos, mantenha o mesmo gateway/session policy e altere somente o modelo/instruções do shadow. Para mais de um candidato, adicione novos bindings shadow; nunca crie outra sessão WhatsApp para benchmark.
