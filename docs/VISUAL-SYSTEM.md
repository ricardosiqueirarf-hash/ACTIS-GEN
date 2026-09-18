# ACTIS GEN — Visual System

> **Fonte canônica de design do produto.** Toda nova tela, feature, refactor visual, release ou atualização de UI deve consultar este documento antes da implementação.
>
> Versão: **1.1** · revisada em **2026-09-16** a partir do produto local real em `/home/lowkanta/ACTIS-GEN`.

## 1. Identidade visual

ACTIS GEN deve parecer um **sistema operacional/control plane para agentes**, não um dashboard SaaS genérico.

A linguagem é a combinação de quatro pilares:

1. **Pixelado** — grid visível, wordmark e agentes em pixel art, ícones simples e geometria precisa.
2. **Gamificado** — agentes têm identidade, cor, estado, nível/atributos e presença no World, sem transformar tarefas sérias em brinquedo.
3. **Técnico** — alta densidade informacional, IDs, estados, models, Runs, tools e telemetria fazem parte da estética.
4. **Dark e austero** — superfícies quase pretas, contraste controlado e cor usada principalmente para estado, foco e identidade.

O produto deve transmitir: **controle, precisão, atividade, inteligência e infraestrutura viva**.

### Não deve parecer

- dashboard corporativo claro;
- glassmorphism;
- SaaS com cartões excessivamente arredondados;
- cyberpunk neon carregado;
- interface infantil apesar da pixel art;
- coleção de componentes com estilos diferentes entre páginas.
## 2. Marca e elementos proprietários

### Wordmark

O wordmark **ACTIS GEN** pixelado é o identificador principal da interface. O SVG atual usa grid de pixels e `shape-rendering: crispEdges`.

Regras:

- não substituir por tipografia comum sem decisão explícita de rebrand;
- manter proporção e leitura pixelada;
- preferir branco/cinza claro em fundo escuro;
- o ponto azul à esquerda pode representar sistema conectado/ativo;
- não aplicar sombras, bevel, blur ou gradientes no wordmark.

### Agentes — espécie BOT-02

Por instrução explícita do usuário em 2026-09-16, somente os mascotes adotam a referência oval do Grok Bot com cores invertidas: corpo branco e dois olhos pretos. O restante da interface mantém seu design atual.

- Todos os agentes compartilham a mesma silhueta oval branca, com renderização SVG suave.
- O editor oferece somente cor de destaque, expressão facial e acessórios. Expressões: amigável (olhos arqueados), curioso (olhos assimétricos), determinado (olhar estreito e inclinado) e zangado (inclinação forte para o centro). Ícones usam os mesmos olhos do mascote. Expressões antigas são mapeadas para as quatro atuais ao renderizar, sem migração destrutiva dos agentes.
- Os olhos acompanham o mouse; piscadas e movimentos discretos usam fases individuais. A preferência de movimento reduzido desativa animações.
- A cor de destaque personaliza acessórios, sem alterar o corpo branco ou cores semânticas da interface.
- Empresas possuem `color` persistida em #RRGGBB, configurável no cabeçalho da empresa. O mascote usa camisa lisa sem gola conforme `company_id`; transferir troca a cor e desalocar remove a camisa. Expressão e acessórios são preservados; renomear a empresa mantém sua cor.
- A expressão Neutra reutiliza os dois olhos originais em cápsula inclinada do BOT-02 e está disponível junto às quatro expressões anteriores.
- Acessórios: coroa, espada, celular, headset, lupa, chave, cursor, cachecol e livro; também é possível ficar sem acessório.
- Os identificadores dos acessórios existentes são preservados para compatibilidade; `none` e `sword` são novas opções.
- Miniaturas e avatares de chat reutilizam o mesmo componente, sem movimento corporal contínuo.
- Novos agentes reutilizam a espécie e podem escolher automaticamente um acessório coerente com sua função.

### Grid visual

Pixel art e padrões de fundo devem usar linhas discretas. O grid serve para dar sensação de espaço operacional, especialmente em World, Empresas e Workflows; ele não deve competir com conteúdo textual.
## 3. Tipografia

A tipografia padrão do ACTIS GEN é **monoespaçada**. Isso faz parte da identidade técnica do produto.

Stack canônica:

```css
--font-mono: "Berkeley Mono", "IBM Plex Mono", ui-monospace,
  SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
```

Regras:

- `Berkeley Mono` é a primeira referência visual quando disponível;
- `IBM Plex Mono` é o fallback preferencial;
- nunca depender de uma fonte proprietária sem fallback funcional;
- labels, títulos, inputs, tabelas, chat e documentação interna usam a mesma família para manter unidade;
- caixa alta é reservada para micro-labels, estados, categorias e telemetria;
- evitar ALL CAPS em parágrafos ou títulos longos.

Escala recomendada:

| Uso | Tamanho | Peso |
|---|---:|---:|
| micro metadata | 9–11 px | 400–600 |
| label / status | 11–12 px | 500–700 |
| controles / tabela | 12–14 px | 400–600 |
| corpo / chat | 14–16 px | 400–500 |
| título de seção | 16–18 px | 600–700 |
| título de página | 18–22 px | 700 |
| hero/documentação | 24 px | 700 |

Texto essencial nunca deve depender de 8 px para ser compreendido.
## 4. Cores

A UI usa quase-preto como base e cores saturadas somente quando carregam significado.

### Tokens base

```css
--bg:       #08090b; /* fundo principal */
--panel:    #0d0f12; /* superfície primária */
--panel-2:  #11141a; /* superfície elevada / input */
--line:     #242831; /* divisória padrão */
--line-2:   #303642; /* borda de foco/ênfase */
--text:     #d8dee9; /* texto principal */
--muted:    #98a4b7; /* texto secundário */
--subtle:   #8793a6; /* metadata de baixa prioridade */
```

### Tokens semânticos

```css
--accent:   #7aa2f7; /* seleção, foco, navegação, ação primária */
--accent-2: #bb9af7; /* delegação / estado especial, uso raro */
--ok:       #9ece6a; /* sucesso / concluído / saudável */
--energy:   #d7ff64; /* atividade viva / running, uso pontual */
--warning:  #e0af68; /* espera / atenção / não salvo */
--danger:   #f7768e; /* falha / erro / ação destrutiva */
--neutral:  #69717d; /* desconhecido / inativo */
```

Cores de agente são livres dentro da paleta do editor, mas não podem substituir `ok`, `warning`, `danger` ou `accent` em controles de sistema.
## 5. Espaçamento e geometria

O sistema deve seguir uma malha simples de **4 px**.

Escala preferencial:

```text
4 / 8 / 12 / 16 / 20 / 24 / 32 px
```

Regras:

- sidebar desktop: `196 px`, recolhível para `64 px`; abaixo de `760 px`, menu acessível pelo botão no topo;
- topbar: aproximadamente `48 px`;
- padding padrão de conteúdo: `24 px` no desktop, `16 px` em notebook compacto e `12 px` em mobile;
- gaps entre cards: `8–12 px`;
- painéis densos usam `8–12 px` internos;
- modais e páginas documentais podem usar `16–24 px`.

### Cantos

ACTIS GEN é geométrico. O raio padrão é baixo:

```css
--radius-sm: 2px;
--radius:    3px;
--radius-lg: 5px;
```

Evitar `12–20 px` de radius em componentes comuns. Pills são permitidos apenas quando o formato comunica claramente tag/status/chip.

### Bordas

Nem toda superfície precisa de borda. Use três níveis: fundo sem borda, superfície com contraste de background e componente interativo/importante com `1px` de borda.
## 6. Hierarquia visual

A interface deve responder rapidamente a três perguntas: **onde estou, o que está acontecendo e o que posso fazer agora?**

Ordem de prioridade visual:

1. estado crítico ou ação principal;
2. entidade principal da tela;
3. conteúdo operacional;
4. metadata técnica;
5. decoração/grid.

Regras:

- não dar o mesmo peso para container, conteúdo e ação;
- uma página deve ter no máximo uma ação primária dominante por região;
- metadata pode ser pequena e muted; informação operacional não;
- títulos de página não competem com topbar;
- usar espaço vazio para separar grupos, não apenas mais bordas;
- tabelas/listas densas devem favorecer scan horizontal consistente.

### Densidade

ACTIS pode ser denso, mas densidade deve vir de **informação útil**, não de microtexto ou excesso de caixas.

Quando uma tela acumular builder + histórico + inspector + ajuda + execução, usar tabs, drawer, painel recolhível ou inspector contextual em vez de mostrar tudo simultaneamente.
## 7. Componentes canônicos

### Botões

- primário: fundo azul escuro + borda `--accent`; apenas para ação principal;
- secundário: fundo quase transparente + borda `--line`;
- perigo: texto/borda `--danger`, sem preencher toda a tela de vermelho;
- altura comum: `28–38 px` conforme densidade;
- rótulos curtos e verbais: `Salvar`, `Executar`, `Criar agente`, `Parar`.

### Inputs

- fundo `--panel-2` ou mais escuro;
- borda `--line`;
- foco com `--accent`, nunca glow forte;
- labels sempre visíveis em formulários complexos;
- placeholder não substitui label quando o significado do campo não é óbvio.

### Cards

Cards devem representar uma entidade real: agente, tool, run, automação, provider etc. Evitar cards apenas para decorar agrupamentos.

### Tabelas e listas

- cabeçalho discreto e estável;
- status alinhados na mesma coluna;
- metadata com menor contraste;
- linha inteira pode ganhar background leve no hover;
- histórico extenso deve oferecer busca/filtro quando o volume justificar.
### Modais

- backdrop preto translúcido; blur discreto é permitido;
- largura proporcional à complexidade, nunca fullscreen sem necessidade;
- header, body rolável e footer de ações claramente separados;
- ação primária à direita;
- evitar modal dentro de modal.

### Empty states

Um empty state deve explicar **o que está vazio e qual é o próximo passo**. Não usar uma área enorme apenas para dizer “nenhum item”.

### Sidebar e topbar

- sidebar é navegação persistente; item ativo usa background sutil + rail azul;
- topbar indica contexto atual e conexão, sem repetir um segundo cabeçalho enorme;
- novas páginas devem entrar na arquitetura existente, não criar navegação paralela.

## 8. Estados operacionais

Estado nunca depende apenas de cor. Sempre combinar cor + texto, ícone ou forma.

| Estado | Cor | Tratamento |
|---|---|---|
| ready / idle | neutro ou verde discreto | `READY` sem animação forte |
| running | `--energy` ou azul | pulso/step animation discreta |
| thinking | `--energy` atenuado | movimento curto da criatura |
| tool | `--accent` | azul técnico |
| waiting | `--warning` | âmbar, sem aparência de erro |
| delegating | `--accent-2` ou azul | conexão/linha animada |
| completed | `--ok` | flash breve, depois retorna ao neutro |
| failed / blocked | `--danger` | vermelho + mensagem legível |
| cancelled | cinza | reduzido, sem vermelho |
## 9. Ícones e linguagem gráfica

Ícones devem parecer parte do ACTIS, não vir de estilos visuais incompatíveis.

Prioridade:

1. símbolo pixel/outline simples;
2. caractere técnico coerente (`▶`, `■`, `◇`, `⌕`, `+`);
3. SVG monocromático;
4. emoji apenas como fallback temporário ou quando o emoji for conteúdo do usuário.

Evitar misturar, no mesmo nível visual, emoji colorido, Material Icons, ícones 3D e símbolos pixelados.

A longo prazo, o produto deve possuir um pequeno **ACTIS icon set** para Browser, Files, Terminal, Computer, Admin, Workflow, Automation, Context e estados.

## 10. Movimento e feedback

Animação existe para comunicar atividade.

- duração comum: `120–250 ms`;
- hover: mudança curta de border/background, sem zoom exagerado;
- agentes podem usar animações `steps()` para preservar sensação pixelada;
- loading/running pode pulsar discretamente;
- notificações entram/saem rapidamente e não bloqueiam operação;
- evitar animação ambiente constante em muitos elementos ao mesmo tempo;
- respeitar `prefers-reduced-motion` quando o sistema visual for extraído para componentes reutilizáveis.
## 11. Padrões por tipo de tela

### Agentes

A criatura e o nome são protagonistas. Mostrar apenas metadata suficiente para decidir qual agente abrir; detalhes avançados pertencem à configuração.

### World

World é **operação viva**: empresas/setores organizam o espaço, mas o estado dos agentes deve chamar mais atenção que as caixas. Comunicação entre agentes pode usar linhas temporárias e event bus discreto.

### Empresas

Empresas é **estrutura organizacional**, não monitoramento. Priorizar alocação, setor, projeto e contexto; reduzir telemetria e microestados que pertencem ao World.

### Runs / Eventos

São consoles operacionais. Devem favorecer scan, filtros, status consistentes, timestamps e drill-down. Informação técnica pode ser densa, mas alinhada.

### Workflows

O canvas é o protagonista. Inspector deve ser contextual. Teste, histórico e ajuda não devem disputar permanentemente a mesma área do canvas.

### Chat

A conversa é o protagonista. Browser, Runs e logs são painéis auxiliares e devem poder recolher/expandir.
### Configuração / formulários

Configurações extensas devem ser divididas por assunto: Geral, Contexto, Tools & Permissões e Aparência. Evitar páginas longas em que preview e formulário competem pelo espaço.

### Documentação interna

Sobre/Objetivo podem ser mais textuais, mas ainda seguem tokens, grid e tipografia. Navegação lateral interna é permitida para documentos longos.

## 12. Responsividade

ACTIS é desktop-first, mas não deve quebrar em telas menores.

Breakpoints de referência:

```text
> 1200 px  desktop completo
900–1200 px desktop compacto / notebook
< 900 px   stack de painéis
< 760 px   mobile simplificado
```

Regras:

- não apenas esconder funções essenciais no mobile;
- sidebars secundárias podem virar drawer;
- grids viram uma coluna antes de comprimir texto a ponto ilegível;
- canvas/World podem manter scroll espacial quando isso for parte da função;
- ações destrutivas e primárias continuam acessíveis sem hover.

## 13. Acessibilidade mínima

- contraste suficiente entre `--text`, `--muted` e superfícies;
- focus visível para teclado;
- status nunca apenas por cor;
- botões com área clicável compatível com uso real;
- imagens/monstros decorativos não substituem labels;
- texto operacional essencial deve permanecer legível em zoom do navegador.
## 14. Regra para novas features e releases

Toda nova interface deve partir deste documento antes de criar CSS próprio.

Ordem de decisão:

```text
1. VISUAL-SYSTEM.md
2. componente/padrão já existente que segue o sistema
3. necessidade específica da feature
4. exceção documentada
```

Se uma feature precisar quebrar uma regra visual por motivo funcional, a exceção deve ser intencional e documentada. Não criar um novo padrão apenas porque é mais rápido no momento.

### Antes de implementar

- identificar o arquétipo da tela: console, builder, organização, chat, settings ou docs;
- reutilizar tokens e componentes existentes;
- definir ação primária e estado principal;
- decidir o que é conteúdo, metadata e decoração;
- verificar se a feature realmente precisa de novo componente.

### Antes de considerar pronto

- comparar visualmente com Agentes, Runs, World e demais superfícies maduras;
- capturar screenshot desktop;
- verificar truncamentos, overflow e scroll;
- testar empty/loading/error/success;
- testar 1600×1000 e largura compacta;
- confirmar que não surgiram cores, radius, fontes ou espaçamentos arbitrários.
## 15. Contrato de implementação

O CSS atual cresceu por camadas históricas. Novas mudanças não devem continuar adicionando overrides globais concorrentes no fim do arquivo.

Direção técnica:

- centralizar tokens em `:root`;
- evitar redefinir a mesma classe várias vezes em blocos distantes;
- preferir classes de componente previsíveis;
- evitar `!important` salvo quando necessário para migração de legado;
- não usar valores hex/radius/font-size arbitrários quando já existe token equivalente;
- ao refatorar uma área, remover regra antiga que foi substituída;
- componentes semelhantes devem compartilhar estilo em vez de duplicar CSS por página.

Exemplo de núcleo desejado:

```css
:root {
  --bg: #08090b;
  --panel: #0d0f12;
  --panel-2: #11141a;
  --line: #242831;
  --text: #d8dee9;
  --muted: #98a4b7;
  --accent: #7aa2f7;
  --ok: #9ece6a;
  --warning: #e0af68;
  --danger: #f7768e;
  --radius: 3px;
}
```
## 16. Checklist visual de release

Uma tela/feature só está visualmente pronta quando:

- [ ] segue a identidade pixel + técnica + dark;
- [ ] usa a stack tipográfica canônica;
- [ ] usa apenas tokens/paleta prevista ou exceção justificada;
- [ ] respeita grid de 4 px e radius baixo;
- [ ] possui hierarquia clara entre ação, conteúdo e metadata;
- [ ] estados usam texto/símbolo além de cor;
- [ ] não introduz iconografia incompatível;
- [ ] empty/loading/error/success foram vistos;
- [ ] scroll começa no ponto correto ao trocar de view;
- [ ] nomes longos não destroem layout;
- [ ] funciona em desktop e largura compacta;
- [ ] screenshot final foi comparado com o restante do ACTIS;
- [ ] CSS novo não duplica ou contradiz regras existentes sem necessidade.

## 17. Governança deste documento

`docs/VISUAL-SYSTEM.md` é a **fonte de verdade visual** do ACTIS GEN.

Quando uma decisão de design mudar de forma permanente, atualizar este documento no mesmo trabalho que altera a interface. O código atual pode possuir legado fora do padrão; isso não transforma o legado em nova regra.

Para features futuras, a pergunta não é “como esta página quer parecer?”, e sim:

> **Como esta feature se expressa dentro da linguagem visual do ACTIS GEN?**

A identidade deve evoluir por extensão controlada, não por reinvenção a cada tela.


## 18. Revisão de UI/UX — 2026-09-16

A revisão 1.1 preserva o wordmark, a espécie GEN-01, os acessórios, o fundo escuro e a família monoespaçada.

- Navegação agrupada em Operação, Organização, Sistema e Projeto; item atual identificado por cor, texto e `aria-current`.
- Menu recolhível com preferência local e controle visível em telas menores; Escape fecha o menu mobile.
- Agentes: busca por nome, modelo e ferramenta, filtro de empresa, contagem de resultados, cartões que permitem nomes longos e abertura por Enter/Espaço.
- Execuções: filtros combinados por texto, agente e estado; tarefas ativas permanecem fora do filtro de histórico. Detalhes têm controle `aria-expanded` e duração em segundos.
- World: atividade recente recolhível; setores vazios indicam a possibilidade de arrastar agentes.
- Workflows: assistente e testes em painéis recolhíveis, canvas proporcional à janela e painel de configuração com largura limitada.
- Ferramentas: ícones SVG geométricos coerentes, textos legíveis e cartões com grade adaptável.
- Aprovações preserva justificativa e escopo em telas pequenas; chat mantém o histórico de execuções disponível.
- A identificação dos agentes na galeria não declara disponibilidade operacional sem consultar o estado de execução. O World continua sendo a superfície de estado ao vivo.

### Evidência desta revisão

JavaScript passou na checagem de sintaxe. CSS canônico passou no parser. Verificação de DOM com respostas locais registradas cobriu as 12 áreas, filtros, estados vazios, abertura por teclado, chat, configuração e campos de automação sem escrita de dados ou chamada de modelo.

A renderização visual em navegador real não foi validada nesta sessão: a política do navegador remoto bloqueou o acesso à prévia local. Responsividade foi implementada em CSS, mas a comparação final de screenshots permanece pendente.

## Harness x Skill sets

Na tela **Ferramentas**, capacidades são exibidas em duas camadas distintas:

- **Harness**: acesso técnico de baixo nível (`browser`, arquivos, terminal, computer use e runtime nativo do ACTIS).
- **Skill sets**: comportamentos compostos e reutilizáveis que podem herdar um ou mais Harnesses.

A mesma separação aparece na configuração do agente. Skills do domínio WhatsApp (`browser.whatsapp`, `whatsapp.messaging`, `whatsapp.inbox`, `whatsapp.labels`, `whatsapp.routing`, `whatsapp.ai-agent`, `whatsapp.customer-service`) pertencem visualmente a **Skill sets**, não ao catálogo de Harness.
