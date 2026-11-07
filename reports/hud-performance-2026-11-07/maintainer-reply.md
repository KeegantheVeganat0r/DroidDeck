I ran the comparison with the full app and Steam running, using repeated real navigation at ~60 FPS—not a HUD-only test or synthetic frame data.

For PR #2194 as submitted, both original hardware rendering and the software fix retained the 15 Hz graph cap inherited from #2191. Three paired 25-second samples per mode, after 5-second warm-ups, gave:

| Measurement | Hardware strip | Software strip |
|---|---:|---:|
| DroidDeck process CPU | 29.92% | 30.71% |
| Main-thread CPU | 5.41% | 6.90% |
| Steam frame telemetry | 60.15 FPS | 60.18 FPS |
| App-window draw p95, mean across runs | 0.65 ms | 2.16 ms |

CPU uses **100% = one fully occupied core**, not the whole device. The fix averaged +0.79 core-percentage points of process CPU, with overlapping run ranges; the clearer cost was +1.49 points on the main thread. No measured app-window frame exceeded 16.67 ms, and thermal status stayed at 0. This was on HONOR BKQ-N49 / Adreno 840, Android 17.

I also measured the app's whole UID, including Steam/runtime processes: 181.12% → 182.63% of one core, but the variation was too large to claim a precise total-runtime overhead. An additional uncapped software stress case cost 42.17% process CPU, versus 30.71% capped, so retaining the graph cap matters.

The result is small overhead on this phone, **not proof that weaker devices are unaffected**. The main-thread draw-time increase is real, and this was active Steam UI navigation rather than a saturated game. I'd keep the cap and get an older-device A/B before claiming broad low-end safety. Raw samples, method, and benchmark source are included in the accompanying report.
