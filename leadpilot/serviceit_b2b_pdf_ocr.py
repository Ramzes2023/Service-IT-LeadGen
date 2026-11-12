"""Bounded, local OCR for public PDFs without a usable text layer."""
from pathlib import Path
import subprocess
import sys

DATA_DIR = Path("/opt/serviceit/tessdata")
MAX_INPUT = 4_000_000
MAX_TEXT = 24000
MAX_PAGES = 3


def recognize_pdf(data, *, data_dir=DATA_DIR):
    """Return tentative text, or an empty string if OCR cannot read the scan."""
    if not 0 < len(data) <= MAX_INPUT:
        return ""
    try:
        if not all((data_dir / (lang + ".traineddata")).is_file()
                   for lang in ("rus", "eng")):
            return ""
        result = subprocess.run(
            [sys.executable, "-IB", str(Path(__file__).resolve()), str(data_dir)],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={"LC_ALL": "C.UTF-8", "OMP_THREAD_LIMIT": "1"},
            timeout=22, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode:
        return ""
    return result.stdout.decode("utf-8", errors="replace")[:MAX_TEXT]


def _recognize(data, data_dir):
    import pypdfium2
    import tesserocr

    chunks = []
    with pypdfium2.PdfDocument(data) as document:
        if not 0 < len(document) <= 200:
            return ""
        with tesserocr.PyTessBaseAPI(
            path=data_dir, lang="rus+eng", psm=tesserocr.PSM.AUTO,
        ) as api:
            for index in range(min(len(document), MAX_PAGES)):
                page = document[index]
                try:
                    width, height = page.get_size()
                    if not (1 <= min(width, height) <= max(width, height) <= 5000):
                        continue
                    scale = min(2.5, 2100 / max(width, height))
                    bitmap = page.render(scale=scale)
                    try:
                        # Keep Pillow's view alive only while the bitmap is open.
                        with bitmap.to_pil() as image:
                            api.SetImage(image)
                            value = api.GetUTF8Text()
                            if api.MeanTextConf() >= 60:
                                chunks.append(value)
                    finally:
                        bitmap.close()
                finally:
                    page.close()
    return "\n".join(chunks)[:MAX_TEXT]


if __name__ == "__main__":
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (17, 19))
    resource.setrlimit(resource.RLIMIT_AS, (400_000_000, 400_000_000))
    payload = sys.stdin.buffer.read(MAX_INPUT + 1)
    if 0 < len(payload) <= MAX_INPUT:
        sys.stdout.buffer.write(_recognize(payload, sys.argv[1]).encode("utf-8"))
