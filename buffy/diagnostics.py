"""Export retained logs without the GitHub URL's length limit."""
import os
import platform
import tempfile
from datetime import datetime

from buffy import __version__
from diagnostics import mask_secrets


def save_report(folder, description, raw_lines, secrets, status, logger):
    sections = [f'Buffy diagnostic report\nVersion: {__version__}\nSystem: {platform.platform()}\n'
                f'Created: {datetime.now().astimezone().isoformat()}\nBot: {status}\n\n'
                f'Description:\n{description}\n\n'
                'Contains all currently retained logs; older rotated data may have expired.\n']
    # Block rollover while taking a consistent snapshot of both files.
    handlers = list(logger.handlers)
    for handler in handlers:
        handler.acquire()
    try:
        for handler in handlers:
            handler.flush()
        for name in ('buffy.log.1', 'buffy.log'):
            try:
                with open(os.path.join(folder, name), encoding='utf-8', errors='replace') as source:
                    sections.append(f'\n===== {name} =====\n' + source.read())
            except FileNotFoundError:
                sections.append(f'\n===== {name}: not available =====\n')
            except OSError:
                sections.append(f'\n===== {name}: read failed; current memory log follows =====\n')
    finally:
        for handler in reversed(handlers):
            handler.release()
    sections.append('\n===== Current session memory (may overlap disk log) =====\n' + '\n'.join(raw_lines))
    report = mask_secrets('\n'.join(sections), secrets)
    reports = os.path.join(folder, 'reports')
    os.makedirs(reports, exist_ok=True)
    target = os.path.join(reports, 'Buffy-diagnostic.txt')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=reports, delete=False) as out:
            temporary = out.name
            out.write(report)
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return target
