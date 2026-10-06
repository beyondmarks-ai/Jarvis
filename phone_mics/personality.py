"""Exactly two presentation styles. Never changes storage or safety rules."""
import re

MODES = ('normal', 'genz')


def validate_mode(mode):
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError('Choose normal or genz')
    return mode


def mode_command(text):
    clean = text.lower().strip().rstrip('.!?।').strip()
    clean = re.sub(r'^(?:hey jarvis[, ]+|jarvis[, ]+|please )', '', clean)
    match = re.fullmatch(r'(?:(?:switch|change)(?: me)? to |(?:set|enable|activate|turn on) )?(gen[ -]?z|normal) mode(?: please)?', clean)
    if match:
        return 'normal' if match[1] == 'normal' else 'genz'
    match = re.fullmatch(r'(gen[ -]?z|normal) mode (?:on karo|chalu karo|mein aa jao|mein aao)', clean)
    if match:
        return 'normal' if match[1] == 'normal' else 'genz'
    match = re.fullmatch(r'(जेन ज़ी|जेन जी|जेन जेड|जेन ज़ेड|नॉर्मल|नार्मल) मोड (?:ऑन करो|चालू करो|में आओ|में आ जाओ)', clean)
    if match:
        return 'normal' if match[1] in ('नॉर्मल', 'नार्मल') else 'genz'
    return None


def style_instruction(mode):
    validate_mode(mode)
    if mode == 'normal':
        return (' Personality: NORMAL. Be friendly, clear and straightforward. '
                'Avoid forced slang, teasing, or a Gen Z persona. Do not copy a different style from recent context.')
    return (' Personality: GEN Z. Sound like a friendly, witty young conversational companion, '
            'not a formal assistant. Keep answers short and useful. Use occasional natural slang '
            'such as bro, low-key or that is a win, not in every sentence. Light teasing is okay '
            'only for harmless topics; never insult or shame the user. No slurs or abusive language. '
            'For Hindi, use natural spoken Hinglish, like "क्या सीन है, ब्रो? चल, स्टेप बाय स्टेप करते हैं।" '
            'Write Hindi in Devanagari and English phrases in pronounceable form for speech. '
            'For English, stay in English; for other languages keep their natural phrasing. '
            'For distress, health, emergencies or other serious topics be calm and respectful, without jokes. '
            'Never sacrifice accuracy, missing-information honesty, exact facts, deletion consent, '
            'counts or confirmation instructions for personality. Never claim an action happened without verification. '
            'This is a presentation style only, not a change of capabilities or an extra mode.')
