# ACTIS GEN — Development Continuity Protocol

Este protocolo é obrigatório para mudanças relevantes de código, configuração, schema, runtime ou UI.
Objetivo: qualquer agente deve conseguir continuar uma tarefa interrompida sem reconstruir o contexto da sessão anterior.

## Estrutura

- `STATUS.md`: índice curto das tarefas ativas/bloqueadas/concluídas recentemente.
- `active/<task-id>.md`: estado operacional detalhado de cada tarefa em andamento.
- `completed/`: handoffs finalizados.
- `abandoned/`: tarefas deliberadamente abandonadas ou substituídas.
- `.ACTIS_DEV_STATE.json`: estado local legível por automação; não é fonte histórica e não vai para Git.
- `AGENT-COMMS.md`: continua sendo o mural append-only para evitar colisões entre agentes.

## Regra central

Nenhuma alteração relevante pode ficar dependente da memória da sessão atual.
Enquanto houver código parcialmente alterado, o arquivo da tarefa deve refletir o estado real do working tree.

## Antes de editar código

1. Leia `AGENTS.md`, `docs/CURRENT-STATE.md`, `docs/dev/STATUS.md` e as tarefas relacionadas em `docs/dev/active/`.
2. Leia entradas recentes de `AGENT-COMMS.md` quando houver risco de colisão.
3. Confira branch, HEAD e `git status --short`.
4. Crie/assuma uma tarefa em `docs/dev/active/` antes da primeira mudança relevante.
5. Registre objetivo, escopo, fora de escopo, estado inicial Git e próximo passo exato.

## Durante a implementação

Mantenha sempre três listas explícitas:

- `PRONTO`: implementado e verificado no nível informado.
- `EM PROGRESSO`: começou, mas ainda não pode ser tratado como concluído.
- `FALTA`: trabalho ainda não iniciado ou não finalizado.

Atualize o handoff imediatamente quando:

- uma etapa relevante terminar ou começar;
- surgir bug, bloqueio ou mudança de estratégia;
- um arquivo entrar em estado parcial;
- um teste for executado;
- houver alteração de schema/config/runtime;
- antes de reiniciar serviço compartilhado;
- antes de commit/push;
- antes de encerrar ou trocar de agente.

O campo `Próximo passo exato` deve permitir continuação sem adivinhação: informe arquivo/função/comando e o resultado esperado.

## Desenvolvimento quase direto em produção

Antes de mudança de alto risco em banco, auth, kernel, General, WhatsApp, migrations, deploy ou serviços compartilhados, prefira um checkpoint Git recuperável somente dos arquivos sob controle da tarefa.
Nunca inclua alterações alheias em um checkpoint apenas para "salvar tudo".
Se o working tree impedir um checkpoint seguro, registre isso no handoff e descreva rollback/recovery manual.

## Interrupção e retomada

Se precisar parar com código incompleto, marque claramente:

- se o código compila/inicia;
- se é seguro manter o runtime ativo;
- quais arquivos estão parciais;
- último teste executado e resultado;
- comando ou ação de rollback, quando aplicável;
- próximo passo exato.

Ao assumir trabalho de outro agente, primeiro reconcilie o handoff com `git diff` e o runtime atual. O código local continua sendo a fonte de verdade; corrija o handoff se estiver desatualizado antes de continuar.

## Conclusão

Uma tarefa só pode sair de `active/` quando:

1. não houver item relevante em `EM PROGRESSO` ou `FALTA` dentro do escopo;
2. os testes aplicáveis estiverem registrados com resultado;
3. arquivos parciais tiverem sido resolvidos ou explicitamente retirados do escopo;
4. `Próximo passo exato` indicar `nenhum — tarefa concluída`;
5. commit(s), se houver, estiverem registrados;
6. `STATUS.md` e `.ACTIS_DEV_STATE.json` estiverem atualizados.

Use `scripts/dev_handoff.py` para criar, atualizar e finalizar tarefas sem depender de edição manual de toda a estrutura.
