import json
import statistics
from pathlib import Path

root = Path('build/hud-performance-active')
rows = [json.loads(line) for line in (root / 'results.jsonl').read_text().splitlines() if line]
metadata = json.loads((root / 'metadata.json').read_text())
fields = ['cpuPercentOneCore', 'mainCpuPercentOneCore', 'uidCpuPercentOneCore',
          'steamFps', 'frameP95Ms', 'drawP95Ms', 'gpuP95Ms']
groups = {}
for mode in ['off', 'hardware15', 'software15', 'software60']:
    samples = [row for row in rows if row['mode'] == mode]
    if not samples:
        continue
    summary = {'n': len(samples)}
    for field in fields:
        values = [row[field] for row in samples if row.get(field) is not None]
        if values:
            summary[field] = {'mean': statistics.mean(values), 'min': min(values), 'max': max(values)}
    summary['hudDrawsPerSecond'] = statistics.mean(row['hudDraws'] / row['wallSeconds'] for row in samples)
    summary['inputEvents'] = [row['inputEvents'] for row in samples]
    summary['focusLost'] = sum(row['focusLostSamples'] for row in samples)
    summary['thermalStatuses'] = sorted({row[key] for row in samples for key in ['thermalStart', 'thermalEnd']})
    summary['disappearedProcesses'] = [name for row in samples for name in row['disappearedProcesses']]
    summary['uiFrames'] = sum(row['windowFrames'] for row in samples)
    summary['slowUiFrames'] = sum(row['framesOver16_667ms'] for row in samples)
    groups[mode] = summary

paired = []
for repeat in sorted({row['repeat'] for row in rows if row['mode'] in ['hardware15', 'software15']}):
    hardware = next((row for row in rows if row['repeat'] == repeat and row['mode'] == 'hardware15'), None)
    software = next((row for row in rows if row['repeat'] == repeat and row['mode'] == 'software15'), None)
    if hardware and software:
        paired.append({'repeat': repeat, **{field: software[field] - hardware[field]
                                            for field in fields if software.get(field) is not None and hardware.get(field) is not None}})

result = {'metadata': metadata, 'groups': groups, 'pairedSoftwareMinusHardware': paired,
          'completeRuns': len(rows)}
(root / 'summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
