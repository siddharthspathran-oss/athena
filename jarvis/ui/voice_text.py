"""What was said: the wake word, mishearings, hallucinations, follow-ups and echoes.

Pure text rules over Whisper's output - no audio, no state. Split out of voice.py
(31 Oct 2026); voice.py imports all of it back, so voice._strip_wake etc. still work.
"""
from __future__ import annotations

import re

from ..config import cfg


# Small speech models mangle proper nouns; these are what "Athena" tends to
# come back as. Cheaper and more predictable than lowering the match threshold.
WAKE_ALIASES = {
    "athena", "athena's", "athenas", "athina", "atheena", "athene", "athenna",
    "atena", "athenah", "asena", "athenia", "thena", "athana", "athen",
}


# Words that may come before the wake word without it counting as having been
# said mid-sentence. "Hey Athena" is addressing it; "...and I told Athena" is not.
_WAKE_LEAD_IN = {
    "hey", "hi", "hello", "ok", "okay", "so", "um", "uh", "er", "ah",
    "yo", "please", "and", "now",
}


# Openings of things said to Athena, as opposed to things said in a video.
_COMMANDISH = re.compile(
    r"^\W*(?:please\s+|can\s+you\s+|could\s+you\s+|will\s+you\s+)?"
    r"(?:open|close|quit|exit|kill|play|pause|resume|stop|skip|next|previous|mute|unmute|volume|"
    r"turn|set|call|ring|dial|text|message|send|whatsapp|email|mail|remind|add|remove|delete|"
    r"what|what's|whats|who|who's|when|where|how|which|why|is|are|do|does|did|tell|show|give|"
    r"read|look|check|search|find|google|book|reserve|order|start|launch|run|lock|shut|shutdown|"
    r"restart|sleep|take|explain|summari[sz]e|brief|list|write|type|create|make|schedule|go|"
    r"switch|minimi[sz]e|maximi[sz]e|screenshot|note|save|copy|paste|end|hang\s+up|answer|help|"
    r"i\s+need|i\s+want|let's|focus|study|hi|hello|hey|good|can|could|would|will|please|are|am|"
    r"any|thanks?|wake|you\s+there)\b", re.I)


# Words that join a follow-up to what came before: "and tomorrow?", "also
# open it", "okay now play something". Taken off before the command check.
_JOINERS = re.compile(r"^\W*(?:(?:and|also|then|but|so|now|actually|ok|okay|oh|wait|right)\W+)+", re.I)


# 15 Oct 2026: follow-ups (no "Athena") need a real command or question.
# _COMMANDISH also lets in "please", "good", "can", "hey"... - fine after the
# name, too loose for an open mic: "Please like, share, and subscribe" from a
# video was answered.
_FOLLOW_CMD = re.compile(
    r"^\W*(?:please\s+|can\s+you\s+|could\s+you\s+|will\s+you\s+|would\s+you\s+)?"
    r"(?:open|close|quit|exit|kill|play|pause|resume|stop|skip|next|previous|mute|unmute|volume|"
    r"turn|set|call|ring|dial|text|message|send|whatsapp|email|mail|remind|add|remove|delete|"
    r"tell\s+me|show\s+me|give\s+me|read|look\s+at|check|search|find|google|book|order|start|launch|"
    r"run|lock|shut|shutdown|restart|take|explain|summari[sz]e|brief|list|write|type|create|make|"
    r"schedule|switch|minimi[sz]e|maximi[sz]e|screenshot|note|save|copy|paste|hang\s+up|focus|"
    r"study|quiz|teach|repeat|cancel|undo)\b"
    r"|^\W*(?:what|what's|whats|who|who's|when|where|how|which|why)\b\W*\S+\W+\S+", re.I)


# What videos and streams say, that is never meant for Athena.
_MEDIA_TALK = re.compile(
    r"\b(?:like(?:,)?\s+(?:share|and\s+subscribe)|subscribe|hit\s+the\s+bell|smash\s+(?:that|the)\s+like|"
    r"thanks?\s+(?:you\s+)?for\s+watching|see\s+you\s+in\s+the\s+next|link\s+in\s+(?:the\s+)?(?:bio|description)|"
    r"comment\s+(?:below|down)|welcome\s+back\s+to\s+(?:my|the)\s+channel|sponsored\s+by)\b", re.I)


def _followish(text: str) -> bool:
    """Sounds like the next thing said to Athena, not room talk (9 Oct 2026):
    a real command or a question of three words or more once the joining
    words are off, a follow-up the agent understands ("what about DSA", "and
    tomorrow"), "open it", or a question Whisper punctuated as one."""
    from ..agent import _OPEN_IT
    if _MEDIA_TALK.search(text):
        return False
    core = _JOINERS.sub("", text)
    return bool(_FOLLOW_CMD.match(core) or _ABOUT.match(text) or _OPEN_IT.match(text)
                or (text.rstrip().endswith("?") and len(core.split()) >= 3))


def _reply_ok(text: str) -> bool:
    """After Athena asked something: the answer can be short ("yes", "the
    second one", "Friday") but not a long sentence from the room."""
    return not _MEDIA_TALK.search(text) and (_followish(text) or len(text.split()) <= 6)


def _clear_speech(segments) -> bool:
    """Whisper is sure this was clear, close speech - a video across the room
    and mumbles come back less certain. Stricter than the wake check, since
    there is no name to go on."""
    lp = min((float(s.avg_logprob or 0) for s in segments), default=-9.0)
    ns = max((float(s.no_speech_prob or 0) for s in segments), default=1.0)
    return (lp >= float(cfg.get("voice.follow_min_logprob", -0.75))
            and ns <= float(cfg.get("voice.follow_max_no_speech", 0.5)))


# Not the agent's own _FOLLOW: its bare "and <anything>" let a lyric through
# ("and then the ocean"); the agent can afford it, the open mic cannot.
_ABOUT = re.compile(r"^\W*(?:and\s+)?(?:what|how)\s+about\s+\S|^\W*(?:and\s+)?same\s+for\s+\S|"
                    r"^\W*(?:and\s+)?(?:for\s+)?(?:today|tomorrow|next\s+week)\W*$", re.I)


_FILLER_ONLY = re.compile(
    r"^\W*(?:yeah|yes|yep|yup|no|nope|okay|ok|oh|ah|um+|uh+|hmm+|right|alright|sure|thanks?(?:\s+you)?|"
    r"so|well|you\s+know|i\s+mean|what|huh|hey|hi|hello)(?:\W+(?:yeah|yes|okay|ok|so|oh|um+|uh+))*\W*$", re.I)


def _trusted_text(segments) -> str:
    """The transcript without the words Whisper invented after you stopped.

    Made-up words come after a pause and with low confidence; what you said
    does not. From the end backwards, a short run of words that follows a
    pause (or a sentence end) is dropped while its confidence is poor.
    """
    words = [w for s in segments for w in (getattr(s, "words", None) or [])]
    if not words:
        return " ".join(s.text for s in segments).strip()
    # Split into runs at pauses and sentence ends.
    runs: list[list] = [[words[0]]]
    for prev, w in zip(words, words[1:]):
        if w.start - prev.end >= 0.45 or prev.word.strip()[-1:] in ".?!":
            runs.append([w])
        else:
            runs[-1].append(w)
    while len(runs) > 1:
        tail = runs[-1]
        conf = sum(w.probability for w in tail) / len(tail)
        if len(tail) <= 4 and conf < float(cfg.get("voice.tail_min_prob", 0.5)):
            runs.pop()
        else:
            break
    return "".join(w.word for r in runs for w in r).strip()


def _echoes_prompt(text: str, segments, seconds: float, prompt: str) -> bool:
    """True when a transcript is Whisper repeating its own primer.

    The primer holds whole commands ("Athena, open Spotify and play music")
    because that is what makes the small model hear the names - and fed a
    cough, a fan or music, Whisper sometimes hands the primer back word for
    word. Spotify then opened with nobody having asked (28 Sep 2026).

    A real command can say the same words, so the words alone decide
    nothing. It is believed unless the decoder itself doubted there was
    speech, or the words came faster than anyone talks.
    """
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    if len(words) < 2:
        return False
    primer = " ".join(re.findall(r"[a-z0-9']+", prompt.lower()))
    if " ".join(words) not in primer:
        return False
    segs = list(segments or [])
    no_speech = max((float(getattr(s, "no_speech_prob", 0) or 0) for s in segs), default=0.0)
    logprob = min((float(getattr(s, "avg_logprob", 0) or 0) for s in segs), default=0.0)
    rate = len(words) / max(seconds - float(cfg.get("voice.silence_timeout", 0.65)), 0.3)
    return (no_speech > float(cfg.get("voice.primer_no_speech", 0.3))
            or logprob < float(cfg.get("voice.primer_logprob", -0.7))
            or rate > 4.5)


def _from_last_name(text: str) -> str:
    """ "Athena, play a music of, Athena, play a music of your choice" - you
    started over, so the last "Athena" is where the command begins."""
    hits = [m for m in re.finditer(r"\b[\w']+\b", text)
            if m.group(0).lower() in WAKE_ALIASES]
    if len(hits) > 1:
        return text[hits[-1].start():]
    return text


def _wake_word(segments):
    """The first wake-word-like word Whisper heard, with its timing, or None."""
    for seg in segments:
        for w in (getattr(seg, "words", None) or []):
            if re.sub(r"[^a-z']", "", w.word.lower()) in WAKE_ALIASES:
                return w
    return None


def _wake_probability(segments) -> float | None:
    """Whisper's confidence in the first wake-word-like word, or None."""
    w = _wake_word(segments)
    return float(w.probability) if w is not None else None


# Real words close enough to "Athena" to pass the fuzzy match - a history
# lecture or a video saying "Athens..." is not her name.
_REAL_WORDS = {"athens", "athene's", "attend", "athlete"}


def _strip_wake(text: str, wake: str, at_start: bool = True) -> tuple[bool, str]:
    """Detect the wake word and return (heard_it, the rest of the sentence).

    Matches exactly, against known mishearings, and finally by fuzzy ratio,
    so a slightly garbled wake word still gets through.

    at_start: require the wake word to open the sentence. Accepting it
        anywhere meant ordinary conversation that merely mentioned Athena was
        taken as a command. A few filler words may still precede it.
    """
    from difflib import SequenceMatcher

    def is_wake(w: str) -> bool:
        # Fuzzy only for words that start like the name and are long enough:
        # "then" is inside "a-THEN-a" and scored 0.8, so "And then turn the
        # text..." woke Athena up (26 Sep 2026).
        # 0.8, not 0.72: noise came back as "Artha, mister, from a mermaid..." and
        # "Atten, make your next step actions" (0.727 each) and was run (Oct 2026).
        return (w == wake or w in WAKE_ALIASES
                or (len(w) >= 5 and w[:1] == wake[:1] and w not in _REAL_WORDS
                    and SequenceMatcher(None, w, wake).ratio() >= 0.8))

    def scan(chunk: str) -> tuple[bool, str]:
        words = chunk.split()
        for i, raw in enumerate(words):
            w = raw.lower().strip(",.!?'\"-")
            if not w:
                continue
            if is_wake(w):
                return True, " ".join(words[i + 1:]).lstrip(" ,.!?'-")
            # Real words before the wake word mean it was mentioned, not used.
            if at_start and w not in _WAKE_LEAD_IN:
                return False, chunk
        return False, chunk

    if not at_start:
        return scan(text)

    # "At the start" means the start of a sentence, not of the recording. With
    # a TV or music in the room a recording never ends on a pause - it runs to
    # the 15-second cap - so "Athena, what time is it" arrives after whatever
    # else was said, and requiring it to open the whole transcript threw it
    # away every time. A sentence of its own still has to begin with it, so
    # "...and I told Athena about it" is still not a command.
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    for n, sentence in enumerate(sentences):
        hit, rest = scan(sentence)
        if hit:
            tail = " ".join(sentences[n + 1:])
            return True, (rest + " " + tail).strip()
    return False, text


# Fed music or noise instead of speech, Whisper invents subtitle credits from
# its training data. These are never real commands, so drop them silently.
_HALLUCINATIONS = (
    "subs by", "subtitles by", "subtitled by", "amara.org", "thanks for watching",
    "thank you for watching", "please subscribe", "like and subscribe",
    "transcription by", "captions by", "www.", ".com", ".org", ".co.uk",
)


# Hesitation noises and throwaway fragments. Treating these as commands is
# what makes an assistant feel like it is butting into your conversation.
_FILLER = {
    "", "you", "thank you", "thanks", "bye", "oh", "uh", "um", "umm", "uhh",
    "hmm", "hm", "mm", "mhm", "ah", "ahh", "eh", "er", "huh", "yeah", "yep",
    "ok", "okay", "right", "so", "well", "like", "hey", "hi", "what",
}


def fix_mishearings(text: str) -> str:
    """Repair substitutions Whisper makes consistently.

    "Open" at the start of a command comes back as "1", "One" or "Won" over
    and over - the leading plosive is quiet and the word is short. Without a
    verb the model has nothing to act on, so the command dies. Only the first
    word is touched, and only for these few high-confidence cases.
    """
    import re

    # Not before a number word - "one hundred and two" is a quantity, not a
    # command - and only when something follows that could be opened.
    return re.sub(
        r"^\s*(1|one|won|wan|opan|ope)\b[\s,.]*"
        r"(?!hundred|thousand|million|billion|dozen|percent|degrees?|"
        r"minutes?|seconds?|hours?|days?|weeks?|months?|years?|"
        r"o'?clock|o\b|am\b|pm\b|and\b|of\b|$)"
        r"(?=\S)",
        "open ", text, count=1, flags=re.IGNORECASE)


def _squash(word: str) -> set[str]:
    """Variants of a word with repeated letters collapsed.

    Whisper writes hesitations as it hears them - "hmmm", "ahhh", "ummmm" -
    so a fixed list never matches. Collapsing runs of letters catches them all.
    """
    import re

    return {word,
            re.sub(r"(.)\1{2,}", r"\1\1", word),
            re.sub(r"(.)\1+", r"\1", word)}


def is_hallucination(text: str) -> bool:
    """True if a transcript is noise, a Whisper artefact, or mere filler."""
    low = text.strip().lower()
    if not low:
        return True
    if any(h in low for h in _HALLUCINATIONS):
        return True

    stripped = low.strip(" .,!?-'\"")
    if _squash(stripped) & _FILLER:
        return True

    words = [w.strip(" .,!?-'\"") for w in stripped.split()]
    words = [w for w in words if w]
    if not words:
        return True

    # A single short word is never a real instruction.
    if len(words) == 1 and len(words[0]) <= 3:
        return True

    # Every word is a hesitation noise, e.g. "ahhh, umm, hmm".
    if all(_squash(w) & _FILLER for w in words):
        return True

    # Whisper loops on non-speech: "buzz, buzz, buzz, buzz, buzz".
    if len(words) >= 4 and len(set(words)) <= 2:
        return True

    return False


# With the speakers playing, her name alone doesn't count - only these short controls
# (or the hold key): "Athena, pause", "next song", "stop", "volume down".
_MEDIA_CMD = re.compile(r"^\W*(?:please\s+)?(?:stop|pause|resume|play|next|previous|skip|mute|unmute|quiet|shut\s+up|enough|"
                        r"volume|louder|quieter|turn\s+(?:it\s+)?(?:up|down|off)|go\s+back|cancel)\b[\w\s%,.'-]{0,40}$", re.I)


_WORD = re.compile(r"[A-Za-z0-9']+")


def _unsaid(answer: str, said: str) -> str:
    """What of the answer the streamed sentences haven't said yet, compared word by
    word: markdown, numbering and line breaks differ between the two, and a list of
    five files was read out and then the whole answer again (25 Oct 2026)."""
    found = list(_WORD.finditer(answer))
    words = [m.group().lower() for m in found]
    spoken = [w.lower() for w in _WORD.findall(said)]
    if not words or not spoken:
        return answer.strip() if not spoken else ""
    if words[:len(spoken)] == spoken:
        return answer[found[len(spoken) - 1].end():].strip(" \n.,;:-*") if len(spoken) < len(words) else ""
    return "" if len(spoken) >= 0.6 * len(words) else answer.strip()       # changed on the way: don't say it twice
