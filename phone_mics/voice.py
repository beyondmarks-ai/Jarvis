"""Two approved Sarvam output voices, independent of personality and identity."""
import re

VOICES = {'male': 'shubh', 'female': 'priya'}


def validate_voice(voice):
    if not isinstance(voice, str) or voice not in VOICES:
        raise ValueError('Choose male or female')
    return voice


def speaker_for(voice):
    return VOICES[validate_voice(voice)]


def voice_command(text):
    clean = text.lower().strip().rstrip('.!?।').strip()
    clean = re.sub(r'^(?:hey jarvis[, ]+|jarvis[, ]+|please )', '', clean)
    if clean in ('change voice', 'change your voice', 'switch voice', 'switch your voice',
                 'voice change karo', 'आवाज़ बदलो', 'आवाज बदलो', 'वॉइस चेंज करो'):
        return 'choose'
    patterns = (
        r'(?:(?:switch|change)(?: your voice)? to |(?:use|enable|activate) )?(?:a |the )?(male|female) voice(?: please)?',
        r'(?:set|change) (?:your |the )?voice to (?:a |the )?(male|female)(?: voice)?',
        r'(male|female) voice (?:on karo|karo|mein bolo|me bolo)',
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, clean)
        if match:
            return match[1]
    match = re.fullmatch(r'(मेल|फीमेल|फ़ीमेल) (?:वॉइस|वॉयस|आवाज़|आवाज) (?:करो|चालू करो|में बोलो)', clean)
    if match:
        return 'male' if match[1] == 'मेल' else 'female'
    return None
