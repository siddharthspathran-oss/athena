"""Reading text in a picture with Windows' own OCR (Windows.Media.Ocr).

Built into Windows, runs on the CPU in a fraction of a second, and needs no
model on the GPU - so "read the text on my screen" no longer swaps the 6 GB
vision model in, and a window whose text UI Automation cannot reach (a PDF
viewer, a video, a game, a picture) still gives its words exactly (9 Oct 2026).
"""
from __future__ import annotations

import asyncio
from pathlib import Path


async def _read(data: bytes) -> str:
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(data)
    await writer.store_async()
    writer.detach_stream()
    stream.seek(0)
    bitmap = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return ""
    result = await engine.recognize_async(bitmap)
    return "\n".join(line.text for line in result.lines)


def text(path: str | Path) -> str:
    """The text in an image file, line by line; '' if none or OCR is unavailable."""
    try:
        return asyncio.run(_read(Path(path).read_bytes())).strip()
    except Exception as e:
        print(f"[ocr: {e}]")
        return ""


if __name__ == "__main__":
    import sys
    print(text(sys.argv[1]))
