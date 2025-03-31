# main.py - Arquivo principal do bot
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
from music_bot.music import Music

# Carregar variáveis de ambiente
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Configuração do bot
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Bot está online como {bot.user.name}')
    # Registrar comandos slash
    try:
        synced = await bot.tree.sync()
        print(f'Sincronizados {len(synced)} comando(s)')
    except Exception as e:
        print(e)
    
    # Adicionar cog de música
    await bot.add_cog(Music(bot))

# Executar o bot
if __name__ == "__main__":
    bot.run(TOKEN)