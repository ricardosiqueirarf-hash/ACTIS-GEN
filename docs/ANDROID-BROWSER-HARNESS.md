# Browser Harness do ChatGPT no Android

Este fluxo roda integralmente no Android/Termux. O PC nao participa.

## Arquitetura

`ChatGPT Android -> Desktop Commander Android -> chatgpt-browser -> Browser Harness -> Chromium headless Android`

O Chromium usa perfil persistente em `~/.local/share/actis-gen/browser-profiles/chatgpt-android`, portanto cookies e logins sobrevivem a reinicios do browser.

## Instalar

```bash
cd ~/ACTIS-GEN
./scripts/bootstrap_chatgpt_browser_android.sh
```

## Usar

```bash
chatgpt-browser <<'PY'
ensure_real_tab()
goto_url('https://example.com')
wait_for_load()
print(page_info())
PY
```

## Teste

```bash
./scripts/smoke_chatgpt_browser_android.sh
```

No Android, o gerenciador adiciona `--no-sandbox`, define um `TMPDIR` gravavel e remove `HeadlessChrome` do User-Agent para compatibilidade com sites como WhatsApp Web.

Gravacao local do Browser Harness fica desativada por padrao. O fluxo do WhatsApp deve permanecer somente leitura ate uma acao de envio ser explicitamente autorizada.

## Fast path do WhatsApp

Para reduzir latencia, `chatgpt-wa` nao sobe o Browser Harness completo a cada acao. Um servidor local persistente fala diretamente com o CDP do Chromium em `127.0.0.1:8767` e reutiliza a mesma sessao.

```bash
chatgpt-wa status
chatgpt-wa open "Nome do chat"
chatgpt-wa draft "Nome do chat" "texto para revisar"
chatgpt-wa clear
```

Envio exige confirmacao explicita no proprio comando:

```bash
chatgpt-wa send "Nome do chat" "mensagem" --confirm-send
```

Sem `--confirm-send`, o envio e bloqueado. O servidor fica restrito ao loopback do Android e reinicia automaticamente quando necessario.

### Latencia observada no Galaxy A55

- status com servidor quente: ~3 ms dentro do servidor;
- reabrir a conversa atual: ~3 ms;
- preparar rascunho na conversa atual: ~140 ms;
- limpar rascunho: ~110 ms;
- trocar para conversa visivel: ~0,4-0,7 s;
- conversa que exige busca/renderizacao do WhatsApp: ~1 s.

O objetivo do fast path e transformar operacoes simples em uma unica chamada deterministica, evitando varias rodadas de inspecao DOM/screenshot antes de cada acao.
