export type LessonStatus = "pending" | "approved" | "rejected";
export const experienceFilters = ["pending", "approved", "rejected", "all"] as const;
export const experienceStatusLabels = { pending: "Pending review", approved: "Approved", rejected: "Rejected", all: "All" };
export type ExperienceFilter = typeof experienceFilters[number];
export type Lesson = {
  id: string; status: LessonStatus; scope: "target" | "technique"; signal: "success" | "deadend";
  context: string; lesson: string; confidence: number; target_key: string | null;
  tags: { tech: string[]; vuln_type: string; waf: string; service: string };
  evidence_refs: { run_id: string; finding_id: string | null; path: string | null };
  source_runs: string[]; created_at: string; reinforced_at: string;
};
export type ExperienceResult = { status?: ExperienceFilter; lessons?: Lesson[]; lesson?: Lesson };
