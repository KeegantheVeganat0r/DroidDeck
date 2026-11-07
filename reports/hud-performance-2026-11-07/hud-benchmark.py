import argparse
import json
import math
import re
import shlex
import statistics
import struct
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


PACKAGE = 'com.droiddeck.launcher.hudgraph'
COMPONENT = PACKAGE + '/com.droiddeck.launcher.hud.HudBenchmarkReceiver'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adb', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=25)
    parser.add_argument('--warmup', type=int, default=5)
    parser.add_argument('--active-only', action='store_true')
    parser.add_argument('--pilot', action='store_true')
    parser.add_argument('--key-interval', type=float, default=0.125)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    def adb(*command, binary=False, check=True):
        p = subprocess.run([args.adb, *command], capture_output=True, timeout=45)
        if check and p.returncode:
            raise RuntimeError(p.stderr.decode(errors='replace'))
        return p.stdout if binary else p.stdout.decode(errors='replace').strip()

    def control(mode, **values):
        command = ['shell', 'am', 'broadcast', '-a', PACKAGE + '.BENCHMARK', '-n', COMPONENT,
                   '--es', 'mode', mode]
        for key, value in values.items():
            command += ['--ei', key, str(value)]
        response = adb(*command)
        if 'result=-1' not in response:
            raise RuntimeError('Benchmark control failed: ' + response)

    def file(name, binary=False):
        return adb('exec-out', 'run-as', PACKAGE, 'cat', name, binary=binary)

    app_pid = int(adb('shell', 'pidof', PACKAGE).split()[0])
    uid = int(re.search(r'^Uid:\s+(\d+)', file('/proc/%d/status' % app_pid), re.M).group(1))
    ticks_per_second = int(adb('shell', 'getconf', 'CLK_TCK'))
    online = adb('shell', 'cat', '/sys/devices/system/cpu/online')

    def cpu_snapshot():
        processes = []
        for row in adb('shell', 'ps', '-A', '-n', '-o', 'UID,PID,NAME').splitlines():
            parts = row.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit() and int(parts[0]) == uid:
                processes.append(int(parts[1]))
        script = 'for p in ' + ' '.join(map(str, processes)) + '; do cat /proc/$p/stat 2>/dev/null; done; true'
        raw = adb('shell', 'run-as', PACKAGE, 'sh', '-c', shlex.quote(script))
        result = {}
        for row in raw.splitlines():
            try:
                pid = int(row.split(' ', 1)[0])
                name = row[row.index('(') + 1:row.rindex(')')]
                fields = row[row.rindex(')') + 2:].split()
                result[str(pid)] = {'ticks': int(fields[11]) + int(fields[12]),
                                    'start': int(fields[19]), 'name': name}
            except (ValueError, IndexError):
                continue
        if str(app_pid) not in result:
            raise RuntimeError('App CPU counters missing; session may have changed')
        return result

    def ring():
        data = file('files/linuxfs/run/droiddeck-hud/frames', binary=True)
        magic, _, capacity = struct.unpack_from('<III', data)
        if magic != 0x46484444 or capacity < 1 or len(data) < 128 + capacity * 16:
            raise RuntimeError('Invalid live frame telemetry')
        count = struct.unpack_from('<Q', data, 16)[0]
        entries = {}
        for index in range(max(0, count - capacity), count):
            duration, stamp = struct.unpack_from('<qq', data, 128 + (index % capacity) * 16)
            if duration > 0:
                entries[index] = (duration / 1e6, stamp)
        return count, entries

    def thermal():
        battery = adb('shell', 'dumpsys', 'battery')
        state = adb('shell', 'dumpsys', 'thermalservice')
        return {'batteryTempTenthsC': int(re.search(r'temperature:\s*(\d+)', battery).group(1)),
                'status': int(re.search(r'Thermal Status:\s*(\d+)', state).group(1))}

    def percentile(values, fraction):
        values = sorted(values)
        return values[int((len(values) - 1) * fraction)] if values else None

    settings = {key: adb('shell', 'settings', 'get', 'system', key)
                for key in ('peak_refresh_rate', 'min_refresh_rate')}
    metadata = {'startedUtc': datetime.now(timezone.utc).isoformat(), 'package': PACKAGE,
                'appPid': app_pid, 'uid': uid, 'cpuOnline': online, 'clkTck': ticks_per_second,
                'settingsBefore': settings, 'warmupSeconds': args.warmup,
                'sampleSeconds': args.seconds, 'syntheticFrames': False,
                'workloads': {'idle': 'Untouched Steam home screen',
                              'navigation': 'Alternating real DPAD Right/Left every %.3f seconds' % args.key_interval},
                'initialThermal': thermal()}
    (args.out / 'metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')

    plan = [
        ('idle', 1, ['off', 'hardware15', 'software15', 'software60']),
        ('idle', 2, ['software60', 'software15', 'hardware15', 'off']),
        ('navigation', 1, ['hardware15', 'off', 'software60', 'software15']),
        ('navigation', 2, ['software15', 'software60', 'off', 'hardware15']),
    ]
    if args.active_only:
        plan = [('navigation', 1, ['off', 'hardware15', 'software15']),
                ('navigation', 2, ['hardware15', 'software15', 'off']),
                ('navigation', 3, ['software15', 'off', 'hardware15']),
                ('navigation', 4, ['software60']), ('navigation', 5, ['software60'])]
    if args.pilot:
        plan = [('navigation', 0, ['software15'])]
    results = []
    try:
        for key in settings:
            adb('shell', 'settings', 'put', 'system', key, '60.0')
        for workload, repeat, modes in plan:
            for mode in modes:
                before_thermal = thermal()
                print('START', workload, repeat, mode, flush=True)
                control(mode, seconds=args.seconds, warmup=args.warmup)
                started = time.monotonic()
                next_key = 0
                start_cpu = end_cpu = None
                start_ring = end_ring = None
                entries = {}
                measured_started = None
                next_ring_at = args.warmup + .25
                end_at = args.warmup + args.seconds + .25
                while time.monotonic() - started < end_at:
                    elapsed = time.monotonic() - started
                    if workload == 'navigation' and elapsed >= next_key * args.key_interval and next_key * args.key_interval < args.warmup + args.seconds:
                        adb('shell', 'input', 'keyevent', '22' if next_key % 2 == 0 else '21')
                        next_key += 1
                    if elapsed >= next_ring_at:
                        count, fresh = ring()
                        entries.update(fresh)
                        if start_ring is None:
                            start_ring = count
                            start_cpu = cpu_snapshot()
                            measured_started = time.monotonic()
                        next_ring_at += 8
                    time.sleep(.08)
                count, fresh = ring()
                entries.update(fresh)
                end_ring = count
                end_cpu = cpu_snapshot()
                measured_wall = time.monotonic() - measured_started
                state = json.loads(file('files/hud-benchmark-state.json'))
                if state['phase'] != 'done':
                    time.sleep(2)
                    state = json.loads(file('files/hud-benchmark-state.json'))
                if state['phase'] != 'done':
                    raise RuntimeError('Measurement did not finish: ' + str(state))
                result = json.loads(file('files/hud-benchmark-results.jsonl').splitlines()[-1])
                if result['mode'] != mode or result['focusLostSamples']:
                    raise RuntimeError('Mode/focus mismatch: ' + str(result))
                durations = [value[0] for index, value in entries.items() if start_ring <= index < end_ring]
                delta_ticks = 0
                disappeared = []
                for pid, values in start_cpu.items():
                    end = end_cpu.get(pid)
                    if end is None or end['start'] != values['start']:
                        disappeared.append(values['name'])
                    else:
                        delta_ticks += max(0, end['ticks'] - values['ticks'])
                for pid, values in end_cpu.items():
                    if pid not in start_cpu:
                        delta_ticks += values['ticks']
                result.update({'workload': workload, 'repeat': repeat,
                               'utc': datetime.now(timezone.utc).isoformat(),
                               'uidCpuPercentOneCore': delta_ticks / ticks_per_second / measured_wall * 100,
                               'uidCpuSeconds': delta_ticks / ticks_per_second,
                               'uidSampleSeconds': measured_wall, 'disappearedProcesses': disappeared,
                               'steamFrameCount': end_ring - start_ring, 'steamFramesSampled': len(durations),
                               'steamFps': (end_ring - start_ring) / measured_wall,
                               'steamFrameP50Ms': percentile(durations, .5),
                               'steamFrameP95Ms': percentile(durations, .95),
                               'steamFrameP99Ms': percentile(durations, .99),
                               'thermalBefore': before_thermal, 'thermalAfter': thermal(),
                               'inputEvents': next_key})
                results.append(result)
                with (args.out / 'results.jsonl').open('a', encoding='utf-8') as target:
                    target.write(json.dumps(result) + '\n')
                print('DONE', workload, repeat, mode,
                      'appCPU=%.2f%%' % result['cpuPercentOneCore'],
                      'UIDCPU=%.2f%%' % result['uidCpuPercentOneCore'],
                      'draws=%d' % result['hudDraws'],
                      'SteamFPS=%.1f' % result['steamFps'], flush=True)
    finally:
        try:
            control('restore')
        finally:
            for key, value in settings.items():
                if value == 'null':
                    adb('shell', 'settings', 'delete', 'system', key)
                else:
                    adb('shell', 'settings', 'put', 'system', key, value)
    print('COMPLETE', len(results), 'measurements; HUD and refresh settings restored', flush=True)


if __name__ == '__main__':
    main()
