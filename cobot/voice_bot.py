import asyncio
import json
import discord
from discord import app_commands
from discord.ext import commands
import os
import argparse
import logging
import re
from fuzzywuzzy import fuzz
from cobot.audio_source import LocalAudioSource, S3AudioSource
import tempfile

log = logging.getLogger('sqcobot')
log.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('[%(levelname)s] %(message)s')
ch.setFormatter(formatter)
log.addHandler(ch)
logging.getLogger('discord').addHandler(ch)
logging.getLogger('discord').setLevel(logging.DEBUG)


# Print discord.py version and git tag if available
def print_discordpy_version():
    version = getattr(discord, '__version__', 'unknown')
    git_tag = getattr(discord, '__git_revision__', None)
    print(f"[INFO] discord.py version: {version}")
    if git_tag:
        print(f"[INFO] discord.py git revision: {git_tag}")


print_discordpy_version()

# Feed loudness measurements from the previous run into
# the one that actually plays the sound.
# Shoot for a (totally arbitrary; change me)
# -15 "integrated loudness target" and 0dB true peak
# https://ffmpeg.org/ffmpeg-filters.html#loudnorm


def filter_settings(loudness):
    crazy_ffmpeg_filter_lines = [
        "loudnorm=i=-15:tp=0:", f"measured_i={loudness['input_i']}:",
        f"measured_tp={loudness['input_tp']}:",
        f"measured_lra={loudness['input_lra']}:",
        f"measured_thresh={loudness['input_thresh']}:",
        "dual_mono=true:linear=true"
    ]
    return "".join(crazy_ffmpeg_filter_lines)


async def get_volume(fname):
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-i",
        fname,
        "-af",
        "loudnorm=print_format=json",
        "-f",
        "null",
        "-",
        stderr=asyncio.subprocess.PIPE)
    _, stderr = await process.communicate()
    s = stderr.decode().strip()
    # Lol ffmpeg doesn't meaningfully split output JSON from other junk.
    inner = s.split('{', 1)[1].split('}', 1)[0]
    full = f"{{{inner}}}"
    return json.loads(full)


def depunctuate(name: str):
    return re.sub(r'[\W_]+', '', name).lower()


def get_fuzzy_match_scores(name: str, sounds):
    return {s: fuzz.partial_ratio(depunctuate(name), s) for s in sounds.keys()}


def sound_name_to_filename(name):
    return f"{name}.ogg"


def get_audio_source(args):
    if args.mock_audio:
        return LocalAudioSource(audio_dir=args.audio_dir)
    else:
        return S3AudioSource(bucket_name=os.environ['AUDIO_BUCKET'])


def parse_args():
    parser = argparse.ArgumentParser(description="Squadron Co-Bot VoiceBot")
    parser.add_argument(
        "--mock-audio",
        action="store_true",
        help="Use local audio files instead of S3 (for local testing)")
    parser.add_argument("--audio-dir",
                        type=str,
                        default="sounds",
                        help="Directory for local audio files (mock mode)")
    parser.add_argument(
        "--guild",
        type=int,
        help="Guild ID for slash command registration (for instant testing)")
    return parser.parse_args()


args = parse_args()
audio_source = get_audio_source(args)

intents = discord.Intents.default()
intents.guild_messages = True
intents.dm_messages = True
intents.message_content = True
intents.messages = True
intents.voice_states = True

description = 'Kernels of wisdom from fighter pilot legends.'
bot = commands.Bot(intents=intents,
                   command_prefix='!co-',
                   description=description)
tree = bot.tree

sounds = {}


def guild_obj():
    return discord.Object(id=args.guild) if args.guild else None


def guild_decorator():
    return {"guild": guild_obj()} if args.guild else {}


def chunk_strings_into(li, chunksize):
    chunked = []
    chunk = []
    total = 0
    for s in li:
        if total + len(s) + 1 > chunksize and chunk:
            chunked.append(chunk)
            chunk = []
            total = 0
        chunk.append(s)
        total += len(s) + 1
    if chunk:
        chunked.append(chunk)
    return chunked


async def join_channel(ctx, channel):
    if ctx.voice_client is not None:
        await ctx.voice_client.move_to(channel)
    else:
        await channel.connect()


@tree.command(name='list', description='List possible sounds')
async def list_sounds(interaction: discord.Interaction):
    HELP_MSG_PREAMBLE = 'These are the topics I can tell you about:\n'
    max_len = 2000 - len(HELP_MSG_PREAMBLE) - 8  # 8 for code block
    sounds_sorted = sorted(sounds.values())
    chunks = chunk_strings_into(sounds_sorted, max_len)
    await interaction.response.defer(ephemeral=True)
    try:
        user = interaction.user
        dm = await user.create_dm()
        for i, chunk in enumerate(chunks):
            msg = HELP_MSG_PREAMBLE if i == 0 else ""
            msg += "```\n" + "\n".join(chunk) + "\n```"
            await dm.send(msg)
        await interaction.followup.send(
            "I've sent you a DM with the sound list.", ephemeral=True)
    except Exception as e:
        log.exception("Failed to DM sound list")
        await interaction.followup.send(
            "Failed to send DM. Do you have DMs disabled?", ephemeral=True)


AUDIO_TMPDIR = tempfile.mkdtemp(prefix="cobot_audio_")


async def sound_name_autocomplete(
        interaction: discord.Interaction,
        current: str) -> list[app_commands.Choice[str]]:
    if not sounds:
        return []
    scores = get_fuzzy_match_scores(current, sounds)
    # Sort by score, highest first
    sorted_keys = sorted(scores, key=scores.get, reverse=True)
    # Limit to 20 results (Discord's limit)
    return [
        app_commands.Choice(name=sounds[k], value=sounds[k])
        for k in sorted_keys[:20]
    ]


# Update your play command to use autocomplete
@tree.command(name='play', description='Play a sound')
@app_commands.describe(sound_name='Name of the sound to play')
@app_commands.autocomplete(sound_name=sound_name_autocomplete)
async def play(interaction: discord.Interaction, sound_name: str):
    if not sounds:
        await interaction.response.send_message(
            'Starting up, give me a minute!', ephemeral=True)
        return

    key = depunctuate(sound_name)
    if key not in sounds:
        await interaction.response.send_message(
            f"I don't know how to play `{sound_name}`.", ephemeral=True)
        return

    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message(
            'You must be in a voice channel to play sounds.', ephemeral=True)
        return

    channel = interaction.user.voice.channel
    await interaction.response.defer(ephemeral=True)

    if not interaction.guild.voice_client:
        vc = await channel.connect()
    else:
        vc = interaction.guild.voice_client

    await interaction.followup.send(f'Playing `{sounds[key]}`.',
                                    ephemeral=True)

    try:
        file_path = await audio_source.download(sounds[key], AUDIO_TMPDIR)
        loudness = await get_volume(file_path)
        audio_filter = filter_settings(loudness)
        if not os.path.exists(file_path):
            log.error(f"Audio file does not exist: {file_path}")
            return await interaction.followup.send(
                f"Error: Audio file for `{sounds[key]}` not found.",
                ephemeral=True)

        log.info(
            f"Audio file exists: {file_path}, size={os.path.getsize(file_path)} bytes"
        )
        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(source=file_path,
                                   options=f"-af {audio_filter}"))
        log.info(f"FFmpeg options: -af {audio_filter}")
        log.info(f"Voice client connected: {vc.is_connected()}")
        vc.play(source)
    except Exception as e:
        log.exception("Failed to play sound")
        await interaction.followup.send(f"Error: {e}", ephemeral=True)


async def join_voice_channel(interaction: discord.Interaction,
                             channel: discord.VoiceChannel):
    if interaction.guild.voice_client is not None:
        await interaction.guild.voice_client.move_to(channel)
    else:
        await channel.connect()


async def voice_channel_autocomplete(
        interaction: discord.Interaction,
        current: str) -> list[app_commands.Choice[str]]:
    if not interaction.guild:
        return []
    channels = [
        vc for vc in interaction.guild.voice_channels
        if current.lower() in vc.name.lower()
    ]
    # Limit to 20 results (Discord's limit)
    return [
        app_commands.Choice(name=vc.name, value=str(vc.id))
        for vc in channels[:20]
    ]


@tree.command(name='join', description="Join a specified voice channel")
@app_commands.describe(channel='Voice channel to join')
@app_commands.autocomplete(channel=voice_channel_autocomplete)
async def join(interaction: discord.Interaction, channel: str):
    vc = discord.utils.get(interaction.guild.voice_channels, id=int(channel))
    if not vc:
        await interaction.response.send_message("Channel not found.",
                                                ephemeral=True)
        return
    try:
        await join_voice_channel(interaction, vc)
    except discord.ClientException:
        # If the bot is already connected, just move it
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.move_to(vc)
    except Exception as e:
        log.exception("Failed to join voice channel")
        await interaction.response.send_message(
            f"Failed to join {vc.name}. Error: {e}", ephemeral=True)
        return
    else:
        log.info(f"Joined voice channel: {vc.name}")
        await interaction.response.send_message(f"Joined {vc.name}",
                                                ephemeral=True)


@tree.command(name='summon', description="Join your current voice channel")
async def summon(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message(
            "You are not connected to a voice channel", ephemeral=True)
        return

    channel = interaction.user.voice.channel
    await join_voice_channel(interaction, channel)
    await interaction.response.defer(ephemeral=True)  # Defer right away
    try:
        await join_voice_channel(interaction, channel)
        await interaction.followup.send(f"Joined {channel.name}",
                                        ephemeral=True)
    except Exception as e:
        await interaction.followup.send(
            f"Tried to summon the bot to {channel}, but it could not be found. Error: {e}",
            ephemeral=True)


@tree.command(name='leave', description="Leave any connected voice channel.")
async def leave(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        channel = vc.channel
        await vc.disconnect()
        await interaction.response.send_message(f"Leaving {channel}",
                                                ephemeral=True)
    else:
        await interaction.response.send_message(
            "I'm not in any voice channel on this server!", ephemeral=True)


@tree.command(name='stop', description="Stop any currently playing sound.")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("Stopped playing sound.",
                                                ephemeral=True)
    else:
        await interaction.response.send_message(
            "No sound is currently playing.", ephemeral=True)


@bot.event
async def on_ready():
    sound_list = audio_source.list_sounds()
    sounds.clear()
    sounds.update((depunctuate(s), s) for s in sound_list)
    log.info(
        f'Found {len(sounds)} sounds in {"local dir" if args.mock_audio else "S3"}.'
    )
    log.info('Logged in as')
    log.info(f'{bot.user.name=}')
    log.info(f'{bot.user.id}')
    log.info('--------------------------------------------')

    try:
        # Register commands globally
        synced = await bot.tree.sync()
        log.info(f"Synced {len(synced)} global commands.")
    except Exception as e:
        log.error(f"Failed to sync commands: {e}")


if __name__ == '__main__':
    token = os.environ['DISCORD_TOKEN']
    bot.run(token)
