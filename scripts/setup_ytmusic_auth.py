#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description='Create a ytmusicapi browser/OAuth credential file.')
    parser.add_argument('--output', default='browser.json', help='Output credential file path')
    args = parser.parse_args()
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        from ytmusicapi import setup
    except ImportError as exc:
        print('ytmusicapi is not installed. Install requirements.txt first.')
        return 2
    print('YouTube Music authentication setup')
    print('Follow the ytmusicapi prompts to provide the required browser request headers.')
    setup(filepath=str(output))
    print(f'Created: {output}')
    print('Keep this file private and do not commit it to Git.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
