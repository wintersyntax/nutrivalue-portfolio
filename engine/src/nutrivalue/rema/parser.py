"""Conservative OCR-only advert hypotheses, price roles and local caption lanes.

General rules extracted from the frozen title refiner and layout research:
prefer printed footer captions, split raised øre by geometry, keep FØR separate,
and bound assignments by neighboring captions. No API price/name vocabulary,
prior-week matching, reference labels or catalog-specific tuning is used.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

NUMBER = r'\d{1,5}(?:[.,]\d{1,2}|[.,]?[-–])?'
MONEY = re.compile(r'(?<![\w.,])(' + NUMBER + r')(?![\w.,])')
PRIOR = re.compile(r'(?:\b(?:FØR|TIDLIGERE|ORD(?:INÆR)?\.?\s*PRIS)\b|^FOR(?=\s+\d))', re.I)
UNIT = re.compile(r'(' + NUMBER + r')\s*(?:kr\.?\s*)?(?:pr\.?|per|/)\s*(kg|kilo|l|liter|stk)\b', re.I)
PACKAGE = re.compile(r'\b\d+(?:[.,]\d+)?\s*(?:kg|g|ml|cl|dl|l|pk|stk)\b', re.I)
PERCENT = re.compile(r'(?<!\w)[-–]?(\d{1,3}(?:[.,]\d+)?)\s*%')
MULTIBUY = re.compile(r'\b(\d+)\s*(?:FOR|STK\.?\s*FOR)\s*(' + NUMBER + r')', re.I)
PAY = re.compile(r'\b(?:KJ[ØO]P\s*)?(\d+)\s*(?:FOR\s*(\d+)|BETAL\s*FOR\s*(\d+))\b', re.I)
CONDITION = re.compile(r'\b(?:MEDLEM\w*|[ÆA]-PRIS|KUPONG\w*|BONUS|KJ[ØO]P|BETAL|MIX|MIKS|'
                       r'BILLIGSTE|UTVALGT\w*|TRUKKET|MAKS\w*|PANT|APPEN|KUN\s+I\s+APP)\b', re.I)
MARKETING = re.compile(r'(?:ALLTID\s+LAVE|LAVE\s+PRISER|REMA\s*[1IJ]?0?0?0|SKANN\s+FOR|OPPSKRIFT|'
                       r'UTVALGET KAN|MED FORBEHOLD|TIPS\s*!?|SLUTTSUMMEN|FAMILIEMIDD|LAPPEN|'
                       r'PRISENE KAN|SE ETTER|SUPPL[ÉE]R|BEDRE DYREVELFERD)', re.I)


def center(box):
    return box['x'] + box['width'] / 2, box['y'] + box['height'] / 2


def union(boxes):
    x = min(b['x'] for b in boxes); y = min(b['y'] for b in boxes)
    return {'x': x, 'y': y, 'width': max(b['x'] + b['width'] for b in boxes) - x,
            'height': max(b['y'] + b['height'] for b in boxes) - y}


def amount(text):
    try:
        text = unicodedata.normalize('NFKC', text).strip().replace(',', '.')
        text = re.sub(r'[.]?[-–]$', '.00', text)
        value = Decimal(text)
        return f'{value:.2f}' if value.is_finite() and value > 0 else None
    except InvalidOperation:
        return None


def plausible_caption(text):
    """Use printed text only. Do not repair OCR spelling or expand product names."""
    letters = [c for c in text if c.isalpha()]
    return (len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) >= .85
            and not MARKETING.search(text) and not PRIOR.search(text) and not UNIT.search(text)
            and not PERCENT.search(text) and not MULTIBUY.search(text)
            and not re.match(r'^(?:[+]\s*)?(?:PANT|PR[.\s]+(?:KG|L|STK|KOPP)|PARTIVARE)\b', text, re.I)
            and not re.fullmatch(r'(?:FOR|PRIS|MEDLEM|BONUS|PR\.?\s*STK|MAKSPRIS)', text.strip(), re.I))


def caption_seeds(lines):
    seeds = []
    for i, line in enumerate(lines):
        text, box = line['text'].strip(), line['box']
        if not plausible_caption(text) or box['height'] > .055 or center(box)[1] < .035:
            continue
        x, y = center(box)
        package = []
        for j, other in enumerate(lines):
            ox, oy = center(other['box'])
            if j != i and 0 < y - oy < .045 and abs(x - ox) < max(.12, box['width'] / 2):
                if PACKAGE.search(other['text']) or UNIT.search(other['text']):
                    package.append(j)
        seeds.append({'title': text, 'box': box, 'line_indices': [i], 'package_lines': package,
                      'issues': [] if package else ['caption_without_package_context']})
    # Join immediately adjacent, aligned uppercase caption lines only. This is
    # evidence concatenation, not fuzzy title repair; source text remains intact.
    seeds.sort(key=lambda s: (-center(s['box'])[1], s['box']['x']))
    merged = []
    for seed in seeds:
        hit = next((s for s in reversed(merged) if not s['package_lines']
                    and abs(s['box']['x'] - seed['box']['x']) < .025
                    and 0 < s['box']['y'] - (seed['box']['y'] + seed['box']['height']) < .012), None)
        if hit:
            hit['title'] += ' ' + seed['title']
            hit['line_indices'] += seed['line_indices']
            hit['box'] = union([hit['box'], seed['box']])
            hit['package_lines'] = seed['package_lines']
            hit['issues'] = seed['issues']
        else:
            merged.append(seed)
    return merged


def price_signals(lines):
    signals, integers = [], []

    def add(kind, value, indices, method='literal_ocr', issues=(), **extra):
        signals.append({'kind': kind, 'value': value, 'line_indices': indices,
                        'box': union([lines[i]['box'] for i in indices]), 'method': method,
                        'issues': list(issues), **extra})

    for i, line in enumerate(lines):
        raw = line['text'].strip(); text = unicodedata.normalize('NFKC', raw)
        box = line['box']
        if PRIOR.search(text):
            values = [amount(m.group(1)) for m in MONEY.finditer(text)]
            values = [v for v in values if v]
            add('former_price', values, [i], issues=[] if len(values) == 1 else ['unresolved_former_price_range'])
            continue
        units = list(UNIT.finditer(text))
        for match in units:
            add('unit_price', {'amount_nok': amount(match.group(1)), 'unit': match.group(2).lower()}, [i])
        for match in PERCENT.finditer(text):
            value = Decimal(match.group(1).replace(',', '.'))
            discount_context = (box['height'] >= .025 or
                                re.search(r'RABATT|TRUKKET|SPAR|AVSLAG', text, re.I))
            add('percentage_discount' if discount_context and value < 100 else 'percentage_context',
                str(value), [i], issues=[] if 0 < value <= 100 else ['invalid_percentage'])
        multibuy = MULTIBUY.search(text)
        pay = PAY.search(text)
        # "3 for 2" expresses a pay-for quantity, not two NOK. Currency,
        # decimals or a trailing dash disambiguate a printed bundle total.
        if multibuy:
            quantity, total = int(multibuy.group(1)), multibuy.group(2)
            is_pay = pay and not re.search(r'[.,–-]|\bkr\b', text, re.I) and int(total) < quantity
            if is_pay:
                add('multibuy', {'quantity': quantity, 'pay_for': int(total), 'total_nok': None}, [i])
            else:
                add('multibuy', {'quantity': quantity, 'pay_for': None, 'total_nok': amount(total)}, [i],
                    issues=[] if quantity > 1 else ['invalid_multibuy_quantity'])
        elif pay:
            add('multibuy', {'quantity': int(pay.group(1)), 'pay_for': int(pay.group(2) or pay.group(3)),
                            'total_nok': None}, [i])
        if CONDITION.search(text):
            add('condition', raw, [i])
        if units or PERCENT.search(text) or multibuy or pay or PACKAGE.search(text) or MARKETING.search(text):
            continue
        literal = re.fullmatch(r'(?:KR\.?\s*)?(' + NUMBER + r')(?:\s*KR\.?)?', text, re.I)
        if re.fullmatch(r'\d{1,3}[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,2}\d?', raw):
            add('current_price', f'{Decimal(text) / 100:.2f}', [i], 'raised_ore_ocr')
        elif literal:
            token = literal.group(1)
            if re.search(r'[.,–-]', token):
                add('current_price', amount(token), [i])
            elif box['height'] >= .027:
                integers.append((i, token, box))
    # A separated FØR marker owns nearby small digits. Never pair those with
    # a current-price integer merely because the boxes happen to be close.
    prior_markers = [s for s in signals if s['kind'] == 'former_price']
    prior_digits = set()
    for i, token, b in integers:
        x, y = center(b)
        for s in prior_markers:
            pb = s['box']; px, py = center(pb)
            if 0 <= b['x'] - (pb['x'] + pb['width']) < .05 and abs(py - y) < .025:
                s['value'] += [amount(token)]; s['line_indices'].append(i)
                s['box'] = union([s['box'], b]); prior_digits.add(i)
                s['issues'] = [] if len(s['value']) == 1 else ['unresolved_former_price_range']
    split = {}
    for i, token, b in integers:
        if i in prior_digits or len(token) > 3:
            continue
        x, y = center(b)
        hits = []
        # Cents can be small enough to have been excluded as integer anchors.
        for j, other in enumerate(lines):
            if j == i or j in prior_digits or not re.fullmatch(r'\d{2}', other['text'].strip()):
                continue
            cb = other['box']; cx, cy = center(cb)
            gap = cb['x'] - (b['x'] + b['width'])
            if (-.012 <= gap <= .07 and 0 < cx - x < .18
                    and 0 <= cy - y < max(.04, b['height'])
                    and .15 <= cb['height'] / b['height'] <= .85):
                hits.append((j, other['text'].strip()))
        if hits:
            split[i] = hits
    consumed = set(prior_digits)
    for i, hits in split.items():
        if i in consumed:
            continue
        token = next(t for k, t, _ in integers if k == i)
        indices = [i] + [j for j, _ in hits]
        if len(hits) == 1:
            add('current_price', f'{int(token)}.{hits[0][1]}', indices, 'split_ore_geometry')
        else:
            add('current_price', None, indices, 'split_ore_geometry', ['ambiguous_split_ore'],
                amount_candidates=[f'{int(token)}.{c}' for _, c in hits])
        consumed.update(indices)
    for i, token, box in integers:
        if i in consumed:
            continue
        if len(token) >= 3:
            add('current_price', None, [i], 'compact_integer_ocr', ['ambiguous_compact_price'],
                amount_candidates=[f'{Decimal(token) / 100:.2f}', amount(token)])
        else:
            add('current_price', None, [i], 'integer_ocr', ['unresolved_integer_or_missing_ore'],
                amount_candidates=[amount(token)])
    for signal in signals:
        if signal['kind'] != 'current_price':
            continue
        x, y = center(signal['box'])
        bundle_markers = []
        for j, other in enumerate(lines):
            marker = re.fullmatch(r'(\d+)\s+FOR', other['text'].strip(), re.I)
            ox, oy = center(other['box'])
            if marker and abs(x - ox) < .15 and 0 < oy - y < .16:
                bundle_markers.append((j, int(marker.group(1))))
        if len(bundle_markers) == 1 and signal['value']:
            j, quantity = bundle_markers[0]
            signal.update(kind='multibuy', value={'quantity': quantity, 'pay_for': None,
                                                'total_nok': signal['value']},
                          method='split_multibuy_geometry')
            signal['line_indices'].append(j)
            signal['box'] = union([signal['box'], lines[j]['box']])
            continue
        markers = []
        for j, other in enumerate(lines):
            match = re.fullmatch(r'PR[.\s]+(KG|L|STK|KOPP)', other['text'].strip(), re.I)
            ox, oy = center(other['box'])
            if match and abs(x - ox) < .15 and 0 < y - oy < .16:
                markers.append((j, match.group(1).lower()))
        if len(markers) == 1 and signal['value']:
            j, unit = markers[0]
            signal.update(kind='unit_price', value={'amount_nok': signal['value'], 'unit': unit},
                          method='separate_unit_label_geometry')
            signal['line_indices'].append(j)
            signal['box'] = union([signal['box'], lines[j]['box']])
        elif any(min([lines[i].get('confidence', 100)] +
                     [w['confidence'] for w in lines[i].get('words', [])]) < 60
                 for i in signal['line_indices']):
            if signal['value']:
                signal.setdefault('amount_candidates', []).append(signal['value'])
            signal['value'] = None
            signal['issues'].append('low_confidence_current_price')
    return signals


def seed_lanes(seeds):
    """Normalized neighboring caption lanes, derived from this page's evidence."""
    for seed in seeds:
        x, y = center(seed['box']); left, right, ceiling = 0., 1., 1.
        for other in seeds:
            if other is seed:
                continue
            ox, oy = center(other['box'])
            if abs(y - oy) < .065:
                if ox < x:
                    left = max(left, (x + ox) / 2)
                elif ox > x:
                    right = min(right, (x + ox) / 2)
            if oy > y + .065 and abs(ox - x) < .25:
                ceiling = min(ceiling, other['box']['y'] - .012)
        seed['lane'] = {'x': left, 'y': max(0., seed['box']['y'] - .06),
                        'width': right - left, 'height': max(.01, ceiling - max(0., seed['box']['y'] - .06))}


def associate(signal, seeds):
    x, y = center(signal['box']); scored = []
    for index, seed in enumerate(seeds):
        sx, sy = center(seed['box']); lane = seed['lane']
        if not (lane['x'] <= x <= lane['x'] + lane['width'] and lane['y'] <= y <= lane['y'] + lane['height']):
            continue
        if y - sy > .62 or sy - y > .055:
            continue
        # Unit/package/condition prose belongs near a footer, price plaques can
        # be substantially above it. Distance never overrides the column lane.
        if (signal['kind'] == 'unit_price' and signal['method'] != 'separate_unit_label_geometry'
                and abs(sy - y) > .06):
            continue
        score = abs(x - sx) * .65 + abs(y - sy)
        scored.append((score, index))
    scored.sort()
    if not scored:
        return None, []
    if len(scored) > 1 and scored[1][0] - scored[0][0] < .025:
        return None, [i for score, i in scored if score - scored[0][0] < .025]
    return scored[0][1], []


def parse_page(page: dict) -> dict:
    lines = page['lines']; seeds = caption_seeds(lines); seed_lanes(seeds)
    signals = price_signals(lines)
    groups = [{'seed': s, 'signals': [], 'issues': list(s['issues'])} for s in seeds]
    unassigned_signals = []
    for signal in signals:
        index, alternatives = associate(signal, seeds)
        if index is not None:
            groups[index]['signals'].append(signal)
        elif signal['kind'] in {'current_price', 'multibuy', 'percentage_discount', 'former_price'}:
            # An isolated large integer alone could be a page number, meal
            # total, marketing headline or packaging. Keep it in diagnostics.
            if signal['method'] in {'integer_ocr', 'compact_integer_ocr'} and not alternatives:
                unassigned_signals.append(signal)
                continue
            groups.append({'seed': None, 'signals': [signal],
                           'issues': ['ambiguous_product_association' if alternatives else 'missing_product_name'],
                           'title_candidates': [seeds[i]['title'] for i in alternatives]})
        else:
            unassigned_signals.append(signal)
    adverts, assigned_lines = [], set()
    for group in groups:
        seed = group['seed']; evidence = group['signals']; issues = group['issues']
        # Caption-only hypotheses need package context; small packaging brand
        # text alone is not an advertised product.
        if seed and not evidence and not seed['package_lines']:
            continue
        current = [s for s in evidence if s['kind'] == 'current_price']
        multi = [s for s in evidence if s['kind'] == 'multibuy']
        percentages = [s['value'] for s in evidence if s['kind'] == 'percentage_discount']
        former = [v for s in evidence if s['kind'] == 'former_price' and not s['issues'] for v in s['value']]
        conditions = [s['value'] for s in evidence if s['kind'] == 'condition']
        unique = {s['value'] for s in current if s['value'] is not None}
        price = next(iter(unique)) if len(unique) == 1 and all(s['value'] for s in current) else None
        if len(current) > 1:
            issues.append('ambiguous_multiple_current_prices')
            price = None
        if len(multi) > 1:
            issues.append('ambiguous_multibuy')
        if current and (multi or percentages):
            issues.append('ambiguous_mixed_pricing')
            price = None
        if not price and not multi and not percentages:
            issues.append('missing_printed_current_price')
        if any(Decimal(v) <= Decimal(price) for v in former if price):
            issues.append('former_not_above_current')
        indices = sorted({i for s in evidence for i in s['line_indices']} |
                         (set(seed['line_indices'] + seed['package_lines']) if seed else set()))
        assigned_lines.update(indices)
        for i in indices:
            if lines[i].get('confidence', 100) < 60:
                issues.append('low_ocr_confidence')
        issues.extend(issue for s in evidence for issue in s['issues'])
        boxes = [lines[i]['box'] for i in indices]
        adverts.append({'page': page['page'], 'product_name': seed['title'] if seed else None,
                        'title_candidates': group.get('title_candidates', []),
                        'current_price_nok': price, 'currency': 'NOK',
                        'former_prices_nok': sorted(set(former)),
                        'unit_prices': [s['value'] for s in evidence if s['kind'] == 'unit_price'],
                        'percentage_discounts': sorted(set(percentages)),
                        'multibuy': multi[0]['value'] if len(multi) == 1 else None,
                        'conditions': list(dict.fromkeys(conditions)),
                        'pricing_mode': ('multibuy' if multi else 'percentage_discount' if percentages
                                         else 'conditional' if conditions else 'ordinary'),
                        'region': union(boxes), 'association_lane': seed['lane'] if seed else None,
                        'region_method': 'ocr_caption_price_hypothesis',
                        'line_indices': indices, 'evidence': evidence,
                        'source_text': [lines[i]['text'] for i in indices],
                        'issues': sorted(set(issues)), 'verification_status': 'review_only',
                        'human_approved': False})
    adverts.sort(key=lambda a: (-a['region']['y'] - a['region']['height'], a['region']['x']))
    return {'page': page['page'], 'adverts': adverts,
            'unassigned_lines': [{'line_index': i, **line} for i, line in enumerate(lines) if i not in assigned_lines],
            'unassigned_signals': unassigned_signals}
