# uni-cli

Execute com o diretório de trabalho apontando para a aplicação:

```sh
cd ../area-do-candidato-front
python ../uni-cli/uni.py build
python ../uni-cli/uni.py create page Cursos/Resumo
python ../uni-cli/uni.py docker up -d
```

Scripts internos e templates são resolvidos em relação ao próprio `uni.py`.
`libs_projects.json` pertence à ferramenta; as dependências da aplicação
são definidas no seu `composer.json`.

`init uniube AreaDoCandidato` cria `src/Core`, `src/Programs` e `src/Config`,
com o namespace `AreaDoCandidato` mapeado diretamente para `src/`. O mesmo
manifesto registra o nome Composer e `extra.uni.docker.port` (padrão 8080).
O comando `docker` usa esses metadados e a pasta atual para selecionar a aplicação.

## Bibliotecas

Dentro do projeto (substitua `uni` por `python ../uni-cli/uni.py` quando necessário):

```sh
uni init minhaempresa MeuProjeto
uni init minhaempresa MinhaApi --libs application
uni init minhaempresa MeuSite --libs components
uni init minhaempresa MeuSite --libs components backend
uni init minhaempresa SemFramework --no-framework
uni composer install
uni composer update
uni composer update frontend
uni composer add components
uni composer add psr/log --version ^3.0
uni composer remove psr/log
uni libs
uni composer list
uni composer show frontend
uni composer search logger
```

O `init` pergunta se deve usar o framework e quais núcleos instalar. Em scripts,
informe `--libs` ou `--no-framework`. Selecionar frontend inclui application;
selecionar components inclui ambos. Backend é um pacote inicial sem funcionalidades.
`--no-install` prepara os clones e o manifesto, mas não instala `vendor/`.

Os remotes estão em `libs_projects.json`. O nome do repositório `front-end-core`
corresponde ao pacote Composer `uniube/frontend-core` e à pasta `libs/frontend-core`.

O CLI clona os núcleos em `libs/`; Composer resolve suas dependências e cria o
autoload em `vendor/`. Pacotes externos são instalados pelo Composer em `vendor/`.
Se Composer não estiver instalado na máquina, o CLI usa o serviço Docker `uni`.

`composer.json`, em `extra.uni.libraries`, registra o remote, a branch e o commit
de cada núcleo. `composer.lock` registra a resolução do Composer. Versione ambos.
`uni composer install` recria clones ausentes nos commits registrados, sem buscar
automaticamente versões novas. `uni composer update` consulta as branches remotas,
avança os clones e atualiza os dois arquivos. As branches atuais usam `dev-main`;
o projeto permite versões de desenvolvimento e prefere versões estáveis externas.

Alterações locais impedem operações que possam substituir fontes. Faça commit ou
stash antes de atualizar; commits divergentes do remote não são descartados.
Remover uma biblioteca retira a dependência direta; Composer conserva dependências
ainda necessárias. Clones desinstalados permanecem em `libs/` para preservar fontes.
Em caso de falha, manifesto, lock e checkouts anteriores são restaurados; se o
Composer já tiver alterado `vendor/`, execute `uni composer install` para reconciliá-lo.

`libs/` é ignorada pelo Git da aplicação porque cada clone tem seu próprio Git.
Uma cópia nova da aplicação deve executar `uni composer install` antes do build.
