# ACTIS GEN — Agent Instructions

## Fonte de verdade
O working tree local deste repositório é a fonte de verdade.
Antes de alterar qualquer feature, inspecione sua implementação atual e arquivos relacionados.
Documentação pode estar desatualizada; quando houver conflito, valide no código e runtime.

## Objetivo do projeto
ACTIS GEN é um control plane local para agentes de IA.
A ideia central é generalizar infraestrutura e especializar agentes por identidade, contexto, memória, tools, permissões, skills e organização.
MAF executa, 9Router resolve provider/modelo e harnesses/MCPs operam sistemas externos.

## Forma de trabalhar
1. Localize o código já existente antes de implementar algo novo.
2. Preserve arquitetura e padrões existentes quando ainda fizerem sentido.
3. Não recrie funcionalidades já presentes com outro caminho paralelo.
4. Faça mudanças pequenas e focadas no pedido atual.
5. Evite alterações destrutivas ou em arquivos não relacionados.
6. Teste o comportamento afetado antes de considerar a tarefa concluída.
7. Atualize documentação quando arquitetura, contratos ou comportamento persistente mudarem.

## Contrato do General
O General é a interface horizontal de administração e coordenação do ACTIS GEN. Uma feature relevante não está arquiteturalmente completa se existir apenas na UI/API e o General não conseguir descobri-la ou ajudar a operá-la.

Toda feature nova deve, no mesmo trabalho:
1. ser registrada no catálogo canônico `src/meuharness/feature_registry.py`;
2. declarar como o General a acessa: `direct`, `hybrid` ou por delegação;
3. expor as tools `actis_*`, capabilities, scopes e permissões administrativas necessárias quando houver operação direta;
4. aparecer no estado/descoberta disponível ao `actis.control-plane` sem depender de prompt estático;
5. devolver resultado verificável para que o General confirme execução em vez de apenas afirmar sucesso;
6. incluir teste de regressão garantindo que o General continue descobrindo e acessando a feature.

Para features de domínio, o General deve conhecer e administrar o domínio, mas não deve assumir automaticamente a identidade/capabilities do agente especializado. Quando a ação pertence ao agente alvo, o General configura/inspeciona o que for administrativo e delega a execução pelo kernel canônico.

## Precedência de contexto
1. Código e runtime local atual
2. `docs/CURRENT-STATE.md`
3. `docs/ACTIS-GEN-ESTADO-ATUAL.md`
4. Documentação específica da área
5. README

## UI / UX
Toda mudança visual deve consultar `docs/VISUAL-SYSTEM.md` antes de editar a interface.
A identidade é pixelada, gamificada, técnica, dark e austera.
Reutilize tokens, componentes e padrões existentes; não introduza dashboard SaaS genérico, radius arbitrário ou nova linguagem visual sem necessidade.

## Fronteiras importantes
- Conversation, Run, Agent, Automation e Workflow Run são entidades distintas.
- Empresa/setor/projeto organizam contexto; não são threads de conversa.
- `ExecutionService.execute_agent()` é o caminho canônico de execução.
- 9Router cuida de provider/modelo; ACTIS cuida de agente, contexto, capability e permissão.
- Browser, Files, Terminal e Computer são capabilities/harnesses, não locais para regra de negócio.

## Git e segurança do working tree
Este projeto pode conter mudanças locais ainda não commitadas.
Nunca descarte, reverta ou sobrescreva mudanças não relacionadas ao pedido atual.
Antes de operações amplas, confira `git status`.
Não trate o último commit como mais atual que o working tree.

## Leitura eficiente
Não leia o repositório inteiro por padrão.
Comece por este arquivo e `docs/CURRENT-STATE.md`; abra documentação e código adicionais apenas conforme a tarefa exigir.

## Comunicação entre agentes
Antes de trabalho que possa colidir com outro agente, leia `AGENT-COMMS.md`.
Registre ali tarefas em andamento, arquivos/áreas afetadas e qualquer intenção de reiniciar/parar serviços compartilhados.
O arquivo funciona como log append-only: não apague entradas de outros agentes.
Se houver conflito de escopo, preserve o trabalho existente e coordene antes de alterar.

## Continuidade obrigatória de desenvolvimento

Toda mudança relevante deve permanecer recuperável por outra IA durante toda a execução, inclusive se a sessão, Desktop Commander ou modelo parar sem aviso.

Antes da primeira alteração de código/config/schema/UI/runtime:
1. leia `docs/dev/STATUS.md` e tarefas relacionadas em `docs/dev/active/`;
2. confira `git status`, branch e HEAD;
3. crie ou assuma uma tarefa em `docs/dev/active/`;
4. registre objetivo, escopo, fora de escopo, estado inicial do Git e `Próximo passo exato`.

Durante o trabalho, mantenha obrigatoriamente separados:
- `PRONTO`: implementado e verificado no nível descrito;
- `EM PROGRESSO`: alteração iniciada, parcial ou ainda não validada;
- `FALTA`: trabalho ainda pendente dentro do escopo.

Atualize o handoff quando uma etapa começar/terminar, surgir bloqueio, mudar a estratégia, um arquivo ficar parcial, um teste rodar, houver mudança de runtime/schema/config, antes de commit/push/restart e antes de encerrar ou trocar de agente.

O `Próximo passo exato` deve indicar arquivo/função/comando e resultado esperado; frases vagas como "continuar implementação" não são suficientes.

Para mudanças de alto risco (banco, migrations, auth, kernel, General, WhatsApp, deploy ou serviços compartilhados), crie checkpoint Git apenas dos arquivos da tarefa quando isso for seguro. Nunca inclua mudanças alheias só para obter um checkpoint. Se não for seguro, documente recovery/rollback no handoff.

Se precisar parar com trabalho incompleto, registre explicitamente:
- arquivos parciais;
- se o código compila/inicia;
- se o runtime atual é seguro;
- último teste e resultado;
- rollback/recovery quando aplicável;
- próximo passo exato.

Ao assumir uma tarefa de outra IA, reconcilie primeiro o handoff com `git diff` e runtime. O working tree local continua sendo a fonte de verdade; se a documentação divergir, corrija o handoff antes de seguir.

Use `scripts/dev_handoff.py` para criar/checkpoint/finalizar tarefas e regenerar `docs/dev/STATUS.md` + `.ACTIS_DEV_STATE.json`. Consulte `docs/dev/README.md` para o protocolo completo.

Uma tarefa só pode ser marcada `DONE` quando não houver trabalho relevante pendente no escopo, os testes aplicáveis estiverem registrados e o próximo passo for explicitamente `nenhum — tarefa concluída`.
