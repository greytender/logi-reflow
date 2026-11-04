"""Rebuild the self-hosted rounded font assets (fontTools + brotli required).

Source archive and the OFL are linked in web/fonts/README.md. This script accepts
the extracted upstream CN stable TTF directory and preserves its character maps.
"""
import argparse
from pathlib import Path

from fontTools.ttLib import TTFont


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=Path('web/fonts'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for upstream, weight in (('Regular', 450), ('Bold', 650)):
        font = TTFont(args.source / f'ResourceHanRoundedCN-{upstream}.ttf')
        font.flavor = 'woff2'
        target = args.output / f'resource-han-rounded-{weight}.woff2'
        font.save(target)
        print(f'{target}: {target.stat().st_size:,} bytes; '
              f'{len(font.getBestCmap()):,} mapped characters')


if __name__ == '__main__':
    main()
