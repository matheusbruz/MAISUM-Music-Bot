# music_bot/music.py - Módulo de música
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import yt_dlp
import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
from dotenv import load_dotenv

# Carregar variáveis de ambiente para Spotify
load_dotenv()
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')

# Configuração do yt-dlp
YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}

# Atualize as opções do FFmpeg para usar o caminho fornecido
FFMPEG_PATH = r"C:\Users\BRUZACA\Desktop\workspace python\MAISUM-Music-Bot\bin\ffmpeg.exe"  # Caminho para o executável FFmpeg
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
    'executable': FFMPEG_PATH  # Define o executável do FFmpeg
}

# Inicializar cliente Spotify se as credenciais existirem
spotify_client = None
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    spotify_client = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET
    ))

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.music_queues = {}  # Dicionário para armazenar filas por servidor
        self.current_songs = {}  # Dicionário para armazenar música atual por servidor
        self.voice_clients = {}  # Dicionário para armazenar conexões de voz
        self.timeout_tasks = {}  # Para rastrear tarefas de timeout

    # Utilitários para manipulação de URLs e filas
    def is_youtube_url(self, url):
        youtube_regex = r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
        return re.match(youtube_regex, url) is not None

    def is_spotify_url(self, url):
        spotify_regex = r'(https?://)?open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)'
        return re.match(spotify_regex, url) is not None

    async def get_youtube_info(self, url):
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            return {
                'url': info['url'],
                'title': info['title'],
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'original_url': url
            }

    async def process_spotify_url(self, url):
        if not spotify_client:
            return None  # Retorna None se o cliente Spotify não estiver configurado
        
        track_list = []
        match = re.match(r'(https?://)?open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)', url)
        if not match:
            return None  # Retorna None se o link não for válido
        
        item_type = match.group(2)
        item_id = match.group(3)
        
        try:
            if item_type == 'track':
                track = spotify_client.track(item_id)
                search_query = f"{track['name']} {' '.join([artist['name'] for artist in track['artists']])}"
                track_list.append(search_query)
            
            elif item_type == 'album':
                album = spotify_client.album(item_id)
                for track in album['tracks']['items']:
                    search_query = f"{track['name']} {' '.join([artist['name'] for artist in track['artists']])}"
                    track_list.append(search_query)
            
            elif item_type == 'playlist':
                playlist = spotify_client.playlist(item_id)
                for item in playlist['tracks']['items']:
                    track = item.get('track')
                    if track:  # Verifica se a faixa existe
                        search_query = f"{track['name']} {' '.join([artist['name'] for artist in track['artists']])}"
                        track_list.append(search_query)
            
            return track_list if track_list else None  # Retorna None se a lista estiver vazia

        except spotipy.exceptions.SpotifyException as e:
            print(f"Erro ao processar link do Spotify: {e}")
            return None

    async def play_next(self, guild_id):
        if guild_id in self.music_queues and self.music_queues[guild_id]:
            voice_client = self.voice_clients.get(guild_id)
            if voice_client and voice_client.is_connected():
                song_info = self.music_queues[guild_id].pop(0)
                self.current_songs[guild_id] = song_info
                
                # Atualizar rich presence
                await self.update_rich_presence(guild_id, song_info)
                
                voice_client.play(
                    discord.FFmpegPCMAudio(song_info['url'], **FFMPEG_OPTIONS),
                    after=lambda e: asyncio.run_coroutine_threadsafe(
                        self.song_finished(guild_id, e), self.bot.loop
                    )
                )
                return True
        return False

    async def song_finished(self, guild_id, error):
        if error:
            print(f"Erro na reprodução: {error}")
        
        if not await self.play_next(guild_id):
            # Sem mais músicas na fila, iniciar timer de inatividade
            self.current_songs.pop(guild_id, None)
            await self.update_rich_presence(guild_id, None)
            
            # Cancelar tarefa existente se houver
            if guild_id in self.timeout_tasks and not self.timeout_tasks[guild_id].done():
                self.timeout_tasks[guild_id].cancel()
            
            # Criar nova tarefa de timeout
            self.timeout_tasks[guild_id] = asyncio.create_task(self.disconnect_after_timeout(guild_id))

    async def disconnect_after_timeout(self, guild_id):
        # Esperar 60 segundos antes de desconectar
        await asyncio.sleep(60)
        voice_client = self.voice_clients.get(guild_id)
        if voice_client and voice_client.is_connected() and not voice_client.is_playing():
            await voice_client.disconnect()
            self.voice_clients.pop(guild_id, None)

    async def update_rich_presence(self, guild_id, song_info):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        
        voice_client = self.voice_clients.get(guild_id)
        if not voice_client or not voice_client.is_connected():
            return
        
        # Se tiver música tocando, atualizar rich presence
        if song_info:
            activity = discord.Activity(
                type=discord.ActivityType.listening,
                name=song_info['title'],
                details=f"No servidor {guild.name}"
            )
            await self.bot.change_presence(activity=activity)
        else:
            # Resetar rich presence quando não há música
            await self.bot.change_presence(activity=None)

    # Comando play (suporta slash command e prefixo)
    @commands.hybrid_command(name="play", description="Reproduz música a partir de um link")
    @app_commands.describe(link="Link do YouTube ou Spotify para reproduzir")
    async def play(self, ctx, *, link: str):
        # Verificar se o usuário está em um canal de voz
        if not ctx.author.voice:
            return await ctx.send("Você precisa estar em um canal de voz para usar este comando.")
        
        guild_id = ctx.guild.id
        
        # Conectar ao canal de voz se ainda não estiver conectado
        if guild_id not in self.voice_clients or not self.voice_clients[guild_id].is_connected():
            self.voice_clients[guild_id] = await ctx.author.voice.channel.connect()
            # Inicializar fila se necessário
            if guild_id not in self.music_queues:
                self.music_queues[guild_id] = []
        
        # Cancelar qualquer tarefa de timeout
        if guild_id in self.timeout_tasks and not self.timeout_tasks[guild_id].done():
            self.timeout_tasks[guild_id].cancel()
        
        # Mensagem temporária enquanto processa
        processing_msg = await ctx.send("Processando seu pedido...")
        
        try:
            if self.is_youtube_url(link):
                # Link do YouTube
                song_info = await self.get_youtube_info(link)
                self.music_queues[guild_id].append(song_info)
                await processing_msg.edit(content=f"Adicionado à fila: {song_info['title']}")
            
            elif self.is_spotify_url(link):
                # Link do Spotify
                track_list = await self.process_spotify_url(link)
                if not track_list:
                    return await processing_msg.edit(content="Não foi possível processar este link do Spotify. Verifique se o link é válido e se as credenciais estão configuradas corretamente.")
                
                if len(track_list) > 1:
                    await processing_msg.edit(content=f"Adicionando {len(track_list)} músicas da playlist/álbum à fila...")
                
                for i, track_query in enumerate(track_list):
                    try:
                        song_info = await self.get_youtube_info(f"ytsearch:{track_query}")
                        self.music_queues[guild_id].append(song_info)
                        
                        if i == 0 and len(track_list) == 1:
                            await processing_msg.edit(content=f"Adicionado à fila: {song_info['title']}")
                    except Exception as e:
                        print(f"Erro ao processar faixa {i+1} ({track_query}): {e}")
                
                if len(track_list) > 1:
                    await processing_msg.edit(content=f"Adicionadas {len(track_list)} músicas à fila!")
            
            else:
                # Tratar como uma pesquisa no YouTube
                song_info = await self.get_youtube_info(f"ytsearch:{link}")
                self.music_queues[guild_id].append(song_info)
                await processing_msg.edit(content=f"Adicionado à fila: {song_info['title']}")
            
            # Iniciar reprodução se não estiver tocando
            voice_client = self.voice_clients[guild_id]
            if not voice_client.is_playing():
                await self.play_next(guild_id)
        
        except Exception as e:
            await processing_msg.edit(content=f"Ocorreu um erro: {str(e)}")

    # Comando pause
    @commands.hybrid_command(name="pause", description="Pausa a música atual")
    async def pause(self, ctx):
        guild_id = ctx.guild.id
        voice_client = self.voice_clients.get(guild_id)
        
        if voice_client and voice_client.is_playing():
            voice_client.pause()
            await ctx.send("Música pausada.")
        else:
            await ctx.send("Não há músicas tocando no momento.")

    # Comando resume
    @commands.hybrid_command(name="resume", description="Continua a reprodução da música")
    async def resume(self, ctx):
        guild_id = ctx.guild.id
        voice_client = self.voice_clients.get(guild_id)
        
        if voice_client and voice_client.is_paused():
            voice_client.resume()
            await ctx.send("Música retomada.")
        else:
            await ctx.send("Não há músicas pausadas no momento.")

    # Comando pular
    @commands.hybrid_command(name="pular", description="Pula para a próxima música na fila")
    async def skip(self, ctx):
        guild_id = ctx.guild.id
        voice_client = self.voice_clients.get(guild_id)
        
        if voice_client and voice_client.is_playing():
            voice_client.stop()
            await ctx.send("Pulando para a próxima música.")
        else:
            await ctx.send("Não há músicas tocando no momento.")

    # Comando fila
    @commands.hybrid_command(name="fila", description="Mostra a fila de reprodução atual")
    async def queue(self, ctx):
        guild_id = ctx.guild.id
        
        if guild_id not in self.music_queues or not self.music_queues[guild_id]:
            return await ctx.send("A fila de reprodução está vazia.")
        
        # Criar embed para a fila
        embed = discord.Embed(title="Fila de Reprodução", color=discord.Color.blue())
        
        # Adicionar música atual
        if guild_id in self.current_songs:
            embed.add_field(
                name="Tocando agora:",
                value=self.current_songs[guild_id]['title'],
                inline=False
            )
        
        # Adicionar próximas músicas
        queue_text = ""
        for i, song in enumerate(self.music_queues[guild_id], 1):
            if i <= 10:  # Limitar a 10 músicas para não sobrecarregar
                queue_text += f"{i}. {song['title']}\n"
        
        if queue_text:
            embed.add_field(name="Próximas músicas:", value=queue_text, inline=False)
            
            # Se houver mais de 10 músicas
            remaining = len(self.music_queues[guild_id]) - 10
            if remaining > 0:
                embed.set_footer(text=f"E mais {remaining} músicas na fila.")
        
        await ctx.send(embed=embed)

    # Comando limpar
    @commands.hybrid_command(name="limpar", description="Limpa a fila de reprodução")
    async def clear(self, ctx):
        guild_id = ctx.guild.id
        
        if guild_id in self.music_queues:
            self.music_queues[guild_id].clear()
            await ctx.send("Fila de reprodução limpa.")
        else:
            await ctx.send("Não há fila de reprodução para limpar.")

    # Comando chamar
    @commands.hybrid_command(name="chamar", description="Chama o bot para seu canal de voz")
    async def join(self, ctx):
        if not ctx.author.voice:
            return await ctx.send("Você precisa estar em um canal de voz para usar este comando.")
        
        guild_id = ctx.guild.id
        voice_client = self.voice_clients.get(guild_id)
        
        # Se já estiver conectado, mover para o novo canal
        if voice_client and voice_client.is_connected():
            await voice_client.move_to(ctx.author.voice.channel)
            await ctx.send(f"Bot movido para o canal {ctx.author.voice.channel.name}.")
        else:
            # Conectar ao novo canal
            self.voice_clients[guild_id] = await ctx.author.voice.channel.connect()
            # Inicializar fila se necessário
            if guild_id not in self.music_queues:
                self.music_queues[guild_id] = []
            await ctx.send(f"Bot conectado ao canal {ctx.author.voice.channel.name}.")

    # Comando expulsar
    @commands.hybrid_command(name="expulsar", description="Remove o bot do canal de voz")
    async def leave(self, ctx):
        guild_id = ctx.guild.id
        voice_client = self.voice_clients.get(guild_id)
        
        if voice_client and voice_client.is_connected():
            # Limpar fila e parar música
            if guild_id in self.music_queues:
                self.music_queues[guild_id].clear()
            
            # Cancelar qualquer tarefa de timeout
            if guild_id in self.timeout_tasks and not self.timeout_tasks[guild_id].done():
                self.timeout_tasks[guild_id].cancel()
            
            # Resetar rich presence
            self.current_songs.pop(guild_id, None)
            await self.update_rich_presence(guild_id, None)
            
            # Desconectar
            await voice_client.disconnect()
            self.voice_clients.pop(guild_id, None)
            await ctx.send("Bot desconectado do canal de voz.")
        else:
            await ctx.send("O bot não está conectado a nenhum canal de voz.")