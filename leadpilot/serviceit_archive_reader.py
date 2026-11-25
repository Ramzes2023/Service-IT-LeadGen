"""Bounded 7z member reader; never extracts paths or executes archive content."""
import base64
import json
from pathlib import PurePosixPath
import sys

MAGIC = bytes.fromhex('377abcaf271c')
DOCUMENTS = {'.doc', '.docx', '.pdf', '.xlsx', '.xls', '.rtf', '.png', '.jpg', '.jpeg', '.txt'}


def read_members(raw):
    import libarchive
    if not raw.startswith(MAGIC) or len(raw) > 4_000_000:
        raise ValueError('SEVENZIP_SIGNATURE_OR_SIZE_INVALID')
    members = []
    seen = set()
    total = 0
    with libarchive.memory_reader(raw) as archive:
        for index, entry in enumerate(archive):
            name = entry.pathname
            if not isinstance(name, str):
                raise ValueError('ARCHIVE_NAME_INVALID')
            path = PurePosixPath(name)
            if (index >= 120 or not name or len(name) > 500 or path.is_absolute()
                    or '..' in path.parts or '\\' in name or ':' in name
                    or any(ord(c) < 32 for c in name) or name.casefold() in seen
                    or entry.issym or entry.islnk or entry.linkpath):
                raise ValueError('ARCHIVE_MEMBER_REJECTED')
            seen.add(name.casefold())
            if entry.isdir:
                continue
            if not entry.isfile or path.suffix.lower() not in DOCUMENTS:
                raise ValueError('ARCHIVE_CONTAINS_UNSUPPORTED_DOCUMENT')
            if not isinstance(entry.size, int) or not 0 <= entry.size <= 10_000_000 - total:
                raise ValueError('ARCHIVE_EXPANDED_SIZE_EXCEEDED')
            payload = bytearray()
            for block in entry.get_blocks():
                total += len(block)
                if total > 10_000_000 or len(payload) + len(block) > entry.size:
                    raise ValueError('ARCHIVE_EXPANDED_SIZE_EXCEEDED')
                payload.extend(block)
            if len(payload) != entry.size:
                raise ValueError('ARCHIVE_MEMBER_INCOMPLETE')
            members.append({'name': name, 'data': base64.b64encode(payload).decode('ascii')})
    if not members:
        raise ValueError('ARCHIVE_NO_DOCUMENTS')
    return members


def main():
    try:
        result = {'members': read_members(sys.stdin.buffer.read(4_000_001))}
    except Exception as exc:
        reason = str(exc)
        result = {'error': reason if reason.startswith(('ARCHIVE_', 'SEVENZIP_')) and reason.replace('_', '').isalnum() else 'SEVENZIP_READ_FAILED'}
    sys.stdout.write(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
