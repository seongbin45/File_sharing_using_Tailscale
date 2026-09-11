"""The Qt engine: a timer, a worker thread, and signals.

Everything the UI needs to react to is a signal, so the window never reaches
into the engine's state and the engine never touches a widget. The actual work
runs in engine_core on a worker thread; this class only schedules it and
relays what it reports.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from . import engine_core
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

    def __init__(self, cfg, log) -> None:
        super().__init__()
        self.cfg = cfg
        self.log = log
        self._running = False
        self._paused = False
        self._thread: QThread | None = None
        self._worker: _SenderWorker | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
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
            # Fire the first run immediately, then fall into the interval.
            self._run_now()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
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
        self._timer.stop()
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
        """Manual trigger, honoured whether paused or idle."""
        if self.cfg.role == "receiver":
            self._on_receiver_tick()
            return
        if self._worker is not None:
            self.log.line("이미 실행 중입니다.")
            return
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
        self.run_finished.emit(result)
        if self._thread:
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self._worker = None
        if self._running and not self._paused:
            self.status.emit("실행 중")
            self._schedule_next()
        elif self._paused:
            self.status.emit("일시중지")

    def _schedule_next(self) -> None:
        import time

        minutes = max(1, int(self.cfg.sender.interval_minutes))
        self._timer.start(minutes * 60 * 1000)
        due = time.time() + minutes * 60
        self.next_run.emit(time.strftime("%Y-%m-%d %H:%M", time.localtime(due)))

    def _on_timer(self) -> None:
        if self._running and not self._paused:
            self._run_now()

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
