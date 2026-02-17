# Bot Discord de Dados + Áudio

Bot para Discord com:

- rolagens de dados comuns (`d20`, `2d6+3`...)
- rolagens Fate (`df`, incluindo regra especial para `4df`)
- cálculo matemático seguro com `r <expressão>`
- tema musical por usuário no `++++`
- playlist de luta no canal de voz (`!luta`)
- comandos administrativos de ban/desban e permissões

---

## Requisitos

- Python 3.10+
- FFmpeg instalado no sistema (necessário para áudio)
- Dependências do `requirements.txt`

Dependências atuais:

- `discord.py>=2.3.2`
- `PyNaCl>=1.5.0`
- `yt-dlp>=2024.12.13`

---

## Instalação

No diretório `Bot-discord`:

```bash
pip install -r requirements.txt
```

---

## Configuração do token

O bot lê o token pela variável de ambiente `DISCORD_BOT_TOKEN`.

### PowerShell (Windows)

```powershell
$env:DISCORD_BOT_TOKEN="SEU_TOKEN_AQUI"
python Bot.py
```

### CMD (Windows)

```cmd
set DISCORD_BOT_TOKEN=SEU_TOKEN_AQUI
python Bot.py
```

Se a variável não estiver definida, o bot encerra com erro.

---

## Áudio e FFmpeg

- O bot usa FFmpeg para tocar áudio em voz.
- O arquivo local `kokusen.ogg` é usado em eventos específicos de `4df`.
- O comando `!luta` carrega uma playlist fixa do YouTube Music e toca em fila.

No Windows, o bot tenta encontrar o `ffmpeg.exe` automaticamente em caminhos comuns (incluindo instalação via Winget). Se não encontrar, usa `ffmpeg` no `PATH`.

---

## Comandos Slash

- `/roll expressao:<texto>`
	- Exemplos: `d20+5`, `2d6`, `4df atacar`, `4df criar vantagem distração`
- `/tema link:<url>`
	- Define seu tema para tocar quando você tirar `++++` em `4df`
- `/ban` (admin)
	- Banir usuário por menção ou ID
- `/desbanir` (admin)
	- Remove usuário da lista de banidos

---

## Comandos de Texto

- `!tema <link>`
	- Salva tema personalizado do usuário
- `!luta`
	- Entra (ou move) para seu canal de voz e inicia a playlist de luta
- `!adm @usuario` ou `!adm ID` (admin)
	- Adiciona novo admin
- `!teste @usuario` ou `!teste ID` (admin)
	- Ativa/desativa modo de teste para o alvo (permite `max`/`min` em `4df`)
- `!ban @usuario` ou `!ban ID` (admin)
	- Bloqueia usuário para rolagens/comandos de rolagem
- `!desbanir @usuario` ou `!desbanir ID` (admin)
	- Remove bloqueio

---

## Rolagens de Dados

### Dados comuns

Formato:

```text
[quantidade]d[lados][modificadores] [texto opcional]
```

Exemplos:

- `d20`
- `2d6+3`
- `3d10-1 ataque pesado`

### Dados Fate (`df`)

Formato geral:

```text
[quantidade]df[modificadores] [texto opcional]
```

Exemplos:

- `df`
- `4df atacar`
- `4df defender escudo`
- `4df criar vantagem terreno alto`

### Regra especial para `4df`

Quando for `4df`, **é obrigatório informar a ação**:

- `Atacar`
- `Defender`
- `Criar Vantagem`
- `Superar`

Se a ação não for informada, o bot avisa e não rola.

---

## Modo de teste (`!teste`)

Usuários com modo de teste ativo podem forçar resultado em `4df`:

- `max` → força `++++`
- `min` → força `----`

Exemplo:

```text
4df atacar max
```

---

## Cálculo matemático (`r`)

Formato:

```text
r <expressão>
```

Exemplos:

- `r 2 + 3 * 4`
- `r (10 + 5) * 2 - 3/4`

O cálculo é validado via AST (mais seguro que `eval`).

---

## Permissões e listas internas

- Admins iniciais e usuários banidos são definidos no código (`ids_admin`, `usuarios_banidos`).
- O comando `!adm` permite expandir a lista de admins em runtime.
- As listas atuais são em memória (reinício do bot perde alterações feitas em runtime).

---

## Execução

Depois de configurar o token:

```bash
python Bot.py
```

Ao iniciar, o bot sincroniza os comandos slash automaticamente.

