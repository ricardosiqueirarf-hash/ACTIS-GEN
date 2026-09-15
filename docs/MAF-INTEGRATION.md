# ACTIS GEN — Microsoft Agent Framework e MCP

## Dependências

Pacotes declarados atualmente:

- `agent-framework-core==1.18.0`
- `agent-framework-openai==1.14.3`
- `openai==3.13.0`

O subdiretório `upstream/agent-framework` é referência de código e não deve ser tratado como a fonte do runtime instalado.

## Composição

`bootstrap.py` compõe:

```text
Harness
  ↓
MAFRuntime
  ↓
NineRouterGateway
```

O `ExecutionService` fica acima dessa composição e injeta instruções, contexto, memória, tools e scopes específicos do ACTIS GEN.

## MCPs atuais

### Harness Browser

Servidor `browser-harness-mcp`, conectado a um Chrome headless gerenciado pelo ACTIS para cada agente. Cada agente possui perfil persistente próprio em `~/.local/share/actis-gen/browser-profiles/<agent-id>/` e porta CDP local dinâmica.

Scopes:

- `browser.read`: page info, screenshot, tabs, wait, HTTP GET etc.;
- `browser.interact`: inclui navegação, click, type, fill, scroll, JS/CDP, upload e recording.

A UI do chat expõe um painel **Browser ao vivo** para agentes com Harness Browser. O painel usa o mesmo CDP do agente para mostrar screenshot, URL/título e permitir intervenção humana por clique, digitação, Enter, reload e navegação manual, sem usar o mouse/teclado do desktop humano.

### Harness Files

Servidor MCP Filesystem oficial, limitado ao diretório home do usuário.

Scopes:

- `files.read`: leitura, listagem, busca e metadata;
- `files.write`: write/edit/create/move.

### Harness Terminal

MCP Shell Server em modo permissivo quando a capability é concedida. O scope é `terminal.exec`. O diretório permitido é o home do usuário.

### Harness Computer

Zavora Computer Use MCP no desktop Linux/Wayland real.

- `computer.view`: leitura de estado, janelas, displays, clipboard e metadata;
- `computer.control`: adiciona mouse, teclado, scroll, abertura/ativação de apps/janelas e demais ações.

`computer_observe` usa screenshot do MCP + chamada multimodal pelo 9Router para devolver uma descrição textual e coordenadas físicas.

## Lifecycle de MCP

MAF recebe os MCPs permitidos para cada Run. A filtragem ocorre antes de o agente ver as operações. Isso é mais forte do que apenas pedir ao modelo para “não usar” uma função.

## Timeouts

- execução comum: settings do 9Router / request;
- Computer Use: deadline mínimo de 300 s no kernel;
- MCP Files: request timeout 30 s;
- MCP Terminal: 60 s;
- MCP Computer: 60 s por tool call;
- observer multimodal: 60 s.

## Regra de dependência

Código de negócio não deve importar MAF diretamente. Integrações framework-specific permanecem em `adapters/maf/`.
