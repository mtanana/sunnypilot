// Thin wrapper around EventSource for /telemetry. EventSource reconnects on its own.

export class TelemetryClient {
  constructor({ onSnapshot, onConnection }) {
    this.onSnapshot = onSnapshot;
    this.onConnection = onConnection;
    this.source = null;
  }

  connect(hz) {
    this.close();
    this.source = new EventSource(`/telemetry?hz=${encodeURIComponent(hz)}`);
    this.source.onopen = () => this.onConnection(true);
    this.source.onerror = () => this.onConnection(false);
    this.source.onmessage = (event) => {
      try {
        this.onSnapshot(JSON.parse(event.data));
      } catch (err) {
        console.warn('bad telemetry message', err);
      }
    };
  }

  close() {
    if (this.source) {
      this.source.close();
      this.source = null;
    }
  }
}
