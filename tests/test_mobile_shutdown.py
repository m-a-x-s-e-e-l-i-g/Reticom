import importlib.util
from pathlib import Path
from types import SimpleNamespace
import threading


ROOT = Path(__file__).parents[1]


def runtime():
    spec = importlib.util.spec_from_file_location("mobile_shutdown_test", ROOT / "android/app/src/main/python/mobile_main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shutdown_exits_http_joins_without_lock_and_persists_rns(monkeypatch):
    module = runtime()
    server = SimpleNamespace(should_exit=False)
    calls = []
    class Worker:
        alive = True
        def join(self, timeout):
            assert server.should_exit
            assert module._lock.acquire(blocking=False)
            module._lock.release()
            calls.append(("join", timeout))
            self.alive = False
        def is_alive(self): return self.alive
    module._server, module._thread = server, Worker()
    monkeypatch.setattr(module.RNS.Reticulum, "get_instance", lambda: object())
    monkeypatch.setattr(module.RNS.Reticulum, "exit_handler", lambda: calls.append("rns-exit"))
    assert module.stop()
    assert calls == [("join", 5.0), "rns-exit"]
    assert module.start("must-not-create-data") is False


def test_shutdown_during_startup_does_not_launch_server(monkeypatch, tmp_path):
    module = runtime()
    entered, proceed = threading.Event(), threading.Event()
    def app(*args):
        entered.set()
        assert proceed.wait(2)
        return object()
    monkeypatch.setattr(module, "create_app", app)
    monkeypatch.setattr(module.uvicorn, "Config", lambda *args, **kwargs: kwargs)
    monkeypatch.setattr(module.uvicorn, "Server", lambda config: SimpleNamespace(run=lambda: (_ for _ in ()).throw(AssertionError("server must not run"))))
    monkeypatch.setattr(module.RNS.Reticulum, "get_instance", lambda: None)
    module._thread = threading.Thread(target=module._run, args=(tmp_path, tmp_path))
    module._thread.start()
    try:
        assert entered.wait(2)
        assert module.stop(timeout=0) is False
    finally:
        proceed.set()
        module._thread.join(2)
    assert not module._thread.is_alive()
    assert module._server is None


def test_notification_shutdown_is_explicit_private_and_cannot_restart_from_onstop():
    service = (ROOT / "android/app/src/main/java/com/retium/field/RetiumNodeService.java").read_text()
    activity = (ROOT / "android/app/src/main/java/com/retium/field/MainActivity.java").read_text()
    manifest = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text()
    assert '"Shut down", shutdown' in service
    assert 'PendingIntent.getService(' in service
    assert 'new Intent(this, RetiumNodeService.class).setAction(ACTION_SHUT_DOWN)' in service
    assert 'PendingIntent.FLAG_IMMUTABLE' in service
    assert 'android:name=".RetiumNodeService"\n            android:exported="false"' in manifest
    shutdown = service.split('private void shutDown() {')[1].split('private void acquireNetworkLocks')[0]
    assert shutdown.index('shuttingDown = true') < shutdown.index('finishAndRemoveTask()')
    assert 'stopForeground(STOP_FOREGROUND_REMOVE)' in shutdown
    assert 'stopSelf()' in shutdown
    assert 'getModule("mobile_main").callAttr("stop")' in shutdown
    assert 'Process.killProcess(Process.myPid())' in shutdown
    for method in ['startNodeService()', 'updateNodeServiceVisibility(boolean foreground)', 'notifyBackgroundTtsVoiceChanged()', 'updateBackgroundAlertPreference(boolean enabled)', 'updateAutomaticLocationPreference(boolean enabled)']:
        assert f'private void {method} {{\n        if (RetiumNodeService.isShuttingDown()) return;' in activity
