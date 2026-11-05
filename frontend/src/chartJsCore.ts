// Statically imports only the Chart.js pieces the dashboard uses so the
// bundler can tree-shake the rest. chart.ts loads this module lazily.
import { Chart, Filler, Interaction, LineController, LineElement, LinearScale, PointElement, Tooltip } from 'chart.js';

Chart.register(LineController, LineElement, PointElement, LinearScale, Tooltip, Filler);

export { Chart, Interaction, Tooltip };
