import discord
from discord.ext import commands
from discord import app_commands
import random
import re
import ast
import os
import glob
import asyncio
import yt_dlp

# -----------------------------------------------------------------------------
# VISÃO GERAL DO BOT (handoff para outro dev)
#
# 1) Entradas suportadas:
#    - Comandos de texto: !luta, !tema, !ban, !desbanir, !adm, !teste
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
#    - Token do bot está hardcoded no final do arquivo (ideal migrar para variável de ambiente).
# -----------------------------------------------------------------------------

# Mapeamento dos resultados dos dados Fate para símbolos visuais.
fate_dice = {-1: '-', 0:'0', 1:'+'}

# Listas/estruturas de controle de usuários e permissões.
usuarios_banidos = [190954369917779968]
usuarios_teste = set()
id_jandei = 332954449918165003
ids_admin = [316323635470270475]

# Configuração base do cliente Discord e comandos slash.
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
comandos_sincronizados = False

# URL fixa da playlist do comando !luta.
luta_playlist_url = 'https://music.youtube.com/playlist?list=PLIEibbGcfrARrAaNARQmPHAT-HwUa8-d8&si=U26AUy2dPYp3gq_C'

# Estado de áudio por servidor (guild) para controle da playlist.
filas_luta = {}
faixa_atual_luta = {}
retomar_faixa_luta = {}
interromper_auto_avanco_luta = set()

# Tema personalizado de cada usuário para ativação no ++++.
temas_usuario = {}


def cancelar_playlist_luta(guild_id):
    # Limpa completamente o estado da playlist de luta para uma guild.
    filas_luta.pop(guild_id, None)
    faixa_atual_luta.pop(guild_id, None)
    retomar_faixa_luta.pop(guild_id, None)
    interromper_auto_avanco_luta.discard(guild_id)


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


def obter_ffmpeg_executavel():
    # Busca o executável do ffmpeg em caminhos comuns do Windows.
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
    ]
    for caminho in caminhos:
        if caminho and os.path.isfile(caminho):
            return caminho
    return 'ffmpeg'


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


def escala_adjetivos_jjk(total):
    # Converte o total Fate em uma escala adjetiva inspirada em JJK.
    if total >= 9:
        return 'Inominável'
    if total == 8:
        return 'Lendário'
    if total == 7:
        return 'Épico'
    if total == 6:
        return 'Fantástico'
    if total == 5:
        return 'Excepcional'
    if total == 4:
        return 'Ótimo'
    if total == 3:
        return 'Bom'
    if total == 2:
        return 'Razoável'
    if total == 1:
        return 'Regular'
    if total == 0:
        return 'Medíocre'
    if total == -1:
        return 'Ruim'
    if total == -2:
        return 'Terrível'
    if total == -3:
        return 'Catastrófico'
    return 'Horrível'


def normalizar_acao_fate(texto):
    # Normaliza ações Fate aceitas para um formato padronizado.
    if not texto:
        return None

    texto_limpo = ' '.join(texto.lower().strip().split())
    acoes_validas = {
        'atacar': 'Atacar',
        'defender': 'Defender',
        'criar vantagem': 'Criar Vantagem',
        'superar': 'Superar'
    }
    return acoes_validas.get(texto_limpo)


def extrair_acao_e_complemento_fate(texto):
    # Extrai ação Fate obrigatória e texto complementar opcional.
    if not texto:
        return None, None

    texto_limpo = ' '.join(texto.strip().split())
    match_acao = re.match(
        r'^(criar\s+vantagem|atacar|defender|superar)(?:\s+(.*))?$',
        texto_limpo,
        re.IGNORECASE
    )
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


def extrair_id_de_texto(texto):
    # Extrai um ID numérico de Discord de um texto livre.
    if not texto:
        return None
    id_match = re.search(r'\d{15,20}', texto)
    if not id_match:
        return None
    return int(id_match.group(0))


def extrair_id_alvo_texto(message, argumento):
    # Resolve alvo de comando por menção ou ID em comando de texto.
    if message.mentions:
        return message.mentions[0].id
    return extrair_id_de_texto(argumento)


def extrair_id_alvo_slash(usuario, usuario_id):
    # Resolve alvo de comando slash por usuário selecionado ou ID informado.
    if usuario is not None:
        return usuario.id
    return extrair_id_de_texto(usuario_id)


def eh_admin(usuario_id):
    # Verifica se um usuário está na lista de administradores.
    return usuario_id in ids_admin


async def tocar_kokusen_no_voz(usuario, canal_texto):
    # Toca kokusen.ogg no canal de voz do usuário e retoma playlist se necessário.
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

        # Se a playlist estiver tocando, prepara retomada depois que o kokusen terminar.
        interrompeu_playlist = preparar_interrupcao_playlist(guild.id, voice_client)

        if voice_client is None:
            voice_client = await canal_voz.connect()
        elif voice_client.channel != canal_voz:
            await voice_client.move_to(canal_voz)

        caminho_audio = os.path.join(os.path.dirname(__file__), 'kokusen.ogg')
        if not os.path.isfile(caminho_audio):
            await canal_texto.send('Arquivo `kokusen.ogg` não encontrado.')
            return

        if voice_client.is_playing():
            # Stop dispara o `after` da faixa atual; o bloqueio de autoavanço evita conflito.
            voice_client.stop()

        def ao_terminar(erro):
            if erro:
                print(f'Erro ao tocar kokusen.ogg: {erro}')
            if interrompeu_playlist:
                client.loop.call_soon_threadsafe(asyncio.create_task, retomar_playlist_interrompida(guild.id, canal_texto))

        voice_client.play(
            discord.FFmpegPCMAudio(caminho_audio, executable=obter_ffmpeg_executavel()),
            after=ao_terminar
        )
    except Exception as erro:
        await canal_texto.send(f'Não consegui tocar `kokusen.ogg` no canal de voz. Erro: `{erro}`')


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

        if voice_client is None:
            voice_client = await canal_voz.connect()
        elif voice_client.channel != canal_voz:
            await voice_client.move_to(canal_voz)

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
    # No ++++, toca tema do usuário; sem tema, toca kokusen apenas em Atacar.
    tema_link = temas_usuario.get(usuario.id)
    if tema_link:
        await tocar_audio_url_no_voz(usuario, canal_texto, tema_link)
        return

    if acao_fate == 'Atacar':
        await tocar_kokusen_no_voz(usuario, canal_texto)


def _extrair_itens_playlist(url):
    # Extrai URLs das faixas de uma playlist usando yt-dlp.
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'skip_download': True,
        'noplaylist': False,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
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
    ydl_opts = {
        'quiet': True,
        'format': 'bestaudio/best',
        'noplaylist': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
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
        if filas_luta.get(guild_id) and not voice_client.is_playing():
            await tocar_proxima_da_fila(guild_id, canal_texto)


def processar_rolagem_dados(conteudo, usuario_id, usuario_mention):
    # Processa expressões de rolagem d/df e retorna mensagens prontas para envio.
    # `match` cobre dados comuns (d20, 2d6+3 etc.).
    match = re.match(r'^(\d*)d(\d+)((?:\s*[+-]\s*\d+)*)(?:\s+(.*))?$', conteudo, re.IGNORECASE)
    # `match2` cobre Fate (df), incluindo ação obrigatória no caso de 4df.
    match2 = re.match(r'^(\d*)df((?:\s*[+-]\s*\d+)*)(?:\s+(.*))?$', conteudo, re.IGNORECASE)

    if match:
        if usuario_id in usuarios_banidos:
            return [
                f'Desculpe {usuario_mention}, eu não escuto furries',
                'mas caso queira falar comigo, resolva esta simples questao de matemática:',
                'https://media.discordapp.net/attachments/1190477143763853393/1471694458629128266/image.png?ex=698fddc5&is=698e8c45&hm=f441a1748e0751a108d3d4adf454c036d63e1f650be231bdf31d4db38340f084&=&format=webp&quality=lossless'
            ]

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
            return [
                f'Desculpe {usuario_mention}, eu não escuto furries',
                'mas caso queira falar comigo, resolva esta simples questao de matemática:',
                'https://media.discordapp.net/attachments/1190477143763853393/1471694458629128266/image.png?ex=698fddc5&is=698e8c45&hm=f441a1748e0751a108d3d4adf454c036d63e1f650be231bdf31d4db38340f084&=&format=webp&quality=lossless'
            ]

        num_dice = int(match2.group(1)) if match2.group(1) else 1
        mods = match2.group(2) if match2.group(2) else ''
        bonus = 0
        mod_display = ''
        mods_encontrados = re.findall(r'([+-])\s*(\d+)', mods)
        if mods_encontrados:
            bonus = sum(int(valor) if sinal == '+' else -int(valor) for sinal, valor in mods_encontrados)
            mod_display = ''.join(f'{sinal}{valor}' for sinal, valor in mods_encontrados)

        texto_adicional_bruto = match2.group(3)
        acao_fate = None
        complemento_fate = None
        forcagem_teste = None
        if num_dice == 4:
            acao_fate, complemento_fate = extrair_acao_e_complemento_fate(texto_adicional_bruto)
            if not acao_fate:
                return [
                    f"{usuario_mention} em `4df` você precisa escolher uma ação: `Atacar`, `Defender`, `Criar Vantagem` ou `Superar`."
                ]
            if usuario_id in usuarios_teste:
                forcagem_teste, complemento_fate = extrair_forcagem_teste(complemento_fate)

        texto_adicional = None
        if num_dice == 4:
            if complemento_fate:
                texto_adicional = f"→ '{complemento_fate}'"
        elif texto_adicional_bruto:
            texto_adicional = f"→ '{texto_adicional_bruto}'"

        if num_dice == 4 and forcagem_teste == 'max':
            rolls = [1, 1, 1, 1]
        elif num_dice == 4 and forcagem_teste == 'min':
            rolls = [-1, -1, -1, -1]
        else:
            rolls = [random.randint(-1, 1) for _ in range(num_dice)]
        rolls_fate = [fate_dice[i] for i in rolls]
        dados_organizados = ', '.join(rolls_fate)
        total_fate = sum(rolls) + bonus
        escala = escala_adjetivos_jjk(total_fate)

        mensagens = []
        if rolls_fate == ['+','+','+','+'] and acao_fate == 'Atacar':
            mensagens.append('Black Flash!')
            mensagens.append('https://tenor.com/view/jjk-jjk-s2-jjk-season-2-jujutsu-kaisen-jujutsu-kaisen-s2-gif-7964484372484357392')
            mensagens.append(f"{usuario_mention} rolled: [**{dados_organizados}**]{mod_display} (**Total: {total_fate}**) | Escala: **{escala}** {f'| Ação: **{acao_fate}** ' if acao_fate else ''}{texto_adicional if texto_adicional else ''}")
            return mensagens

        if rolls_fate == ['-','-','-','-']:
            mensagens.append(f"{usuario_mention} rolled: [**{dados_organizados}**]{mod_display} (**Total: {total_fate}**) | Escala: **{escala}** {f'| Ação: **{acao_fate}** ' if acao_fate else ''}{texto_adicional if texto_adicional else ''} ")
            mensagens.append('https://cdn.discordapp.com/attachments/1264409229150785609/1451361408028639316/a5z6jq.gif?ex=698f1064&is=698dbee4&hm=a1ecc438a4c2434f9ea70349dd156d6ac2d7c5197ce7dc0b801974d462b55fb5')
            return mensagens

        return [f"{usuario_mention} rolled: [{dados_organizados}]{mod_display} (**Total: {total_fate}**) | Escala: **{escala}** {f'| Ação: **{acao_fate}** ' if acao_fate else ''}{texto_adicional if texto_adicional else ''} "]

    return None

@client.event
async def on_ready():
    # Evento disparado quando o bot conecta; sincroniza comandos slash uma vez.
    global comandos_sincronizados
    if not comandos_sincronizados:
        await tree.sync()
        comandos_sincronizados = True
        print('Slash commands sincronizados.')
    print(f'We have logged in as {client.user}')


@tree.command(name='roll', description='Rola dados com expressão tipo d20, 2d6+3, 4df atacar')
@app_commands.describe(expressao='Ex: d20+5, 4df atacar banana, 2d8-1')
async def roll_slash(interaction: discord.Interaction, expressao: str):
    # Comando /roll: usa o processador central e dispara áudio no ++++ válido.
    mensagens = processar_rolagem_dados(expressao, interaction.user.id, interaction.user.mention)
    if not mensagens:
        await interaction.response.send_message('Expressão inválida. Use exemplos: `d20+5`, `2d6`, `4df atacar`')
        return

    await interaction.response.send_message(mensagens[0])
    for msg in mensagens[1:]:
        await interaction.followup.send(msg)

    if interaction.channel is not None:
        conteudo_msgs = ' '.join(mensagens)
        teve_mais_quatro = '+, +, +, +' in conteudo_msgs
        num_df_slash = re.match(r'^(\d*)df', expressao.strip(), re.IGNORECASE)
        acao_slash = None
        if num_df_slash and (int(num_df_slash.group(1)) if num_df_slash.group(1) else 1) == 4:
            match_df_completo = re.match(r'^(\d*)df((?:\s*[+-]\s*\d+)*)(?:\s+(.*))?$', expressao.strip(), re.IGNORECASE)
            if match_df_completo:
                acao_slash, _comp = extrair_acao_e_complemento_fate(match_df_completo.group(3))

        if teve_mais_quatro and acao_slash in ('Atacar', 'Defender', 'Criar Vantagem', 'Superar'):
            await tocar_audio_ao_mais_quatro(interaction.user, interaction.channel, acao_slash)


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
async def ban_slash(interaction: discord.Interaction, usuario: discord.User = None, usuario_id: str = None):
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
async def desbanir_slash(interaction: discord.Interaction, usuario: discord.User = None, usuario_id: str = None):
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
    comando_adm = re.match(r'^!adm(?:\s+(.*))?$', message.content, re.IGNORECASE)
    comando_luta = re.match(r'^!luta\s*$', message.content, re.IGNORECASE)
    comando_tema = re.match(r'^!tema(?:\s+(.*))?$', message.content, re.IGNORECASE)

    # !tema: salva tema personalizado por usuário.
    if comando_tema:
        link_tema = (comando_tema.group(1) or '').strip()
        if not link_tema or not re.match(r'^https?://', link_tema, re.IGNORECASE):
            await message.channel.send(f'{usuario.mention} use `!tema <link>` com URL válida.')
            return

        temas_usuario[usuario.id] = link_tema
        await message.channel.send(f'{usuario.mention} tema salvo! Vou tocar no seu ++++ em 4df.')
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
            if voice_client is None:
                voice_client = await canal_voz.connect()
            elif voice_client.channel != canal_voz:
                await voice_client.move_to(canal_voz)

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
            await message.channel.send(f'Modo de teste ativado para `{alvo_id}`. Em `4df`, a pessoa pode usar `max`/`min` no fim da mensagem.')
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

    # Matchers gerais para rolagens e gatilho "jandei".
    match = re.match(r'^(\d*)d(\d+)((?:\s*[+-]\s*\d+)*)(?:\s+(.*))?$', message.content, re.IGNORECASE)
    match2 = re.match(r'^(\d*)df((?:\s*[+-]\s*\d+)*)(?:\s+(.*))?$', message.content, re.IGNORECASE)
    # `r` só aceita expressão matemática real (evita capturar palavras aleatórias).
    match3 = re.match(r'^r\s*((?:\d+(?:\.\d+)?|\.\d+)\s*(?:(?:\*\*|//|[+\-*/%])\s*[-+]?(?:\d+(?:\.\d+)?|\.\d+)\s*)+)$', message.content)
    match4 = re.search(r'jandei', message.content, re.IGNORECASE)
    jandei_foi_mencionado = any(mencionado.id == id_jandei for mencionado in message.mentions)

    # Fluxo de rolagem de dados comuns (d20, 2d6+3 etc.).
    if match:
        if usuario.id in usuarios_banidos:
            await message.channel.send(f'Desculpe {usuario.mention}, eu não escuto furries')
            await message.channel.send('mas caso queira falar comigo, resolva esta simples questao de matemática:')
            await message.channel.send('https://media.discordapp.net/attachments/1190477143763853393/1471694458629128266/image.png?ex=698fddc5&is=698e8c45&hm=f441a1748e0751a108d3d4adf454c036d63e1f650be231bdf31d4db38340f084&=&format=webp&quality=lossless')
            return
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
        await message.channel.send(f'{usuario.mention} rolled: {rolls} {mod_display} (**Total: {sum(rolls) + bonus}**) {texto_adicional}')
    # Fluxo de rolagem Fate (df), incluindo regras de ação e efeitos especiais.
    elif match2:
        if usuario.id in usuarios_banidos:
            await message.channel.send(f'Desculpe {usuario.mention}, eu não escuto furries')
            await message.channel.send('mas caso queira falar comigo, resolva esta simples questao de matemática:')
            await message.channel.send('https://media.discordapp.net/attachments/1190477143763853393/1471694458629128266/image.png?ex=698fddc5&is=698e8c45&hm=f441a1748e0751a108d3d4adf454c036d63e1f650be231bdf31d4db38340f084&=&format=webp&quality=lossless')
            return


        num_dice = int(match2.group(1)) if match2.group(1) else 1
        mods = match2.group(2) if match2.group(2) else ''
        bonus = 0
        mod_display = ''
        mods_encontrados = re.findall(r'([+-])\s*(\d+)', mods)
        if mods_encontrados:
            bonus = sum(int(valor) if sinal == '+' else -int(valor) for sinal, valor in mods_encontrados)
            mod_display = ''.join(f'{sinal}{valor}' for sinal, valor in mods_encontrados)

        texto_adicional_bruto = match2.group(3)
        acao_fate = None
        complemento_fate = None
        forcagem_teste = None
        if num_dice == 4:
            acao_fate, complemento_fate = extrair_acao_e_complemento_fate(texto_adicional_bruto)
            if not acao_fate:
                await message.channel.send(
                    f"{usuario.mention} em `4df` você precisa escolher uma ação: `Atacar`, `Defender`, `Criar Vantagem` ou `Superar`."
                )
                return
            if usuario.id in usuarios_teste:
                forcagem_teste, complemento_fate = extrair_forcagem_teste(complemento_fate)

        texto_adicional = None
        if num_dice == 4:
            if complemento_fate:
                texto_adicional = f"→ '{complemento_fate}'"
        elif texto_adicional_bruto:
            texto_adicional = f"→ '{texto_adicional_bruto}'"

        if num_dice == 4 and forcagem_teste == 'max':
            rolls = [1, 1, 1, 1]
        elif num_dice == 4 and forcagem_teste == 'min':
            rolls = [-1, -1, -1, -1]
        else:
            rolls = [random.randint(-1, 1) for _ in range(num_dice)]
        rolls_fate = []
        for i in rolls:
            rolls_fate.append(fate_dice[i])
        dados_organizados = ', '.join(rolls_fate)
        total_fate = sum(rolls) + bonus
        escala = escala_adjetivos_jjk(total_fate)
        if rolls_fate == ['+','+','+','+'] and acao_fate == 'Atacar':
            await message.channel.send('Black Flash!')
            await message.channel.send('https://tenor.com/view/jjk-jjk-s2-jjk-season-2-jujutsu-kaisen-jujutsu-kaisen-s2-gif-7964484372484357392')
            await message.channel.send(f"{usuario.mention} rolled: [**{dados_organizados}**]{mod_display} (**Total: {total_fate}**) | Escala: **{escala}** {f'| Ação: **{acao_fate}** ' if acao_fate else ''}{texto_adicional if texto_adicional else ''}")
            await tocar_audio_ao_mais_quatro(usuario, message.channel, acao_fate)
        elif rolls_fate == ['-','-','-','-']:
            await message.channel.send(f"{usuario.mention} rolled: [**{dados_organizados}**]{mod_display} (**Total: {total_fate}**) | Escala: **{escala}** {f'| Ação: **{acao_fate}** ' if acao_fate else ''}{texto_adicional if texto_adicional else ''} ")
            await message.channel.send('https://cdn.discordapp.com/attachments/1264409229150785609/1451361408028639316/a5z6jq.gif?ex=698f1064&is=698dbee4&hm=a1ecc438a4c2434f9ea70349dd156d6ac2d7c5197ce7dc0b801974d462b55fb5')
        else:
            await message.channel.send(f"{usuario.mention} rolled: [{dados_organizados}]{mod_display} (**Total: {total_fate}**) | Escala: **{escala}** {f'| Ação: **{acao_fate}** ' if acao_fate else ''}{texto_adicional if texto_adicional else ''} ")
            if rolls_fate == ['+','+','+','+'] and acao_fate in ('Defender', 'Criar Vantagem', 'Superar'):
                await tocar_audio_ao_mais_quatro(usuario, message.channel, acao_fate)
    # Fluxo de cálculo matemático seguro com comando r.
    elif match3:
        if usuario.id in usuarios_banidos:
            await message.channel.send(f'Desculpe {usuario.mention}, eu não escuto furries')
            await message.channel.send('mas caso queira falar comigo, resolva esta simples questao de matemática:')
            await message.channel.send('https://media.discordapp.net/attachments/1190477143763853393/1471694458629128266/image.png?ex=698fddc5&is=698e8c45&hm=f441a1748e0751a108d3d4adf454c036d63e1f650be231bdf31d4db38340f084&=&format=webp&quality=lossless')
            return

        expr = match3.group(1).strip()
        if not expr:
            await message.channel.send(f'{usuario.mention} use: `r 2 + 3 * (4 - 1)`')
            return

        try:
            resultado = calcular_expressao(expr)
            if isinstance(resultado, float) and resultado.is_integer():
                resultado = int(resultado)
            await message.channel.send(f'{usuario.mention} `r {expr}` = **{resultado}**')
        except ZeroDivisionError:
            await message.channel.send(f'{usuario.mention} não dá para dividir por zero.')
        except Exception:
            await message.channel.send(f'{usuario.mention} expressão inválida. Exemplo: `r (10 + 5) * 2 - 3/4`')
    # Gatilho por texto/menção de "jandei", redirecionando para canal específico.
    elif match4 or jandei_foi_mencionado:
        canal_destino = client.get_channel(1471692261371674676)
        if canal_destino is None:
            try:
                canal_destino = await client.fetch_channel(1471692261371674676)
            except Exception:
                canal_destino = message.channel

        await canal_destino.send('https://tenor.com/view/furry-fursuit-lua-excited-discord-gif-25290457')
        await canal_destino.send(f'Jandei foi citado! "{message.content}". lembrando que Jandei é um furry <@332954449918165003>')

    # Inicialização do bot (token atualmente fixo no arquivo).
client.run("Token")