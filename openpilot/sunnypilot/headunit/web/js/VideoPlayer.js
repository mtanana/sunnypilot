import { FramedStreamReader, codecFromKeyframe } from './FramedStreamReader.js';

// Plays /video in the browser with WebCodecs. Browsers only expose WebCodecs to secure
// contexts, so over plain http://<comma-ip> this needs a browser flag (see README).
export class VideoPlayer {
  constructor(canvas, onMessage) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.onMessage = onMessage;
    this.abort = null;
    this.decoder = null;
  }

  static supported() {
    return window.isSecureContext && 'VideoDecoder' in window;
  }

  async start(camera) {
    this.stop();
    if (!VideoPlayer.supported()) {
      this.onMessage('WebCodecs unavailable on this origin; use ffplay or the browser flag in README.md');
      return;
    }

    this.abort = new AbortController();
    const abort = this.abort;
    let response;
    try {
      response = await fetch(`/video?camera=${encodeURIComponent(camera)}`, { signal: abort.signal });
    } catch (err) {
      if (err.name !== 'AbortError') this.onMessage(`request failed: ${err.message}`);
      return;
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      this.onMessage(`refused: ${body.reason || response.status}`);
      return;
    }

    this.onMessage('waiting for keyframe...');
    const reader = response.body.getReader();
    const framer = new FramedStreamReader();
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        for (const frame of framer.push(value)) this.decode(frame);
      }
      this.onMessage('stream ended (see last stop)');
    } catch (err) {
      if (err.name !== 'AbortError') this.onMessage(`stream error: ${err.message}`);
    } finally {
      // a newer start() may own the decoder by now
      if (this.abort === abort) this.closeDecoder();
    }
  }

  decode(frame) {
    if (this.decoder?.state === 'closed') this.decoder = null; // closed itself after an error
    if (!this.decoder) {
      if (!frame.keyframe) return;
      const codec = codecFromKeyframe(frame.data);
      if (!codec) return;
      this.decoder = new VideoDecoder({
        output: (videoFrame) => this.draw(videoFrame),
        error: (err) => this.onMessage(`decoder error: ${err.message}`),
      });
      this.decoder.configure({ codec, optimizeForLatency: true });
      this.skipping = false;
      this.onMessage(`playing ${codec}`);
    }
    // a backed-up decoder means we're behind; skip to the next keyframe
    if (frame.keyframe) {
      this.skipping = false;
    } else if (this.skipping || this.decoder.decodeQueueSize > 10) {
      this.skipping = true;
      return;
    }
    this.decoder.decode(new EncodedVideoChunk({
      type: frame.keyframe ? 'key' : 'delta',
      timestamp: frame.ptsUs,
      data: frame.data,
    }));
  }

  draw(videoFrame) {
    if (this.canvas.width !== videoFrame.displayWidth) this.canvas.width = videoFrame.displayWidth;
    if (this.canvas.height !== videoFrame.displayHeight) this.canvas.height = videoFrame.displayHeight;
    this.ctx.drawImage(videoFrame, 0, 0);
    videoFrame.close();
  }

  stop() {
    if (this.abort) {
      this.abort.abort();
      this.abort = null;
    }
    this.closeDecoder();
  }

  closeDecoder() {
    if (this.decoder && this.decoder.state !== 'closed') this.decoder.close();
    this.decoder = null;
  }
}
