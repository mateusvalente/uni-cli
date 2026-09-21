# Uni CLI

O Python roda no Windows. Criacao de fontes, Git, mapa local e selecao ficam no host.
Composer, PHP, limpeza de cache e **todo o build**, incluindo compilados, assets, manifestos e versoes,
rodam no Linux do Docker. Nao e necessario instalar PHP/Composer no Windows.

## Instalacao e workspace

Instale Python, Git, Docker Desktop com containers Linux e Compose. Adicione esta
pasta ao PATH; `uni.cmd` encaminha os argumentos ao Python. No PowerShell:

```powershell
uni shell install
uni start C:/workspace
uni status
```

Abra outro terminal depois de instalar a integracao. A politica PowerShell precisa permitir scripts locais; o instalador nao altera essa politica automaticamente. `uni start` consulta o catalogo,
verifica Git/Docker/Compose/PHP e mapeia os composer.json das aplicacoes.
Ignora libs, vendor, storage, tests e diretorios de ferramentas. Duplicatas interrompem
o mapeamento sem substituir o mapa anterior. `--offline` apenas mapeia o disco.
Projetos existentes e novos ficam diretamente na raiz do workspace, ao lado dos diretorios Docker.

`libs_projects.json` e o catalogo compartilhavel. `.uni/workspace.json` no workspace
guarda caminhos locais, ambiente e projeto selecionado. `.local.json` ao lado do CLI
aponta para o workspace em uso; ambos sao locais e nao devem ser publicados.

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

Um ambiente pode ter front/back ou somente uma aplicacao. `use` verifica os dois
repositorios Docker antes de parar o ambiente anterior, atualiza suas branches apenas
por fast-forward, gera `.env` com os caminhos locais e sobe backend antes de frontend.
Verifica portas, PHP e Nginx. Alteracoes locais no Docker impedem a troca.
Se a troca falhar, tenta restaurar branches, arquivos .env e containers anteriores;
qualquer falha de recuperacao fica registrada e aparece em `status`.

`back`/`front` selecionam o projeto para os proximos comandos sem reiniciar containers.
A funcao PowerShell instalada por `shell install` tambem muda a pasta do terminal.
A selecao do ambiente/projeto e compartilhada no workspace (um ambiente por vez).
Em projeto avulso nao mapeado, o CLI usa o composer.json da pasta atual ou ancestral.

Depois de `use`, escolha: manter as janelas (Enter/padrao), abrir novas ou reutilizar
uma janela do VS Code. Sem terminal interativo, nao altera o editor. `--editor none`,
`new` ou `reuse` permite escolher explicitamente. Fora do terminal VS Code, reuse abre
novas janelas. Arquivos nao salvos ficam sob os controles do editor.

## Registrar, clonar e publicar catalogo

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

`register` pode receber somente o Git: consulta composer.json da branch padrao sem
executar o codigo remoto. `--path` permite ler o manifesto local, inclusive para um
repositorio vazio. `--name` escolhe o identificador. O comando valida duplicatas,
registra associacoes e prepara uma branch Docker a partir da main se ainda nao houver.
Usa `docker_repository` do catalogo ou `--docker-repository`.

Por padrao, publica o cadastro com commit apenas do JSON e push na branch atual do
uni-cli. Alteracoes alheias nao entram no commit do catalogo. Concorrencia/divergencia
Git impede o push, sem forcar sobrescrita. `--local` salva sem publicar.
`catalog_url` e verificado depois do push; GitHub Pages pode demorar para propagar.
`catalog verify` permite confirmar a publicacao depois. Falha de push deixa o commit
local recuperavel por `uni push --cli`; nao informa sucesso remoto indevidamente.

`clone` instala as bibliotecas pelo Composer e atualiza o mapa; use `--no-install`
para apenas clonar. O caminho local nunca entra no catalogo compartilhado.
`delete` remove apenas o cadastro do catalogo e publica a alteracao; `--local`
deixa a publicacao pendente. Projetos associados como backend devem ser
desassociados antes da exclusao. Repositorios e pastas locais sao preservados.
Depois de limpar os arquivos locais do projeto, solicite ou exclua manualmente
a branch correspondente no repositorio Docker; `delete` mostra esse aviso.

## Criar projeto

Crie primeiro um repositorio Git vazio no provedor. Depois:

```powershell
uni init minhaempresa MeuBack --name meu-back --role back --environment meu --repository https://github.com/usuario/meu-back.git --libs application
uni init minhaempresa MeuFront --name meu-front --role front --environment meu --backend meu-back --repository https://github.com/usuario/meu-front.git --libs components
```

Sem `--libs` ou `--no-framework`, pergunta se deseja framework e quais partes.
As dependencias transitivas sao instaladas automaticamente. Backend rejeita frontend
ou components. `--port` configura a porta; defaults front 8080 e back 8082.
A estrutura e index sao criados no Windows, depois Composer/build no Docker.
O CLI inicializa e publica o Git do projeto, cria/publica a branch Docker a partir da
main e publica o catalogo. Cada etapa remota e independente: em falha, os arquivos e
commits anteriores ficam preservados para recuperacao, sem apagar repositorios.
`--local` cria sem publicacoes; `--no-install` prepara as fontes sem instalar vendor.
O namespace PHP usa o argumento MeuBack/MeuFront, sem hifens, mapeado para src/.

## Desenvolvimento e dependencias

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
uni push --message "Atualiza pagina de cursos"
```

Composer usa clones Git em libs/ e dependencias em vendor/. Fontes sujas nao sao
substituidas por updates. Remover um pacote preserva seu clone local.
`build` apenas dispara o worker Linux e mostra o resultado; nao percorre links Linux
nem publica artefatos pelo Windows. Build executa mesmo com os servidores parados.
`uni docker ...` encaminha argumentos ao Compose do projeto selecionado.

`push` exige Git proprio, origin, branch e ausencia de conflitos/rebase. Mostra as
alteracoes e solicita mensagem quando faltam commits; em scripts use --message.
Publica somente o projeto selecionado. `--cli` seleciona explicitamente a ferramenta.
Nunca usa force push nem inclui os repositorios Docker automaticamente.

O banner aparece somente em `uni` sem argumentos e na ajuda.

## Testes

Os testes novos do CLI estao em tests/. Execute `python -m unittest discover -s tests`.
Para um checkout somente leitura no Docker, defina UNI_TEST_TMP para uma pasta dentro
dos testes da aplicacao. A suite usa Git local para verificar publicacao, duplicatas,
rollback, init, branches Docker e preservacao de arquivos alheios.
