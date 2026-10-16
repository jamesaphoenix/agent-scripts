"""Inspect and replay bounded adverts from already cached public PDFs. No network calls."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from discovery import atomic_json, file_hash

MAX_PDF_BYTES = 20 * 1024 * 1024


def normal(value):
    return ' '.join(value.split())


def command(arguments):
    try:
        return subprocess.run(arguments, capture_output=True, text=True, check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError('Public PDF inspection failed: ' + type(error).__name__) from error


def pdf_advert_unit(page, item):
    number, bounds = item.get('pdfPage'), item.get('bounds')
    if (type(number) is not int or not 1 <= number <= 500
            or not isinstance(bounds, list) or len(bounds) != 4
            or any(type(n) is not int or not 0 <= n <= 2000 for n in bounds)
            or not bounds[2] or not bounds[3]):
        raise ValueError('Expected a bounded PDF page and rectangle in points')
    if page.stat().st_size > MAX_PDF_BYTES or not page.read_bytes().startswith(b'%PDF-'):
        raise ValueError('Expected a bounded public PDF document')
    info = command(['pdfinfo', '-f', str(number), '-l', str(number), str(page)])
    pages = re.search(r'^Pages:\s+(\d+)\s*$', info, re.M)
    size = re.search(r'^Page\s+(?:' + str(number) + r'\s+)?size:\s+([\d.]+) x ([\d.]+) pts', info, re.M)
    if not pages or number > int(pages[1]) or not size:
        raise ValueError('PDF page or its dimensions are absent')
    width, height = float(size[1]), float(size[2])
    if bounds[0] + bounds[2] > width or bounds[1] + bounds[3] > height:
        raise ValueError('PDF advert rectangle exceeds its page')
    arguments = ['-f', str(number), '-l', str(number), '-r', '72',
                 '-x', str(bounds[0]), '-y', str(bounds[1]), '-W', str(bounds[2]), '-H', str(bounds[3])]
    text = normal(command(['pdftotext', *arguments, '-layout', '-nopgbrk', str(page), '-']))
    if not text or len(text) > 12000:
        raise ValueError('Expected nonempty bounded PDF advert text')
    return text, {'pdfPage': number, 'bounds': bounds, 'extractor': 'poppler_pdftotext_72dpi_v1'}


def source_files(root, item):
    source_root = (root / 'public-source-review').resolve(strict=True)
    page, receipt_path = [(root / item[key]).resolve(strict=True) for key in ('page', 'receipt')]
    if not page.is_relative_to(source_root) or not receipt_path.is_relative_to(source_root):
        raise ValueError('PDF evidence must belong to the campaign public-source cache')
    if receipt_path.stat().st_size > 64000 or file_hash(receipt_path) != item['receiptSha256']:
        raise ValueError('PDF receipt hash or size mismatch')
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get('status') != 200 or page.stat().st_size > MAX_PDF_BYTES
            or file_hash(page) != receipt.get('sha256')
            or receipt.get('contentType', '').split(';')[0].strip() != 'application/pdf'):
        raise ValueError('PDF body, response or content type mismatch')
    return page, receipt_path, receipt


def reviewed_unit(root, item):
    page, receipt_path, receipt = source_files(root, item)
    text, selector = pdf_advert_unit(page, item)
    visual = item.get('visualReview')
    if (not isinstance(visual, dict) or visual.get('pdfSha256') != receipt['sha256']
            or visual.get('pdfPage') != selector['pdfPage'] or visual.get('bounds') != selector['bounds']):
        raise ValueError('Missing visual evidence of this PDF advert rectangle')
    image = (root / visual['image']).resolve(strict=True)
    if (not image.is_relative_to((root / 'public-source-review').resolve(strict=True))
            or image.stat().st_size > 8000000 or file_hash(image) != visual['sha256']
            or not image.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')):
        raise ValueError('PDF visual review image hash, format or cache path mismatch')
    if normal(item['unitText']) != text or item['sourceKind'] != 'public_advert':
        raise ValueError('Expected one complete reviewed PDF advert rectangle')
    evidence = {'url': receipt['url'], 'contentHash': receipt['sha256'], 'text': text}
    proof = {'page': str(page), 'receipt': str(receipt_path), 'receiptSha256': item['receiptSha256'],
             'observedAt': receipt.get('observedAt'), **selector, 'visualReview': visual}
    return evidence, proof


def inspect_advert(root, page, receipt, number, bounds, output):
    """Render a source rectangle and save a review template without granting qualification."""
    root = root.resolve(strict=True)
    item = {'sourceType': 'pdf_advert', 'sourceKind': 'public_advert',
            'page': str(page.resolve(strict=True).relative_to(root)),
            'receipt': str(receipt.resolve(strict=True).relative_to(root)),
            'receiptSha256': file_hash(receipt), 'pdfPage': number, 'bounds': bounds}
    page, _, saved = source_files(root, item)
    text, _ = pdf_advert_unit(page, item)
    image = output.with_suffix('.png')
    if not image.parent.resolve(strict=True).is_relative_to((root / 'public-source-review').resolve(strict=True)):
        raise ValueError('PDF inspection output must belong to the public-source cache')
    if image.is_symlink() or output.with_suffix('.json').is_symlink():
        raise ValueError('PDF inspection output cannot be a symlink')
    if output.with_suffix('.json').resolve() in (page, receipt.resolve()):
        raise ValueError('PDF inspection cannot replace its source evidence')
    command(['pdftoppm', '-f', str(number), '-l', str(number), '-r', '72',
             '-x', str(bounds[0]), '-y', str(bounds[1]), '-W', str(bounds[2]), '-H', str(bounds[3]),
             '-singlefile', '-png', str(page), str(image.with_suffix(''))])
    image.chmod(0o600)
    item.update(unitText=text, visualReview={'image': str(image.resolve(strict=True).relative_to(root)),
        'sha256': file_hash(image), 'pdfSha256': saved['sha256'], 'pdfPage': number, 'bounds': bounds})
    atomic_json(output.with_suffix('.json'), item)
    return item


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--pdf', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--page', type=int, required=True)
    parser.add_argument('--bounds', type=int, nargs=4, required=True, metavar=('X', 'Y', 'WIDTH', 'HEIGHT'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_advert(args.run_dir, args.pdf, args.receipt, args.page, args.bounds, args.output)))
