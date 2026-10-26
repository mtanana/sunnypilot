// Bird's-eye view drawn from modelV2 geometry and radarState leads.
// Model points are device frame (x forward, y right); radarState yRel is left positive.

const RANGE_X = 100; // m ahead
const RANGE_Y = 12;  // m each side
const EGO_MARGIN = 30; // px below x = 0
const CAR_W = 1.8;
const CAR_L = 4.5;

export class TopDownView {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
  }

  toScreen(x, yRight) {
    const { width, height } = this.canvas;
    const usable = height - EGO_MARGIN;
    return [width / 2 + (yRight / RANGE_Y) * (width / 2), usable - (x / RANGE_X) * usable];
  }

  render(snapshot) {
    const { ctx, canvas } = this;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const model = snapshot.model;
    if (!model) return;

    this.drawGrid();
    this.drawPath(model.path);
    model.roadEdges.forEach((edge) => {
      const alpha = edge.std == null ? 0.5 : Math.max(0.15, Math.min(1, 1 - edge.std));
      this.drawLine(edge, `rgba(229, 72, 77, ${alpha})`, 3);
    });
    model.laneLines.forEach((line) => {
      const alpha = line.prob == null ? 0.5 : Math.max(0.08, line.prob);
      this.drawLine(line, `rgba(255, 255, 255, ${alpha})`, 2);
    });

    const lead = snapshot.lead;
    if (lead?.one?.present) {
      if (lead.desiredDistM != null) this.drawMarker(lead.desiredDistM, '#2fbf71');
      this.drawCar(lead.one.dRel, -lead.one.yRel, '#e0a526');
    }
    if (lead?.two?.present) this.drawCar(lead.two.dRel, -lead.two.yRel, 'rgba(224, 165, 38, 0.4)');
    this.drawCar(-CAR_L / 2, 0, '#4f8cff');
  }

  drawGrid() {
    const { ctx, canvas } = this;
    ctx.strokeStyle = '#1f252d';
    ctx.fillStyle = '#4a525c';
    ctx.font = '10px system-ui';
    ctx.lineWidth = 1;
    for (let x = 0; x <= RANGE_X; x += 20) {
      const [, sy] = this.toScreen(x, 0);
      ctx.beginPath();
      ctx.moveTo(0, sy);
      ctx.lineTo(canvas.width, sy);
      ctx.stroke();
      ctx.fillText(`${x} m`, 4, sy - 2);
    }
  }

  drawLine(xy, color, width) {
    if (!xy?.x?.length) return;
    const { ctx } = this;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    xy.x.forEach((x, i) => {
      const [sx, sy] = this.toScreen(x, xy.y[i]);
      if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
    });
    ctx.stroke();
  }

  drawPath(path) {
    if (!path?.x?.length) return;
    const { ctx } = this;
    const half = CAR_W / 2;
    ctx.fillStyle = 'rgba(79, 140, 255, 0.25)';
    ctx.beginPath();
    path.x.forEach((x, i) => {
      const [sx, sy] = this.toScreen(x, path.y[i] - half);
      if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
    });
    for (let i = path.x.length - 1; i >= 0; i--) {
      const [sx, sy] = this.toScreen(path.x[i], path.y[i] + half);
      ctx.lineTo(sx, sy);
    }
    ctx.closePath();
    ctx.fill();
  }

  drawCar(x, yRight, color) {
    const [x0, y0] = this.toScreen(x + CAR_L, yRight - CAR_W / 2);
    const [x1, y1] = this.toScreen(x, yRight + CAR_W / 2);
    this.ctx.fillStyle = color;
    this.ctx.fillRect(x0, y0, Math.max(4, x1 - x0), Math.max(4, y1 - y0));
  }

  drawMarker(x, color) {
    const [sxL, sy] = this.toScreen(x, -2.5);
    const [sxR] = this.toScreen(x, 2.5);
    const { ctx } = this;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(sxL, sy);
    ctx.lineTo(sxR, sy);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}
