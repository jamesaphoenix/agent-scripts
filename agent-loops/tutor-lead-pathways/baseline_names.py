"""Prove missing baseline name hints from its frozen public page evidence."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import urllib.parse

from audit_directory_identities import complete_own_name, professional_name, complete_identity_hint
from directory_profiles import Document, admitted_url, decode_page, profile_record


def verified_name_overlay(baseline, profile_root):
    proof = {'verifiedNames': 0, 'identities': [], 'heldReasons': {}, 'paidCalls': 0, 'httpRequests': 0,
             'frozenBaselineModified': False}
    if profile_root is None:
        return baseline, proof
    root, overlay, held = Path(profile_root).resolve(), [], Counter()
    for old in baseline:
        name = complete_own_name(old.get('name', ''))
        if complete_identity_hint(old.get('professionalNameHint')) or not old.get('canonicalId', '').startswith('tutor:') or not name:
            overlay.append(old)
            continue
        try:
            evidence = old.get('pageEvidence', [])
            members = set(old.get('memberKeys', []))
            if not evidence or not members or set(old.get('sourceUrls', [])) != {e['url'] for e in evidence}:
                raise ValueError('missing_frozen_identity_evidence')
            verified_members = set()
            for item in evidence:
                url, sha = item['url'], item['sha256']
                if admitted_url(url) != url or not re.fullmatch('[a-f0-9]{64}', sha):
                    raise ValueError('invalid_frozen_page_evidence')
                key = hashlib.sha256(url.encode()).hexdigest()
                receipt_path = root / 'receipts' / (key + '.json')
                page_path = root / 'pages' / (key + '.html')
                if not receipt_path.resolve().is_relative_to(root) or not page_path.resolve().is_relative_to(root):
                    raise ValueError('cache_path_left_profile_root')
                receipt, body = json.loads(receipt_path.read_text()), page_path.read_bytes()
                if (receipt.get('url') != url or receipt.get('status') != 200 or receipt.get('sha256') != sha
                        or hashlib.sha256(body).hexdigest() != sha):
                    raise ValueError('frozen_page_hash_mismatch')
                text = decode_page(body, receipt.get('contentType', ''))
                parsed = profile_record(text, url)
                if (not parsed or parsed['platform'] not in ('findtutors.co.uk', 'childcare.co.uk')
                        or complete_own_name(parsed['name']) != name):
                    raise ValueError('owned_complete_name_not_proved')
                member = parsed['platform'] + ':' + parsed['personId']
                if member not in members:
                    raise ValueError('frozen_member_does_not_match_page')
                verified_members.add(member)
                if parsed['platform'] == 'findtutors.co.uk':
                    section = Document(text).root.first(lambda n: n.attrs.get('id') == 'sectionaddetail')
                    info = section.first(lambda n: n.has_class('info')) if section else None
                    link = info.first(lambda n: n.attrs.get('id') == 'lnkProfile') if info else None
                    hint = professional_name(urllib.parse.urljoin(url, link.attrs.get('href', ''))) if link else None
                    if hint and hint != name:
                        raise ValueError('conflicting_own_professional_name')
            if verified_members != members:
                raise ValueError('unproved_frozen_member')
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            held[str(error) if isinstance(error, ValueError) else type(error).__name__] += 1
            overlay.append(old)
            continue
        overlay.append(dict(old, professionalNameHint=name))
        proof['identities'].append({'canonicalId': old['canonicalId'], 'professionalNameHint': name,
                                    'memberKeys': sorted(members),
                                    'pageEvidence': [{'url': e['url'], 'sha256': e['sha256']} for e in evidence]})
    proof['verifiedNames'], proof['heldReasons'] = len(proof['identities']), dict(held)
    return overlay, proof
