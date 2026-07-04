import React, { useRef, useState } from 'react';

/**
 * Cursor-following tooltip for the results charts.
 *
 * Usage:
 *   const { containerRef, tip, showTip, hideTip } = useChartTooltip();
 *   <div className="chart-tooltip__container" ref={containerRef}>
 *     <target onMouseMove={(e) => showTip(e, content)} onMouseLeave={hideTip} />
 *     <ChartTooltip tip={tip} />
 *   </div>
 */
export function useChartTooltip() {
  const containerRef = useRef(null);
  const [tip, setTip] = useState(null);

  const showTip = (e, content) => {
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const x = Math.min(
      Math.max(e.clientX - rect.left, 90),
      rect.width - 90
    );
    setTip({ x, y: e.clientY - rect.top, content });
  };

  const hideTip = () => setTip(null);

  return { containerRef, tip, showTip, hideTip };
}

export function ChartTooltip({ tip }) {
  if (!tip) return null;
  return (
    <div className="chart-tooltip" style={{ left: tip.x, top: tip.y }}>
      {tip.content}
    </div>
  );
}
