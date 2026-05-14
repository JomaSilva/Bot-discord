import discord
from discord.ext import commands
from discord import app_commands
import random
import re
import ast
import os
from pathlib import Path
import ssl
import glob
import asyncio
import shutil
import sys
import math
from typing import Any, cast
import aiohttp.connector
import certifi
import yt_dlp

# -----------------------------------------------------------------------------
# VISÃO GERAL DO BOT (handoff para outro dev)
#
# 1) Entradas suportadas:
#    - Comandos de texto: !luta, !tocar, !skipar, !parar, !tema, !invencivel, !escala, !duelo, !fimduelo, !ban, !desbanir, !adm, !teste, !max, !min
#    - Comandos slash: /roll, /tema, /ban, /desbanir
#    - Mensagens de rolagem: dN e df (ex.: d20+3, 4df atacar)
#
# 2) Fluxo principal:
#    - `on_message` lida com comandos de texto e gatilhos regex.
#    - `/roll` reutiliza `processar_rolagem_dados` para evitar divergência de regra.
#    - Áudio é centralizado nas funções `tocar_*` e no controle de fila de `!luta`.
#
# 3) Pontos críticos:
#    - Estado de voz por guild fica em dicionários globais (filas/retomada/faixa atual).
#    - Interrupção por tema/kokusen usa sinalizador para pausar autoavanço da playlist.
#    - Ao terminar tema/kokusen, a faixa interrompida volta para o início da fila.
#
# 4) Observação técnica:
#    - Token do bot deve ser fornecido por variável de ambiente (`DISCORD_BOT_TOKEN`).
# -----------------------------------------------------------------------------

def carregar_arquivo_env(caminho_env: Path) -> None:
    for linha in caminho_env.read_text(encoding='utf-8').splitlines():
        linha_limpa = linha.strip()
        if not linha_limpa or linha_limpa.startswith('#') or '=' not in linha_limpa:
            continue

        chave, valor = linha_limpa.split('=', 1)
        chave = chave.strip().removeprefix('export ').strip()
        valor = valor.strip()

        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in {'"', "'"}:
            valor = valor[1:-1]

        if chave:
            os.environ.setdefault(chave, valor)

def carregar_variaveis_ambiente() -> None:
    caminhos_verificados = set()
    bases_busca = (Path.cwd().resolve(), Path(__file__).resolve().parent)

    for base in bases_busca:
        for diretorio in (base, *base.parents):
            for nome_arquivo in ('.env.local', '.env'):
                caminho_env = diretorio / nome_arquivo
                if caminho_env in caminhos_verificados:
                    continue
                caminhos_verificados.add(caminho_env)

                if caminho_env.is_file():
                    carregar_arquivo_env(caminho_env)
                    return

# Mapeamento dos resultados dos dados Fate para símbolos visuais.
fate_dice = {-1: '-', 0:'0', 1:'+'}

# Listas/estruturas de controle de usuários e permissões.
usuarios_banidos = [190954369917779968]
usuarios_teste = set()
id_jandei = 332954449918165003
ids_admin = [316323635470270475]

# Limite máximo aceito para quantidade de dados e lados numéricos.
LIMITE_VALOR_DADO = 1_000
LIMITE_VALOR_DADO_TEXTO = str(LIMITE_VALOR_DADO)
PADRAO_TERMO_DADO = re.compile(r'(?<![\w])(\d*)d(f|\d+)(?![\w])', re.IGNORECASE)
ACOES_FATE_VALIDAS = {
    'atacar': 'Atacar',
    'atk': 'Atacar',
    'defender': 'Defender',
    'def': 'Defender',
    'criar vantagem': 'Criar Vantagem',
    'vantagem': 'Criar Vantagem',
    'criar': 'Criar Vantagem',
    'cv': 'Criar Vantagem',
    'superar': 'Superar',
    'sup': 'Superar',
}
PADRAO_ACAO_FATE = re.compile(
    r'^('
    + '|'.join(
        re.escape(acao).replace(r'\ ', r'\s+')
        for acao in sorted(ACOES_FATE_VALIDAS, key=len, reverse=True)
    )
    + r')(?:\s+(.*))?$',
    re.IGNORECASE,
)

# Configuração base do cliente Discord e comandos slash.
# No macOS, algumas instalações novas de Python não expõem uma cadeia CA confiável ao aiohttp.
_ssl_context_verificado = ssl.create_default_context(cafile=certifi.where())
_ssl_context_verificado.set_alpn_protocols(("http/1.1",))
aiohttp.connector._SSL_CONTEXT_VERIFIED = _ssl_context_verificado
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
comandos_sincronizados = False

# URL fixa da playlist do comando !luta.
luta_playlist_url = 'https://music.youtube.com/playlist?list=PLWJ_MRpZU6ocB4WUwm30W3gS5GnnkaG9n&si=BHeX7STFUBXr1SSm'

# Estado de áudio por servidor (guild) para controle da playlist.
filas_luta = {}
faixa_atual_luta = {}
retomar_faixa_luta = {}
interromper_auto_avanco_luta = set()
filas_avulsas = {}
faixa_atual_avulsa = {}
modo_reproducao = {}
canal_texto_reproducao = {}

# Tema personalizado de cada usuário para ativação no ++++.
temas_usuario = {}

# Escalas registradas por usuário e duelo ativo por guild.
escalas_usuario = {}
duelos_ativos = {}

# Forçagens administrativas para a próxima rolagem simples de 4df por usuário.
forcagens_proximo_4df = {}
tarefa_console_local = None
tema_mais_quatro_atual = 'jujutsu'

TEMAS_MAIS_QUATRO = {
    'jujutsu': {
        'nome': 'Jujutsu',
        'frase_efeito': 'Black Flash!',
        'gif_efeito': 'https://tenor.com/view/jjk-jjk-s2-jjk-season-2-jujutsu-kaisen-jujutsu-kaisen-s2-gif-7964484372484357392',
        'audio_local': 'kokusen.ogg',
        'acao_necessaria': 'Atacar',
    },
    'invencivel': {
        'nome': 'Invencível',
        'frase_efeito': 'ele é...',
        'gif_efeito': 'https://tenor.com/view/invulnerable-gif-22484955',
        'audio_local': 'invencivel.ogg',
        'acao_necessaria': None,
    },
}


def obter_config_tema_mais_quatro():
    # Devolve a configuração ativa do efeito especial de `++++`.
    return TEMAS_MAIS_QUATRO[tema_mais_quatro_atual]


def ativar_tema_invencivel():
    # Alterna o efeito especial de `++++` para o modo Invencível.
    global tema_mais_quatro_atual
    if tema_mais_quatro_atual == 'invencivel':
        return False

    tema_mais_quatro_atual = 'invencivel'
    return True


def deve_ativar_efeito_mais_quatro(teve_mais_quatro, acao_fate):
    # Decide se o efeito especial do `++++` deve disparar no tema ativo.
    if not teve_mais_quatro:
        return False

    acao_necessaria = obter_config_tema_mais_quatro()['acao_necessaria']
    return acao_necessaria is None or acao_fate == acao_necessaria


def montar_mensagem_rolagem_fate(usuario_mention, dados_organizados, mod_display, total_fate, escala, acao_fate, texto_adicional, bonus_duelo=0, participante_duelo=False, ignorou_bonus_escala=False):
    # Centraliza a linha principal exibida para rolagens Fate.
    partes = [
        f'{usuario_mention} rolled: [{dados_organizados}]{mod_display} (**Total: {formatar_numero_resultado(total_fate)}**)',
        f'Escala: **{escala}**',
    ]

    if participante_duelo:
        if ignorou_bonus_escala:
            partes.append('Bônus de Escala: **ignorado com `noscale`**')
        else:
            partes.append(f'Bônus de Escala: **{formatar_numero_com_sinal(bonus_duelo)}**')

    if acao_fate:
        partes.append(f'Ação: **{acao_fate}**')
    if texto_adicional:
        partes.append(texto_adicional)

    return ' | '.join(partes)


def cancelar_playlist_luta(guild_id):
    # Limpa completamente o estado da playlist de luta para uma guild.
    filas_luta.pop(guild_id, None)
    faixa_atual_luta.pop(guild_id, None)
    retomar_faixa_luta.pop(guild_id, None)
    interromper_auto_avanco_luta.discard(guild_id)


def cancelar_fila_avulsa(guild_id):
    # Limpa completamente a fila temporária de músicas avulsas para uma guild.
    filas_avulsas.pop(guild_id, None)
    faixa_atual_avulsa.pop(guild_id, None)
    if modo_reproducao.get(guild_id) == 'avulsa':
        modo_reproducao.pop(guild_id, None)
    canal_texto_reproducao.pop(guild_id, None)


def resetar_estado_audio(guild_id):
    # Reseta todo o estado de áudio da guild, incluindo fila fixa, fila temporária e retomadas.
    cancelar_playlist_luta(guild_id)
    cancelar_fila_avulsa(guild_id)
    modo_reproducao.pop(guild_id, None)
    canal_texto_reproducao.pop(guild_id, None)


def preparar_interrupcao_playlist(guild_id, voice_client):
    # Marca a faixa atual para retomada quando tema/kokusen interrompem a playlist.
    if voice_client is None:
        return False

    faixa_atual = faixa_atual_luta.get(guild_id)
    if faixa_atual and voice_client.is_playing():
        retomar_faixa_luta[guild_id] = faixa_atual
        interromper_auto_avanco_luta.add(guild_id)
        return True

    return False


async def conectar_ao_canal_voz(guild, canal_voz):
    # Centraliza a conexão de voz para aplicar timeout/reconnect e mensagens mais úteis.
    if guild is None:
        raise RuntimeError('Guild inválida para conexão de voz.')

    voice_client = guild.voice_client
    try:
        if voice_client is None:
            return await canal_voz.connect(timeout=30.0, reconnect=True, self_deaf=True)

        if voice_client.channel != canal_voz:
            await voice_client.move_to(canal_voz)

        return voice_client
    except asyncio.TimeoutError as erro:
        raise RuntimeError(
            'Tempo esgotado ao conectar no canal de voz. Isso normalmente indica bloqueio de rede, firewall ou problema temporário do endpoint de voz do Discord.'
        ) from erro
    except discord.ClientException as erro:
        raise RuntimeError(f'Falha do cliente de voz do Discord: {erro}') from erro
    except Exception as erro:
        if getattr(erro, 'code', None) == 4017:
            raise RuntimeError(
                'O Discord exigiu o protocolo de voz E2EE/DAVE neste canal (código 4017). A biblioteca atual do bot não suporta esse protocolo, então a conexão de voz não pode ser concluída aqui.'
            ) from erro
        raise RuntimeError(f'Falha ao estabelecer conexão de voz: {erro}') from erro


async def retomar_playlist_interrompida(guild_id, canal_texto):
    # Retoma a faixa interrompida após o término do tema/kokusen.
    # Remove estado temporário de retomada; se não houver nada salvo, não faz nada.
    faixa_retomar = retomar_faixa_luta.pop(guild_id, None)
    interromper_auto_avanco_luta.discard(guild_id)
    if not faixa_retomar:
        return

    fila = filas_luta.setdefault(guild_id, [])
    fila.insert(0, faixa_retomar)

    guild = canal_texto.guild
    if guild is None:
        return

    voice_client = guild.voice_client
    if voice_client is None or voice_client.is_playing():
        return

    await tocar_proxima_da_fila(guild_id, canal_texto)


async def tocar_proxima_da_fila_avulsa(guild_id, canal_texto):
    # Toca a próxima faixa da fila temporária e, ao terminar, retoma !luta se houver interrupção pendente.
    fila = filas_avulsas.get(guild_id)
    if not fila:
        faixa_atual_avulsa.pop(guild_id, None)
        modo_reproducao.pop(guild_id, None)
        if retomar_faixa_luta.get(guild_id):
            await retomar_playlist_interrompida(guild_id, canal_texto)
        return

    voice_client = canal_texto.guild.voice_client
    if voice_client is None or voice_client.is_playing():
        return

    proxima_url = fila.pop(0)
    faixa_atual_avulsa[guild_id] = proxima_url
    modo_reproducao[guild_id] = 'avulsa'
    canal_texto_reproducao[guild_id] = canal_texto

    try:
        stream_url, titulo = await asyncio.to_thread(_extrair_stream_audio, proxima_url)
        if not stream_url:
            await canal_texto.send('Não consegui obter o áudio da música solicitada.')
            faixa_atual_avulsa.pop(guild_id, None)
            if fila:
                await tocar_proxima_da_fila_avulsa(guild_id, canal_texto)
            elif retomar_faixa_luta.get(guild_id):
                await retomar_playlist_interrompida(guild_id, canal_texto)
            return

        def ao_terminar(erro):
            if erro:
                print(f'Erro ao tocar música avulsa: {erro}')
            faixa_atual_avulsa.pop(guild_id, None)
            if filas_avulsas.get(guild_id):
                client.loop.call_soon_threadsafe(asyncio.create_task, tocar_proxima_da_fila_avulsa(guild_id, canal_texto))
                return
            modo_reproducao.pop(guild_id, None)
            if retomar_faixa_luta.get(guild_id):
                client.loop.call_soon_threadsafe(asyncio.create_task, retomar_playlist_interrompida(guild_id, canal_texto))

        voice_client.play(
            discord.FFmpegPCMAudio(
                stream_url,
                executable=obter_ffmpeg_executavel(),
                before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                options='-vn'
            ),
            after=ao_terminar
        )
        await canal_texto.send(f'Tocando agora: **{titulo}**')
    except Exception as erro:
        await canal_texto.send(f'Falha ao tocar música solicitada: `{erro}`')
        faixa_atual_avulsa.pop(guild_id, None)
        if filas_avulsas.get(guild_id) and not voice_client.is_playing():
            await tocar_proxima_da_fila_avulsa(guild_id, canal_texto)
        elif retomar_faixa_luta.get(guild_id):
            await retomar_playlist_interrompida(guild_id, canal_texto)


def obter_ffmpeg_executavel():
    # Busca o executável do ffmpeg por variável de ambiente, PATH e caminhos comuns do sistema.
    for nome_variavel in ('FFMPEG_EXECUTABLE', 'FFMPEG_PATH'):
        caminho_configurado = os.environ.get(nome_variavel, '').strip()
        if caminho_configurado and os.path.isfile(caminho_configurado):
            return caminho_configurado

    caminho_no_path = shutil.which('ffmpeg')
    if caminho_no_path:
        return caminho_no_path

    caminhos = [
        '/opt/homebrew/bin/ffmpeg',
        '/usr/local/bin/ffmpeg',
        '/usr/bin/ffmpeg',
    ]

    if os.name == 'nt':
        local_app_data = os.environ.get('LOCALAPPDATA', '')
        padrao_winget = os.path.join(
            local_app_data,
            'Microsoft', 'WinGet', 'Packages',
            'Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe',
            'ffmpeg-*-full_build', 'bin', 'ffmpeg.exe'
        )
        caminhos_winget = sorted(glob.glob(padrao_winget), reverse=True)
        caminhos = caminhos_winget + [
            os.path.join(local_app_data, 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe'),
            r'C:\Program Files\FFmpeg\bin\ffmpeg.exe',
            r'C:\Program Files\ffmpeg\bin\ffmpeg.exe',
        ] + caminhos

    for caminho in caminhos:
        if caminho and os.path.isfile(caminho):
            return caminho

    raise RuntimeError(
        'FFmpeg não encontrado. Instale o FFmpeg no sistema e garanta que ele esteja no PATH ou defina FFMPEG_EXECUTABLE com o caminho completo do binário.'
    )


def calcular_expressao(expr):
    # Avalia expressão matemática de forma segura usando AST.
    def avaliar(no):
        if isinstance(no, ast.BinOp):
            esquerda = avaliar(no.left)
            direita = avaliar(no.right)

            if isinstance(no.op, ast.Add):
                return esquerda + direita
            if isinstance(no.op, ast.Sub):
                return esquerda - direita
            if isinstance(no.op, ast.Mult):
                return esquerda * direita
            if isinstance(no.op, ast.Div):
                return esquerda / direita
            if isinstance(no.op, ast.FloorDiv):
                return esquerda // direita
            if isinstance(no.op, ast.Mod):
                return esquerda % direita
            if isinstance(no.op, ast.Pow):
                return esquerda ** direita
            raise ValueError('Operador não permitido.')

        if isinstance(no, ast.UnaryOp):
            valor = avaliar(no.operand)
            if isinstance(no.op, ast.UAdd):
                return +valor
            if isinstance(no.op, ast.USub):
                return -valor
            raise ValueError('Operador unário não permitido.')

        if isinstance(no, ast.Constant) and isinstance(no.value, (int, float)):
            return no.value

        raise ValueError('Expressão inválida.')

    arvore = ast.parse(expr, mode='eval')
    return avaliar(arvore.body)


def formatar_numero_resultado(valor):
    # Normaliza números para evitar floats longos demais nas respostas do bot.
    if isinstance(valor, float):
        if valor.is_integer():
            return str(int(valor))
        return f'{valor:.6f}'.rstrip('0').rstrip('.')
    return str(valor)


def formatar_numero_com_sinal(valor):
    # Formata números com sinal explícito quando positivos.
    texto = formatar_numero_resultado(valor)
    if valor > 0:
        return f'+{texto}'
    return texto


def interpretar_valor_escala(texto):
    # Aceita escala numérica não negativa usando ponto ou vírgula decimal.
    texto_limpo = ' '.join((texto or '').strip().split()).replace(',', '.')
    if not texto_limpo:
        raise ValueError('Use `!escala <valor>` com um número maior ou igual a zero.')

    valor = float(texto_limpo)
    if not math.isfinite(valor) or valor < 0:
        raise ValueError('A escala precisa ser um número maior ou igual a zero.')

    if valor.is_integer():
        return int(valor)
    return valor


def mensagens_usuario_banido(usuario_mention):
    # Centraliza a resposta enviada para usuários bloqueados.
    return [
        f'Desculpe {usuario_mention}, eu não escuto furries',
        'mas caso queira falar comigo, resolva esta simples questao de matemática:',
        'https://media.discordapp.net/attachments/1190477143763853393/1471694458629128266/image.png?ex=698fddc5&is=698e8c45&hm=f441a1748e0751a108d3d4adf454c036d63e1f650be231bdf31d4db38340f084&=&format=webp&quality=lossless'
    ]


def mensagens_ban_por_valor_dado_excessivo(usuario_mention, termo):
    # Informa que o limite permitido foi ultrapassado e que o usuário foi bloqueado.
    return [
        f'{usuario_mention} valores de dado acima de `{LIMITE_VALOR_DADO}` não são permitidos.',
        f'Você foi adicionado à lista de banidos por tentar usar `{termo}`.'
    ]


def valor_dado_excede_limite(valor_texto):
    # Compara o valor textual com o limite sem depender de int para números enormes.
    if not valor_texto:
        return False

    valor_normalizado = valor_texto.lstrip('0') or '0'
    if len(valor_normalizado) != len(LIMITE_VALOR_DADO_TEXTO):
        return len(valor_normalizado) > len(LIMITE_VALOR_DADO_TEXTO)

    return valor_normalizado > LIMITE_VALOR_DADO_TEXTO


def validar_limite_termos_dado(conteudo, usuario_id, usuario_mention):
    # Bloqueia automaticamente quem tentar usar quantidade/lados acima do limite.
    for match in PADRAO_TERMO_DADO.finditer(conteudo):
        termo = match.group(0)
        quantidade_texto = match.group(1)
        if valor_dado_excede_limite(quantidade_texto):
            if usuario_id not in usuarios_banidos:
                usuarios_banidos.append(usuario_id)
            return mensagens_ban_por_valor_dado_excessivo(usuario_mention, termo)

        identificador = match.group(2).lower()
        if identificador == 'f':
            continue

        if valor_dado_excede_limite(identificador):
            if usuario_id not in usuarios_banidos:
                usuarios_banidos.append(usuario_id)
            return mensagens_ban_por_valor_dado_excessivo(usuario_mention, termo)

    return None


def rolar_termo_dados_em_expressao(termo):
    # Resolve um único termo de dado usado dentro de uma expressão matemática maior.
    match = re.fullmatch(r'(\d*)d(f|\d+)', termo.strip(), re.IGNORECASE)
    if not match:
        raise ValueError('Termo de dado inválido.')

    quantidade = int(match.group(1)) if match.group(1) else 1
    if quantidade <= 0:
        raise ValueError('A quantidade de dados precisa ser positiva.')

    identificador = match.group(2).lower()
    if identificador == 'f':
        rolls = [random.randint(-1, 1) for _ in range(quantidade)]
        simbolos = [fate_dice[item] for item in rolls]
        total = sum(rolls)
        return total, f'{termo}=[{", ".join(simbolos)}] (Total: {formatar_numero_resultado(total)})'

    lados = int(identificador)
    if lados <= 0:
        raise ValueError('A quantidade de lados precisa ser positiva.')

    rolls = [random.randint(1, lados) for _ in range(quantidade)]
    total = sum(rolls)
    return total, f'{termo}={rolls} (Total: {formatar_numero_resultado(total)})'


def processar_expressao_com_dados(conteudo, usuario_mention, prefixo='r '):
    # Permite misturar matemática e múltiplos tipos de dados em uma única expressão.
    detalhes_dados = []

    def substituir(match):
        termo = match.group(0)
        total, detalhes = rolar_termo_dados_em_expressao(termo)
        detalhes_dados.append(detalhes)
        return str(total)

    expressao_substituida = PADRAO_TERMO_DADO.sub(substituir, conteudo)
    resultado = calcular_expressao(expressao_substituida)
    resultado_formatado = formatar_numero_resultado(resultado)

    mensagem = f'{usuario_mention} `{prefixo}{conteudo}` = **{resultado_formatado}**'
    if detalhes_dados:
    
        mensagem += f' | Substituída: `{expressao_substituida}` | Dados: {"; ".join(detalhes_dados)}'

    return [mensagem]


def eh_expressao_aditiva_de_dados(conteudo):
    # Detecta expressões formadas apenas por dados, inteiros e operadores +/-, sem exigir prefixo `r`.
    texto_limpo = conteudo.strip()
    if not texto_limpo:
        return False

    match_expressao = re.fullmatch(
        r'[+-]?\s*(?:\d*d(?:f|\d+)|\d+)(?:\s*[+-]\s*(?:\d*d(?:f|\d+)|\d+))*\s*',
        texto_limpo,
        re.IGNORECASE
    )
    if not match_expressao:
        return False

    return re.search(r'\d*d(?:f|\d+)', texto_limpo, re.IGNORECASE) is not None


def eh_rolagem_simples(conteudo):
    # Detecta rolagens legadas de um único bloco, como `d20+5` ou `4df atacar`.
    return bool(
        re.match(r'^(\d*)d(\d+)((?:\s*[+-]\s*\d+)*)(?:\s+(.*))?$', conteudo, re.IGNORECASE)
        or re.match(r'^(\d*)df((?:\s*[+-]\s*\d+)*)(?:\s+(.*))?$', conteudo, re.IGNORECASE)
    )


def extrair_metadados_rolagem(conteudo, mensagens):
    # Identifica se a rolagem simples permite efeitos especiais como o áudio do ++++.
    match_fate = re.match(r'^(\d*)df((?:\s*[+-]\s*\d+)*)(?:\s+(.*))?$', conteudo.strip(), re.IGNORECASE)
    acao_fate = None
    if match_fate and (int(match_fate.group(1)) if match_fate.group(1) else 1) == 4:
        _ignorar_bonus_escala, texto_tratado = extrair_flag_noscale(match_fate.group(3))
        acao_fate, _complemento = extrair_acao_e_complemento_fate(texto_tratado)

    return {
        'acao_fate': acao_fate,
        'teve_mais_quatro': '+, +, +, +' in ' '.join(mensagens),
    }


def processar_entrada_rolagem(conteudo, usuario_id, usuario_mention, prefixo='r ', permitir_expressao_com_dados=True, guild_id=None):
    # Decide entre rolagem simples (d20/4df) e expressão mista com dados embutidos.
    conteudo = conteudo.strip()
    if not conteudo:
        return [f'{usuario_mention} use: `r d20+5`, `r 4df atacar` ou `r 1d20+4df+3d6`'], None

    if usuario_id in usuarios_banidos:
        return mensagens_usuario_banido(usuario_mention), None

    mensagens_limite = validar_limite_termos_dado(conteudo, usuario_id, usuario_mention)
    if mensagens_limite:
        return mensagens_limite, None

    mensagens = processar_rolagem_dados(conteudo, usuario_id, usuario_mention, guild_id=guild_id)
    if mensagens:
        return mensagens, extrair_metadados_rolagem(conteudo, mensagens)

    if not permitir_expressao_com_dados:
        if not eh_expressao_aditiva_de_dados(conteudo):
            return None, None

    try:
        mensagens = processar_expressao_com_dados(conteudo, usuario_mention, prefixo=prefixo)
        return mensagens, {'acao_fate': None, 'teve_mais_quatro': False}
    except ZeroDivisionError:
        return [f'{usuario_mention} não dá para dividir por zero nessa expressão.'], None
    except Exception:
        return None, None


def escala_adjetivos_jjk(total):
    # Converte o total Fate em uma escala adjetiva inspirada em JJK.
    if total >= 9:
        return 'Inominável'
    if total >= 8:
        return 'Lendário'
    if total >= 7:
        return 'Épico'
    if total >= 6:
        return 'Fantástico'
    if total >= 5:
        return 'Excepcional'
    if total >= 4:
        return 'Ótimo'
    if total >= 3:
        return 'Bom'
    if total >= 2:
        return 'Razoável'
    if total >= 1:
        return 'Regular'
    if total >= 0:
        return 'Medíocre'
    if total >= -1:
        return 'Ruim'
    if total >= -2:
        return 'Terrível'
    if total >= -3:
        return 'Catastrófico'
    return 'Horrível'


def normalizar_acao_fate(texto):
    # Normaliza ações Fate aceitas para um formato padronizado.
    if not texto:
        return None

    texto_limpo = ' '.join(texto.lower().strip().split())
    return ACOES_FATE_VALIDAS.get(texto_limpo)


def extrair_acao_e_complemento_fate(texto):
    # Extrai ação Fate obrigatória e texto complementar opcional.
    if not texto:
        return None, None

    texto_limpo = ' '.join(texto.strip().split())
    match_acao = PADRAO_ACAO_FATE.match(texto_limpo)
    if not match_acao:
        return None, None

    acao_bruta = ' '.join(match_acao.group(1).lower().split())
    acao_fate = normalizar_acao_fate(acao_bruta)
    complemento = (match_acao.group(2) or '').strip()
    return acao_fate, complemento


def extrair_forcagem_teste(texto):
    # Extrai token de teste (max/min) e devolve o restante da mensagem.
    if not texto:
        return None, texto

    texto_limpo = ' '.join(texto.strip().split())
    if not texto_limpo:
        return None, ''

    tokens = texto_limpo.split(' ')
    for indice, token in enumerate(tokens):
        token_normalizado = token.lower().strip()
        if token_normalizado in ('max', 'min'):
            restante_tokens = tokens[:indice] + tokens[indice + 1:]
            restante = ' '.join(restante_tokens).strip()
            return token_normalizado, restante

    return None, texto_limpo


def extrair_flag_noscale(texto):
    # Remove o token `noscale` do texto adicional de rolagens Fate.
    if not texto:
        return False, ''

    tokens = ' '.join(texto.strip().split()).split(' ')
    ignorar_escala = False
    tokens_restantes = []
    for token in tokens:
        if token.lower().strip() == 'noscale':
            ignorar_escala = True
            continue
        tokens_restantes.append(token)

    return ignorar_escala, ' '.join(tokens_restantes).strip()


def extrair_id_de_texto(texto):
    # Extrai um ID numérico de Discord de um texto livre.
    if not texto:
        return None
    id_match = re.search(r'\d{15,20}', texto)
    if not id_match:
        return None
    return int(id_match.group(0))


def extrair_ids_de_texto(texto):
    # Extrai múltiplos IDs numéricos de Discord de um texto livre.
    if not texto:
        return []
    return [int(item) for item in re.findall(r'\d{15,20}', texto)]


def extrair_id_alvo_texto(message, argumento):
    # Resolve alvo de comando por menção ou ID em comando de texto.
    if message.mentions:
        return message.mentions[0].id
    return extrair_id_de_texto(argumento)


def extrair_ids_alvos_texto(message, argumento):
    # Resolve múltiplos alvos por menções e/ou IDs em comando de texto.
    ids_alvo = [mencionado.id for mencionado in message.mentions]
    ids_alvo.extend(extrair_ids_de_texto(argumento))
    return list(dict.fromkeys(ids_alvo))


def extrair_id_alvo_slash(usuario, usuario_id):
    # Resolve alvo de comando slash por usuário selecionado ou ID informado.
    if usuario is not None:
        return usuario.id
    return extrair_id_de_texto(usuario_id)


def obter_escala_usuario(usuario_id):
    # Escala padrão é zero quando o usuário ainda não configurou valor.
    return escalas_usuario.get(usuario_id, 0)


def calcular_bonus_duelo_fate(guild_id, usuario_id, ignorar_bonus_escala=False):
    # Calcula o bônus Fate vindo da diferença para a menor escala do duelo ativo.
    if guild_id is None:
        return 0, False

    participantes = duelos_ativos.get(guild_id)
    if not participantes or len(participantes) < 2 or usuario_id not in participantes:
        return 0, False

    if ignorar_bonus_escala:
        return 0, True

    menor_escala = min(obter_escala_usuario(participante_id) for participante_id in participantes)
    bonus = (obter_escala_usuario(usuario_id) - menor_escala) * 2
    return bonus, True


def descrever_duelo_ativo(guild_id):
    # Resume o duelo ativo da guild com as escalas e bônus atuais.
    participantes = duelos_ativos.get(guild_id)
    if not participantes or len(participantes) < 2:
        return 'Nenhum duelo ativo nesta guild.'

    partes = []
    for participante_id in participantes:
        escala = obter_escala_usuario(participante_id)
        bonus, _participa = calcular_bonus_duelo_fate(guild_id, participante_id)
        partes.append(
            f'`{participante_id}`: escala `{formatar_numero_resultado(escala)}` / bônus `{formatar_numero_com_sinal(bonus)}`'
        )

    return 'Duelo ativo: ' + ', '.join(partes)


def eh_admin(usuario_id):
    # Verifica se um usuário está na lista de administradores.
    return usuario_id in ids_admin


def descrever_forcagem_fate(tipo_forcagem):
    # Converte o tipo interno de forcagem em descrição legível.
    if tipo_forcagem == 'max':
        return '+4 (++++)'
    if tipo_forcagem == 'min':
        return '-4 (----)'
    raise ValueError('Tipo de forcagem Fate inválido.')


def registrar_forcagem_proximo_4df(usuario_id, tipo_forcagem):
    # Agenda uma única forcagem para a próxima rolagem simples de 4df do usuário.
    if tipo_forcagem not in ('max', 'min'):
        raise ValueError('Tipo de forcagem Fate inválido.')

    forcagens_proximo_4df[usuario_id] = tipo_forcagem
    return f'Próximo `4df` de `{usuario_id}` será forçado para {descrever_forcagem_fate(tipo_forcagem)}.'


def consumir_forcagem_proximo_4df(usuario_id):
    # Usa e remove a forcagem pendente do usuário, se existir.
    return forcagens_proximo_4df.pop(usuario_id, None)


def resumir_forcagens_pendentes():
    # Resume as forcagens administrativas ainda não consumidas.
    if not forcagens_proximo_4df:
        return 'Nenhuma forcagem pendente.'

    partes = [
        f'{usuario_id}: {descrever_forcagem_fate(tipo_forcagem)}'
        for usuario_id, tipo_forcagem in sorted(forcagens_proximo_4df.items())
    ]
    return 'Forcagens pendentes: ' + ', '.join(partes)


def interpretar_comando_terminal_local(linha):
    # Interpreta comandos administrativos digitados no terminal local do processo.
    linha_limpa = ' '.join((linha or '').strip().split())
    if not linha_limpa:
        return None

    if re.fullmatch(r'!?help', linha_limpa, re.IGNORECASE):
        return 'Comandos locais: !max ID, !min ID, !status, !help.'

    if re.fullmatch(r'!?status', linha_limpa, re.IGNORECASE):
        return resumir_forcagens_pendentes()

    match_forcagem = re.fullmatch(r'!?(max|min)(?:\s+(.*))?', linha_limpa, re.IGNORECASE)
    if match_forcagem:
        tipo_forcagem = match_forcagem.group(1).lower()
        alvo_id = extrair_id_de_texto(match_forcagem.group(2) or '')
        if not alvo_id:
            return f'Use `!{tipo_forcagem} ID` no terminal local.'
        return registrar_forcagem_proximo_4df(alvo_id, tipo_forcagem)

    return 'Comando local inválido. Use !help para ver os comandos disponíveis.'


async def console_terminal_local():
    # Mantém um loop de leitura do terminal local sem bloquear o cliente do Discord.
    print('Console local ativo. Use !max ID, !min ID, !status ou !help.')

    while not client.is_closed():
        try:
            linha = await asyncio.to_thread(input, 'bot> ')
        except EOFError:
            print('Console local encerrado: entrada padrão indisponível.')
            return
        except KeyboardInterrupt:
            print('Console local interrompido.')
            return
        except Exception as erro:
            print(f'Falha ao ler comando do terminal local: {erro}')
            return

        resposta = interpretar_comando_terminal_local(linha)
        if resposta:
            print(resposta)


async def tocar_audio_local_no_voz(usuario, canal_texto, nome_arquivo):
    # Toca um arquivo de áudio local do projeto no canal de voz do usuário.
    try:
        guild = canal_texto.guild
        if guild is None:
            return

        if isinstance(usuario, discord.Member):
            membro = usuario
        else:
            membro = guild.get_member(usuario.id)
            if membro is None:
                return

        if membro.voice is None or membro.voice.channel is None:
            return

        canal_voz = membro.voice.channel
        voice_client = guild.voice_client

        # Se a playlist estiver tocando, prepara retomada depois que o áudio especial terminar.
        interrompeu_playlist = preparar_interrupcao_playlist(guild.id, voice_client)

        voice_client = await conectar_ao_canal_voz(guild, canal_voz)

        caminho_audio = os.path.join(os.path.dirname(__file__), nome_arquivo)
        if not os.path.isfile(caminho_audio):
            await canal_texto.send(f'Arquivo `{nome_arquivo}` não encontrado.')
            return

        if voice_client.is_playing():
            # Stop dispara o `after` da faixa atual; o bloqueio de autoavanço evita conflito.
            voice_client.stop()

        def ao_terminar(erro):
            if erro:
                print(f'Erro ao tocar {nome_arquivo}: {erro}')
            if interrompeu_playlist:
                client.loop.call_soon_threadsafe(asyncio.create_task, retomar_playlist_interrompida(guild.id, canal_texto))

        voice_client.play(
            discord.FFmpegPCMAudio(caminho_audio, executable=obter_ffmpeg_executavel()),
            after=ao_terminar
        )
    except Exception as erro:
        await canal_texto.send(f'Não consegui tocar `{nome_arquivo}` no canal de voz. Erro: `{erro}`')


async def tocar_kokusen_no_voz(usuario, canal_texto):
    # Toca kokusen.ogg no canal de voz do usuário.
    await tocar_audio_local_no_voz(usuario, canal_texto, 'kokusen.ogg')


async def tocar_invencivel_no_voz(usuario, canal_texto):
    # Toca invencivel.ogg no canal de voz do usuário.
    await tocar_audio_local_no_voz(usuario, canal_texto, 'invencivel.ogg')


async def tocar_audio_url_no_voz(usuario, canal_texto, audio_url):
    # Toca um áudio de URL (tema do usuário) no canal de voz do usuário.
    try:
        guild = canal_texto.guild
        if guild is None:
            return

        if isinstance(usuario, discord.Member):
            membro = usuario
        else:
            membro = guild.get_member(usuario.id)
            if membro is None:
                return

        if membro.voice is None or membro.voice.channel is None:
            return

        canal_voz = membro.voice.channel
        voice_client = guild.voice_client

        # Mesmo comportamento do kokusen: interrompe e agenda retomada da playlist.
        interrompeu_playlist = preparar_interrupcao_playlist(guild.id, voice_client)

        voice_client = await conectar_ao_canal_voz(guild, canal_voz)

        stream_url, _titulo = await asyncio.to_thread(_extrair_stream_audio, audio_url)
        if not stream_url:
            await canal_texto.send('Não consegui obter o áudio do tema configurado.')
            return

        if voice_client.is_playing():
            # Interrompe o que estiver tocando para priorizar o tema do usuário.
            voice_client.stop()

        def ao_terminar(erro):
            if erro:
                print(f'Erro ao tocar tema: {erro}')
            if interrompeu_playlist:
                client.loop.call_soon_threadsafe(asyncio.create_task, retomar_playlist_interrompida(guild.id, canal_texto))

        voice_client.play(
            discord.FFmpegPCMAudio(
                stream_url,
                executable=obter_ffmpeg_executavel(),
                before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                options='-vn'
            ),
            after=ao_terminar
        )
    except Exception as erro:
        await canal_texto.send(f'Não consegui tocar o tema no canal de voz. Erro: `{erro}`')


async def tocar_audio_ao_mais_quatro(usuario, canal_texto, acao_fate):
    # No ++++, toca o efeito configurado para o tema ativo.
    if tema_mais_quatro_atual == 'invencivel':
        await tocar_invencivel_no_voz(usuario, canal_texto)
        return

    tema_link = temas_usuario.get(usuario.id)
    if tema_link:
        await tocar_audio_url_no_voz(usuario, canal_texto, tema_link)
        return

    if acao_fate == 'Atacar':
        await tocar_kokusen_no_voz(usuario, canal_texto)


def _extrair_itens_playlist(url):
    # Extrai URLs das faixas de uma playlist usando yt-dlp.
    class _YDLLogger:
        @staticmethod
        def debug(msg):
            return

        @staticmethod
        def warning(msg):
            if 'No supported JavaScript runtime could be found' in msg:
                return
            print(f'[yt-dlp] {msg}')

        @staticmethod
        def error(msg):
            print(f'[yt-dlp] {msg}')

    player_client = ['android', 'web'] if shutil.which('node') else ['web']
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'logger': _YDLLogger(),
        'extractor_args': {'youtube': {'player_client': player_client}},
        'extract_flat': True,
        'skip_download': True,
        'noplaylist': False,
    }
    with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
        info = ydl.extract_info(url, download=False)

    entradas = []
    for entrada in info.get('entries', []):
        if not entrada:
            continue
        if entrada.get('url'):
            entradas.append(entrada.get('url'))
        elif entrada.get('id'):
            entradas.append(f"https://www.youtube.com/watch?v={entrada['id']}")
    return entradas


def _extrair_stream_audio(url):
    # Extrai URL direta de stream de áudio para reprodução no Discord.
    class _YDLLogger:
        @staticmethod
        def debug(msg):
            return

        @staticmethod
        def warning(msg):
            if 'No supported JavaScript runtime could be found' in msg:
                return
            print(f'[yt-dlp] {msg}')

        @staticmethod
        def error(msg):
            print(f'[yt-dlp] {msg}')

    player_client = ['android', 'web'] if shutil.which('node') else ['web']
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'logger': _YDLLogger(),
        'extractor_args': {'youtube': {'player_client': player_client}},
        'format': 'bestaudio/best',
        'noplaylist': True,
    }
    with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get('url'), info.get('title', 'Faixa')


async def tocar_proxima_da_fila(guild_id, canal_texto):
    # Toca a próxima faixa da fila de !luta e agenda avanço automático.
    fila = filas_luta.get(guild_id)
    if not fila:
        return

    # Enquanto tema/kokusen estiver ativo, não avança automaticamente a playlist.
    if guild_id in interromper_auto_avanco_luta:
        return

    voice_client = canal_texto.guild.voice_client
    if voice_client is None:
        return

    if voice_client.is_playing():
        return

    proxima_url = fila.pop(0)
    # Guarda referência da faixa atual para permitir retomada após interrupção.
    faixa_atual_luta[guild_id] = proxima_url
    modo_reproducao[guild_id] = 'luta'
    canal_texto_reproducao[guild_id] = canal_texto
    try:
        stream_url, titulo = await asyncio.to_thread(_extrair_stream_audio, proxima_url)
        if not stream_url:
            await canal_texto.send('Não consegui obter o áudio da próxima faixa.')
            faixa_atual_luta.pop(guild_id, None)
            if fila:
                await tocar_proxima_da_fila(guild_id, canal_texto)
            return

        def ao_terminar(erro):
            if erro:
                print(f'Erro ao tocar faixa: {erro}')
            # Se houver interrupção intencional, não autoavança aqui.
            if guild_id in interromper_auto_avanco_luta:
                return
            faixa_atual_luta.pop(guild_id, None)
            modo_reproducao.pop(guild_id, None)
            if filas_luta.get(guild_id):
                client.loop.call_soon_threadsafe(asyncio.create_task, tocar_proxima_da_fila(guild_id, canal_texto))

        voice_client.play(
            discord.FFmpegPCMAudio(
                stream_url,
                executable=obter_ffmpeg_executavel(),
                before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                options='-vn'
            ),
            after=ao_terminar
        )
        await canal_texto.send(f'Tocando agora: **{titulo}**')
    except Exception as erro:
        await canal_texto.send(f'Falha ao tocar faixa da playlist: `{erro}`')
        faixa_atual_luta.pop(guild_id, None)
        modo_reproducao.pop(guild_id, None)
        if filas_luta.get(guild_id) and not voice_client.is_playing():
            await tocar_proxima_da_fila(guild_id, canal_texto)


def processar_rolagem_dados(conteudo, usuario_id, usuario_mention, guild_id=None):
    # Processa expressões de rolagem d/df e retorna mensagens prontas para envio.
    # `match` cobre dados comuns (d20, 2d6+3 etc.).
    match = re.match(r'^(\d*)d(\d+)((?:\s*[+-]\s*\d+)*)(?:\s+(.*))?$', conteudo, re.IGNORECASE)
    # `match2` cobre Fate (df), incluindo ação obrigatória no caso de 4df.
    match2 = re.match(r'^(\d*)df((?:\s*[+-]\s*\d+)*)(?:\s+(.*))?$', conteudo, re.IGNORECASE)

    if match:
        if usuario_id in usuarios_banidos:
            return mensagens_usuario_banido(usuario_mention)

        num_dice = int(match.group(1)) if match.group(1) else 1
        sides = int(match.group(2))
        mods = match.group(3) if match.group(3) else ''
        bonus = 0
        mod_display = ''
        mods_encontrados = re.findall(r'([+-])\s*(\d+)', mods)
        if mods_encontrados:
            bonus = sum(int(valor) if sinal == '+' else -int(valor) for sinal, valor in mods_encontrados)
            mod_display = ''.join(f'{sinal}{valor}' for sinal, valor in mods_encontrados)

        texto_adicional = match.group(4) if match.group(4) else ''
        rolls = [random.randint(1, sides) for _ in range(num_dice)]
        return [f'{usuario_mention} rolled: {rolls} {mod_display} (**Total: {sum(rolls) + bonus}**) {texto_adicional}']

    if match2:
        if usuario_id in usuarios_banidos:
            return mensagens_usuario_banido(usuario_mention)

        num_dice = int(match2.group(1)) if match2.group(1) else 1
        mods = match2.group(2) if match2.group(2) else ''
        bonus = 0
        mod_display = ''
        mods_encontrados = re.findall(r'([+-])\s*(\d+)', mods)
        if mods_encontrados:
            bonus = sum(int(valor) if sinal == '+' else -int(valor) for sinal, valor in mods_encontrados)
            mod_display = ''.join(f'{sinal}{valor}' for sinal, valor in mods_encontrados)

        texto_adicional_bruto = match2.group(3)
        ignorar_bonus_escala = False
        if texto_adicional_bruto:
            ignorar_bonus_escala, texto_adicional_bruto = extrair_flag_noscale(texto_adicional_bruto)

        acao_fate = None
        complemento_fate = None
        forcagem_rolagem = None
        if num_dice == 4:
            acao_fate, complemento_fate = extrair_acao_e_complemento_fate(texto_adicional_bruto)
            if not acao_fate:
                complemento_fate = (texto_adicional_bruto or '').strip() or None
            forcagem_rolagem = consumir_forcagem_proximo_4df(usuario_id)
            if forcagem_rolagem is None and usuario_id in usuarios_teste:
                forcagem_rolagem, complemento_fate = extrair_forcagem_teste(complemento_fate)

        texto_adicional = None
        if num_dice == 4:
            if complemento_fate:
                texto_adicional = f"→ '{complemento_fate}'"
        elif texto_adicional_bruto:
            texto_adicional = f"→ '{texto_adicional_bruto}'"

        if num_dice == 4 and forcagem_rolagem == 'max':
            rolls = [1, 1, 1, 1]
        elif num_dice == 4 and forcagem_rolagem == 'min':
            rolls = [-1, -1, -1, -1]
        else:
            rolls = [random.randint(-1, 1) for _ in range(num_dice)]
        rolls_fate = [fate_dice[i] for i in rolls]
        dados_organizados = ', '.join(rolls_fate)
        bonus_duelo, participante_duelo = calcular_bonus_duelo_fate(guild_id, usuario_id, ignorar_bonus_escala)
        total_fate = sum(rolls) + bonus + bonus_duelo
        escala = escala_adjetivos_jjk(total_fate)

        mensagens = []
        mensagem_principal = montar_mensagem_rolagem_fate(
            usuario_mention,
            dados_organizados,
            mod_display,
            total_fate,
            escala,
            acao_fate,
            texto_adicional,
            bonus_duelo=bonus_duelo,
            participante_duelo=participante_duelo,
            ignorou_bonus_escala=ignorar_bonus_escala and participante_duelo,
        )

        if deve_ativar_efeito_mais_quatro(rolls_fate == ['+','+','+','+'], acao_fate):
            config_tema = obter_config_tema_mais_quatro()
            mensagens.append(config_tema['frase_efeito'])
            mensagens.append(config_tema['gif_efeito'])
            mensagens.append(mensagem_principal.replace(f'[{dados_organizados}]', f'[**{dados_organizados}**]', 1))
            return mensagens

        if rolls_fate == ['-','-','-','-']:
            mensagens.append(mensagem_principal.replace(f'[{dados_organizados}]', f'[**{dados_organizados}**]', 1))
            mensagens.append('https://cdn.discordapp.com/attachments/1264409229150785609/1451361408028639316/a5z6jq.gif?ex=698f1064&is=698dbee4&hm=a1ecc438a4c2434f9ea70349dd156d6ac2d7c5197ce7dc0b801974d462b55fb5')
            return mensagens

        return [mensagem_principal]

    return None

@client.event
async def on_ready():
    # Evento disparado quando o bot conecta; sincroniza comandos slash uma vez.
    global comandos_sincronizados, tarefa_console_local
    if not comandos_sincronizados:
        await tree.sync()
        comandos_sincronizados = True
        print('Slash commands sincronizados.')

    if sys.stdin is not None and sys.stdin.isatty() and (tarefa_console_local is None or tarefa_console_local.done()):
        tarefa_console_local = asyncio.create_task(console_terminal_local())

    print(f'We have logged in as {client.user}')


@tree.command(name='roll', description='Rola dados simples ou expressões como 1d20+4df+3d6')
@app_commands.describe(expressao='Ex: d20+5, 4df atacar banana, 1d20+4df+3d6')
async def roll_slash(interaction: discord.Interaction, expressao: str):
    # Comando /roll: aceita rolagens simples e expressões com múltiplos tipos de dado.
    mensagens, metadados = processar_entrada_rolagem(
        expressao,
        interaction.user.id,
        interaction.user.mention,
        prefixo='',
        guild_id=interaction.guild_id,
    )
    if not mensagens:
        await interaction.response.send_message('Expressão inválida. Use exemplos: `d20+5`, `4df atacar` ou `1d20+4df+3d6`')
        return

    await interaction.response.send_message(mensagens[0])
    for msg in mensagens[1:]:
        await interaction.followup.send(msg)

    if interaction.channel is not None and metadados:
        if deve_ativar_efeito_mais_quatro(metadados['teve_mais_quatro'], metadados['acao_fate']):
            await tocar_audio_ao_mais_quatro(interaction.user, interaction.channel, metadados['acao_fate'])


@tree.command(name='tema', description='Define seu tema de ++++ usando link (YouTube, SoundCloud etc.)')
@app_commands.describe(link='Link da música para tocar quando você tirar ++++ em 4df')
async def tema_slash(interaction: discord.Interaction, link: str):
    # Comando /tema: salva o link de tema pessoal do usuário.
    link = link.strip()
    if not link or not re.match(r'^https?://', link, re.IGNORECASE):
        await interaction.response.send_message('Envie um link válido começando com `http://` ou `https://`.', ephemeral=True)
        return

    temas_usuario[interaction.user.id] = link
    await interaction.response.send_message('Tema salvo com sucesso! Agora seu ++++ tocará essa música.')


@tree.command(name='ban', description='(Admin) Adiciona usuário na lista de banidos')
@app_commands.describe(usuario='Mencione o usuário para banir', usuario_id='Ou informe o ID do usuário')
async def ban_slash(interaction: discord.Interaction, usuario: discord.User | None = None, usuario_id: str | None = None):
    # Comando /ban (admin): adiciona usuário na lista de banidos.
    if not eh_admin(interaction.user.id):
        await interaction.response.send_message('Você não tem permissão para usar este comando.', ephemeral=True)
        return

    alvo_id = extrair_id_alvo_slash(usuario, usuario_id)
    if not alvo_id:
        await interaction.response.send_message('Use `/ban` mencionando alguém ou informando um ID válido.', ephemeral=True)
        return

    if alvo_id in usuarios_banidos:
        await interaction.response.send_message(f'O usuário `{alvo_id}` já está banido.', ephemeral=True)
        return

    usuarios_banidos.append(alvo_id)
    await interaction.response.send_message(f'Usuário `{alvo_id}` foi adicionado aos banidos.')


@tree.command(name='desbanir', description='(Admin) Remove usuário da lista de banidos')
@app_commands.describe(usuario='Mencione o usuário para desbanir', usuario_id='Ou informe o ID do usuário')
async def desbanir_slash(interaction: discord.Interaction, usuario: discord.User | None = None, usuario_id: str | None = None):
    # Comando /desbanir (admin): remove usuário da lista de banidos.
    if not eh_admin(interaction.user.id):
        await interaction.response.send_message('Você não tem permissão para usar este comando.', ephemeral=True)
        return

    alvo_id = extrair_id_alvo_slash(usuario, usuario_id)
    if not alvo_id:
        await interaction.response.send_message('Use `/desbanir` mencionando alguém ou informando um ID válido.', ephemeral=True)
        return

    if alvo_id not in usuarios_banidos:
        await interaction.response.send_message(f'O usuário `{alvo_id}` não está banido.', ephemeral=True)
        return

    usuarios_banidos.remove(alvo_id)
    await interaction.response.send_message(f'Usuário `{alvo_id}` foi removido dos banidos.')

@client.event
async def on_message(message):
    # Manipulador de mensagens de texto: comandos ! e gatilhos gerais do bot.
    usuario = message.author
    if usuario == client.user:
        return

    # Reconhecimento de comandos de texto.
    comando_ban = re.match(r'^!ban(?:\s+(.*))?$', message.content, re.IGNORECASE)
    comando_desbanir = re.match(r'^!desbanir(?:\s+(.*))?$', message.content, re.IGNORECASE)
    comando_teste = re.match(r'^!teste(?:\s+(.*))?$', message.content, re.IGNORECASE)
    comando_max = re.match(r'^!max(?:\s+(.*))?$', message.content, re.IGNORECASE)
    comando_min = re.match(r'^!min(?:\s+(.*))?$', message.content, re.IGNORECASE)
    comando_adm = re.match(r'^!adm(?:\s+(.*))?$', message.content, re.IGNORECASE)
    comando_invencivel = re.match(r'^!invencivel\s*$', message.content, re.IGNORECASE)
    comando_escala = re.match(r'^!escala(?:\s+(.*))?$', message.content, re.IGNORECASE)
    comando_duelo = re.match(r'^!duelo(?:\s+(.*))?$', message.content, re.IGNORECASE)
    comando_fimduelo = re.match(r'^!fimduelo\s*$', message.content, re.IGNORECASE)
    comando_luta = re.match(r'^!luta\s*$', message.content, re.IGNORECASE)
    comando_tocar = re.match(r'^!tocar(?:\s+(.*))?$', message.content, re.IGNORECASE)
    comando_skipar = re.match(r'^!skipar\s*$', message.content, re.IGNORECASE)
    comando_parar = re.match(r'^!parar\s*$', message.content, re.IGNORECASE)
    comando_tema = re.match(r'^!tema(?:\s+(.*))?$', message.content, re.IGNORECASE)

    if comando_invencivel:
        if ativar_tema_invencivel():
            await message.channel.send('Tema de `++++` alterado para **Invencível**. Agora qualquer `++++` envia o GIF do Invencível e toca `invencivel.ogg`.')
        else:
            await message.channel.send('O tema de `++++` já está em **Invencível**.')
        return

    if comando_escala:
        argumento_escala = (comando_escala.group(1) or '').strip()
        if not argumento_escala:
            await message.channel.send(
                f'{usuario.mention} sua escala atual é `{formatar_numero_resultado(obter_escala_usuario(usuario.id))}`. Use `!escala <valor>` para alterar.'
            )
            return

        try:
            valor_escala = interpretar_valor_escala(argumento_escala)
        except ValueError as erro:
            await message.channel.send(f'{usuario.mention} {erro}')
            return

        escalas_usuario[usuario.id] = valor_escala
        await message.channel.send(
            f'{usuario.mention} escala registrada em `{formatar_numero_resultado(valor_escala)}`.'
        )
        return

    if comando_duelo:
        if message.guild is None:
            await message.channel.send('O comando `!duelo` só pode ser usado dentro de um servidor.')
            return

        ids_alvo = extrair_ids_alvos_texto(message, comando_duelo.group(1) or '')
        if len(ids_alvo) < 2:
            await message.channel.send(
                f'{usuario.mention} use `!duelo @jogador1 @jogador2 ...` ou IDs. Mínimo: 2 participantes. {descrever_duelo_ativo(message.guild.id)}'
            )
            return

        duelos_ativos[message.guild.id] = ids_alvo
        await message.channel.send(f'Duelo registrado. {descrever_duelo_ativo(message.guild.id)}')
        return

    if comando_fimduelo:
        if message.guild is None:
            await message.channel.send('O comando `!fimduelo` só pode ser usado dentro de um servidor.')
            return

        if duelos_ativos.pop(message.guild.id, None) is None:
            await message.channel.send('Não há duelo ativo para encerrar nesta guild.')
        else:
            await message.channel.send('Duelo encerrado. Os participantes foram removidos do duelo ativo.')
        return

    # !tema: salva tema personalizado por usuário.
    if comando_tema:
        link_tema = (comando_tema.group(1) or '').strip()
        if not link_tema or not re.match(r'^https?://', link_tema, re.IGNORECASE):
            await message.channel.send(f'{usuario.mention} use `!tema <link>` com URL válida.')
            return

        temas_usuario[usuario.id] = link_tema
        await message.channel.send(f'{usuario.mention} tema salvo! Vou tocar no seu ++++ em 4df.')
        return

    # !parar: interrompe qualquer reprodução ativa e limpa todo o estado de áudio da guild.
    if comando_parar:
        guild = message.guild
        if guild is None:
            return

        voice_client = guild.voice_client
        tinha_estado = any([
            filas_luta.get(guild.id),
            filas_avulsas.get(guild.id),
            faixa_atual_luta.get(guild.id),
            faixa_atual_avulsa.get(guild.id),
            retomar_faixa_luta.get(guild.id),
            modo_reproducao.get(guild.id),
            voice_client is not None and (voice_client.is_playing() or voice_client.is_paused()),
        ])

        resetar_estado_audio(guild.id)

        if voice_client is not None:
            if voice_client.is_playing() or voice_client.is_paused():
                voice_client.stop()
            await voice_client.disconnect(force=False)

        if tinha_estado:
            await message.channel.send('Reprodução encerrada.')
        else:
            await message.channel.send('Não havia música ou playlist ativa para parar.')
        return

    # !tocar: adiciona uma música em uma fila temporária e retoma !luta quando a fila acabar.
    if comando_tocar:
        link_audio = (comando_tocar.group(1) or '').strip()
        if not link_audio or not re.match(r'^https?://', link_audio, re.IGNORECASE):
            await message.channel.send(f'{usuario.mention} use `!tocar <link>` com URL válida.')
            return

        if not isinstance(usuario, discord.Member):
            membro = message.guild.get_member(usuario.id) if message.guild else None
        else:
            membro = usuario

        if membro is None or membro.voice is None or membro.voice.channel is None:
            await message.channel.send(f'{usuario.mention} entre em um canal de voz para usar `!tocar`.')
            return

        canal_voz = membro.voice.channel

        try:
            voice_client = await conectar_ao_canal_voz(message.guild, canal_voz)
            fila_avulsa = filas_avulsas.setdefault(message.guild.id, [])
            fila_avulsa.append(link_audio)
            canal_texto_reproducao[message.guild.id] = message.channel

            modo_atual = modo_reproducao.get(message.guild.id)
            if modo_atual == 'luta':
                preparar_interrupcao_playlist(message.guild.id, voice_client)

            if voice_client.is_playing():
                if modo_atual != 'avulsa':
                    voice_client.stop()
                    await asyncio.sleep(0)
                    await tocar_proxima_da_fila_avulsa(message.guild.id, message.channel)
                    await message.channel.send('Fila temporária iniciada. A playlist atual será retomada quando ela acabar.')
                else:
                    await message.channel.send(f'Música adicionada à fila temporária. Posição: **{len(fila_avulsa)}**.')
                return

            await tocar_proxima_da_fila_avulsa(message.guild.id, message.channel)
        except Exception as erro:
            await message.channel.send(f'Falha no comando `!tocar`: `{erro}`')
        return

    # !skipar: pula para a próxima música da fila ativa (playlist fixa ou fila temporária).
    if comando_skipar:
        guild = message.guild
        if guild is None:
            return

        voice_client = guild.voice_client
        if voice_client is None:
            await message.channel.send('Não há reprodução ativa para pular.')
            return

        modo_atual = modo_reproducao.get(guild.id)
        if voice_client.is_playing():
            voice_client.stop()
            if modo_atual == 'avulsa':
                await message.channel.send('Pulando para a próxima música da fila temporária.')
            else:
                await message.channel.send('Pulando para a próxima música da playlist.')
            return

        if modo_atual == 'avulsa' and filas_avulsas.get(guild.id):
            await tocar_proxima_da_fila_avulsa(guild.id, message.channel)
            return

        if filas_luta.get(guild.id):
            await tocar_proxima_da_fila(guild.id, message.channel)
            return

        await message.channel.send('Não há próxima música para tocar agora.')
        return

    # !luta: carrega playlist fixa e inicia fila no canal de voz do usuário.
    if comando_luta:
        if not isinstance(usuario, discord.Member):
            membro = message.guild.get_member(usuario.id) if message.guild else None
        else:
            membro = usuario

        if membro is None or membro.voice is None or membro.voice.channel is None:
            await message.channel.send(f'{usuario.mention} entre em um canal de voz para usar `!luta`.')
            return

        canal_voz = membro.voice.channel
        voice_client = message.guild.voice_client if message.guild else None

        try:
            voice_client = await conectar_ao_canal_voz(message.guild, canal_voz)

            itens_playlist = await asyncio.to_thread(_extrair_itens_playlist, luta_playlist_url)
            if not itens_playlist:
                await message.channel.send('Não consegui carregar a playlist `!luta`.')
                return

            # Reinicia completamente o estado anterior de luta da guild antes da nova fila.
            cancelar_playlist_luta(message.guild.id)
            filas_luta[message.guild.id] = itens_playlist

            if voice_client.is_playing():
                voice_client.stop()

            await message.channel.send(f'Playlist de luta carregada com **{len(itens_playlist)}** faixas.')
            await tocar_proxima_da_fila(message.guild.id, message.channel)
        except Exception as erro:
            print(f'Erro detalhado em !luta: {erro}')
            await message.channel.send(f'Falha no comando `!luta`: `{erro}`')
        return

    # !adm: adiciona novo administrador (apenas admins atuais).
    if comando_adm:
        if not eh_admin(usuario.id):
            await message.channel.send(f'{usuario.mention} você não tem permissão para usar este comando.')
            return

        alvo_id = extrair_id_alvo_texto(message, comando_adm.group(1))
        if not alvo_id:
            await message.channel.send('Use `!adm @usuario` ou `!adm ID`.')
            return

        if alvo_id in ids_admin:
            await message.channel.send(f'O usuário `{alvo_id}` já é admin.')
            return

        ids_admin.append(alvo_id)
        await message.channel.send(f'Usuário `{alvo_id}` adicionado como admin.')
        return

    # !teste: alterna modo de teste para permitir max/min em 4df.
    if comando_teste:
        if not eh_admin(usuario.id):
            await message.channel.send(f'{usuario.mention} você não tem permissão para usar este comando.')
            return

        alvo_id = extrair_id_alvo_texto(message, comando_teste.group(1))
        if not alvo_id:
            await message.channel.send('Use `!teste @usuario` ou `!teste ID`.')
            return

        if alvo_id in usuarios_teste:
            usuarios_teste.remove(alvo_id)
            await message.channel.send(f'Modo de teste removido para `{alvo_id}`.')
        else:
            usuarios_teste.add(alvo_id)
            await message.channel.send(f'Modo de teste ativado para `{alvo_id}`. Em `r 4df atacar`, a pessoa pode usar `max`/`min` no fim da mensagem.')
        return

    # !max / !min: força a próxima rolagem simples de 4df do alvo.
    if comando_max or comando_min:
        if not eh_admin(usuario.id):
            await message.channel.send(f'{usuario.mention} você não tem permissão para usar este comando.')
            return

        comando_forcagem = comando_max or comando_min
        tipo_forcagem = 'max' if comando_max else 'min'
        alvo_id = extrair_id_alvo_texto(message, comando_forcagem.group(1))
        if not alvo_id:
            await message.channel.send(f'Use `!{tipo_forcagem} @usuario` ou `!{tipo_forcagem} ID`.')
            return

        await message.channel.send(registrar_forcagem_proximo_4df(alvo_id, tipo_forcagem))
        return

    # !ban: bloqueia usuário para comandos de rolagem.
    if comando_ban:
        if not eh_admin(usuario.id):
            await message.channel.send(f'{usuario.mention} você não tem permissão para usar este comando.')
            return

        alvo_id = extrair_id_alvo_texto(message, comando_ban.group(1))
        if not alvo_id:
            await message.channel.send('Use `!ban @usuario` ou `!ban ID`.')
            return

        if alvo_id in usuarios_banidos:
            await message.channel.send(f'O usuário `{alvo_id}` já está banido.')
            return

        usuarios_banidos.append(alvo_id)
        await message.channel.send(f'Usuário `{alvo_id}` foi adicionado aos banidos.')
        return

    # !desbanir: remove bloqueio de usuário.
    if comando_desbanir:
        if not eh_admin(usuario.id):
            await message.channel.send(f'{usuario.mention} você não tem permissão para usar este comando.')
            return

        alvo_id = extrair_id_alvo_texto(message, comando_desbanir.group(1))
        if not alvo_id:
            await message.channel.send('Use `!desbanir @usuario` ou `!desbanir ID`.')
            return

        if alvo_id not in usuarios_banidos:
            await message.channel.send(f'O usuário `{alvo_id}` não está banido.')
            return

        usuarios_banidos.remove(alvo_id)
        await message.channel.send(f'Usuário `{alvo_id}` foi removido dos banidos.')
        return

    # Matchers gerais para rolagem com prefixo `r`, rolagens simples sem prefixo e gatilho "jandei".
    comando_rolagem = re.match(r'^r(?=\s|[()\d+\-dD])\s*(.*)$', message.content, re.IGNORECASE)
    conteudo_sem_prefixo = message.content.strip()
    comando_rolagem_sem_prefixo = eh_rolagem_simples(conteudo_sem_prefixo) or eh_expressao_aditiva_de_dados(conteudo_sem_prefixo)
    match4 = re.search(r'jandei', message.content, re.IGNORECASE)
    jandei_foi_mencionado = any(mencionado.id == id_jandei for mencionado in message.mentions)

    if comando_rolagem:
        expressao_rolagem = (comando_rolagem.group(1) or '').strip()
        mensagens, metadados = processar_entrada_rolagem(
            expressao_rolagem,
            usuario.id,
            usuario.mention,
            prefixo='r ',
            permitir_expressao_com_dados=True,
            guild_id=message.guild.id if message.guild else None,
        )
        if not mensagens:
            await message.channel.send(f'{usuario.mention} expressão inválida. Use: `r d20+5`, `r 4df atacar` ou `r 1d20+4df+3d6`.')
            return

        for mensagem in mensagens:
            await message.channel.send(mensagem)

        if metadados and deve_ativar_efeito_mais_quatro(metadados['teve_mais_quatro'], metadados['acao_fate']):
            await tocar_audio_ao_mais_quatro(usuario, message.channel, metadados['acao_fate'])
        return
    elif comando_rolagem_sem_prefixo:
        mensagens, metadados = processar_entrada_rolagem(
            conteudo_sem_prefixo,
            usuario.id,
            usuario.mention,
            prefixo='',
            permitir_expressao_com_dados=False,
            guild_id=message.guild.id if message.guild else None,
        )
        if not mensagens:
            return

        for mensagem in mensagens:
            await message.channel.send(mensagem)

        if metadados and deve_ativar_efeito_mais_quatro(metadados['teve_mais_quatro'], metadados['acao_fate']):
            await tocar_audio_ao_mais_quatro(usuario, message.channel, metadados['acao_fate'])
        return
    # Gatilho por texto/menção de "jandei", redirecionando para canal específico.
    elif match4 or jandei_foi_mencionado:
        canal_destino = client.get_channel(1471692261371674676)
        if canal_destino is None:
            try:
                canal_destino = await client.fetch_channel(1471692261371674676)
            except Exception:
                canal_destino = message.channel

        canal_envio = canal_destino if canal_destino is not None and hasattr(canal_destino, 'send') else message.channel
        canal_envio = cast(Any, canal_envio)

        await canal_envio.send('https://tenor.com/view/furry-fursuit-lua-excited-discord-gif-25290457')
        await canal_envio.send(f'Jandei foi citado! "{message.content}". lembrando que Jandei é um furry <@332954449918165003>')

carregar_variaveis_ambiente()

# Inicialização do bot com token via variável de ambiente.
token_bot = os.environ.get('DISCORD_BOT_TOKEN', '').strip()
if not token_bot:
    raise RuntimeError('Defina DISCORD_BOT_TOKEN no .env.local, .env ou na variável de ambiente antes de iniciar o bot.')

client.run(token_bot)