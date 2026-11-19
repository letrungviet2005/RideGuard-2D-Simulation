"""Measure the RESULT cadence and the per-stage timing on the real ESP32.

Every stage is stamped with the *same* PC clock (``perf_counter_ns``), so a
duration is only ever taken between two points that share that clock.  The
ESP32's ``micros()`` clock is never subtracted from the PC clock: the two are
not synchronised, and the board's own stamps are used instead to describe
what happened on the board.

Stages captured (all in memory; nothing is written to the serial port):

  generated  the simulator built the envelope
  enqueue    the message was handed to the link's TX queue (depth recorded)
  write      the worker thread actually wrote the bytes to the UART
  receive    the worker thread parsed a RESULT line back
  ui         the GUI last refreshed its panels with this result

The board additionally reports its own view inside ``RESULT.diag``:
``emit_us`` (its publish clock), ``write_us`` (UART write duration),
``parse_max_us`` (worst single-line parse), ``rx_slice_max_us`` (worst read
slice) and per-stream ``[seq, t_ms, lf_us, processed_us]``.

Usage:
    python measure_result_timing.py --port COM5 --scenario 2 --duration 10
"""
import argparse
import json
import time
from pathlib import Path

from rideguard_sim.protocol import BAUD, budget_bytes_per_second
from rideguard_sim.serial_link import SerialLink
from rideguard_sim.session import Session


def stats(values):
    """Quantiles of one sample set, or None when there is no sample."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        value = float(ordered[0])
        return dict(n=1, p50=value, p95=value, max=value)

    def quantile(p):
        k = (len(ordered) - 1) * p / 100.0
        low = int(k)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (k - low)

    return dict(n=len(ordered), p50=quantile(50), p95=quantile(95), max=float(ordered[-1]))


def _gaps(times):
    """Consecutive differences of an ascending list."""
    return [(b - a) / 1e6 for a, b in zip(times, times[1:])]


def summarize(events, session):
    """Summarise one recorded session.

    ``events`` are the entries of a ``TimingTrace``; ``session`` is the
    session id the run belongs to.

    The report deliberately keeps two families apart:

    * ``result_gap_ms`` and ``pc_sample_to_result_ms`` are measured on the PC
      clock only.
    * ``firmware_emit_gap_ms`` and ``firmware_previous_write_ms`` come from
      the ESP32's own clock and are never mixed with the PC durations.
    """
    events = [event for event in events if event.get('session') == session]

    # --- RESULT arrivals on the PC clock ---------------------------------
    received = sorted((event['pc_ns'] for event in events
                       if event.get('stage') == 'receive'
                       and event.get('type') == 'RESULT'))
    result_gap_ms = stats(_gaps(received))

    # --- RESULT sequence numbers ------------------------------------------
    result_seqs = [event['seq'] for event in events
                   if event.get('stage') == 'receive' and event.get('type') == 'RESULT']
    sequence_gaps = sum(1 for a, b in zip(result_seqs, result_seqs[1:]) if b != a + 1)

    # --- the board's own view ---------------------------------------------
    emit_times, previous_write_us = [], []
    for event in events:
        if event.get('stage') != 'receive' or event.get('type') != 'RESULT':
            continue
        diagnostics = event.get('diagnostics') or {}
        if 'emit_us' in diagnostics:
            emit_times.append(diagnostics['emit_us'])
        if 'write_us' in diagnostics:
            previous_write_us.append(diagnostics['write_us'] / 1000.0)
    firmware_emit_gap_ms = stats([(b - a) / 1000.0
                                  for a, b in zip(emit_times, emit_times[1:])])
    firmware_previous_write_ms = stats(previous_write_us)

    # --- first sensor sample written -> first RESULT that reports it -------
    # A heartbeat that merely reports "still stale" must not be counted as a
    # fresh observation, so the measurement starts at the sensor sample that
    # was actually written and ends at the first RESULT that saw it valid.
    written = sorted(event['pc_ns'] for event in events
                     if event.get('stage') == 'write'
                     and event.get('type') in ('IMU', 'RADAR_FRAME'))
    pc_sample_to_result_ms = None
    if written and received:
        first_sample = written[0]
        first_valid_result = None
        for event in events:
            if event.get('stage') != 'receive' or event.get('type') != 'RESULT':
                continue
            if event['pc_ns'] < first_sample:
                continue
            sensors = event.get('sensors') or {}
            if any((sensors.get(name) or {}).get('valid') for name in ('IMU', 'LEFT', 'RIGHT')):
                first_valid_result = event['pc_ns']
                break
        if first_valid_result is not None:
            pc_sample_to_result_ms = stats(
                [(first_valid_result - first_sample) / 1e6])

    audit_duration_s = 0.0
    if written and received:
        # The audit covers the whole run: from the first sensor sample that
        # was written to the last RESULT that came back.
        last = max(received[-1], written[-1])
        audit_duration_s = (last - written[0]) / 1e9

    return dict(result_gap_ms=result_gap_ms,
                firmware_emit_gap_ms=firmware_emit_gap_ms,
                firmware_previous_write_ms=firmware_previous_write_ms,
                sequence_gaps=sequence_gaps,
                pc_sample_to_result_ms=pc_sample_to_result_ms,
                audit_duration_s=audit_duration_s)


def run(port, scenario, duration, speed, imu_period, legacy, log_dir):
    session = Session(log_dir)
    link = SerialLink(port, BAUD)
    session.link = link
    session.state = 'STOPPED'
    session.imu_period_ms = imu_period
    session.timing_legacy = legacy

    budget = budget_bytes_per_second(session.radar.config.period_ms,
                                     session.radar.config.max_targets, speed,
                                     session.imu_period_ms)
    limit = BAUD / 10 * .8
    if budget > limit:
        raise SystemExit(f'budget {budget:.0f} B/s exceeds {limit:.0f} B/s')

    results = []
    try:
        if not session.wait_link_ready(8):
            raise SystemExit('link never ready')
        session.start(scenario)
        deadline = time.monotonic() + 3
        while session.pending and time.monotonic() < deadline:
            session.poll()
            time.sleep(.005)
        if session.pending:
            raise SystemExit('SESSION_START ACK missing')

        t0 = time.monotonic()
        while time.monotonic() - t0 < duration:
            session.step()
            session.poll()
            r = session.result
            if r and r.get('seq', 0) > (results[-1]['seq'] if results else 0):
                results.append(r)
            if session.state == 'ERROR':
                raise SystemExit(f'session error {session.events[-3:]}')
            time.sleep(.005)
        session.command('SESSION_STOP', 'STOPPED')
    finally:
        session.close()

    if not results:
        raise SystemExit('no RESULT received')

    trace = list(link.trace.events)
    summary = summarize(trace, session.envelope.session)

    # --- per-message stage durations (same PC clock) ----------------------
    stages = {}
    for event in trace:
        if event.get('type') == 'RESULT':
            continue
        key = (event.get('type'), event.get('radar'), event.get('seq'))
        stages.setdefault(key, {})[event['stage']] = event
    gen_to_enq, enq_to_write = [], []
    for bystage in stages.values():
        if 'generated' in bystage and 'enqueue' in bystage:
            gen_to_enq.append((bystage['enqueue']['pc_ns'] - bystage['generated']['pc_ns']) / 1e6)
        if 'enqueue' in bystage and 'write' in bystage:
            enq_to_write.append((bystage['write']['pc_ns'] - bystage['enqueue']['pc_ns']) / 1e6)

    diag = results[-1].get('diag', {})
    depths = [e['depth'] for e in trace if e['stage'] == 'enqueue']
    sizes = [e['size'] for e in trace if e['stage'] == 'write']

    def show(label, value, unit='ms'):
        if not value:
            print(f'  {label:<28} n/a')
            return
        print(f'  {label:<28} n={value["n"]:<5} p50={value["p50"]:8.2f} '
              f'p95={value["p95"]:8.2f} max={value["max"]:9.2f} {unit}')

    print(f'=== scenario {scenario} | speed {speed}x | {duration:.1f}s | '
          f'legacy={legacy} | imu {imu_period}ms ===')
    print(f'  RESULT lines received       {len(results)}')
    show('RESULT gap (PC clock)', summary['result_gap_ms'])
    show('RESULT gap (ESP32 clock)', summary['firmware_emit_gap_ms'])
    show('gen -> enqueue', stats(gen_to_enq))
    show('enqueue -> UART write', stats(enq_to_write))
    show('first valid -> RESULT', summary['pc_sample_to_result_ms'])
    show('ESP32 UART write', summary['firmware_previous_write_ms'])
    print(f'  sequence gaps               {summary["sequence_gaps"]}')
    print(f'  TX bytes                    {sum(sizes)} ({sum(sizes)/duration:.0f} B/s)')
    print(f'  TX queue depth peak         {max(depths) if depths else 0}')
    print(f'  board parser errors         {diag.get("parser_errors")}')
    print(f'  board parse_max_us          {diag.get("parse_max_us")}')
    print(f'  board rx_slice_max_us       {diag.get("rx_slice_max_us")}')
    print(f'  board rx_peak               {diag.get("rx_peak")}')
    print(f'  board rx_lines              {diag.get("rx_lines")}')

    report = dict(scenario=scenario, speed=speed, duration=duration, legacy=legacy,
                  imu_period_ms=imu_period, summary=summary,
                  gen_to_enqueue_ms=stats(gen_to_enq),
                  enqueue_to_write_ms=stats(enq_to_write),
                  bytes=sum(sizes), bytes_per_s=sum(sizes) / duration,
                  queue_peak=max(depths) if depths else 0, diag=diag)
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    Path(log_dir, f'timing_s{scenario}_{"legacy" if legacy else "normal"}.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', required=True)
    ap.add_argument('--scenario', type=int, default=2)
    ap.add_argument('--duration', type=float, default=10.0)
    ap.add_argument('--speed', type=float, default=1.0)
    ap.add_argument('--imu-period', type=int, default=10)
    ap.add_argument('--legacy', action='store_true')
    ap.add_argument('--log-dir', default='../../.local/timing')
    a = ap.parse_args()
    run(a.port, a.scenario, a.duration, a.speed, a.imu_period, a.legacy, a.log_dir)
