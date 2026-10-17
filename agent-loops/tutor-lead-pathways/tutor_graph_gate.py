"""Publish only model-qualified local tutor-owned hostnames from saved crawl evidence."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from crawl_graph import domain, public_url


SOURCE_HOSTS = {
    'findtutors.co.uk', 'tutorperch.com', 'childcare.co.uk', 'tutordex.net', 'tutorextra.co.uk',
    'uktutors.com', 'tutorhunt.com', 'tuitionlist.co.uk', 'teachtutti.co.uk', 'apprentus.co.uk',
    'universitytutor.com', 'firsttutors.com', 'thetutorsassociation.org.uk',
    'thetutorsassociation.co.uk', 'wildapricot.org', 'yell.com', 'facebook.com', 'instagram.com',
    'linkedin.com', 'google.com', 'google.co.uk', 'netmums.com', 'tutorful.co.uk', 'superprof.co.uk',
    'superprof.com', 'bark.com', 'threebestrated.co.uk', 'qualifiedtutor.org', 'qualifiedtutor.directory',
}
SUBJECTS = {'english', 'maths', 'mathematics', 'primary', '11+', '11_plus', 'eleven_plus',
            'verbal_reasoning', 'non_verbal_reasoning', 'verbal reasoning', 'non-verbal reasoning'}
THRESHOLDS = {'kindProbability': .8, 'niche': .85, 'local': .85}


def source_host(host):
    return (any(host == root or host.endswith('.' + root) for root in SOURCE_HOSTS)
            or host.endswith(('.gov.uk', '.sch.uk', '.ac.uk')))


def admission(profile, observed):
    host = domain(profile.get('website'))
    if not host or source_host(host):
        return None, 'not_tutor_owned_hostname'
    decision = profile.get('decision') or {}
    if profile.get('status') != 'model_qualified_research' or decision.get('kind') != 'independent_tutor':
        return None, 'not_model_qualified_independent_tutor'
    if not decision.get('decisionKey') or any(
            not isinstance(decision.get(k), (int, float)) or isinstance(decision[k], bool)
            or not THRESHOLDS[k] <= decision[k] <= 1 for k in THRESHOLDS):
        return None, 'missing_or_low_decision'
    outside = decision.get('outside')
    if not isinstance(outside, (int, float)) or isinstance(outside, bool) or not 0 <= outside < .8:
        return None, 'outside_or_uncertain_locality'
    if not profile.get('localityClaim') or not set(profile.get('matchedSubjects') or []) & SUBJECTS:
        return None, 'missing_local_subject_evidence'
    evidence = [p for p in profile.get('pageEvidence') or [] if domain(p.get('url')) == host
                and re.fullmatch(r'[0-9a-f]{64}', p.get('textSha256') or '')]
    if not evidence:
        return None, 'missing_same_host_page_evidence'
    if host not in observed or observed[host].get('textPages', 0) < 1:
        return None, 'missing_observed_page_text'
    return host, 'admitted_model_research'


def build(candidate_graph, accepted, previous=None):
    """Directory records can seed discovery but never become tutor-owned graph nodes."""
    observed = {n['id']: n for n in candidate_graph['nodes']}
    approved, exclusions = {}, Counter()
    identities = set()
    for profile in accepted:
        host, reason = admission(profile, observed)
        if not host:
            exclusions[reason] += 1
            continue
        identity = profile.get('canonicalId')
        if not identity:
            exclusions['missing_canonical_identity'] += 1
            continue
        if (identity, host) in identities:
            exclusions['duplicate_identity_and_hostname'] += 1
            continue
        identities.add((identity, host))
        approved.setdefault(host, []).append(profile)
    nodes = []
    for host, profiles in sorted(approved.items()):
        node = deepcopy(observed[host])
        node['category'] = 'research'
        node['profiles'] = [{'name': p.get('name'), 'url': public_url(p.get('website')),
            'subjects': p['matchedSubjects'], 'locality': p['localityClaim'],
            'decisionKey': p['decision']['decisionKey'], 'decision': p['decision']['kind'],
            'status': 'model_qualified_research', 'outreachEligible': False} for p in profiles]
        node['tutorSignal'] = True
        node['graphAdmission'] = {'policy': 'local-independent-owned-site-v1',
            'canonicalIds': sorted(p['canonicalId'] for p in profiles),
            'decisionKeys': sorted(p['decision']['decisionKey'] for p in profiles),
            'ownershipVerified': False, 'outreachEligible': False}
        nodes.append(node)
    edges = [deepcopy(e) for e in candidate_graph['edges'] if e['source'] in approved and e['target'] in approved]
    incoming, outgoing = Counter(), Counter()
    for edge in edges:
        outgoing[edge['source']] += 1
        incoming[edge['target']] += 1
    for node in nodes:
        node['inDegree'], node['outDegree'] = incoming[node['id']], outgoing[node['id']]
    old = {n['id'] for n in (previous or {}).get('nodes', [])}
    current = set(approved)
    meta = deepcopy(candidate_graph['meta'])
    meta.pop('artifactManifest', None)
    meta.update({'tutorOnly': True, 'nodeCount': len(nodes), 'edgeCount': len(edges),
        'domainsFetched': sum(n['fetchedPages'] > 0 for n in nodes),
        'domainsWithText': sum(n['textPages'] > 0 for n in nodes), 'domainsHeldOnly': 0,
        'fetchedPages': sum(n['fetchedPages'] for n in nodes), 'textPages': sum(n['textPages'] for n in nodes),
        'allModelQualifiedProfiles': len(accepted), 'modelQualifiedProfiles': len(identities),
        'profilesWithDecisions': len(identities),
        'coverage': 'Tutor-owned hostnames with saved independent-tutor Decisions results and same-host text evidence. '
            'Locality and requested subject predicates must pass the existing classifier thresholds. '
            'Only recorded edges between admitted hostnames are shown. Disconnected tutors stay disconnected. '
            'Directories and general websites remain in the separate discovery evidence graph. '
            'Model research does not verify ownership, availability or permission for outreach.'})
    audit = {'builtAt': datetime.now(timezone.utc).isoformat(), 'policy': 'local-independent-owned-site-v1',
        'candidateHostnames': len(observed), 'acceptedInputProfiles': len(accepted),
        'admittedHostnames': len(nodes), 'admittedProfileIdentities': len(identities),
        'newHostnames': sorted(current - old), 'removedHostnames': sorted(old - current),
        'excludedProfiles': dict(exclusions), 'thresholds': THRESHOLDS,
        'reclassificationCalls': 0, 'outreachEligible': 0,
        'edgesInferred': 0, 'hostnameScope': 'Own advertised local offer, as model research; not verified ownership.'}
    return {'schemaVersion': 1, 'meta': meta, 'nodes': nodes, 'edges': edges}, audit


def atomic(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(value, separators=(',', ':'), ensure_ascii=False))
    temporary.chmod(0o600)
    temporary.replace(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('candidate-graph', 'accepted', 'output', 'audit'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--previous', type=Path)
    args = parser.parse_args()
    graph_raw, accepted_raw = args.candidate_graph.read_bytes(), args.accepted.read_bytes()
    previous = json.loads(args.previous.read_bytes()) if args.previous and args.previous.exists() else None
    graph, audit = build(json.loads(graph_raw), json.loads(accepted_raw), previous)
    audit.update({'candidateGraphSha256': hashlib.sha256(graph_raw).hexdigest(),
                  'acceptedSha256': hashlib.sha256(accepted_raw).hexdigest()})
    atomic(args.output, graph)
    atomic(args.audit, audit)
    print(json.dumps({k: v for k, v in audit.items() if k not in ('newHostnames', 'removedHostnames')}, indent=2))
