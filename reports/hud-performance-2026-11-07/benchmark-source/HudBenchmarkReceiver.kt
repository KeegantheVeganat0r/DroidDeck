package com.droiddeck.launcher.hud

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Debug
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.PowerManager
import android.os.Process
import android.os.SystemClock
import android.view.FrameMetrics
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.inspector.WindowInspector
import com.droiddeck.launcher.session.SessionPrefs
import org.json.JSONObject
import java.io.File

class HudBenchmarkReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val result = runCatching {
            when (val mode = intent.getStringExtra("mode") ?: "status") {
                "restore" -> HudBenchmark.restore()
                "status" -> Unit
                else -> HudBenchmark.start(context, mode, intent.getIntExtra("seconds", 30),
                    intent.getIntExtra("warmup", 8))
            }
        }
        resultCode = if (result.isSuccess) Activity.RESULT_OK else Activity.RESULT_CANCELED
        resultData = result.exceptionOrNull()?.toString() ?: "OK"
    }
}

private object HudBenchmark {
    private val main = Handler(Looper.getMainLooper())
    private val metricThread = HandlerThread("hud-benchmark-metrics").apply { start() }
    private var hud: DroidDeckHudView? = null
    private var host: DroidDeckHudHost? = null
    private var activity: Activity? = null
    private var originalEnabled = false
    @Volatile private var collecting = false
    private var active = false
    private var mode = ""
    private var draws = 0
    private var callbacks = 0
    private var focusLost = 0
    private var processStart = 0L
    private var mainStart = 0L
    private var wallStart = 0L
    private var thermalStart = 0
    private val frameTimes = ArrayList<Double>()
    private val drawTimes = ArrayList<Double>()
    private val gpuTimes = ArrayList<Double>()
    private var droppedReports = 0
    private var listener: Window.OnFrameMetricsAvailableListener? = null
    private val callback = object : Runnable {
        override fun run() {
            if (!active) return
            if (collecting) {
                callbacks++
                if (activity?.hasWindowFocus() != true) focusLost++
            }
            main.postDelayed(this, 1000)
        }
    }

    private fun findHud(): DroidDeckHudView? {
        fun walk(view: View): DroidDeckHudView? {
            if (view is DroidDeckHudView) return view
            if (view is ViewGroup) for (i in 0 until view.childCount) walk(view.getChildAt(i))?.let { return it }
            return null
        }
        return WindowInspector.getGlobalWindowViews().firstNotNullOfOrNull(::walk)
    }

    fun start(context: Context, requested: String, seconds: Int, warmup: Int) {
        require(requested in setOf("off", "hardware15", "software15", "software60"))
        require(seconds in 10..90 && warmup in 2..30)
        check(!active) { "A measurement is already running" }
        val view = findHud() ?: error("Start a visible Steam session first")
        if (hud == null) {
            hud = view
            activity = view.context as Activity
            val field = activity!!.javaClass.getDeclaredField("hud").apply { isAccessible = true }
            host = field.get(activity) as DroidDeckHudHost
            originalEnabled = SessionPrefs.hudEnabled(context)
        }
        check(view === hud) { "The session changed during the benchmark" }
        check(activity!!.hasWindowFocus()) { "Unlock the phone and leave Steam visible" }
        mode = requested
        active = true
        SessionPrefs.setHudEnabled(context, mode != "off")
        host!!.refresh()
        view.benchmarkStripLayer = if (mode == "hardware15") View.LAYER_TYPE_NONE else View.LAYER_TYPE_SOFTWARE
        view.benchmarkStripPeriodMs = if (mode == "software60") 0L else 66L
        view.applyConfig(DroidDeckHudConfig.encode(DroidDeckHudConfig.load(context) +
            ("hudSize" to "strip") + ("hudDock" to "none") + ("showFPSGraph" to "1")))
        view.benchmarkDraw = { if (collecting) draws++ }
        main.post(callback)
        writeState(context, "warmup")
        main.postDelayed({ beginMeasurement(context, seconds) }, warmup * 1000L)
    }

    private fun beginMeasurement(context: Context, seconds: Int) {
        draws = 0; callbacks = 0; focusLost = 0
        synchronized(frameTimes) {
            frameTimes.clear(); drawTimes.clear(); gpuTimes.clear(); droppedReports = 0
        }
        listener = Window.OnFrameMetricsAvailableListener { _, metrics, dropped ->
            if (collecting) synchronized(frameTimes) {
                frameTimes += metrics.getMetric(FrameMetrics.TOTAL_DURATION) / 1e6
                drawTimes += metrics.getMetric(FrameMetrics.DRAW_DURATION) / 1e6
                gpuTimes += metrics.getMetric(FrameMetrics.GPU_DURATION) / 1e6
                droppedReports += dropped
            }
        }.also { activity!!.window.addOnFrameMetricsAvailableListener(it, Handler(metricThread.looper)) }
        thermalStart = context.getSystemService(PowerManager::class.java).currentThermalStatus
        writeState(context, "measuring")
        processStart = Process.getElapsedCpuTime()
        mainStart = Debug.threadCpuTimeNanos()
        wallStart = SystemClock.elapsedRealtimeNanos()
        collecting = true
        main.postDelayed({ finish(context) }, seconds * 1000L)
    }

    private fun finish(context: Context) {
        val endWall = SystemClock.elapsedRealtimeNanos()
        val endMain = Debug.threadCpuTimeNanos()
        val endProcess = Process.getElapsedCpuTime()
        collecting = false
        active = false
        main.removeCallbacks(callback)
        listener?.let { activity!!.window.removeOnFrameMetricsAvailableListener(it) }
        listener = null
        val seconds = (endWall - wallStart) / 1e9
        fun percentile(values: List<Double>, q: Double): Double? =
            values.sorted().takeIf { it.isNotEmpty() }?.let { it[((it.size - 1) * q).toInt()] }
        val result = synchronized(frameTimes) {
            JSONObject().put("mode", mode).put("wallSeconds", seconds)
                .put("processCpuMs", endProcess - processStart)
                .put("mainCpuMs", (endMain - mainStart) / 1e6)
                .put("cpuPercentOneCore", (endProcess - processStart) / (seconds * 10))
                .put("mainCpuPercentOneCore", (endMain - mainStart) / (seconds * 1e7))
                .put("hudDraws", draws).put("focusSamples", callbacks).put("focusLostSamples", focusLost)
                .put("windowFrames", frameTimes.size).put("droppedMetricReports", droppedReports)
                .put("frameP50Ms", percentile(frameTimes, .5)).put("frameP95Ms", percentile(frameTimes, .95))
                .put("frameP99Ms", percentile(frameTimes, .99)).put("drawP95Ms", percentile(drawTimes, .95))
                .put("gpuP95Ms", percentile(gpuTimes, .95))
                .put("framesOver16_667ms", frameTimes.count { it > 1000.0 / 60 })
                .put("thermalStart", thermalStart)
                .put("thermalEnd", context.getSystemService(PowerManager::class.java).currentThermalStatus)
                .put("heapBytes", Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory())
                .put("nativeHeapBytes", Debug.getNativeHeapAllocatedSize())
        }
        val file = File(context.filesDir, "hud-benchmark-results.jsonl")
        file.appendText(result.toString() + "\n")
        writeState(context, "done")
    }

    private fun writeState(context: Context, phase: String) {
        File(context.filesDir, "hud-benchmark-state.json").writeText(
            JSONObject().put("mode", mode).put("phase", phase).toString())
    }

    fun restore() {
        val view = hud ?: return
        collecting = false; active = false
        main.removeCallbacksAndMessages(null)
        listener?.let { activity!!.window.removeOnFrameMetricsAvailableListener(it) }
        listener = null
        view.benchmarkDraw = null
        view.benchmarkStripLayer = null
        view.benchmarkStripPeriodMs = null
        SessionPrefs.setHudEnabled(view.context, originalEnabled)
        host!!.refresh()
        writeState(view.context, "restored")
        hud = null; host = null; activity = null
    }
}
