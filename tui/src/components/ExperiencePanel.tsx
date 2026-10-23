import { useEffect, useRef, useState } from "react";
import type { ScrollBoxRenderable } from "@opentui/core";
import { useKeyboard, useRenderer } from "@opentui/react";
import { copyText } from "../platform/clipboard";
import { experienceFilters, experienceStatusLabels as labels, type ExperienceFilter, type ExperienceResult, type Lesson } from "../session/experience";
import { palettes, type ThemeId } from "../theme/palette";
import { currentLanguage, useI18n } from "../i18n";
import { visibleText } from "./content";
import { MarkdownBody } from "./MarkdownBody";
import { FormPanel } from "./FormPanel";

type Focus = "filters" | "list" | "detail" | "actions";

export function ExperiencePanel({ initial, loading, error: initialError, active, disabled, theme, width, onList, onReview, onEdit, onClose, onNotify }: {
  initial: ExperienceResult; loading: boolean; error?: string; active: boolean; disabled: boolean;
  theme: ThemeId; width: number;
  onList: (status: ExperienceFilter) => Promise<ExperienceResult>;
  onReview: (id: string, status: "approved" | "rejected") => Promise<ExperienceResult>;
  onEdit: (id: string, changes: { context: string; lesson: string }) => Promise<ExperienceResult>;
  onClose: () => void; onNotify: (message: string) => void;
}) {
  const t = useI18n(), renderer = useRenderer(), p = palettes[theme];
  const narrow = width < 90;
  const [filter, setFilter] = useState<ExperienceFilter>(initial.status ?? initial.lesson?.status ?? "pending");
  const [lessons, setLessons] = useState(initial.lessons ?? (initial.lesson ? [initial.lesson] : []));
  const [selectedId, setSelectedId] = useState(initial.lesson?.id ?? initial.lessons?.[0]?.id ?? "");
  const selectedRef = useRef(selectedId);
  const [view, setView] = useState<"list" | "detail">(initial.lesson ? "detail" : "list");
  const [focus, setFocusState] = useState<Focus>(initial.lesson ? "detail" : "filters");
  const focusRef = useRef(focus);
  const [action, setAction] = useState(0), actionRef = useRef(0);
  const [editor, setEditor] = useState<{ context: string; lesson: string } | null>(null);
  const [busy, setBusy] = useState(false), pending = useRef(false);
  const [error, setError] = useState(initialError ?? "");
  const closed = useRef(false), revision = useRef(0);
  const focusInitial = useRef(initial);
  const listScroll = useRef<ScrollBoxRenderable>(null), detailScroll = useRef<ScrollBoxRenderable>(null);
  const selected = lessons.find((lesson) => lesson.id === selectedId);
  const working = busy || loading;
  function focusOn(next: Focus) { focusRef.current = next; setFocusState(next); }
  function select(id: string) { selectedRef.current = id; setSelectedId(id); }
  function close() { closed.current = true; revision.current++; onClose(); }
  useEffect(() => { closed.current = false; return () => { closed.current = true; revision.current++; }; }, []);
  useEffect(() => {
    revision.current++; closed.current = false; pending.current = false; setBusy(false);
    const status = initial.status ?? initial.lesson?.status ?? "pending";
    const rows = initial.lessons ?? (initial.lesson ? [initial.lesson] : []);
    setFilter(status); setLessons(rows); select(initial.lesson?.id ?? rows[0]?.id ?? "");
    setEditor(null); setError(initialError ?? "");
    setView(initial.lesson ? "detail" : "list"); focusOn(initial.lesson ? "detail" : "filters");
    if (initial.lesson && !initial.lessons) void refresh(status, initial.lesson.id);
  }, [initial, initialError]);
  useEffect(() => { listScroll.current?.scrollChildIntoView(`experience-row-${selectedId}`); detailScroll.current?.scrollTo(0); }, [selectedId]);
  async function refresh(status = filter, preferred = selectedRef.current) {
    if (pending.current || loading || disabled) return;
    pending.current = true; setBusy(true); setError("");
    const version = ++revision.current;
    const index = Math.max(0, lessons.findIndex((lesson) => lesson.id === preferred));
    try {
      const result = await onList(status);
      if (closed.current || revision.current !== version) return;
      const rows = result.lessons ?? [];
      setFilter(status); setLessons(rows);
      select(rows.find((lesson) => lesson.id === preferred)?.id ?? rows[Math.min(index, rows.length - 1)]?.id ?? "");
      if (!rows.length) setView("list");
    } catch (error) { if (!closed.current && revision.current === version) setError(String(error)); }
    finally { if (!closed.current && revision.current === version) { pending.current = false; setBusy(false); } }
  }
  function install(lesson: Lesson) {
    const index = Math.max(0, lessons.findIndex((item) => item.id === lesson.id));
    const rows = lessons.map((item) => item.id === lesson.id ? lesson : item)
      .filter((item) => filter === "all" || item.status === filter);
    setLessons(rows); select(rows.find((item) => item.id === lesson.id)?.id ?? rows[Math.min(index, rows.length - 1)]?.id ?? "");
    if (!rows.length) setView("list");
  }
  async function review(status: "approved" | "rejected") {
    if (!selected || selected.status === status || pending.current || loading || disabled) return;
    pending.current = true; setBusy(true); setError("");
    const version = ++revision.current;
    try {
      const result = await onReview(selected.id, status);
      if (!closed.current && revision.current === version && result.lesson) install(result.lesson);
    } catch (error) { if (!closed.current && revision.current === version) setError(String(error)); }
    finally { if (!closed.current && revision.current === version) { pending.current = false; setBusy(false); } }
  }
  function edit() {
    if (!selected || working || disabled) return;
    setError(""); setEditor({ context: selected.context, lesson: selected.lesson });
  }
  async function save() {
    if (!editor || !selected || pending.current || disabled) return;
    pending.current = true; setBusy(true); setError("");
    const version = ++revision.current;
    try {
      const result = await onEdit(selected.id, editor);
      if (!closed.current && revision.current === version && result.lesson) { install(result.lesson); setEditor(null); focusOn("detail"); }
    } catch (error) { if (!closed.current && revision.current === version) setError(String(error)); }
    finally { if (!closed.current && revision.current === version) { pending.current = false; setBusy(false); } }
  }
  function copy() {
    if (selected) void copyText(renderer, `${selected.context}\n\n${selected.lesson}`).then(onNotify).catch((error: unknown) => onNotify(String(error)));
  }
  const detailVisible = !narrow || view === "detail";
  const actions = [
    { id: "edit", label: "[E] Edit", disabled: !selected || disabled || working, run: edit },
    { id: "approve", label: "[A] Approve", disabled: !selected || selected.status === "approved" || disabled || working, run: () => { void review("approved"); } },
    { id: "reject", label: "[R] Reject", disabled: !selected || selected.status === "rejected" || disabled || working, run: () => { void review("rejected"); } },
    { id: "refresh", label: "[F5] Refresh", disabled: disabled || working, run: () => { void refresh(); } },
    { id: "close", label: "[Esc] Close", disabled: false, run: close },
  ];
  const listVisible = !narrow || view === "list";
  const visibleActions = actions.filter((button) => detailVisible || ["refresh", "close"].includes(button.id));
  const enabledActions = visibleActions.filter((button) => !button.disabled);
  const groups: Focus[] = [
    ...(listVisible && !disabled ? ["filters" as const] : []),
    ...(listVisible && lessons.length ? ["list" as const] : []),
    ...(detailVisible && selected ? ["detail" as const] : []), "actions",
  ];
  useEffect(() => {
    // Initial-data changes reset the local rows in the preceding effect.
    // Normalize focus on the next render, when those rows are installed.
    if (focusInitial.current !== initial) { focusInitial.current = initial; return; }
    if (!groups.includes(focusRef.current)) focusOn(groups[0]!);
    if (focusRef.current === "actions" && !enabledActions.includes(actions[actionRef.current]!)) {
      actionRef.current = actions.indexOf(enabledActions[0]!); setAction(actionRef.current);
    }
  });
  useKeyboard((key) => {
    if (!active || key.defaultPrevented || editor) return;
    const name = key.name.toLowerCase();
    if (name === "escape" || key.ctrl && name === "c") {
      key.preventDefault(); key.stopPropagation();
      if (narrow && view === "detail" && !key.ctrl) { setView("list"); focusOn("list"); } else close();
      return;
    }
    if (name === "tab") {
      key.preventDefault(); key.stopPropagation();
      focusOn(groups[(groups.indexOf(focusRef.current) + (key.shift ? -1 : 1) + groups.length) % groups.length]!); return;
    }
    if (name === "f5") { key.preventDefault(); void refresh(); return; }
    if (focusRef.current === "actions" && ["left", "right", "up", "down", "return"].includes(name)) {
      key.preventDefault(); key.stopPropagation();
      if (name === "return") {
        const button = actions[actionRef.current]!; if (!button.disabled) button.run();
      } else {
        const index = enabledActions.indexOf(actions[actionRef.current]!);
        const direction = name === "left" || name === "up" ? -1 : 1;
        actionRef.current = actions.indexOf(enabledActions[(index + direction + enabledActions.length) % enabledActions.length]!);
        setAction(actionRef.current);
      }
      return;
    }
    if (working) return;
    if (!key.ctrl && !key.meta && detailVisible && ["e", "a", "r", "m"].includes(name)) {
      key.preventDefault(); key.stopPropagation();
      if (name === "e") edit(); else if (name === "m") copy(); else void review(name === "a" ? "approved" : "rejected"); return;
    }
    if (focusRef.current === "filters" && ["left", "right"].includes(name)) {
      key.preventDefault(); key.stopPropagation(); void refresh(experienceFilters[(experienceFilters.indexOf(filter) + (name === "left" ? -1 : 1) + experienceFilters.length) % experienceFilters.length]);
    } else if (focusRef.current === "filters" && ["down", "return"].includes(name) && selected) {
      key.preventDefault(); key.stopPropagation(); focusOn("list");
    } else if (focusRef.current === "list" && ["up", "down", "pageup", "pagedown"].includes(name)) {
      key.preventDefault(); key.stopPropagation();
      const step = (name === "up" || name === "pageup" ? -1 : 1) * (name.startsWith("page") ? 5 : 1);
      const index = Math.max(0, lessons.findIndex((lesson) => lesson.id === selectedRef.current));
      if (name === "up" && index === 0 && !disabled) focusOn("filters");
      else select(lessons[Math.max(0, Math.min(lessons.length - 1, index + step))]?.id ?? "");
    } else if (focusRef.current === "list" && name === "return" && selected) {
      key.preventDefault(); key.stopPropagation(); setView("detail"); focusOn("detail");
    } else if (focusRef.current === "detail" && ["up", "down", "pageup", "pagedown"].includes(name)) {
      key.preventDefault(); const direction = name === "up" || name === "pageup" ? -1 : 1;
      detailScroll.current?.scrollBy(direction * (name.startsWith("page") ? Math.max(1, (detailScroll.current?.viewport.height ?? 2) - 1) : 1));
    }
  });
  function date(value: string) {
    const stamp = new Date(value);
    return Number.isNaN(stamp.getTime()) ? value : stamp.toLocaleString(currentLanguage() === "zh" ? "zh-CN" : "en-US");
  }
  function metadata(lesson: Lesson) {
    const values: [string, string][] = [
      ["Lesson ID", lesson.id], ["Status", t(labels[lesson.status])],
      ["Experience scope", t(lesson.scope === "target" ? "Target-specific" : "Technique")],
      ["Signal", t(lesson.signal === "success" ? "Successful approach" : "Dead end")],
      ["Confidence", `${Math.round(lesson.confidence * 100)}%`],
      ["Target", lesson.target_key ?? ""], ["Technology", lesson.tags.tech.join(", ")],
      ["Vulnerability type", lesson.tags.vuln_type], ["Service", lesson.tags.service], ["WAF", lesson.tags.waf],
      ["Source run", lesson.evidence_refs.run_id], ["Finding ID", lesson.evidence_refs.finding_id ?? ""],
      ["Evidence reference", lesson.evidence_refs.path ?? ""], ["Source runs", lesson.source_runs.join(", ")],
      ["Created", date(lesson.created_at)], ["Reinforced", date(lesson.reinforced_at)],
    ];
    return values.filter(([, value]) => value).map(([label, value]) => `${t(label)}: ${value}`).join("\n");
  }
  if (editor) return <FormPanel title={t("Edit experience: {id}", { id: selectedId })} theme={theme} active={active} busy={busy}
    rows={[
      { key: "context", label: "Matching context", value: editor.context, type: "text" },
      { key: "lesson", label: "Experience guidance", value: editor.lesson, type: "text" },
      { key: "confirm", label: "Save changes", footer: true, disabled, action: () => { void save(); } },
      { key: "cancel", label: "Cancel edit", footer: true, action: () => { setEditor(null); setError(""); focusOn("detail"); } },
    ]} notice={error || (disabled ? t("Experience management requires an idle session.") : undefined)}
    onChange={(key, value) => setEditor((old) => old ? { ...old, [key]: String(value) } : old)}
    onSave={() => { void save(); }} onCancel={() => { setEditor(null); setError(""); focusOn("detail"); }} />;
  return <box id="experience-pane" title={t("Experience review")} border borderColor={p.action} backgroundColor={p.panel}
    flexDirection="column" flexGrow={1} minHeight={0} paddingX={1}>
    {(!narrow || view === "list") && <box flexDirection="row" flexWrap="wrap" flexShrink={0} gap={1}>
      {experienceFilters.map((status) => <text id={`experience-filter-${status}`} key={status}
        bg={filter === status ? p.action : p.plate} fg={filter === status ? p.background : focus === "filters" ? p.action : p.soft}
        onMouseDown={() => { if (active && !editor) { focusOn("filters"); void refresh(status); } }}>{`${focus === "filters" && filter === status ? "›" : " "} ${t(labels[status])} `}</text>)}
      <text fg={p.muted}>{t("{count} lessons", { count: lessons.length })}</text>
    </box>}
    {narrow && view === "detail" && <text id="experience-back" height={1} flexShrink={0} fg={p.action} onMouseDown={() => { if (active) { setView("list"); focusOn("list"); } }}>{t("← Back to list")}</text>}
    <box flexDirection="row" flexGrow={1} minHeight={0}>
      {listVisible && <box id="experience-list-pane" title={`${focus === "list" ? "› " : ""}${t("Lessons")}`} border
        borderColor={focus === "list" ? p.action : p.border} width={narrow ? "100%" : 30} flexDirection="column" flexShrink={0} minHeight={0}>
      <scrollbox id="experience-list" ref={listScroll} scrollX={false} flexGrow={1} focused={active && focus === "list"}>
        {!lessons.length && <text wrapMode="word" fg={p.muted}>{t(loading ? "Working…" : "No lessons in this view.")}</text>}
        {lessons.map((lesson) => <box id={`experience-row-${lesson.id}`} key={lesson.id} flexDirection="column" flexShrink={0}
          backgroundColor={lesson.id === selectedId ? p.plate : p.panel} paddingRight={1}
          onMouseDown={() => { if (active && !working) { select(lesson.id); if (narrow) { setView("detail"); focusOn("detail"); } else focusOn("list"); } }}>
          <text fg={lesson.id === selectedId ? focus === "list" ? p.action : p.soft : p.text} height={1} truncate>{visibleText(`${lesson.id === selectedId ? focus === "list" ? "›" : "•" : " "} ${lesson.id}`)}</text>
          <text fg={p.muted} height={1} truncate>{visibleText(lesson.context.replace(/\s+/g, " "))}</text>
        </box>)}
      </scrollbox></box>}
      {detailVisible && <box id="experience-detail-pane" title={`${focus === "detail" ? "› " : ""}${t("Lesson details")}`} border
        borderColor={focus === "detail" ? p.action : p.border} flexDirection="column" flexGrow={1} minWidth={0} minHeight={0}
        onMouseDown={() => { if (active && selected) focusOn("detail"); }}>
      <scrollbox id="experience-detail" key={selectedId} ref={detailScroll} flexGrow={1} minWidth={0} scrollX={false}
        focused={active && focus === "detail"}>
        {selected ? <box flexDirection="column" paddingLeft={narrow ? 0 : 1}>
          <text fg={p.soft} wrapMode="word">{visibleText(metadata(selected))}</text>
          <text fg={p.action} marginTop={1}><b>{t("Matching context")}</b></text>
          <text fg={p.text} wrapMode="word">{visibleText(selected.context)}</text>
          <text fg={p.action} marginTop={1}><b>{t("Experience guidance")}</b></text>
          <MarkdownBody id={`experience-body-${selected.id}`} text={selected.lesson} width={Math.max(1, width - (narrow ? 8 : 39))} theme={theme} />
        </box> : <text fg={p.muted} wrapMode="word">{t("Select a lesson to inspect its content and evidence.")}</text>}
      </scrollbox></box>}
    </box>
    {(error || disabled) && <text fg={error ? p.error : p.muted} wrapMode="word">{visibleText(error || t("Experience management requires an idle session."))}</text>}
    <box flexDirection="column" flexShrink={0}>
      {(narrow && detailVisible ? [actions.slice(0, 2), actions.slice(2)] : [actions.filter((button) => detailVisible || ["refresh", "close"].includes(button.id))]).map((buttons, index) =>
        <box key={index} flexDirection="row" height={1} flexShrink={0} gap={1}>
          {buttons.map((button) => <text key={button.id} id={`experience-${button.id}`}
            fg={button.disabled ? p.hint : focus === "actions" && actions[action]?.id === button.id ? p.background : button.id === "reject" ? p.error : p.action}
            bg={focus === "actions" && actions[action]?.id === button.id ? p.action : p.panel}
            onMouseDown={() => { if (active && !button.disabled) { actionRef.current = actions.indexOf(button); setAction(actionRef.current); focusOn("actions"); button.run(); } }}>{`${focus === "actions" && actions[action]?.id === button.id ? "›" : ""}${t(button.label)}`}</text>)}
        </box>)}
    </box>
    <text fg={p.muted} wrapMode="word" flexShrink={0}>{t(working ? "Working…" : focus === "filters"
      ? "←→ filter · ↓/Enter list" : focus === "list" ? "↑↓/PgUp/PgDn select · Enter details · ↑ at first: filters"
        : focus === "detail" ? "↑↓/PgUp/PgDn scroll · E edit · A approve · R reject · M copy"
          : "←→ choose action · Enter activate")}{"\n"}{t("Tab/Shift+Tab focus · F5 refresh · Esc back/close")}</text>
  </box>;
}
