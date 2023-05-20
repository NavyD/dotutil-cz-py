#!/usr/bin/env python3
import sys
from pathlib import Path
from shutil import which
from subprocess import check_call

import dotenv


# restic check && restic unlock
# restic backup --verbose 2 --exclude-file /home/navyd/.restic.ignore --tag ar:location:local /
# restic forget --tag ar:location:local --prune --keep-hourly 1 --keep-weekly 16 --group-by host --group-by tags --keep-daily 14 --keep-monthly 18 --keep-yearly 3
def backup_all(bin, dry_run=False):
    check_call([bin, 'check'])
    check_call([bin, 'unlock'])

    tags = ','.join(['all'])
    args = ([bin, 'backup'] +  # noqa W504
            (['--dry-run'] if dry_run else []) +  # noqa W504
            ['--verbose', '2',
                '--exclude-file', str(Path.home().joinpath('.restic.ignore')),
                '--tag', tags] +  # noqa W504
            ['/'])
    print(f'running: {" ".join(args)}')
    check_call(args)

    # default
    group = ','.join(['host', 'paths'])
    args = ([bin, 'forget'] +  # noqa W504
            (['--dry-run'] if dry_run else []) +  # noqa W504
            ['--tag', tags,
             '--group-by', group,
             '--prune',
             '--keep-hourly', '1',
             '--keep-weekly', '16',
             '--keep-daily', '14',
             '--keep-monthly', '18',
             '--keep-yearly', '3'])
    print(f'running: {" ".join(args)}')
    check_call(args)


def main():
    bin = which('restic')
    if not bin:
        raise Exception('not found restic')

    dotenv.load_dotenv(Path.home().joinpath('.autorestic.env'))

    try:
        if backup_db_bin := which('backup-db.sh'):
            print(f'backup database with {backup_db_bin}')
            check_call([backup_db_bin])
        backup_all(bin)
    except KeyboardInterrupt:
        print('Interrupt by user', file=sys.stderr)
        exit(1)


if __name__ == "__main__":
    main()
