"""The Qt engine: a timer, a worker thread, and signals.

Everything the UI needs to react to is a signal, so the window never reaches
into the engine's state and the engine never touches a widget. The actual work
runs in engine_core on a worker thread; this class only schedules it and
relays what it reports.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from . import engine_core
from .engine_core import RETRY_INTERVAL_MINUTES, next_retry_state
from .receiver import Receiver


class _SenderWorker(QObject):
    finished = Signal(object)          # RunResult
    progress = Signal(str, int)        # phase, percent
    line = Signal(str)

    def __init__(self, cfg) -> None:
        super().__init__()
        self._cfg = cfg
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        result = engine_core.run_sender_once(
            self._cfg,
            log=self.line.emit,
            progress=lambda phase, pct: self.progress.emit(phase, pct),
            cancelled=lambda: self._cancel,
        )
        self.finished.emit(result)


class Engine(QObject):
    # Everything the window binds to.
    status = Signal(str)               # "대기", "실행 중", "일시중지" ...
    progress = Signal(str, int)
    line = Signal(str)
    run_finished = Signal(object)
    next_run = Signal(str)             # human text for the next scheduled run
    # The next scheduled run as a Unix timestamp (0 = none), so screens can
    # word it their own way ("오늘 새벽 4시").
    next_due = Signal(float)
    # Fires exactly once per failure incident, only after retries are
    # exhausted - never on every failed attempt. The tray's notification and
    # error icon hang off this, not off run_finished, so a run that fails
    # once and then succeeds on retry never notifies at all.
    failed_after_retries = Signal(object)  # RunResult

    def __init__(self, cfg, log) -> None:
        super().__init__()
        self.cfg = cfg
        self.log = log
        self._running = False
        self._paused = False
        self._thread: QThread | None = None
        self._worker: _SenderWorker | None = None
        self._retry_count = 0

        # A due time checked every 30 seconds rather than one long single
        # shot: a long QTimer does not survive sleep reliably, and a slot
        # passed while asleep must still fire on wake (tsbackup/schedule.py).
        self._due: float | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(30_000)
        self._timer.timeout.connect(self._on_timer)

        self._receiver = Receiver(cfg, self._emit_line)
        self._recv_timer = QTimer(self)
        self._recv_timer.timeout.connect(self._on_receiver_tick)

        log.subscribe(self.line.emit)

    def _emit_line(self, msg: str) -> None:
        self.log.line(msg)

    # ------------------------------------------------------------- control

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._paused = False
        if self.cfg.role == "receiver":
            self._start_receiver()
        else:
            self.status.emit("실행 중")
            # Catch up once if a slot passed while the app was not running;
            # otherwise wait for the next clock slot.
            from datetime import datetime

            from . import schedule

            s = self.cfg.sender
            if schedule.due_now(datetime.now(), self._last_run(), s.interval_minutes, s.at_time):
                self._run_now()
            else:
                self._schedule_next()

    def stop(self) -> None:
        self._running = False
        self._retry_count = 0
        self._disarm()
        self._recv_timer.stop()
        self._receiver.stop_http()
        if self._worker:
            self._worker.cancel()
        self.status.emit("대기")
        self.next_run.emit("")

    def pause(self) -> None:
        if not self._running:
            return
        self._paused = True
        self._disarm()
        self.status.emit("일시중지")
        self.next_run.emit("일시중지됨")

    def resume(self) -> None:
        if not self._running or not self._paused:
            return
        self._paused = False
        if self.cfg.role == "receiver":
            self.status.emit("수신 대기")
            self._recv_timer.start(5000)
        else:
            self.status.emit("실행 중")
            self._schedule_next()

    def run_now(self) -> None:
        """Manual trigger, honoured whether paused or idle. Cancels any
        pending retry timer first - a fresh manual run replaces it rather
        than racing it when the retry later fires on its own."""
        if self.cfg.role == "receiver":
            self._on_receiver_tick()
            return
        if self._worker is not None:
            self.log.line("이미 실행 중입니다.")
            return
        self._disarm()
        self._retry_count = 0
        self._run_now()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def busy(self) -> bool:
        return self._worker is not None

    # ------------------------------------------------------------- sender

    def _run_now(self) -> None:
        if self._worker is not None:
            return
        self.status.emit("압축·전송 중")
        self.next_run.emit("실행 중")
        import time
        self._run_started = time.time()
        thread = QThread(self)
        worker = _SenderWorker(self.cfg)
        worker.moveToThread(thread)
        worker.progress.connect(self.progress)
        worker.line.connect(self.log.line)
        worker.finished.connect(self._on_run_finished)
        thread.started.connect(worker.run)
        self._thread, self._worker = thread, worker
        thread.start()

    def _on_run_finished(self, result) -> None:
        import time

        from . import history
        from .config import config_dir

        result.elapsed = time.time() - getattr(self, "_run_started", time.time())
        history.record(config_dir() / history.FILENAME, result, result.elapsed)
        self.run_finished.emit(result)
        if self._thread:
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self._worker = None
        # A transport can pin state into cfg as a side effect of a run (the
        # SFTP transport's trust-on-first-connect host key, for one) - save
        # unconditionally so that never depends on a settings-dialog save
        # happening to follow.
        self.cfg.save()

        if not self._running:
            return
        if self._paused:
            self.status.emit("일시중지")
            return

        self.status.emit("실행 중")
        action, self._retry_count = next_retry_state(result.ok, self._retry_count)
        if action == "retry":
            self._schedule_retry()
        else:
            if action == "exhausted":
                self.failed_after_retries.emit(result)
            self._schedule_next()

    def _schedule_next(self) -> None:
        from datetime import datetime

        from . import schedule

        s = self.cfg.sender
        self._arm(schedule.next_slot(datetime.now(), s.interval_minutes, s.at_time).timestamp())

    def _schedule_retry(self) -> None:
        import time

        self._arm(time.time() + RETRY_INTERVAL_MINUTES * 60,
                  f" (재시도 {self._retry_count}/{engine_core.MAX_RETRIES})")

    def _arm(self, due: float, suffix: str = "") -> None:
        import time

        self._due = due
        self._timer.start()
        self.next_run.emit(time.strftime("%Y-%m-%d %H:%M", time.localtime(due)) + suffix)
        self.next_due.emit(due)

    def _disarm(self) -> None:
        self._due = None
        self._timer.stop()
        self.next_due.emit(0.0)

    @property
    def due(self) -> float | None:
        return self._due

    def _on_timer(self) -> None:
        import time

        if self._running and not self._paused and self._due is not None and time.time() >= self._due:
            self._disarm()
            self._run_now()

    def _last_run(self):
        """When the sender last ran, from the run history (tsbackup/history.py)."""
        from datetime import datetime

        from . import history
        from .config import config_dir

        rows = history.load(config_dir() / history.FILENAME, 1)
        return datetime.fromtimestamp(rows[0]["at"]) if rows else None

    # ------------------------------------------------------------ receiver

    def _start_receiver(self) -> None:
        self.status.emit("수신 대기")
        if self.cfg.receiver_uses_http():
            self._receiver.start_http()
        self._recv_timer.start(5000)
        self._on_receiver_tick()

    def _on_receiver_tick(self) -> None:
        try:
            self._receiver.scan_once()
        except Exception as exc:  # noqa: BLE001
            self.log.line(f"수신 처리 오류: {exc}")
