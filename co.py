import discord
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
from fuzzywuzzy import fuzz

log = logging.getLogger('sqcobot')
log.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)

# create formatter
formatter = logging.Formatter('[%(levelname)s] %(message)s')

# add formatter to ch
ch.setFormatter(formatter)

# add ch to logger
log.addHandler(ch)


def get_mod_path():
    filepath = os.path.abspath(__file__)
    dirname, fname = os.path.split(filepath)
    return dirname


client = discord.Client()

audiodir = os.path.join(get_mod_path(), 'sounds')
description = 'Kernels of wisdom from fighter pilot legends.'
bot = dcmd.Bot(command_prefix='!co-', description=description)

log.info(f'{audiodir=}')

if not discord.opus.is_loaded():
    if not discord.opus._load_default():
        # the 'opus' library here is opus.dll on windows
        # or libopus.so on linux in the current directory
        # you should replace this with the location the
        # opus library is located in and with the proper filename.
        # note that on windows this DLL is automatically provided for you
        log.info(
            "Unable to find default location of libopus-0.so... trying opus.dll")
        discord.opus.load_opus('opus')


@bot.command(description='Send friendly salutations to this bot.')
async def hello(ctx):
    author = ctx.author
    msg = 'Hello {}'.format(author.mention)
    await ctx.send(msg)


@bot.command(description='Solicit some wisdom from the CO.')
async def play(ctx, sound_name=''):
    if sound_name == '':
        msg = 'These are the topics I can tell you about:\n'
        msg += '```\n'
        msg += '\n'.join(sorted(sounds.values()))
        msg += '```'
        await ctx.author.send(msg)
    else:
        await play_sound(ctx, sound_name)


async def join_channel(ctx, channel, errmsg=None):
    if ctx.voice_client is not None:
        try:
            await ctx.voice_client.move_to(channel)
        except dcmd.errors.BadArgument:
            if errmsg is not None:
                await ctx.send(errmsg)
    else:
        try:
            await channel.connect()
        except dcmd.errors.BadArgument:
            if errmsg is not None:
                await ctx.author.send(errmsg)


@bot.command(description="Join the given voice channel.")
async def join(ctx, *, channel: discord.VoiceChannel):
    """Joins a voice channel"""
    errmsg = "Can't find channel by the name of {}".format(channel)
    await join_channel(ctx, channel, errmsg)


@bot.command(description="Join the user's voice channel.")
async def summon(ctx):
    if ctx.author.voice and ctx.author.voice.channel:
        channel = ctx.author.voice.channel
        await join_channel(ctx, channel)
    else:
        await ctx.message.send("You are not connected to a voice channel")


@bot.command(description="Leave any connected voice channel.")
async def leave(ctx):
    try:
        await ctx.voice_client.disconnect()
    except AttributeError:
        await ctx.author.send("I'm not in any voice channel on this server!")


@bot.command(description="Stops any audio being played.")
async def stop(ctx):
    ctx.voice_client.stop()


async def get_volume(fname):
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-i", fname,
        "-af", "loudnorm=print_format=json",
        "-f", "null", "-",
        stderr=asyncio.subprocess.PIPE
    )
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
        "loudnorm=i=-15:tp=0:",
        f"measured_i={loudness['input_i']}:",
        f"measured_tp={loudness['input_tp']}:",
        f"measured_lra={loudness['input_lra']}:",
        f"measured_thresh={loudness['input_thresh']}:",
        "dual_mono=true:linear=true"
    ]
    return "".join(crazy_ffmpeg_filter_lines)


# Get an alpha only, lowercase version of the given sound name
def depunctuate(name):
    return re.sub(r'[\W_]+', '', name).lower()


def get_fuzzy_match_scores(name):
    return {s: fuzz.ratio(depunctuate(name), s) for s in sounds.keys()}


async def perform_fuzzy_search(ctx, name):
    MIN_SCORE_THRESHOLD = 50
    HIGH_SCORE_THRESHOLD = 70
    scores = get_fuzzy_match_scores(name)
    log.debug(f'fuzzy search results for {name}: {scores}')

    def limit_to(scores, level):
        return {name: score for name, score in scores.items() if score > level}

    scores = limit_to(scores, MIN_SCORE_THRESHOLD)
    log.debug(f'results with score closer than {MIN_SCORE_THRESHOLD}: {scores}')
    if len(scores) < 1:
        # we didn't find anything; bail with no results.
        return []
    elif len(scores) == 1:
        best_match = list(scores.keys())[0]
        # if there was exactly one match, just play it.
        s = f"Couldn't find an exact match for {name}, "
        s += f"but I'll play `{sounds[best_match]}` which is the "
        s += "closest I can find."
        await ctx.send(s)
        return [best_match]

    def score_from_pair(pair):
        return pair[1]

    high_scores = limit_to(scores, HIGH_SCORE_THRESHOLD)
    log.debug(f'{high_scores=}')

    high_scores_sorted = sorted(high_scores.items(),
                                key=score_from_pair)
    high_scores_sorted.reverse()
    log.debug(f'{high_scores_sorted=}')

    # we found something that's better than HIGH_SCORE_THRESHOLD
    # so let the user know and return it to be played.
    if len(high_scores_sorted) > 0:
        best_match = high_scores_sorted[0]
        s = f"Couldn't find an exact match for {name}, "
        s += f"but I'll play `{sounds[best_match[0]]}` which is pretty close."
        await ctx.send(s)
        return best_match

    best_sounds = [k for k, v in scores.items()]
    log.debug(f'{best_sounds=}')
    s = f"I couldn't find a good match for {name} "
    s += "but here's some things that are pretty close:\n"
    s += "```{}```".format('\n'.join(sounds[b] for b in best_sounds))
    await ctx.send(s)
    return []


async def play_sound(ctx, name):
    try:

        # If the user typed in an exact match, use that.
        if name in sounds.values():
            fname = sound_name_to_filename(name)
        # otherwise, perform a fuzzy search.
        else:
            search_results = await perform_fuzzy_search(ctx, name)
            # results = find_sound(name)

            if len(search_results) < 1:
                return await ctx.author.send(f"I couldn't figure out how to play ```{name}```")
            else:
                fname = sound_name_to_filename(sounds[search_results[0]])

        log.debug(f'About to play a sound with the name {fname}.')
        assert(os.path.exists(fname))
        loudness = await get_volume(fname)
        audio_filter = filter_settings(loudness)

        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(
            source=fname,
            options="-af " + audio_filter
        ))
        try:
            ctx.voice_client.play(
                source, after=lambda e: print(
                    'Player error: %s' %
                    e) if e else None)
        except AttributeError:
            await ctx.author.send('I need to be in a voice channel to do that!')

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
    sounds = [os.path.split(fname)[1][:-4] for fname in file_list]
    # Map the depunctuated version of each sound name to their actual name
    sounds = dict((depunctuate(s), s) for s in sounds)
    log.info('Logged in as')
    log.info(f'{bot.user.name=}')
    log.info(f'{bot.user.id}')
    log.info('------')


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
