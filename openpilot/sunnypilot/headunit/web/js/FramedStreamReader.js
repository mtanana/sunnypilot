// Splits the /video "framed" byte stream into frames.
// Header: uint32 BE payload length, uint8 flags (bit0 keyframe), uint64 BE pts (us).

const HEADER_BYTES = 13;

export class FramedStreamReader {
  constructor() {
    this.buffer = new Uint8Array(0);
  }

  push(chunk) {
    const merged = new Uint8Array(this.buffer.length + chunk.length);
    merged.set(this.buffer);
    merged.set(chunk, this.buffer.length);
    this.buffer = merged;

    const frames = [];
    let offset = 0;
    while (this.buffer.length - offset >= HEADER_BYTES) {
      const view = new DataView(this.buffer.buffer, this.buffer.byteOffset + offset, HEADER_BYTES);
      const length = view.getUint32(0);
      if (this.buffer.length - offset - HEADER_BYTES < length) break;
      const keyframe = (view.getUint8(4) & 1) === 1;
      const ptsUs = Number(view.getBigUint64(5));
      const start = offset + HEADER_BYTES;
      frames.push({ keyframe, ptsUs, data: this.buffer.slice(start, start + length) });
      offset = start + length;
    }
    this.buffer = this.buffer.slice(offset);
    return frames;
  }
}

// avc1.PPCCLL codec string from the SPS NAL in an Annex B keyframe, or null.
export function codecFromKeyframe(data) {
  for (let i = 0; i + 4 < data.length; i++) {
    const startCode = data[i] === 0 && data[i + 1] === 0 && (data[i + 2] === 1 || (data[i + 2] === 0 && data[i + 3] === 1));
    if (!startCode) continue;
    const nal = data[i + 2] === 1 ? i + 3 : i + 4;
    if (nal + 3 < data.length && (data[nal] & 0x1f) === 7) {
      const hex = (b) => b.toString(16).padStart(2, '0');
      return `avc1.${hex(data[nal + 1])}${hex(data[nal + 2])}${hex(data[nal + 3])}`;
    }
  }
  return null;
}
