import discord
from discord import AutocompleteContext, ApplicationContext
from discord.utils import basic_autocomplete
import os
import os.path
import glob
from discord.ext import commands as dcmd
import json
import asyncio
import re
import traceback
import sys
import logging
from textwrap import wrap
from fuzzywuzzy import fuzz
import datetime
import typing

log = logging.getLogger('sqcobot')
log.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)

formatter = logging.Formatter('[%(levelname)s] %(message)s')
ch.setFormatter(formatter)
log.addHandler(ch)
logging.getLogger('discord').addHandler(ch)
logging.getLogger('discord').setLevel(logging.INFO)


def get_mod_path():
    filepath = os.path.abspath(__file__)
    dirname, fname = os.path.split(filepath)
    return dirname


def chunk_strings_into(li, chunksize):
    """
    Given a list of strings, return a list of lists. The total length
    of all strings in each nested list is no larger than chunksize.
    """
    chunked = [
        x.split() for x in wrap(' '.join(li),
                                width=chunksize,
                                break_long_words=False,
                                break_on_hyphens=False)
    ]
    return chunked


intents = discord.Intents.default()
intents.guild_messages = True
intents.dm_messages = True
intents.message_content = True
intents.messages = True

audiodir = os.path.join(get_mod_path(), 'sounds')
description = 'Kernels of wisdom from fighter pilot legends.'
bot = dcmd.Bot(intents=intents,
               command_prefix='!co-',
               description=description,
               debug_guilds=[582602200619024406])

log.info(f'{audiodir=}')

if not discord.opus.is_loaded():
    if not discord.opus._load_default():
        # the 'opus' library here is opus.dll on windows
        # or libopus.so on linux in the current directory
        # you should replace this with the location the
        # opus library is located in and with the proper filename.
        # note that on windows this DLL is automatically provided for you
        log.info(
            "Unable to find default location of libopus-0.so... trying opus.dll"
        )
        discord.opus.load_opus('opus')


async def perform_fuzzy_search(ctx: AutocompleteContext) -> typing.List[str]:
    name = ctx.options['sound_name']
    scores = get_fuzzy_match_scores(name)
    scores = sorted(scores, key=scores.get, reverse=True)
    log.debug(f'fuzzy search results for {name}: {scores}')
    return [sounds[s] for s in scores]


@bot.slash_command(name='list', description='List possible sounds')
async def list_sounds(ctx: ApplicationContext):
    DISCORD_MAX_MESSAGE_LEN = 2000
    HELP_MSG_PREAMBLE = 'These are the topics I can tell you about:\n'
    chunksize = DISCORD_MAX_MESSAGE_LEN - len(HELP_MSG_PREAMBLE) - 32
    sounds_sorted = sorted(sounds.values())
    sounds_chunked = chunk_strings_into(sounds_sorted, chunksize)
    await ctx.respond(f"Sending a list of sounds to {ctx.author.display_name}")

    msg = HELP_MSG_PREAMBLE
    for sound_list in sounds_chunked:
        msg += '```\n'
        msg += '\n'.join(sound_list)
        msg += '```'
        await ctx.author.send(msg)
        msg = ''
        await asyncio.sleep(0.1)


PLAY_DESC = 'Solicit some wisdom from the CO.'


@bot.slash_command(name='play', description=PLAY_DESC)
@discord.option("sound_name",
                description='Name of the sound to play',
                autocomplete=perform_fuzzy_search)
async def play(ctx: ApplicationContext, sound_name: str):
    """Solicit some wisdom from the CO."""
    if 'sounds' not in globals():
        await ctx.respond('Starting up, give me a minute!')
        return

    await ctx.respond('Playing `{sound_name}`.')
    await play_sound(ctx, sound_name)


async def join_channel(ctx, channel):
    if ctx.voice_client is not None:
        await ctx.voice_client.move_to(channel)
    else:
        await channel.connect()


async def list_voice_channels(ctx: ApplicationContext):
    channels = ctx.guild.voice_channels
    return [channel.name for channel in channels]


@bot.slash_command(description="Join the given voice channel.")
async def join(ctx: ApplicationContext, channel: discord.VoiceChannel):
    """Joins a voice channel"""
    ctx.guild.voice_channels[0]
    try:
        await join_channel(ctx, channel)
    except dcmd.errors.BadArgument:
        await ctx.respond(f"Can't find channel by the name of {channel}")
    else:
        await ctx.respond(f"Joined {channel}")


@bot.slash_command(description="Join your current voice channel")
async def summon(ctx: ApplicationContext):
    is_in_voice = ctx.author.voice and ctx.author.voice.channel
    if not is_in_voice:
        await ctx.respond("You are not connected to a voice channel")
        return

    channel = ctx.author.voice.channel
    try:
        await join_channel(ctx, channel)
    except dcmd.errors.BadArgument:
        await ctx.respond(
            f"Tried to summon the bot to {channel}, but it could not be found")
    else:
        await ctx.respond(f"Joined {channel.name}")


@bot.slash_command(description="Leave any connected voice channel.")
async def leave(ctx: ApplicationContext):
    channel = ctx.voice_client.channel
    try:
        await ctx.voice_client.disconnect()
    except AttributeError:
        await ctx.respond("I'm not in any voice channel on this server!")
    else:
        await ctx.respond(f"Leaving {channel}")


@bot.slash_command(description="Stops any audio being played.")
async def stop(ctx: ApplicationContext):
    ctx.voice_clirespondent.stop()
    await ctx.respond("Sorry, shutting up!")


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
    # Lol ffmpeg doesn't meaningfully split output JSON from other junk.
    return json.loads("{" + stderr.decode().strip().split("{")[-1])


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


# Get an alpha only, lowercase version of the given sound name
def depunctuate(name: str):
    return re.sub(r'[\W_]+', '', name).lower()


def get_fuzzy_match_scores(name: str):
    return {s: fuzz.partial_ratio(depunctuate(name), s) for s in sounds.keys()}


async def play_sound(ctx, name):
    try:
        if name in sounds.values():
            fname = sound_name_to_filename(name)

        log.info(f'About to play a sound with the name {fname}.')
        assert (os.path.exists(fname))
        loudness = await get_volume(fname)
        audio_filter = filter_settings(loudness)

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(source=fname,
                                   options="-af " + audio_filter))
        try:
            ctx.voice_client.play(source,
                                  after=lambda e: print('Player error: %s' % e)
                                  if e else None)
        except AttributeError:
            await ctx.respond('I need to be in a voice channel to do that!')

    except Exception as e:
        fmt = 'An error occurred while processing this request: ```py\n{}\n```'
        exc_type, exc_value, exc_tb = sys.exc_info()
        log.exception('Unhandled exception while processing the request')
        s = traceback.format_exception(exc_type, exc_value, exc_tb)
        await ctx.send(fmt.format(''.join(s)))


def sound_name_to_filename(name):
    return f"{os.path.join(audiodir, name)}.ogg"


@bot.event
async def on_ready():
    global sounds
    file_list = glob.glob('{}/*.ogg'.format(audiodir))
    sound_list = [os.path.split(fname)[1][:-4] for fname in file_list]
    # Map the depunctuated version of each sound name to their actual name
    sounds = dict((depunctuate(s), s) for s in sound_list)
    log.info(f'Found {len(sounds)} sounds.')
    log.info('Logged in as')
    log.info(f'{bot.user.name=}')
    log.info(f'{bot.user.id}')
    log.info('------')


@bot.event
async def on_error(event, *args, **kwargs):
    RGB_ERROR_RED = 0xE74C3C
    embed = discord.Embed(title=':x: Event Error', colour=RGB_ERROR_RED)
    embed.add_field(name='Event', value=event)
    embed.description = '```py\n%s\n```' % traceback.format_exc()
    embed.timestamp = datetime.datetime.utcnow()
    log.warning(traceback.format_exc())
    await bot.AppInfo.owner.send(embed=embed)


if __name__ == '__main__':
    configfile = os.path.join(get_mod_path(), 'config.json')
    if not os.path.exists(configfile):
        log.warning('Warning, config file not found.')
        print("Config file not found, please enter your auth token here:")
        token = input('--> ')
        token = token.strip()
        with open(configfile, 'w') as jsonconfig:
            json.dump({'token': token}, jsonconfig)

    with open(configfile, 'r') as jsonconfig:
        config = json.load(jsonconfig)
        token = config['token']
        if token == 'YOUR_TOKEN_HERE':
            raise ValueError("You must set a token in config.json.")
    bot.run(token)
