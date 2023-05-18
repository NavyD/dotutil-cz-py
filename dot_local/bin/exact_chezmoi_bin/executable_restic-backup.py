#!/usr/bin/env python3
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
    check_call([bin, 'backup'] +  # noqa W504
               (['--dry-run'] if dry_run else []) +  # noqa W504
               ['--verbose', '2',
                '--exclude-file', Path.home().joinpath('.restic.ignore'),
                '--tag', tags] +  # noqa W504
               ['/'])

    group = ','.join(['host', 'paths'])
    check_call([bin, 'forget'] +  # noqa W504
               (['--dry-run'] if dry_run else []) +  # noqa W504
               ['--tag', tags,
                '--group-by', group,
                '--prune',
                '--keep-hourly', '1',
                '--keep-weekly', '16',
                '--keep-daily', '14',
                '--keep-monthly', '18',
                '--keep-yearly', '3'
                ])


def main():
    bin = which('restic')
    if not bin:
        raise Exception('not found restic')

    dotenv.load_dotenv(Path.home().joinpath('.autorestic.env'))
    backup_all(bin)


if __name__ == "__main__":
    main()
