# Bot Discord de Dados + Áudio

Bot para Discord com:

- rolagens de dados comuns sem prefixo (`d20`, `4df atacar`, `1d20+2d6`...)
- expressões matemáticas com dados via prefixo `r` (`r 5*4/1d20+14`, `r 1d20+4df+3d6`...)
- rolagens Fate (`df`, incluindo regra especial para `4df`)
- cálculo matemático seguro com `r <expressão>`
- tema musical por usuário no `++++`
- troca do efeito especial de `++++` para o modo Invencível com `!invencivel`
- escala por usuário com `!escala` e bônus automático de duelo Fate com `!duelo`
- playlist de luta no canal de voz (`!luta`)
- comandos administrativos de ban/desban e permissões

---

## Requisitos

- Python 3.10+
- FFmpeg instalado no sistema (necessário para áudio)
- Dependências do `requirements.txt`

Dependências atuais:

- `discord.py[voice]>=2.7.1`
- `PyNaCl>=1.5.0`
- `yt-dlp>=2024.12.13`
- `ffmpeg-python>=0.2.0`
- `certifi>=2024.12.14`

---

## Instalação

No diretório `Bot-discord`:

```bash
pip install -r requirements.txt
```

---

## Configuração do token

O bot lê o token pela variável de ambiente `DISCORD_BOT_TOKEN`.

Por padrão, ele tenta carregar automaticamente um arquivo `.env.local` ou `.env` no diretório atual, na pasta do script e nos diretórios pai.

Arquivo recomendado:

```env
DISCORD_BOT_TOKEN="SEU_TOKEN_AQUI"
```

No macOS, o projeto também configura automaticamente o CA bundle do `certifi` para evitar falhas de SSL em instalações do Python que não trazem a cadeia de certificados corretamente.

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

Se nenhuma dessas opções estiver definida, o bot encerra com erro.

---

## Áudio e FFmpeg

- O bot usa FFmpeg para tocar áudio em voz.
- O arquivo local `kokusen.ogg` é usado em eventos específicos de `4df`.
- O arquivo local `invencivel.ogg` é usado quando o modo `!invencivel` está ativo.
- O comando `!luta` carrega uma playlist fixa do YouTube Music e toca em fila.

No Windows, o bot tenta encontrar o `ffmpeg.exe` automaticamente em caminhos comuns (incluindo instalação via Winget). Se não encontrar, usa `ffmpeg` no `PATH`.

---

## Comandos Slash

- `/roll expressao:<texto>`
	- Exemplos: `d20+5`, `2d6`, `4df atacar`, `4df criar vantagem distração`, `4df cv distração`
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
- `!invencivel`
	- Troca o efeito especial de `++++` para o modo Invencível
- `!escala <valor>`
	- Registra sua escala com número maior ou igual a zero
- `!duelo @usuario1 @usuario2 ...` ou `!duelo ID1 ID2 ...`
	- Define o duelo ativo da guild para aplicar bônus de escala em rolagens Fate
- `!fimduelo`
	- Encerra o duelo ativo da guild e remove seus participantes
- `!luta`
	- Entra (ou move) para seu canal de voz e inicia a playlist de luta
- `!adm @usuario` ou `!adm ID` (admin)
	- Adiciona novo admin
- `!teste @usuario` ou `!teste ID` (admin)
	- Ativa/desativa modo de teste para o alvo (permite `max`/`min` em `r 4df`)
- `!max @usuario` ou `!max ID` (admin)
	- Força o próximo `4df` simples do alvo para `+4`
- `!min @usuario` ou `!min ID` (admin)
	- Força o próximo `4df` simples do alvo para `-4`
- `!ban @usuario` ou `!ban ID` (admin)
	- Bloqueia usuário para rolagens/comandos de rolagem
- `!desbanir @usuario` ou `!desbanir ID` (admin)
	- Remove bloqueio

---

## Rolagens de Dados

No chat, rolagens simples e somas/subtrações de dados podem ser enviadas sem prefixo.
Quando a expressão tiver conta mais complexa, como multiplicação, divisão ou parênteses, use `r`.

Exemplos:

- `d20+5`
- `4df atacar`
- `1d20+2d6`
- `r d20+5`
- `r 4df atacar`
- `r 1d20+4df+3d6`
- `r 5*4/1d20+14`

### Dados comuns

Formato:

```text
[quantidade]d[lados][modificadores] [texto opcional]
```

Exemplos:

- `d20`
- `2d6+3`
- `3d10-1 ataque pesado`
- `1d20+2d6`

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
- `4df vantagem terreno alto`
- `4df cv terreno alto`
- `r 23*73/4df+87`

### Regra especial para `4df`

Quando for uma rolagem simples de `4df`, informar a ação é **opcional**. Se quiser, você ainda pode usar:

- `Atacar`
- `Defender`
- `Criar Vantagem` também aceita `vantagem`, `criar` e `cv`
- `Superar`

Em expressões matemáticas maiores, como `r 1d20+4df+3d6` ou `r 23*73/4df+87`, o `4df` é tratado como um termo numérico normal da expressão.

Quando o modo `!invencivel` está ativo, qualquer resultado `++++` em `4df` envia o GIF do Invencível e toca `invencivel.ogg`.

Em rolagens Fate simples, se houver um duelo ativo via `!duelo`, o bot compara a escala registrada dos participantes e soma `+2` no total para cada ponto de diferença acima da menor escala do duelo.

Se quiser ignorar esse bônus em uma rolagem específica de Fate, adicione `noscale` ao final da rolagem.

Exemplos:

- `!escala 2`
- `!duelo @jogador1 @jogador2 @jogador3`
- `!fimduelo`
- `4df atacar noscale`
- `r 4df defender noscale`

---

## Modo de teste (`!teste`)

Usuários com modo de teste ativo podem forçar resultado em `4df`:

- `max` → força `++++`
- `min` → força `----`

Exemplo:

```text
r 4df atacar max
```

---

## Forçagem admin do próximo `4df`

Admins podem agendar uma única forcagem para a próxima rolagem simples de `4df` de um usuário:

- `!max 123456789012345678`
- `!min 123456789012345678`
- `!max @usuario`
- `!min @usuario`

Essa forcagem fica pendente até o alvo fazer um `4df` simples e é consumida automaticamente na rolagem.

---

## Console local no terminal

Enquanto o bot estiver rodando no terminal do seu PC, você também pode digitar comandos direto nele para interferir no bot sem mandar mensagem no Discord:

- `!max 123456789012345678`
- `!min 123456789012345678`
- `!status`
- `!help`

O console local controla as mesmas forcagens pendentes usadas pelos comandos de admin do Discord.

---

## Cálculo matemático (`r`)

Formato:

```text
r <expressão>
```

Exemplos:

- `r 2 + 3 * 4`
- `r (10 + 5) * 2 - 3/4`
- `r 1d20+4df+3d6`
- `r 5*4/1d20+14`

O cálculo é validado via AST (mais seguro que `eval`) e pode misturar números com múltiplos tipos de dado. Sem `r`, o bot fica só nas rolagens simples e nas somas/subtrações de dados.

Qualquer tentativa de usar quantidade de dados ou número de lados acima de `1000000` faz o bot recusar a rolagem e adicionar o autor à lista de banidos.

---

## Permissões e listas internas

- Admins iniciais e usuários banidos são definidos no código (`ids_admin`, `usuarios_banidos`).
- O comando `!adm` permite expandir a lista de admins em runtime.
- As listas atuais são em memória (reinício do bot perde alterações feitas em runtime).

---

## Execução

Depois de criar o `.env.local` ou configurar a variável manualmente:

```bash
python Bot.py
```

Ao iniciar, o bot sincroniza os comandos slash automaticamente.

