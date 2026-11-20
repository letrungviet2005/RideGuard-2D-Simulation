"""Model selection under the frozen protocol in ai/configs/selection-protocol.json.

Offline research only: never touches the app, the ESP32 or the model used by
the demo.  Fits only on the v1 train split, chooses only on v1 validation,
treats the v1 test split as a regression check and evaluates the new holdout
once after the choice is written to ``selection-frozen.json``.
"""
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import time
import joblib
import numpy as np
from .features import Features, FEATURES
from .offline_rules import OfflineRules
from .stream import read_rows, sensor_events
import math

SIDES = ('LEFT', 'RIGHT')
EXTRA = ('yaw_int_deg', 'yaw_toward_int_deg', 'gz_toward_mean', 'gz_toward_last',
         'ay_toward_mean', 'lateral_approach_mps')
FEATURES_V2 = tuple(FEATURES) + EXTRA
SIMPLICITY = {'rule': 0, 'logreg': 1, 'hgb': 2, 'forest': 3}


class FeaturesV2(Features):
    """rg-causal-v1 plus six causal features (past samples only)."""

    def at(self, t_us, side):
        x, reason = super().at(t_us, side)
        cfg = self.config
        lo = t_us - cfg['window_us']
        sign = 1. if side == 'LEFT' else -1.
        imu = [(t, p) for t, p in self.imu if lo <= t <= t_us]
        yaw = 0.
        for (t0, p0), (t1, p1) in zip(imu, imu[1:]):
            yaw += .5 * (p0['gz_dps'] + p1['gz_dps']) * (t1 - t0) / 1e6
        gz = [p['gz_dps'] for _, p in imu]
        ay = [p['ay_mps2'] for _, p in imu]
        gz_last = sign * gz[-1] if gz else 0.
        gz_mean = sign * sum(gz) / len(gz) if gz else 0.
        ay_mean = sign * sum(ay) / len(ay) if ay else 0.
        best = None
        for radar, q in self.frames.items():
            frames = [(t, p) for t, p in q if lo <= t <= t_us]
            if not frames or t_us - frames[-1][0] > cfg['stale_us']:
                continue
            offset = cfg['mount_offset_m'] if radar == 'LEFT' else -cfg['mount_offset_m']
            for target in frames[-1][1]['targets']:
                y = target['y_m']
                lateral = offset - y * math.tan(math.radians(target['angle_deg']))
                if ('LEFT' if lateral >= 0 else 'RIGHT') != side or not (-40 <= -y <= 2 and abs(lateral) <= 7):
                    continue
                history = []
                for stamp, frame in frames:
                    match = next((v for v in frame['targets'] if v['track_id'] == target['track_id']), None)
                    if match:
                        history.append((stamp, offset - match['y_m'] * math.tan(math.radians(match['angle_deg']))))
                    else:
                        history = []
                if best is None or y < best[0]:
                    best = (y, history)
        approach = 0.
        if best and len(best[1]) > 1:
            span = (best[1][-1][0] - best[1][0][0]) / 1e6
            if span:
                # positive = target's |lateral| shrinking towards the EGO centre line
                approach = -sign * (best[1][-1][1] - best[1][0][1]) / span
        return x + [yaw, sign * yaw, gz_mean, gz_last, ay_mean, approach], reason


class Dataset:
    """Rows in (session, time, side) order plus episode groups."""

    def __init__(self, name, X, meta):
        self.name = name
        self.X = np.asarray(X, dtype=float)
        for key, value in meta.items():
            setattr(self, key, np.asarray(value))
        self.n = len(self.X)
        self.ready = self.quality == 'READY'
        self.common = self.ready & (self.rule != 'UNKNOWN')
        groups = defaultdict(list)
        for i in range(self.n):
            groups[(self.session[i], self.side[i])].append(i)
        self.groups = {k: np.asarray(sorted(v, key=lambda i: self.t[i])) for k, v in groups.items()}
        lookup = {(self.session[i], int(self.t[i]), self.side[i]): self.label[i] for i in range(self.n)}
        self.other_high = np.asarray([
            lookup.get((self.session[i], int(self.t[i]), 'RIGHT' if self.side[i] == 'LEFT' else 'LEFT')) == 'HIGH'
            for i in range(self.n)])


def build(root, metas, config, name):
    X = []
    meta = defaultdict(list)
    for m in metas:
        folder = Path(root) / 'sessions' / m['session_id']
        events = sensor_events(folder)
        labels = read_rows(folder / 'labels.csv')
        by_time = defaultdict(list)
        for row in labels:
            by_time[int(row['timestamp_ms']) * 1000].append(row)
        feats = FeaturesV2(config)
        rules = OfflineRules()
        index = 0
        for stamp in sorted(by_time):
            while index < len(events) and events[index].timestamp_us <= stamp:
                feats.ingest(events[index])
                rules.ingest(events[index])
                index += 1
            rule = rules.at(stamp)
            for row in by_time[stamp]:
                x, reason = feats.at(stamp, row['side'])
                X.append(x)
                meta['session'].append(m['session_id'])
                meta['family'].append(m['scenario'])
                meta['side'].append(row['side'])
                meta['t'].append(int(row['timestamp_ms']))
                meta['label'].append(row['label'])
                meta['quality'].append(reason)
                meta['rule'].append(rule[row['side']] if reason == 'READY' else 'UNKNOWN')
    return Dataset(name, X, meta)


def load(repo, name, root, metas, config, cache_dir):
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f'{name}.joblib'
    if path.exists():
        return joblib.load(path)
    started = time.monotonic()
    ds = build(root, metas, config, name)
    joblib.dump(ds, path)
    print(f'built {name}: {ds.n} rows in {time.monotonic() - started:.0f}s', flush=True)
    return ds


# ---------------------------------------------------------------- decisions
def probabilities(model, ds, columns):
    classes = [str(c) for c in model.classes_]
    proba = np.zeros((ds.n, len(classes)))
    idx = np.flatnonzero(ds.ready)
    if len(idx):
        proba[idx] = model.predict_proba(ds.X[idx][:, columns])
    return classes, proba


def decide(ds, classes, proba, theta=None, k=1):
    """Return decision array; UNKNOWN wherever the sensor-quality gate says so."""
    high = classes.index('HIGH')
    others = [i for i in range(len(classes)) if i != high]
    nonhigh = np.asarray(classes, dtype=object)[np.asarray(others)[np.argmax(proba[:, others], axis=1)]]
    if theta is None:
        raw_high = np.argmax(proba, axis=1) == high
    else:
        raw_high = proba[:, high] >= theta
    out = np.where(raw_high, 'HIGH', nonhigh).astype(object)
    out[~ds.ready] = 'UNKNOWN'
    if k > 1:
        out = persist(ds, out, nonhigh, k)
    return out


def persist(ds, dec, nonhigh, k):
    out = dec.copy()
    for idx in ds.groups.values():
        run = 0
        previous = None
        for i in idx:
            if dec[i] == 'HIGH':
                run = run + 1 if previous is not None and ds.t[i] - previous <= 50 and dec[previous_i] == 'HIGH' else 1
                if run < k:
                    out[i] = nonhigh[i]
            else:
                run = 0
            previous, previous_i = ds.t[i], i
    return out


def rule_decisions(ds):
    return np.asarray(ds.rule, dtype=object)


# ------------------------------------------------------------------ metrics
def _runs(flag, t):
    runs = []
    start = None
    for i in range(len(flag)):
        if flag[i]:
            if start is None:
                start = i
            elif t[i] - t[i - 1] > 50:
                runs.append((start, i - 1))
                start = i
        elif start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(flag) - 1))
    return runs


def evaluate(ds, dec, mask=None, families=None):
    """Episode + tick metrics.  ``mask`` forces UNKNOWN outside it."""
    dec = np.asarray(dec, dtype=object).copy()
    if mask is not None:
        dec[~mask] = 'UNKNOWN'
    m = dict(ticks=0, valid_ticks=0, truth_episodes=0, truth_episodes_with_valid=0, hit_episodes=0,
             missed_episodes=0, missing_data_episodes=0, predicted_episodes=0, false_episodes=0,
             false_duration_s=0., wrong_side_ticks=0, truth_high_ticks=0, pred_high_ticks=0,
             tp_ticks=0)
    leads = []
    for (session, side), idx in ds.groups.items():
        if families is not None and ds.family[idx[0]] not in families:
            continue
        t = ds.t[idx]
        label_high = ds.label[idx] == 'HIGH'
        pred = dec[idx]
        valid = pred != 'UNKNOWN'
        pred_high = pred == 'HIGH'
        m['ticks'] += len(idx)
        m['valid_ticks'] += int(valid.sum())
        m['truth_high_ticks'] += int((label_high & valid).sum())
        m['pred_high_ticks'] += int(pred_high.sum())
        m['tp_ticks'] += int((pred_high & label_high).sum())
        wrong = pred_high & ~label_high & ds.other_high[idx]
        m['wrong_side_ticks'] += int(wrong.sum())
        m['false_duration_s'] += .05 * int((pred_high & ~label_high).sum())
        truth = _runs(label_high, t)
        predicted = _runs(pred_high, t)
        m['truth_episodes'] += len(truth)
        m['predicted_episodes'] += len(predicted)
        for a, b in predicted:
            if not label_high[a:b + 1].any():
                m['false_episodes'] += 1
        for a, b in truth:
            if not valid[a:b + 1].any():
                m['missing_data_episodes'] += 1
                continue
            m['truth_episodes_with_valid'] += 1
            matched = [p for p in predicted if t[p[0]] <= t[b] and t[p[1]] >= t[a]]
            if matched:
                m['hit_episodes'] += 1
                leads.append((min(t[p[0]] for p in matched) - t[a]) / 1000)
            else:
                m['missed_episodes'] += 1
    m['coverage'] = m['valid_ticks'] / max(1, m['ticks'])
    m['episode_recall'] = m['hit_episodes'] / m['truth_episodes_with_valid'] if m['truth_episodes_with_valid'] else None
    m['tick_precision'] = m['tp_ticks'] / m['pred_high_ticks'] if m['pred_high_ticks'] else None
    m['tick_recall'] = m['tp_ticks'] / m['truth_high_ticks'] if m['truth_high_ticks'] else None
    m['lead_median_s'] = float(np.median(leads)) if leads else None
    m['lead_p90_s'] = float(np.percentile(leads, 90)) if leads else None
    m['false_duration_s'] = round(m['false_duration_s'], 2)
    return m


# ------------------------------------------------------------------- models
def fit_models(train, seeds):
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    keep = np.flatnonzero(train.ready)
    y = train.label[keep]
    n_base = len(FEATURES)
    configs = []
    for feat, columns in (('base32', list(range(n_base))), ('v2_38', list(range(len(FEATURES_V2))))):
        for C in (.1, 1, 10):
            for weight in (None, 'balanced'):
                configs.append(('logreg', f'logreg-C{C}-{weight}-{feat}', feat, columns,
                                make_pipeline(StandardScaler(), LogisticRegression(C=C, class_weight=weight, max_iter=1000))))
        for depth in (3, 6):
            for weight in (None, 'balanced'):
                configs.append(('hgb', f'hgb-d{depth}-{weight}-{feat}', feat, columns,
                                HistGradientBoostingClassifier(max_depth=depth, learning_rate=.1, max_iter=150,
                                                               class_weight=weight, random_state=seeds)))
    configs.append(('forest', 'forest-48-d10-refit-v2_38', 'v2_38', list(range(len(FEATURES_V2))),
                    RandomForestClassifier(n_estimators=48, max_depth=10, min_samples_leaf=10, random_state=seeds, n_jobs=2)))
    fitted = []
    for family, name, feat, columns, model in configs:
        started = time.monotonic()
        model.fit(train.X[keep][:, columns], y)
        print(f'fit {name} [{feat}] {time.monotonic() - started:.1f}s', flush=True)
        fitted.append(dict(family=family, name=name, features=feat, columns=columns, model=model))
    return fitted


THETAS = (None, .3, .4, .5, .6, .7, .8, .9)
KS = (1, 2, 3, 4, 6)


def sweep(ds, entries, mask):
    rows = []
    for e in entries:
        classes, proba = e['proba'][ds.name]
        for theta in THETAS:
            for k in KS:
                m = evaluate(ds, decide(ds, classes, proba, theta, k), mask)
                rows.append(dict(family=e['family'], name=e['name'], features=e['features'],
                                 theta=theta, k=k, **m))
    return rows


def acceptable(row, ref):
    if row['episode_recall'] is None or ref['episode_recall'] is None:
        return False
    if row['episode_recall'] < ref['episode_recall'] - .03:
        return False
    if row['lead_median_s'] is not None and ref['lead_median_s'] is not None and row['lead_median_s'] > ref['lead_median_s'] + .25:
        return False
    return True


def best(rows, ref):
    ok = [r for r in rows if acceptable(r, ref)]
    if not ok:
        return None
    return min(ok, key=lambda r: (r['false_episodes'], r['false_duration_s'], SIMPLICITY[r['family']]))


def write_csv(path, rows):
    if not rows:
        return
    keys = list(rows[0])
    with Path(path).open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def run(repo, config_path, out):
    repo = Path(repo)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    protocol = json.loads((repo / 'ai/configs/selection-protocol.json').read_text(encoding='utf-8'))
    (out / 'selection-protocol.json').write_text(json.dumps(protocol, indent=2, ensure_ascii=False), encoding='utf-8')
    config = json.loads(Path(config_path).read_text())
    v1 = repo / config['dataset']
    ho = repo / 'data/RideGuard_Holdout_v2'
    manifest = json.loads((v1 / 'sessions.json').read_text())
    hold = json.loads((ho / 'sessions.json').read_text())
    cache = repo / '.local/ai/features-selection-2026-11-20'
    data = {
        'train': load(repo, 'train', v1, [m for m in manifest if m['split'] == 'train'], config, cache),
        'validation': load(repo, 'validation', v1, [m for m in manifest if m['split'] == 'validation'], config, cache),
        'test_old': load(repo, 'test_old', v1, [m for m in manifest if m['split'] == 'test'], config, cache),
        'holdout_v2': load(repo, 'holdout_v2', ho, hold, config, cache),
    }
    # Frozen production model, loaded exactly as the app does.
    from .inference import load_trusted
    frozen = load_trusted(repo, config_path)['model']
    n_base = len(FEATURES)
    entries = [dict(family='forest', name='forest-48-d10-FROZEN', features='base32', columns=list(range(n_base)), model=frozen)]
    entries += fit_models(data['train'], config['seed'])
    for e in entries:
        e['proba'] = {name: probabilities(e['model'], ds, e['columns']) for name, ds in data.items()}
    ref_entry = entries[0]
    val = data['validation']
    mask_val = val.common
    # ---- validation only: reference, grid, choice --------------------------
    classes, proba = ref_entry['proba']['validation']
    reference = evaluate(val, decide(val, classes, proba), mask_val)
    rule_val = evaluate(val, rule_decisions(val), mask_val)
    grid = sweep(val, entries, mask_val)
    write_csv(out / 'validation-grid.csv', grid)
    chosen = best(grid, reference)
    isolated = {}
    for fam in ('logreg', 'hgb', 'forest'):
        sub = [r for r in grid if r['family'] == fam]
        for label, cond in (
                ('model_only(base32,plain)', lambda r: r['features'] == 'base32' and r['theta'] is None and r['k'] == 1),
                ('features_only(v2,plain)', lambda r: r['features'] == 'v2_38' and r['theta'] is None and r['k'] == 1),
                ('threshold_only(base32)', lambda r: r['features'] == 'base32' and r['theta'] is not None and r['k'] == 1),
                ('persistence_only(base32)', lambda r: r['features'] == 'base32' and r['theta'] is None and r['k'] > 1),
                ('threshold+persistence(base32)', lambda r: r['features'] == 'base32' and r['theta'] is not None and r['k'] > 1),
                ('all(v2+threshold+persistence)', lambda r: r['features'] == 'v2_38' and r['theta'] is not None and r['k'] > 1)):
            pool = [r for r in sub if cond(r)]
            isolated[f'{fam}/{label}'] = best(pool, reference) if pool else None
    # frozen-forest isolated threshold / persistence effects (no refit involved)
    forest_pool = [r for r in grid if r['name'] == 'forest-48-d10-FROZEN']
    for label, cond in (('plain', lambda r: r['theta'] is None and r['k'] == 1),
                        ('threshold_only', lambda r: r['theta'] is not None and r['k'] == 1),
                        ('persistence_only', lambda r: r['theta'] is None and r['k'] > 1),
                        ('threshold+persistence', lambda r: r['theta'] is not None and r['k'] > 1)):
        pool = [r for r in forest_pool if cond(r)]
        isolated[f'forest-FROZEN/{label}'] = best(pool, reference) if pool else None
    # keep rule
    keep_rf = True
    reason = 'no configuration met the recall/lead constraints on validation'
    if chosen:
        gain_abs = reference['false_episodes'] - chosen['false_episodes']
        gain_rel = gain_abs / reference['false_episodes'] if reference['false_episodes'] else 0.
        keep_rf = not (gain_rel >= .2 and gain_abs >= 3)
        reason = (f"validation false HIGH episodes {reference['false_episodes']} -> {chosen['false_episodes']} "
                  f"({gain_rel:.0%}, {gain_abs} episodes)")
    frozen_choice = dict(chosen=chosen, reference_validation=reference, rule_validation=rule_val,
                         keep_existing_forest_by_validation_rule=keep_rf, validation_reason=reason,
                         isolated=isolated, protocol_sha256=hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest(),
                         written_at=time.strftime('%Y-%m-%dT%H:%M:%S'),
                         note='written BEFORE test_old/holdout_v2 are scored')
    (out / 'selection-frozen.json').write_text(json.dumps(frozen_choice, indent=2, ensure_ascii=False), encoding='utf-8')
    if chosen:
        picked = next(e for e in entries if e['name'] == chosen['name'])
        joblib.dump(dict(model=picked['model'], columns=picked['columns'], features=list(FEATURES_V2)[:max(picked['columns']) + 1],
                         theta=chosen['theta'], persistence_ticks=chosen['k'], note='research candidate; NOT the demo model'),
                    out / 'candidate-model.joblib')
    # ---- matched recall operating points (still validation-chosen) ---------
    matched = {}
    target_recall = reference['episode_recall'] - .02
    for e in entries:
        pool = [r for r in grid if r['name'] == e['name'] and r['episode_recall'] is not None and r['episode_recall'] >= target_recall]
        if pool:
            matched[e['name']] = min(pool, key=lambda r: (r['false_episodes'], r['false_duration_s']))
    # ---- evaluation on test_old and holdout_v2 (after the freeze) ----------
    configs_to_score = {'rule(offline ESP32 port)': None}
    scored = {}
    roster = [('RF frozen plain', ref_entry, None, 1)]
    if chosen:
        picked = next(e for e in entries if e['name'] == chosen['name'])
        roster.append((f"CHOSEN {chosen['name']} {chosen['features']} theta={chosen['theta']} k={chosen['k']}", picked, chosen['theta'], chosen['k']))
    for name, m in matched.items():
        e = next(x for x in entries if x['name'] == name)
        roster.append((f"matched-recall {name} theta={m['theta']} k={m['k']}", e, m['theta'], m['k']))
    # plain best models as well (model-only effect on unseen data)
    for fam in ('logreg', 'hgb'):
        r = isolated.get(f'{fam}/model_only(base32,plain)')
        if r:
            e = next(x for x in entries if x['name'] == r['name'])
            roster.append((f"model-only {r['name']} base32 plain", e, None, 1))
        r = isolated.get(f'{fam}/features_only(v2,plain)')
        if r:
            e = next(x for x in entries if x['name'] == r['name'])
            roster.append((f"features-only {r['name']} v2 plain", e, None, 1))
    r = isolated.get('forest/features_only(v2,plain)')
    if r:
        e = next(x for x in entries if x['name'] == r['name'])
        roster.append((f"features-only {r['name']} v2 plain", e, None, 1))
    for split in ('validation', 'test_old', 'holdout_v2'):
        ds = data[split]
        mask = ds.common
        scored[split] = {'rule(offline ESP32 port)': dict(all=evaluate(ds, rule_decisions(ds), mask),
                                                          families={f: evaluate(ds, rule_decisions(ds), mask, {f}) for f in sorted(set(ds.family))},
                                                          own_coverage=evaluate(ds, rule_decisions(ds), None)['coverage'])}
        for label, e, theta, k in roster:
            classes, proba = e['proba'][split]
            dec = decide(ds, classes, proba, theta, k)
            scored[split][label] = dict(all=evaluate(ds, dec, mask),
                                        families={f: evaluate(ds, dec, mask, {f}) for f in sorted(set(ds.family))},
                                        own_coverage=evaluate(ds, dec, None)['coverage'])
    results = dict(counts={name: dict(rows=ds.n, ready=int(ds.ready.sum()), common=int(ds.common.sum()),
                                      high_labels=int((ds.label == 'HIGH').sum())) for name, ds in data.items()},
                   frozen=frozen_choice, matched_recall_operating_points=matched, scores=scored)
    (out / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    print(json.dumps(dict(chosen=chosen and {k: chosen[k] for k in ('name', 'features', 'theta', 'k', 'false_episodes', 'episode_recall')},
                          keep_existing_forest_by_validation_rule=keep_rf, reason=reason), indent=1, default=str))
    return results
