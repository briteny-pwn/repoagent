"""Generate deployment secrets without printing them. Run once before deployment."""
import argparse
import base64
import os
from pathlib import Path
import secrets
import re


def initialize(root: Path, domain: str):
    if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?',domain):
        raise ValueError('Use a hostname only, without scheme, path, or port')
    env=root/'.env'
    if env.exists():
        raise FileExistsError('.env already exists; refusing to replace encryption keys')
    controller=secrets.token_urlsafe(32)
    template=(root/'.env.example').read_text()
    replacements={'ADMIN_PASSWORD':secrets.token_urlsafe(32),'ENCRYPTION_KEY':base64.urlsafe_b64encode(os.urandom(32)).decode(),'MIHOMO_CONTROLLER_SECRET':controller,'SITE_ADDRESS':domain}
    content='\n'.join(f'{line.split("=",1)[0]}={replacements[line.split("=",1)[0]]}' if '=' in line and line.split('=',1)[0] in replacements else line for line in template.splitlines())+'\n'
    with env.open('x') as f:
        os.chmod(env,0o600);f.write(content)
    proxy=root/'data'/'proxy'
    proxy.mkdir(parents=True,exist_ok=True)
    os.chmod(root/'data',0o700);os.chmod(proxy,0o700)
    config=proxy/'config.yaml'
    if not config.exists():
        with config.open('x') as f:
            os.chmod(config,0o600)
            f.write(f'mixed-port: 7890\nallow-lan: false\nbind-address: 127.0.0.1\nexternal-controller: 127.0.0.1:9090\nsecret: "{controller}"\nmode: rule\nrules:\n  - MATCH,DIRECT\n')
    return env


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain',required=True)
    parser.add_argument('--root',default=str(Path(__file__).resolve().parents[1]))
    args=parser.parse_args()
    initialize(Path(args.root),args.domain)
    print('Created .env and initial private proxy config. Read ADMIN_PASSWORD locally from .env; do not share this file.')
    print('For Docker on Linux: sudo chown -R 10001:10001 data')
