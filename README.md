# Uni CLI

O Python roda no Windows. Criação de fontes, Git, mapa local e seleção ficam no host.
Composer, PHP, limpeza de cache e **todo o build**, incluindo compilados, assets, manifestos e versões,
rodam no Linux do Docker. Não é necessário instalar PHP/Composer no Windows.

## Instalação e workspace

Instale Python, Git, Docker Desktop com containers Linux e Compose. Adicione esta
pasta ao PATH; `uni.cmd` encaminha os argumentos ao Python. No PowerShell:

```powershell
uni shell install
uni start C:/workspace
uni status
```

Abra outro terminal depois de instalar a integração. A política PowerShell precisa permitir scripts locais; o instalador não altera essa política automaticamente. `uni start` consulta o catálogo,
verifica Git/Docker/Compose/PHP e mapeia os composer.json das aplicações.
Ignora libs, vendor, storage, tests e diretórios de ferramentas. Duplicatas interrompem
o mapeamento sem substituir o mapa anterior. `--offline` apenas mapeia o disco.
Projetos existentes e novos ficam diretamente na raiz do workspace, ao lado dos diretórios Docker.

`libs_projects.json` é o catálogo compartilhável. `.uni/workspace.json` no workspace
guarda caminhos locais, ambiente e projeto selecionado. `.local.json` ao lado do CLI
aponta para o workspace em uso; ambos são locais e não devem ser publicados.

## Ambientes e projeto de trabalho

```powershell
uni projects
uni use area-candidato
uni back
uni front
uni where
uni up
uni down
uni status
```

Um ambiente pode ter front/back ou somente uma aplicação. `use` verifica os dois
repositórios Docker antes de parar o ambiente anterior, atualiza suas branches apenas
por fast-forward, gera `.env` com os caminhos locais e sobe backend antes de frontend.
Verifica portas, PHP e Nginx. Alterações locais no Docker impedem a troca.
Se a troca falhar, tenta restaurar branches, arquivos .env e containers anteriores;
qualquer falha de recuperação fica registrada e aparece em `status`.

`back`/`front` selecionam o projeto para os próximos comandos sem reiniciar containers.
A função PowerShell instalada por `shell install` também muda a pasta do terminal.
A seleção do ambiente/projeto é compartilhada no workspace (um ambiente por vez).
Em projeto avulso não mapeado, o CLI usa o composer.json da pasta atual ou ancestral.

Depois de `use`, escolha: manter as janelas (Enter/padrão), abrir novas ou reutilizar
uma janela do VS Code. Sem terminal interativo, não altera o editor. `--editor none`,
`new` ou `reuse` permite escolher explicitamente. Fora do terminal VS Code, reuse abre
novas janelas. Arquivos não salvos ficam sob os controles do editor.

## Registrar, clonar e publicar catálogo

```powershell
uni project register https://github.com/usuario/meu-back.git --role back --environment meu
uni project register https://github.com/usuario/meu-front.git --role front --environment meu --backend meu-back
uni project clone meu-back
uni project clone meu-front
uni project delete meu-front
uni catalog sync
uni catalog publish
uni catalog verify
```

`register` pode receber somente o Git: consulta composer.json da branch padrão sem
executar o código remoto. `--path` permite ler o manifesto local, inclusive para um
repositório vazio. `--name` escolhe o identificador. O comando valida duplicatas,
registra associações e prepara uma branch Docker a partir de `docker_base_branch` se ainda não houver.
Usa `docker_repository` do catálogo ou `--docker-repository`.

Por padrão, publica o cadastro com commit apenas do JSON e push na branch atual do
uni-cli. Alterações alheias não entram no commit do catálogo. Concorrência/divergência
Git impede o push, sem forçar sobrescrita. `--local` salva sem publicar.
`catalog_url` é verificado depois do push; GitHub Pages pode demorar para propagar.
`catalog verify` permite confirmar a publicação depois. Falha de push deixa o commit
local recuperável por `uni push --cli`; não informa sucesso remoto indevidamente.

`clone` instala as bibliotecas pelo Composer e atualiza o mapa; use `--no-install`
para apenas clonar. O caminho local nunca entra no catálogo compartilhado.
`delete` publica a remoção do cadastro e depois exclui a branch correspondente no
repositório Docker. Se essa segunda etapa falhar, execute o mesmo `delete` novamente:
sem cadastro, ele consulta o catálogo remoto e exige `--docker-repository URL` para
concluir a limpeza da branch órfã. `--local` preserva a branch Docker e deixa a
publicação pendente. Projetos associados como backend devem ser desassociados antes da
exclusão. Pastas locais e o
repositório da aplicação são preservados.

## Criar projeto

### Antes de começar

1. Instale Python, Git e Docker Desktop com containers Linux e Compose. Instale o CLI no
   `PATH`, abra um terminal novo e valide o workspace:

   ```powershell
   uni shell install
   uni start C:/workspace
   uni projects
   ```

   O resultado esperado é o mapa local em `.uni/workspace.json` e a lista dos projetos
   já cadastrados. Use `uni start C:/workspace --offline` somente para mapear o disco;
   o fluxo normal também valida Git, Docker, Compose, PHP e o catálogo.

2. Confirme os três repositórios envolvidos:

   - **Aplicação nova:** receberá fontes, `composer.json` e a `main` criada pelo CLI.
   - **`uni-cli` existente:** contém `libs_projects.json`; a branch atual já deve existir.
   - **Docker existente:** contém o modelo; sua branch modelo `padrao` já deve existir e ter
     `uni/Dockerfile`, `php/start.sh`, `php/development.ini` e `nginx/default.conf`.

   Não crie manualmente a `main` da aplicação nem a branch Docker do projeto. O CLI cria
   a `main` da aplicação e uma branch Docker com o valor de `--name`; não altera a
   `padrao` do Docker.

3. Configure acesso antes de executar o comando. Repositório público pode dispensar
   autenticação para leitura, mas toda escrita exige permissão. A pessoa/conta que roda
   o CLI precisa fazer push direto na `main` da aplicação, criar e enviar a branch Docker
   e fazer commit/push na branch atual de `uni-cli`. Proteções que exijam revisão ou
   bloqueiem essas ações fazem o fluxo falhar. Use HTTPS ou SSH sem senha/token na URL;
   configure credenciais no Git ou uma chave SSH. Nunca grave token, senha, chave privada
   ou `.env` no Git, catálogo ou linha de comando.

### Criar uma aplicação simples

4. Crie manualmente um repositório **totalmente vazio** no provedor. No GitHub, escolha
   organização/proprietário, nome e visibilidade pública ou privada, conceda o acesso
   necessário e copie a URL HTTPS ou SSH. Não marque README, `.gitignore`, licença,
   commit ou branch inicial. Em outro provedor, aplique o mesmo critério. O CLI recusa
   qualquer repositório que já tenha referências.

5. Crie o projeto:

   ```powershell
   uni init uniube MeuProjeto --name meu-projeto --role front --environment projeto --repository https://github.com/uniube/meu-projeto.git --libs application
   ```

   - `uniube` é o `vendor` Composer; usa minúsculas, números, `.`, `_` ou `-`.
   - `MeuProjeto` é o nome PHP; começa por letra e forma o Composer/namespace (hifens saem
     do namespace).
   - `--name meu-projeto` é o identificador do catálogo, diretório padrão e branch Docker.
   - `--role front` define o papel; `--environment projeto` define o ambiente.
   - `--repository` é a URL do repositório vazio da aplicação.
   - `--libs application` seleciona núcleos do catálogo e suas dependências transitivas.

   O resultado esperado é a aplicação em `C:/workspace/meu-projeto`, commit/push em `main`,
   branch Docker `meu-projeto` e o cadastro publicado. `--port PORTA` troca a porta local
   (padrão 8080 para front e 8082 para back); `--docker-repository URL` substitui o
   repositório Docker definido no catálogo.

6. Ative e confira o resultado:

   ```powershell
   uni projects
   uni use projeto
   uni status
   uni where
   ```

   `projects` deve listar o ambiente; `use` prepara/atualiza os Docker e sobe os
   containers; `status` mostra branches e serviços; `where` mostra o projeto selecionado.

### Criar backend e frontend

7. Crie os dois projetos na ordem que preferir. Para **backend primeiro**, indique
   `--link` em ambos (sem terminal interativo, `--link` ou `--no-link` é obrigatório):

   ```powershell
   uni init uniube MeuBack --name meu-back --role back --environment meu --link --repository https://github.com/uniube/meu-back.git --libs application --port 8082
   uni init uniube MeuFront --name meu-front --role front --environment meu --link --repository https://github.com/uniube/meu-front.git --libs components --port 8080
   ```

   `--role back` cria o backend; `--environment meu` o agrupa com o futuro frontend;
   `--port 8082` explicita a porta padrão. Backend rejeita os núcleos `frontend` e
   `components`.

8. Para **frontend primeiro**, faça os mesmos comandos na ordem inversa:

   ```powershell
   uni init uniube MeuFront --name meu-front --role front --environment meu --link --repository https://github.com/uniube/meu-front.git --libs components --port 8080
   uni init uniube MeuBack --name meu-back --role back --environment meu --link --repository https://github.com/uniube/meu-back.git --libs application --port 8082
   ```

   O frontend começa sem proxy `/api`; quando o backend é criado, o CLI atualiza somente
   `compose.yaml` e `nginx/default.conf` gerados na branch Docker do frontend. Se esses
   arquivos foram personalizados, faça a revisão manual. `--backend meu-back` continua
   válido no frontend e pede vínculo explícito com esse backend, inclusive para projetos
   antigos sem decisão registrada. Novos vínculos exigem o mesmo `--environment`.
   O vínculo só é registrado após a preparação Docker. Depois, execute `uni use meu`,
   `uni status` e `uni where`.

`--no-link` mantém os projetos independentes. Mesmo no mesmo ambiente, `uni use meu`
inicia os dois; um frontend sozinho funciona sem proxy `/api`. Se o frontend escolheu
`--no-link` e o backend é criado depois com `--link`, o terminal interativo pede
confirmação para mudar a decisão. Em scripts, use `uni project link meu-front meu-back`
para alterar a decisão explicitamente; `--link` do segundo não substitui `--no-link`
do primeiro. O comando `project link` é seguro para repetir. Se a atualização Docker
ou o push falhar, o segundo projeto continua cadastrado sem relação: resolva o erro e
repita `uni project link meu-front meu-back`, sem refazer `init`.

`uni project register` aceita as mesmas flags. Com `--path`, grava a intenção também
no `composer.json` local; sem `--path`, registra a intenção no catálogo sem alterar o
repositório da aplicação. `--local` registra a intenção, mas adia o vínculo até as
branches Docker serem publicadas. Projetos antigos com `--backend` seguem aceitos.

### Opções locais e recuperação

`--no-framework` cria a base sem núcleos. Sem `--libs` ou `--no-framework`, o terminal
interativo pergunta quais núcleos usar; em automação informe uma dessas opções.
`--no-install` prepara o manifesto, mas nao baixa bibliotecas nem instala `vendor/`; sem ele, Composer
e o build necessário rodam no Docker Linux.

`--local` ainda cria estrutura, `composer.json`, `.gitignore`, Git local e commit inicial.
Com `--repository`, valida o remoto vazio, grava `origin` e registra apenas o catálogo
local; não envia a aplicação, não cria a branch Docker e não publica o catálogo. Não há
um único comando para publicar esse estado depois: publique a aplicação manualmente,
registre-a conforme o estado com `uni project register` e publique só o catálogo com
`uni catalog publish`. A criação posterior da branch Docker depende de
`docker_repository` configurado e de a branch ainda não existir.

Em falhas, arquivos e commits anteriores são preservados, sem exclusão nem force push.
Corrija manualmente acesso, credenciais, proteção de branch ou configuração antes de
seguir. `uni push --cli` retoma somente um push pendente do catálogo já commitado;
`uni catalog verify` apenas confere uma publicação HTTP configurada. Não reexecute
`uni init` como recuperação genérica: ele exige destino e repositório de aplicação vazios.

## Desenvolvimento e dependências

```powershell
uni create page Cursos/Resumo
uni create component Atoms/Badge
uni build
uni composer install
uni composer update
uni composer add psr/log --version ^3.0
uni composer remove psr/log
uni composer list
uni composer show application
uni composer search logger
uni libs
uni cache clear
uni push --message "Atualiza página de cursos"
```

O Composer instala os pacotes internos em `libs/`, com metadados do catalogo e
`oomphinc/composer-installers-extender`. As demais dependencias ficam em `vendor/`.
Downloads `dist` evitam `.git` aninhado; nao sao criados repositorios `path` ou
symlinks. Os namespaces continuam explicitos no PSR-4 da raiz.

`uni composer configure` migra a configuracao de um projeto existente, preservando
as demais entradas. Ele nao baixa arquivos nem apaga repositorios aninhados.
Depois execute `uni composer update` para resolver o lock. Clones antigos em
`libs/` precisam ser preservados e separados manualmente antes da instalacao.

`uni composer install` reproduz `composer.lock`. `uni composer update` sem nomes
atualiza somente as bibliotecas gerenciadas; informe os nomes para pacotes externos.
A resolucao precede a instalacao: se falhar, manifesto e lock sao restaurados.
Se o download falhar, o lock resolvido permanece para retomar com `install`.
Fontes locais modificadas bloqueiam atualizacoes; publique as mudancas em um
clone separado e registre o estado do projeto primeiro. O lock e a fonte das
revisoes, substituindo os antigos campos `extra.uni.libraries.*.ref`.

`uni build` atualiza as bibliotecas e usa o compilador PHP do proprio CLI para
produzir rotas, templates, componentes, assets e manifestos. Use
`uni build --skip-update` para recompilar somente o codigo local. Nao delega a
`compiler.py`. `uni serve` disponibiliza o painel local no Docker, e
`uni minify caminho.js` / `uni unminify caminho.js` processam assets.

O Git acompanha `libs/`, `vendor/`, `public/assets/`, os PHP compilados,
`storage/framework/manifest/{build,assets,components}.php` e
`storage/framework/assets/`. Cache, logs, credenciais, arquivos temporarios,
ferramentas e clones `*-core-dev/` ficam ignorados. As regras sao criadas no init
e atualizadas na migracao/build. Nao se executa update no servidor de producao.

Para Gogs, o CLI consulta o Git e cria ZIPs imutaveis em `.uni/packages/`;
mirrors ficam em `.uni/git/`. O Composer resolve e instala esses arquivos.
O lock registra URL e commit de origem; `uni composer install` reconstroi
archives ausentes usando a revisao exata. Por isso, use os comandos do CLI
para preparar um clone novo, em vez de executar Composer diretamente.
As credenciais Git HTTPS locais sao reutilizadas em memoria e repassadas ao
Docker via `COMPOSER_AUTH`. Voce tambem pode definir essa variavel no ambiente.
Credenciais, mirrors e ZIPs nunca devem ser versionados.

`uni docker ...` encaminha argumentos ao Compose do projeto selecionado.

`push` exige Git próprio, origin, branch e ausência de conflitos/rebase. Mostra as
alterações e solicita mensagem quando faltam commits; em scripts use --message.
Publica somente o projeto selecionado. `--cli` seleciona explicitamente a ferramenta.
Nunca usa force push nem inclui os repositórios Docker automaticamente.

O banner aparece somente em `uni` sem argumentos e na ajuda.

## Testes

Os testes novos do CLI estão em tests/. Execute `python -m unittest discover -s tests`.
Para um checkout somente leitura no Docker, defina UNI_TEST_TMP para uma pasta dentro
dos testes da aplicação. A suíte usa Git local para verificar publicação, duplicatas,
rollback, init, branches Docker e preservação de arquivos alheios.
