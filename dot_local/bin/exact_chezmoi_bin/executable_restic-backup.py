#!/usr/bin/env python3
import logging
import sys
from pathlib import Path
from shutil import which
from subprocess import check_call

import dotenv

log = logging.getLogger(Path(__file__).stem)

# restic check && restic unlock
# restic backup --verbose 2 --exclude-file /home/navyd/.restic.ignore --tag ar:location:local /
# restic forget --tag ar:location:local --prune --keep-hourly 1 --keep-weekly 16 --group-by host --group-by tags --keep-daily 14 --keep-monthly 18 --keep-yearly 3


def backup_all(bin, dry_run=False):
    log.info('pre checking and unlock for backup')
    check_call([bin, 'check'])
    check_call([bin, 'unlock'])

    tags = ','.join(['all'])
    args = ([bin, 'backup'] +  # noqa W504
            (['--dry-run'] if dry_run else []) +  # noqa W504
            ['--verbose', '2',
                '--exclude-file', str(Path.home().joinpath('.restic.ignore')),
                '--tag', tags] +  # noqa W504
            ['/'])
    log.info(f'running: {" ".join(args)}')
    check_call(args)

    args = ([bin, 'forget'] +  # noqa W504
            (['--dry-run'] if dry_run else []) +  # noqa W504
            ['--tag', tags,
             '--group-by', ','.join(['host', 'paths']),  # default host,paths
             '--prune',
             # Removing snapshots according to a policy:
             # https://restic.readthedocs.io/en/stable/060_forget.html#removing-snapshots-according-to-a-policy
             '--keep-hourly', '1',
             '--keep-daily', '7',
             '--keep-weekly', '6'])
    log.info(f'running: {" ".join(args)}')
    check_call(args)


def main():
    logging.basicConfig(format='%(asctime)s.%(msecs)03d [%(levelname)-5s] [%(name)s.%(funcName)s]: %(message)s',
                        level=logging.ERROR,
                        datefmt='%Y-%m-%d %H:%M:%S')
    log.setLevel(logging.INFO)

    bin = which('restic')
    if not bin:
        raise Exception('not found restic')

    envfile = Path.home().joinpath('.autorestic.env')
    log.debug(f'loading restic env from {envfile}')
    dotenv.load_dotenv(envfile)

    try:
        if backup_db_bin := which('backup-db.sh'):
            log.info(f'backup database with {backup_db_bin}')
            check_call([backup_db_bin])
        backup_all(bin)
    except KeyboardInterrupt:
        print('Interrupt by user', file=sys.stderr)
        exit(1)


if __name__ == "__main__":
    main()
