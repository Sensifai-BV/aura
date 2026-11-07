/** AURA Telemetry — window.Aura */
type ModuleId = 'stt' | 'intent' | 'gesture' | 'speaker';
type InteractionState = 'idle' | 'listening' | 'transcribing' | 'resolving' | 'confirming' | 'executing' | 'accepted' | 'rejected' | 'safe_stop';
type Thresholds = { reject: number; accept: number };
/** A color token name, e.g. 'mod-stt', 'ok', 'accent'. */
type ColorToken = string;

export interface PanelProps { title: string; source?: string; live?: boolean | 'stale'; flush?: boolean; children?: React.ReactNode; }
export interface ModuleTagProps { module: ModuleId | string; }
export interface StateBadgeProps { state: InteractionState; label?: string; }
export interface ConfidenceMeterProps { value: number; thresholds?: Thresholds; }
export interface StatTileProps { label: string; value: string; unit?: string; delta?: number; deltaUnit?: string; higherIsBetter?: boolean; caption?: string; spark?: number[]; sparkColor?: ColorToken; tone?: 'ok' | 'warn' | 'danger'; }
export interface HRIEventRow { id: string | number; time: number | string | Date; module: ModuleId; event: string; detail?: string; intent?: string; confidence?: number; state?: InteractionState; latencyMs?: number; }
export interface LogTableProps { rows: HRIEventRow[]; columns?: Array<'time' | 'module' | 'event' | 'detail' | 'intent' | 'confidence' | 'state' | 'latency'>; maxRows?: number; height?: number; thresholds?: Thresholds; emptyText?: string; }
export interface TimeSeriesChartProps { series: Array<{ name: string; color: ColorToken; points: Array<{ t: number; v: number }> }>; yMin?: number; yMax?: number; unit?: string; digits?: number; height?: number; thresholds?: Array<{ value: number; label: string; tone?: ColorToken }>; legend?: boolean; ariaLabel?: string; }
export interface BarChartProps { items: Array<{ label: string; value: number; color?: ColorToken }>; color?: ColorToken; unit?: string; digits?: number; max?: number; }
export interface ModuleStatus { health: 'ok' | 'degraded' | 'down'; rate?: number; rateUnit?: string; latencyMs?: number; }
export interface PipelineMapProps { modules: Partial<Record<ModuleId, ModuleStatus>>; sink?: { name?: string; rate?: number; lagMs?: number }; }
