"""What the screens say, in the design guide's voice (나루 디자인 참고 조사 §5):
friendly 해요체, words everyone knows, and failures that end with what
happens next.

engine.py's status strings stay as they are - code compares them - and are
only translated here, on their way to the screen.
"""

from __future__ import annotations

STATUS = {
    "대기": "꺼져 있어요",
    "실행 중": "켜져 있어요",
    "압축·전송 중": "보내는 중이에요",
    "일시중지": "잠시 멈췄어요",
    "수신 대기": "받을 준비가 됐어요",
}


def status(raw: str) -> str:
    return STATUS.get(raw, raw)
