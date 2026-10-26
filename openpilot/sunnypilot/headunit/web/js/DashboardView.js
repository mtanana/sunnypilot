import { format, getPath } from './units.js';

// Fills every [data-field] element from the snapshot, using its data-format.
export class DashboardView {
  constructor(root) {
    this.fields = [...root.querySelectorAll('[data-field]')].map((el) => ({
      el,
      path: el.dataset.field,
      kind: el.dataset.format || 'text',
    }));
    this.alert = root.querySelector('#alert');
    this.seq = root.querySelector('#seq');
    this.raw = root.querySelector('#raw');
    this.rawDetails = this.raw ? this.raw.closest('details') : null;
    this.lastRawUpdate = 0;
  }

  render(snapshot) {
    for (const { el, path, kind } of this.fields) {
      const value = getPath(snapshot, path);
      el.textContent = format(value, kind, snapshot.isMetric);
      if (kind === 'bool') {
        el.classList.toggle('true', value === true);
        el.classList.toggle('false', value !== true);
      }
    }

    if (this.alert) {
      this.alert.className = `alert ${snapshot.engage?.alertStatus || ''}`;
    }
    if (this.seq) this.seq.textContent = snapshot.seq;

    const now = performance.now();
    if (this.rawDetails?.open && now - this.lastRawUpdate > 500) {
      this.raw.textContent = JSON.stringify(snapshot, null, 2);
      this.lastRawUpdate = now;
    }
  }
}
