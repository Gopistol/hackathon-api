"""숏폼 자막: 대본을 화면에 맞는 짧은 조각으로 나누고, 음성에 맞춰 시간을 붙인다.

시간은 ElevenLabs forced alignment 의 글자별 시각을 쓴다. 정렬이 없으면 글자 수 비율로 나눈다.
결과는 영상에 굽는 .ass 와 플레이어용 .vtt 두 가지로 쓴다.
"""

import math
import re
from dataclasses import dataclass

# 한 번에 보이는 자막 최대 글자 수 (2줄 기준). 세로 720px 화면에서 읽기 편한 길이.
MAX_CHARS = {"ko": 24, "zh": 20, "en": 52}

# 자막 위치·크기는 720x1280 기준. 아래 여백은 피드 화면의 가게 카드에 가리지 않게 넉넉히 둔다.
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},50,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,1,2,56,56,300,1
Style: Title,{font},62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,5,2,8,48,48,170,1
Style: Card,{font},54,&H00FFFFFF,&H00FFFFFF,&H40101010,&H40101010,-1,0,0,0,100,100,0,0,3,22,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


@dataclass
class Cue:
    start: float
    end: float
    text: str


# (글자, 시작초, 끝초). 이어 붙이면 대본 text 와 같아야 한다.
Alignment = list[tuple[str, float, float]]


def build_cues(text: str, lang: str, duration: float, alignment: Alignment | None = None) -> list[Cue]:
    chunks = _chunks(text, MAX_CHARS.get(lang, 24))
    if alignment and "".join(c for c, _, _ in alignment) == text:
        cues = _timed_by_alignment(text, chunks, alignment)
    else:
        cues = _timed_by_length(chunks, duration)
    # 다음 자막이 나올 때까지 앞 자막을 남겨 깜빡임을 줄인다.
    for a, b in zip(cues, cues[1:]):
        a.end = max(a.end, min(b.start, a.end + 0.6))
    return cues


def _chunks(text: str, limit: int) -> list[str]:
    """문장 → 쉼표 → 띄어쓰기 순서로 끊어 limit 글자 이하 조각을 만든다."""
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?。！？])\s*", text.strip()):
        if not sentence:
            continue
        parts = re.split(r"(?<=[,，、])\s*", sentence) if len(sentence) > limit else [sentence]
        buf = ""
        for part in parts:
            if buf and len(buf) + len(part) + 1 > limit:
                out.append(buf)
                buf = ""
            buf = f"{buf} {part}".strip() if buf and not _is_cjk_join(buf, part) else buf + part
        if buf:
            out.append(buf)
    return [piece for chunk in out for piece in _split_long(chunk, limit)]


def _is_cjk_join(left: str, right: str) -> bool:
    # 중국어는 띄어쓰기 없이 잇는다.
    return bool(re.search(r"[一-鿿，、]$", left)) and bool(re.match(r"[一-鿿]", right))


def _split_long(chunk: str, limit: int) -> list[str]:
    """긴 조각을 비슷한 길이로 나눈다. 끝에 "있어요." 같은 짧은 꼬리가 혼자 남지 않게 한다."""
    if len(chunk) <= limit:
        return [chunk]
    target = math.ceil(len(chunk) / math.ceil(len(chunk) / limit))
    words = chunk.split(" ")
    if len(words) == 1:  # 띄어쓰기가 없는 중국어는 글자 수로 자른다
        return [chunk[i : i + target] for i in range(0, len(chunk), target)]
    out, buf = [], ""
    for w in words:
        if buf and len(buf) + len(w) + 1 > target and len(buf) >= target * 0.6:
            out.append(buf)
            buf = w
        else:
            buf = f"{buf} {w}".strip()
    return out + ([buf] if buf else [])


def _timed_by_alignment(text: str, chunks: list[str], alignment: Alignment) -> list[Cue]:
    cues, pos = [], 0
    for chunk in chunks:
        start = text.find(chunk, pos)
        if start < 0:  # 조각을 못 찾으면 정렬을 포기하고 비율로 나눈다
            return _timed_by_length(chunks, alignment[-1][2])
        end = start + len(chunk)
        spoken = [a for a in alignment[start:end] if a[0].strip()]
        cues.append(Cue(spoken[0][1], spoken[-1][2], chunk))
        pos = end
    return cues


def _timed_by_length(chunks: list[str], duration: float) -> list[Cue]:
    total = sum(len(c) for c in chunks) or 1
    cues, t = [], 0.0
    for c in chunks:
        end = t + duration * len(c) / total
        cues.append(Cue(t, end, c))
        t = end
    return cues


def to_vtt(cues: list[Cue]) -> str:
    lines = ["WEBVTT", ""]
    for c in cues:
        lines += [f"{_vtt_ts(c.start)} --> {_vtt_ts(c.end)}", c.text, ""]
    return "\n".join(lines)


# 가격은 노란색으로 강조한다. 예: 4천 원, 4,000원, ₩4,000, four thousand won, 4000韩元
PRICE = re.compile(
    r"(₩\s?[\d,]+|[\d,]+\s?천?\s?원|[\d,]+\s?won\b|\b[a-z-]+ thousand won\b|[\d一二三四五六七八九十百千万]+\s?韩元)",
    re.IGNORECASE,
)
YELLOW, WHITE = r"{\c&H0035D7FF&}", r"{\c&H00FFFFFF&}"
# 자막이 살짝 작게 시작해 커지며 나타나는 효과
POP = r"{\fad(80,0)\fscx86\fscy86\t(0,140,\fscx100\fscy100)}"


def to_ass(cues: list[Cue], font: str, duration: float = 0.0, title: str = "", card: list[str] | None = None) -> str:
    """자막 + 시작 제목(처음 2.4초) + 끝 정보 카드(마지막 3초)."""
    events = [_event("Default", c.start, c.end, POP + _highlight(_escape(c.text))) for c in cues]
    if title:
        events.append(_event("Title", 0.15, 2.4, r"{\fad(200,250)}" + _escape(title)))
    if card and duration > 4:
        text = r"\N".join(_highlight(_escape(line)) for line in card)
        events.append(_event("Card", duration - 3.0, duration, r"{\fad(250,0)}" + text))
    return ASS_HEADER.format(font=font) + "\n".join(events) + "\n"


def _event(style: str, start: float, end: float, text: str) -> str:
    return f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(end)},{style},,0,0,0,,{text}"


def _escape(text: str) -> str:
    # ASS 는 { } 를 효과 태그로, \ 를 줄바꿈 등으로 읽는다.
    return text.replace("{", "(").replace("}", ")").replace("\\", "/").replace("\n", " ")


def _highlight(text: str) -> str:
    return PRICE.sub(lambda m: YELLOW + m.group(0) + WHITE, text)


def _vtt_ts(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3_600_000:02}:{ms // 60_000 % 60:02}:{ms // 1000 % 60:02}.{ms % 1000:03}"


def _ass_ts(seconds: float) -> str:
    cs = round(seconds * 100)
    return f"{cs // 360_000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"
