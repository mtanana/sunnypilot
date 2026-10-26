// Formatting helpers for the test page. Telemetry is SI; carState.vCruise* are km/h.

const MS_TO_MPH = 2.23694;
const MS_TO_KPH = 3.6;
const KPH_TO_MPH = 0.621371;

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

export function getPath(obj, path) {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

export function format(value, kind, isMetric) {
  if (value === undefined || value === null) return '-';
  switch (kind) {
    case 'bool':
      return value ? 'yes' : 'no';
    case 'speed':
      if (!isNum(value)) return '-';
      return isMetric ? `${(value * MS_TO_KPH).toFixed(1)} km/h` : `${(value * MS_TO_MPH).toFixed(1)} mph`;
    case 'kph':
      if (!isNum(value)) return '-';
      return isMetric ? `${value.toFixed(0)} km/h` : `${(value * KPH_TO_MPH).toFixed(0)} mph`;
    case 'lastSpeed':
      return Array.isArray(value) && value.length ? format(value[value.length - 1], 'speed', isMetric) : '-';
    case 'accel':
      return isNum(value) ? `${value.toFixed(2)} m/s²` : '-';
    case 'dist':
      return isNum(value) ? `${value.toFixed(1)} m` : '-';
    case 'sec':
      return isNum(value) ? `${value.toFixed(1)} s` : '-';
    case 'ms':
      return isNum(value) ? `${(value * 1000).toFixed(1)} ms` : '-';
    case 'deg':
      return isNum(value) ? `${value.toFixed(1)}°` : '-';
    case 'pct':
      return isNum(value) ? `${value.toFixed(0)}%` : '-';
    case 'temp':
      return isNum(value) ? `${value.toFixed(1)} °C` : '-';
    case 'list':
      return Array.isArray(value) ? (value.length ? value.join(', ') : 'none') : String(value);
    default:
      return isNum(value) ? String(Math.round(value * 100) / 100) : String(value);
  }
}
