"""Reuse native crawler access holds before admitting new paid searches."""
from contextlib import closing
from pathlib import Path
import sqlite3

from focused_frontier import DIRECTORY_HOSTS
from source_groups import positive_sites


def held_profile_sources(roots):
    sites, errors = {}, []
    for root in roots:
        path = Path(root).resolve() / 'frontier.sqlite'
        if not path.exists():
            continue
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
                rows = db.execute('''SELECT site,count(*) FROM pages WHERE state='error'
                    AND (reason LIKE 'Go fetch:%status 403%' OR reason LIKE 'Go fetch:%status 429%')
                    GROUP BY site HAVING count(*)>=3''').fetchall()
            for site, count in rows:
                if site in DIRECTORY_HOSTS:
                    item = sites.setdefault(site, {'deniedPages': 0, 'frontiers': []})
                    item['deniedPages'] += count
                    item['frontiers'].append(str(path))
        except (OSError, sqlite3.Error) as error:
            errors.append({'frontier': str(path), 'error': type(error).__name__})
    return {'sites': sites, 'errors': errors}
