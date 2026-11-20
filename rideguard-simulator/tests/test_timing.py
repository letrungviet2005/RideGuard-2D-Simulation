from rideguard_sim.timing import TimingTrace
from scripts.diagnostics.measure_result_timing import stats, summarize


def test_trace_is_bounded_and_reports_loss():
    trace=TimingTrace(2)
    for i in range(3):trace.mark('enqueue',dict(type='IMU',seq=i))
    assert len(trace.events)==2 and trace.dropped==1
    assert trace.events[-1]['seq']==2


def test_summary_does_not_subtract_pc_and_device_clocks():
    def result(seq,pc,device):
        return dict(stage='receive',session='s',type='RESULT',seq=seq,pc_ns=pc,
                    size=100,diagnostics=dict(emit_us=device,write_us=2000,parse_max_us=40))
    events=[result(1,1_000_000_000,4_000_000), result(2,1_101_000_000,4_100_000)]
    summary=summarize(events,'s')
    assert summary['result_gap_ms']['max']==101
    assert summary['firmware_emit_gap_ms']['max']==100
    assert summary['firmware_previous_write_ms']['max']==2
    assert summary['sequence_gaps']==0


def test_quantiles_empty_and_single():
    assert stats([]) is None
    assert stats([7])==dict(n=1,p50=7,p95=7,max=7)


def test_feedback_counts_first_valid_observation_not_stale_heartbeat():
    base=dict(session='s',radar=None)
    events=[dict(base,stage='write',type='IMU',seq=1,pc_ns=1_000_000,
                 start_ns=900_000,size=10)]
    for seq,valid in [(1,True),(2,True),(3,False)]:
        events.append(dict(base,stage='receive',type='RESULT',seq=seq,
                           pc_ns=(seq+1)*1_000_000,size=100,
                           sensors={'IMU':dict(seq=1,valid=valid)}))
    summary=summarize(events,'s')
    assert summary['pc_sample_to_result_ms']==dict(n=1,p50=1,p95=1,max=1)
    assert summary['audit_duration_s']==.003
