# ACTIS ChatGPT Browser MCP — POC

Experimento isolado para provar que um cliente MCP externo consegue controlar o Browser Harness do ACTIS sem registrar nada no produto.

## Isolamento

- Vive somente em `experiments/chatgpt_browser_mcp/`.
- Não altera `src/`, rotas, agentes, tools ou configuração de produção.
- Usa o Browser Harness já instalado no computador.
- Reaproveita apenas `meuharness.browser_manager.ensure_browser()` para criar um Chrome dedicado.
- Profile exclusivo: `~/.local/share/actis-gen/browser-profiles/chatgpt-poc`.
- Runtime MCP exclusivo dentro desse profile.
- Auditoria: `~/.local/share/actis-chatgpt-browser-poc/audit.jsonl`.

## Resultado da prova

O smoke test MCP real validou:

1. handshake MCP por stdio;
2. descoberta de 17 ferramentas;
3. inicialização do profile `chatgpt-poc`;
4. navegação para uma página local;
5. snapshot semântico com refs `@eN`;
6. digitação usando uma ref;
7. clique usando uma ref;
8. leitura do estado resultante;
9. screenshot final.

Resultado observado: `recebido:teste-chatgpt`.
## Ferramentas expostas

Inclui status, navegação, page info, screenshot, snapshot semântico, clique/digitação por ref, clique por coordenada, fill, teclado, scroll, wait e controle básico de abas.

O servidor **não expõe** shell, filesystem, `browser_js`, CDP bruto nem upload de arquivos nesta POC.

## Executar servidor

```bash
./experiments/chatgpt_browser_mcp/run.sh
```

Um cliente MCP stdio pode usar `mcp-config.example.json` como referência.

## Smoke test

O teste usa `fixture.html`. Sirva a pasta e execute:

```bash
cd experiments/chatgpt_browser_mcp
python3 -m http.server 8877 --bind 127.0.0.1
/home/lowkanta/.local/share/uv/tools/browser-harness/bin/python smoke_client.py
```

## Próxima etapa

A camada local está comprovada. Para o ChatGPT consumir isto diretamente, ainda é necessária a ponte compatível com o produto ChatGPT (por exemplo MCP remoto/túnel suportado pelo plano/ambiente). Não é necessário alterar o ACTIS para testar essa próxima etapa.
