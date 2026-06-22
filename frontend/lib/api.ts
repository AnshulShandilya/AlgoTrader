import axios from "axios";

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
});

export default api;

// Portfolio
export const getPortfolioSnapshot = () => api.get("/portfolio/snapshot").then(r => r.data);
export const getPositions = () => api.get("/portfolio/positions").then(r => r.data);
export const getSettings = () => api.get("/portfolio/settings").then(r => r.data);
export const getActiveBroker = () => api.get("/portfolio/broker").then(r => r.data);
export const saveSettings = (data: Record<string, unknown>) => api.put("/portfolio/settings", data).then(r => r.data);

// Strategies
export const getStrategyTemplates = () => api.get("/strategies/templates").then(r => r.data);
export const getStrategies = () => api.get("/strategies/").then(r => r.data);
export const createStrategy = (data: Record<string, unknown>) => api.post("/strategies/", data).then(r => r.data);
export const updateStrategy = (id: number, data: Record<string, unknown>) => api.patch(`/strategies/${id}`, data).then(r => r.data);
export const deleteStrategy = (id: number) => api.delete(`/strategies/${id}`).then(r => r.data);
export const runSignal = (id: number) => api.post(`/strategies/${id}/run-signal`).then(r => r.data);

// Trades
export const getTrades = (params?: Record<string, unknown>) => api.get("/trades/", { params }).then(r => r.data);
export const getTradeStats = () => api.get("/trades/stats").then(r => r.data);
export const getEquityCurve = (days = 30) => api.get("/trades/equity-curve", { params: { days } }).then(r => r.data);
export const getIntradayCurve = () => api.get("/trades/intraday-curve").then(r => r.data);
export const executeStrategy = (id: number) => api.post(`/trades/execute/${id}`).then(r => r.data);

// Emergency
export const closeAllPositions = () => api.post("/portfolio/close-all").then(r => r.data);

// Market
export const getBars = (symbol: string, timeframe = "1Day", limit = 100) =>
  api.get(`/market/bars/${symbol}`, { params: { timeframe, limit } }).then(r => r.data);
export const getQuote = (symbol: string) => api.get(`/market/quote/${symbol}`).then(r => r.data);
export const getWatchlist = (symbols: string) => api.get("/market/watchlist", { params: { symbols } }).then(r => r.data);

// Automation
export const getAutomationStatus = () => api.get("/automation/status").then(r => r.data);
export const reloadJobs = () => api.post("/automation/reload").then(r => r.data);
export const runNow = (id: number) => api.post(`/automation/run-now/${id}`).then(r => r.data);
export const pauseAll = () => api.post("/automation/pause-all").then(r => r.data);

// Scanner
export const getLastScan = () => api.get("/scanner/last").then(r => r.data);
export const triggerScan = () => api.post("/scanner/run").then(r => r.data);

// Backtesting
export const runBacktest = (data: Record<string, unknown>) =>
  api.post("/backtest/run", data).then(r => r.data);
export const runPairsBacktest = (data: Record<string, unknown>) =>
  api.post("/backtest/run-pairs", data).then(r => r.data);
export const findPairs = (period = "1y") =>
  api.get("/backtest/find-pairs", { params: { period } }).then(r => r.data);
export const runWalkForward = (data: Record<string, unknown>) =>
  api.post("/backtest/walk-forward", data).then(r => r.data);

// Auto-Pilot
export const runAutoPilot = (data?: Record<string, unknown>) =>
  api.post("/autopilot/run", data ?? {}).then(r => r.data);
export const getLastAutoPilot = () =>
  api.get("/autopilot/last").then(r => r.data);
export const getAutoPilotProgress = () =>
  api.get("/autopilot/progress").then(r => r.data);

export const deployPreview = (data: Record<string, unknown>) =>
  api.post("/autopilot/deploy/preview", data).then(r => r.data);
export const deployExecute = (data: Record<string, unknown>) =>
  api.post("/autopilot/deploy/execute", data).then(r => r.data);

// Events
export const getEvents = (params?: { limit?: number; type?: string; severity?: string }) =>
  api.get("/events/", { params }).then(r => r.data);
export const getEventsSummary = () => api.get("/events/summary").then(r => r.data);

// Sessions (daily trading journal + learning)
export const getTodaySession = () => api.get("/sessions/today").then(r => r.data);
export const startSession = (bias = "neutral", notes?: string) =>
  api.post("/sessions/start", null, { params: { bias, notes } }).then(r => r.data);
export const closeSession = (notes?: string) =>
  api.post("/sessions/close", null, { params: { notes } }).then(r => r.data);
export const getSessionHistory = (limit = 30) =>
  api.get("/sessions/history", { params: { limit } }).then(r => r.data);
export const getCalibration = () => api.get("/sessions/calibration").then(r => r.data);
export const getGapAnalysis = () => api.get("/sessions/gap-analysis").then(r => r.data);
export const getPreTradeCheck = (strategyId: number) =>
  api.get(`/trades/pre-trade-check/${strategyId}`).then(r => r.data);

// Grok Trader
export const getGrokSetups  = () => api.get("/grok-trader/setups").then(r => r.data);
export const getGrokHealth  = () => api.get("/grok-trader/health").then(r => r.data);
export const triggerGrokScan = (ctx?: object) =>
  api.post("/grok-trader/scan", ctx ?? {}).then(r => r.data);

// Grok Auto-Trade controls
export const getGrokAutoTradeStatus = () =>
  api.get("/automation/grok-auto-trade/status").then(r => r.data);
export const toggleGrokAutoTrade = (enabled: boolean) =>
  api.post("/automation/grok-auto-trade/toggle", null, { params: { enabled } }).then(r => r.data);
export const setGrokMinConfidence = (confidence: number) =>
  api.post("/automation/grok-auto-trade/min-confidence", null, { params: { confidence } }).then(r => r.data);

// Grok full scan (news + sentiment + contracts)
export const getGrokLastScan = () =>
  api.get("/automation/grok/last-scan").then(r => r.data);

// Grok filtered candidates (4-layer funnel output)
export const getGrokCandidates = () =>
  api.get("/automation/grok/candidates").then(r => r.data);

// Universe screener (boom stocks + penny movers)
export const triggerUniverseRefresh = () =>
  api.post("/market/universe-refresh").then(r => r.data);
export const getUniverseMovers = (filter = "all", limit = 50) =>
  api.get("/market/universe-movers", { params: { filter, limit } }).then(r => r.data);
export const getBoomCheck = (symbols: string) =>
  api.get("/market/boom-check", { params: { symbols } }).then(r => r.data);
export const getUniverseStats = () =>
  api.get("/market/universe-stats").then(r => r.data);
export const getUniverseCandidates = (
  params: { asset_class?: string; sort_by?: string; direction?: string; limit?: number } = {}
) => api.get("/market/universe-candidates", { params }).then(r => r.data);

