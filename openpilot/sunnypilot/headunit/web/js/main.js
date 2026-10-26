import { DashboardView } from './DashboardView.js';
import { TelemetryClient } from './TelemetryClient.js';
import { TopDownView } from './TopDownView.js';
import { VideoPlayer } from './VideoPlayer.js';

class TestPage {
  constructor() {
    this.dashboard = new DashboardView(document);
    this.topDown = new TopDownView(document.getElementById('topdown'));
    this.conn = document.getElementById('conn');
    this.hz = document.getElementById('hz');
    this.videoMsg = document.getElementById('videoMsg');
    this.camera = document.getElementById('camera');
    this.player = new VideoPlayer(document.getElementById('video'), (msg) => { this.videoMsg.textContent = msg; });
    this.telemetry = new TelemetryClient({
      onSnapshot: (snapshot) => this.onSnapshot(snapshot),
      onConnection: (up) => this.setConnected(up),
    });
    this.pending = null;
  }

  start() {
    this.hz.addEventListener('change', () => this.telemetry.connect(this.hz.value));
    document.getElementById('videoStart').addEventListener('click', () => this.player.start(this.camera.value));
    document.getElementById('videoStop').addEventListener('click', () => this.stopVideo());
    if (!VideoPlayer.supported()) {
      this.videoMsg.textContent = 'WebCodecs needs a secure context; see README.md for ffplay or the browser flag';
    }
    this.telemetry.connect(this.hz.value);
  }

  onSnapshot(snapshot) {
    // render once per animation frame no matter how fast snapshots arrive
    const first = this.pending === null;
    this.pending = snapshot;
    if (first) {
      requestAnimationFrame(() => {
        this.dashboard.render(this.pending);
        this.topDown.render(this.pending);
        this.pending = null;
      });
    }
  }

  setConnected(up) {
    this.conn.textContent = up ? 'connected' : 'disconnected';
    this.conn.className = `pill ${up ? 'on' : 'off'}`;
  }

  async stopVideo() {
    this.player.stop();
    await fetch('/video/stop', { method: 'POST' }).catch(() => {});
  }
}

new TestPage().start();
