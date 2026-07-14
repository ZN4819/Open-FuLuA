export const RA_OPTIONS = ["1", "0.5", "0.2"] as const;
export const RK_OPTIONS = ["1", "1.2"] as const;

type TechnicalMetric = {
  d?: string | null;
  a?: string | null;
  k?: string | null;
  ra?: string | null;
  rk?: string | null;
  object_score?: string | null;
  unit_score?: string | null;
};

type TechnicalRow = {
  unit: string;
  metric_result?: TechnicalMetric | null;
};

type ManagementMetric = {
  compliance?: string | null;
  unit_score?: string | null;
};

type ManagementRow = {
  unit: string;
  metric_result?: ManagementMetric | null;
};

const METRIC_OPTIONS = new Set(["√", "×", "/"]);
const SCORE_SCALE = 10_000;
const MANAGEMENT_SCORES = new Map([
  ["符合", "1.0000"],
  ["部分符合", "0.5000"],
  ["不符合", "0.0000"],
  ["不适用", "/"]
]);

export function calculateObjectScore(metric: TechnicalMetric): string {
  const d = text(metric.d);
  const a = text(metric.a);
  const k = text(metric.k);
  if (!d || !a || !k || !METRIC_OPTIONS.has(d) || !METRIC_OPTIONS.has(a) || !METRIC_OPTIONS.has(k)) {
    return "";
  }
  const ra = factor(metric.ra, RA_OPTIONS, "1");
  const rk = factor(metric.rk, RK_OPTIONS, "1");
  if (ra === null || rk === null) {
    return "";
  }
  if (d === "/" && a === "/" && k === "/") {
    return "/";
  }
  if (d !== "√") {
    return "0.0000";
  }
  let score = 1;
  if (a !== "√") {
    score *= 0.5 * ra;
  }
  if (k !== "√") {
    score *= 0.5 * rk;
  }
  return formatScaled(Math.round(score * SCORE_SCALE));
}

export function calculateUnitScore(scores: Array<string | null | undefined>): string {
  if (scores.length === 0) {
    return "";
  }
  const numericScores: number[] = [];
  for (const value of scores) {
    const score = text(value);
    if (!score) {
      return "";
    }
    if (score === "/") {
      continue;
    }
    const numeric = Number(score);
    if (!Number.isFinite(numeric)) {
      return "";
    }
    numericScores.push(Math.round(numeric * SCORE_SCALE));
  }
  if (numericScores.length === 0) {
    return "/";
  }
  const total = numericScores.reduce((sum, score) => sum + score, 0);
  return formatScaled(Math.floor(total / numericScores.length + 0.5));
}

export function calculateTechnicalRows<T extends TechnicalRow>(rows: T[]): T[] {
  const withObjectScores = rows.map((source) => {
    const metric = source.metric_result ?? {};
    return {
      ...source,
      metric_result: {
        ...metric,
        ra: text(metric.ra) || "1",
        rk: text(metric.rk) || "1",
        object_score: calculateObjectScore(metric)
      }
    } as T;
  });

  const scoresByUnit = new Map<string, Array<string | null | undefined>>();
  withObjectScores.forEach((row) => {
    const unit = row.unit.trim();
    scoresByUnit.set(unit, [...(scoresByUnit.get(unit) ?? []), row.metric_result?.object_score]);
  });
  const unitScores = new Map<string, string>();
  scoresByUnit.forEach((scores, unit) => unitScores.set(unit, calculateUnitScore(scores)));

  return withObjectScores.map((source) => ({
    ...source,
    metric_result: {
      ...(source.metric_result ?? {}),
      unit_score: unitScores.get(source.unit.trim()) ?? ""
    }
  } as T));
}

export function calculateManagementRows<T extends ManagementRow>(rows: T[]): T[] {
  const scoresByUnit = new Map<string, string[]>();
  rows.forEach((row) => {
    const compliance = text(row.metric_result?.compliance);
    const score = MANAGEMENT_SCORES.get(compliance) ?? "";
    const unit = row.unit.trim();
    scoresByUnit.set(unit, [...(scoresByUnit.get(unit) ?? []), score]);
  });
  const unitScores = new Map<string, string>();
  scoresByUnit.forEach((scores, unit) => unitScores.set(unit, calculateUnitScore(scores)));
  return rows.map((row) => ({
    ...row,
    metric_result: {
      ...(row.metric_result ?? {}),
      unit_score: unitScores.get(row.unit.trim()) ?? ""
    }
  } as T));
}

function factor(value: string | null | undefined, allowed: readonly string[], fallback: string): number | null {
  const normalized = text(value) || fallback;
  return allowed.includes(normalized) ? Number(normalized) : null;
}

function formatScaled(value: number): string {
  return (value / SCORE_SCALE).toFixed(4);
}

function text(value: string | null | undefined): string {
  return (value ?? "").trim();
}
