# PR #2194: live-app CPU comparison

Measured 7 November 2026 on HONOR BKQ-N49, Snapdragon SM8850 / Adreno 840, Android 17, eight online CPU cores. This is a single-device comparison, not validation on weaker devices.

## Result

With Steam continuously navigating at approximately 60 FPS, the software-strip fix increased mean DroidDeck process CPU usage from **29.92% to 30.71% of one CPU core**: **+0.79 percentage points**, or about **+2.6% relative process CPU time**. Run-to-run ranges overlap, so the small net process difference should not be treated as a precise universal overhead.

The main-thread cost was more consistent: **5.41% to 6.90% of one core**, an increase of **1.49 points**. Average run-level app-window draw p95 rose from **0.65 ms to 2.16 ms**. The fix trades additional UI-thread rasterization for stable rendering; it is not CPU-free.

Both renderers maintained approximately **60.2 FPS in Gamescope's Steam frame telemetry**. No measured app-window frame exceeded 16.67 ms in the hardware or capped-software samples. This does not establish zero dropped display frames: app-window timing and Gamescope telemetry are not physical-display presentation measurements.

## Which code and cap were tested?

- PR #2194: https://github.com/Droid-Deck/DroidDeck/pull/2194, fix commit `0652b0db5`.
- Its base is PR #2191 at `cbf9cb04d`, which already limits **strip graph invalidations** to 66 ms intervals, described as 15 updates/sec. This is not a Steam FPS or display refresh cap.
- **Fix off:** original hardware-accelerated strip, `LAYER_TYPE_NONE`, graph enabled, existing 66 ms graph cap.
- **Fix on:** entire undocked strip uses `LAYER_TYPE_SOFTWARE`, graph enabled, same 66 ms cap.
- **HUD off:** extra baseline with the normal HUD feature disabled.
- **Uncapped software:** separate stress case with the graph invalidation limit removed. This is not the current PR configuration; it represents the higher redraw frequency of the earlier v2 diagnostic APK.

Metrics refreshes can also invalidate the view. The capped runs therefore recorded about 16.5 total HUD draws/sec, despite the graph-specific cap. The uncapped stress runs recorded about 57.8 HUD draws/sec.

## Results

CPU percentages below use **100% = one fully occupied CPU core**. They are not percentages of the entire eight-core device. Whole-app UID totals can exceed 100% because Steam and the runtime use multiple processes and cores.

| Condition | Runs | DroidDeck process CPU, mean (range) | Main thread CPU, mean | All app-UID process CPU, mean | Steam telemetry FPS, mean |
|---|---:|---:|---:|---:|---:|
| HUD off | 3 | 14.14% (14.10–14.17) | 1.66% | 190.62% | 60.19 |
| Original hardware strip, capped | 3 | 29.92% (28.71–31.66) | 5.41% | 181.12% | 60.15 |
| Software strip fix, capped | 3 | 30.71% (28.47–31.83) | 6.90% | 182.63% | 60.18 |
| Uncapped software stress case | 2 | 42.17% (41.52–42.81) | 12.88% | 196.61% | 60.17 |

The primary comparison is hardware versus software with identical graph caps. Their paired process-CPU differences were **−0.91, +3.11, and +0.17 points**. The mean +0.79-point difference is smaller than that observed spread. The paired main-thread increases were **+1.53, +1.57, and +1.37 points**, supporting a repeatable UI-thread cost.

Whole-UID CPU averaged +1.51 points for capped software versus hardware, but paired differences ranged from −3.09 to +7.05 points. These measurements are too noisy to claim a precise total-runtime CPU penalty or saving. In particular, the lower UID total with the HUD enabled than with it disabled should not be interpreted as evidence that enabling the HUD saves CPU.

| App-window timing | Original hardware | Capped software | Uncapped software |
|---|---:|---:|---:|
| Mean of per-run total-frame p95 | 5.34 ms | 7.75 ms | 6.18 ms |
| Mean of per-run draw p95 | 0.65 ms | 2.16 ms | 1.84 ms |
| Measured frames over 16.67 ms / measured app-window frames | 0 / 1,237 | 0 / 1,235 | 0 / 2,890 |

The uncapped case used **11.46 additional core-percentage points of process CPU** versus capped software, about **37% more process CPU time**. It was measured last, at a warmer device temperature, and is a stress comparison rather than an interleaved same-cap renderer comparison. It supports retaining the existing graph redraw cap; the higher-frequency graph should not be described as free.

## Method

1. A full debug build of the separate test app ran the normal signed-in Steam session, native compositor, guest runtime, metrics collection and real frame feed. There were no synthetic frame statistics and no standalone HUD-only test activity.
2. Temporary debug-only controls selected the renderer, HUD enabled state, and graph interval. The renderer selection was the same `LAYER_TYPE_NONE` versus `LAYER_TYPE_SOFTWARE` change as the production fix. The controls did not inject frames or call the graph redraw path independently of the normal frame feed.
3. Real Android DPAD Right/Left input repeatedly navigated Steam. The scheduling target was one key every 0.125 seconds; ADB overhead reduced the actual delivered count. Hardware/software pairs received 200/201, 200/199, and 196/196 key events over each 30-second warm-up-plus-sample run. Their frame telemetry stayed approximately 60 FPS. This is matched active navigation, not a bit-identical replay of pixels or a game workload.
4. Each sample had 5 seconds of warm-up and 25 seconds of measurement. The primary order rotated across three rounds: off/hardware/software, hardware/software/off, software/off/hardware. The two uncapped stress samples followed those nine runs.
5. The display was temporarily requested at 60 Hz for repeatability. Its original settings (`peak_refresh_rate=Infinity`, `min_refresh_rate=0.0`) and original HUD state were restored and read back after testing.
6. DroidDeck process CPU came from `Process.getElapsedCpuTime()`; main-thread CPU came from `Debug.threadCpuTimeNanos()`. Both were divided by measured elapsed wall time. Whole-UID CPU summed `/proc/PID/stat` user and system ticks for the app's Android and Linux child processes, with 100 ticks/sec. No tracked process disappeared during the measured samples.
7. `Window.OnFrameMetricsAvailableListener` recorded the Android app window's frame, draw and GPU durations. It does not measure the game's SurfaceView pipeline as a whole. Gamescope's existing shared-memory frame telemetry separately checked that Steam was actively producing frames at comparable rates. No screen recording was running during the timed samples.
8. All measured focus-loss counts and dropped frame-metric report counts were zero. Android thermal status remained 0 throughout; battery temperature rose from 27°C to 32°C. The device was USB-connected/charging. CPU clocks and core placement were not pinned.

The debug controller samples focus once/sec and observes window frame metrics. It adds some measurement overhead, which is present in both renderer conditions. The approximately equal HUD draw rates and input counts make the hardware/software pair the useful estimate of this fix's cost. HUD-off is contextual: it also disables the feature's existing metrics/frame polling, not just drawing.

## Limits and conclusion for weaker devices

These results support **a small net process-CPU cost on this Adreno 840 device at the existing graph cap**, with a measurable main-thread and draw-time increase. They do **not** justify saying that weaker devices are unaffected. A slower CPU, different memory bandwidth, lower/higher pixel count, different GPU driver, sustained game load, or thermal limits can change the outcome.

This was Steam UI navigation, not an actual game running under CPU/GPU saturation. It was a short repeated test, not a long thermal, battery-life, or energy measurement. Three paired runs are not enough to establish that a sub-percentage-point process difference is statistically significant. Global system processes such as SurfaceFlinger are outside the app-UID CPU total, and physical presentation stalls were not measured.

Keep the existing cap and request at least one older-device comparison before claiming broad low-end safety. If that coverage is unavailable, making the software workaround selectable or limiting it to confirmed affected hardware would bound the unmeasured impact. No such behavior change is included in PR #2194 at present.

## Evidence and reproduction material

- `results.jsonl`: all 11 completed active samples, including input counts, timing counters, CPU data and thermal checks.
- `results.csv`: the same data flattened for inspection.
- `summary.json` and `metadata.json`: aggregates and starting conditions.
- `hud-benchmark.py`: the host runner; the active invocation used `--active-only --seconds 25 --warmup 5`.
- `hud-benchmark-summary.py`: aggregate calculations.
- `benchmark-source/`: the temporary instrumented HUD class, debug receiver and manifest. These were not added to the production PR.

Earlier idle and pilot measurements are not pooled into these active results.
