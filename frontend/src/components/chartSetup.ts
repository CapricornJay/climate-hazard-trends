import {
  BarController,
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from "chart.js";
import annotationPlugin from "chartjs-plugin-annotation";

ChartJS.register(BarController, BarElement, CategoryScale, Filler, Legend, LineController, LineElement, LinearScale, PointElement, Tooltip, annotationPlugin);

ChartJS.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
ChartJS.defaults.font.size = 12;
ChartJS.defaults.color = "#4b5563";
ChartJS.defaults.borderColor = "#e5e7eb";

export const COLORS = {
  heatwave: { main: "#c2410c", soft: "rgba(194, 65, 12, 0.18)", strong: "#7c2d12" },
  heavy_rain: { main: "#1d4ed8", soft: "rgba(29, 78, 216, 0.18)", strong: "#1e3a8a" },
  trend: "#0f766e",
  rolling: "#111827",
  muted: "#d4d4d8",
};
