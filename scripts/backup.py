"""Consistent SQLite backup. Encryption key must be backed up separately."""
import argparse
import os
import sqlite3
from pathlib import Path

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    args=parser.parse_args()
    if not Path(args.source).is_file(): raise SystemExit('Source database does not exist')
    dest=Path(args.destination)
    if dest.exists(): raise SystemExit('Destination exists; refusing to overwrite')
    fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    with sqlite3.connect(args.source) as source, sqlite3.connect(dest) as target:
        source.backup(target)
    print('Encrypted application database backup created. Store ENCRYPTION_KEY separately.')
