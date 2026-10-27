import { useEffect, useState } from "react";
import { MouseButton } from "@opentui/core";
import type { Card } from "../session/controller";
import { executionPresentation, executionResult, failedExecution, fitExecution, operationLabel,
  operationReceipt, operationSections, operationTitle, type Operation } from "../session/executions";
import { palettes, type ThemeId } from "../theme/palette";
import { disclosureIcon, visibleText } from "./content";
import { useI18n } from "../i18n";
import { useExpansion } from "./useExpansion";
import { ExpandedContent } from "./ExpandedContent";
import type { ExecutionDetailRef } from "../transport/protocol.generated";
import type { DetailPage, ExecutionDetailCache } from "../session/details";

export interface ExecutionNavigation {
  actions: Map<string, () => void>;
  selected: string;
  select(id: string): void;
  expanded: Map<string, boolean>;
  details: ExecutionDetailCache;
}
function Action({ id, navigation, action, children, color }: {
  id: string; navigation: ExecutionNavigation; action(): void; children: React.ReactNode; color: string;
}) {
  useEffect(() => { navigation.actions.set(id, action); return () => { navigation.actions.delete(id); }; }, [id, navigation.actions, action]);
  return <text id={id} fg={color} height={1} alignSelf="flex-start" maxWidth="100%" onMouseDown={(event) => {
    if (event.button === MouseButton.LEFT) { navigation.select(id); action(); }
  }}>{navigation.selected === id ? "› " : "  "}{children}</text>;
}
function DetailBody({ id, detail, theme, navigation }: {
  id: string; detail: ExecutionDetailRef; theme: ThemeId; navigation: ExecutionNavigation;
}) {
  const t = useI18n(), p = palettes[theme];
  const [offsets, setOffsets] = useState([0]);
  const [state, setState] = useState<{ page?: DetailPage; error?: string }>({});
  const [retry, setRetry] = useState(0);
  const offset = offsets.at(-1)!;
  useEffect(() => {
    let active = true;
    setState({});
    void navigation.details.read(detail, offset).then((page) => {
      if (active) setState({ page });
    }).catch((error: unknown) => { if (active) setState({ error: String(error) }); });
    return () => { active = false; };
  }, [detail.id, offset, navigation.details, retry]);
  const page = state.page?.offset === offset ? state.page : undefined;
  return <box flexDirection="column" flexShrink={0}>
    {page ? <>
      <text fg={p.muted}>{t("Bytes {start}–{end} of {total}", {
        start: page.offset + 1, end: page.next_offset, total: page.bytes,
      })}</text>
      <text id={`${id}-content`} wrapMode="char" fg={p.text}>{visibleText(page.text)}</text>
      {offsets.length > 1 && <Action id={`${id}-previous`} navigation={navigation} color={p.accent}
        action={() => setOffsets((values) => values.slice(0, -1))}>{t("Previous page")}</Action>}
      {page.next_offset < page.bytes && <Action id={`${id}-next`} navigation={navigation} color={p.accent}
        action={() => setOffsets((values) => [...values, page.next_offset])}>{t("Next page")}</Action>}
    </> : state.error ? <>
      <text fg={p.error}>{visibleText(state.error)}</text>
      <Action id={`${id}-retry`} navigation={navigation} color={p.accent}
        action={() => setRetry((value) => value + 1)}>{t("Retry")}</Action>
    </> : <text fg={p.muted}>{t("Loading…")}</text>}
  </box>;
}
function Section({ id, label, text, detail, theme, navigation, collapsible = true, marginTop = 0 }: {
  id: string; label: string; text: string; detail?: ExecutionDetailRef;
  theme: ThemeId; navigation: ExecutionNavigation; collapsible?: boolean; marginTop?: number;
}) {
  const [open, toggle] = useExpansion(id, navigation.expanded);
  const t = useI18n(), p = palettes[theme];
  return <box flexDirection="column" flexShrink={0} marginTop={marginTop}>
    {collapsible ? <Action id={id} navigation={navigation} color={p.muted} action={toggle}>
      {disclosureIcon(open)}{t(label)}{detail ? ` · ${t("{count} bytes", { count: detail.bytes })}` : ""}
    </Action> : <text fg={p.muted}>{t(label)}{detail ? ` · ${t("{count} bytes", { count: detail.bytes })}` : ""}</text>}
    {(!collapsible || open) && (detail ? <DetailBody key={detail.id} id={id} detail={detail}
      theme={theme} navigation={navigation} />
      : <text id={`${id}-content`} wrapMode="char" fg={p.text}>{visibleText(text)}</text>)}
  </box>;
}
function OperationBody({ operation, id, theme, navigation }: {
  operation: Operation; id: string; theme: ThemeId; navigation: ExecutionNavigation;
}) {
  const p = palettes[theme];
  const original = operationTitle(operation);
  const sections = operationSections(operation);
  return <box flexDirection="column" flexShrink={0}>
    <text id={`${id}-command`} wrapMode="char" fg={p.text}>{visibleText(original)}</text>
    {operation.error && <text fg={p.error} wrapMode="word">{visibleText(operation.error)}</text>}
    {sections.map((section) => <Section key={section.label} id={`${id}-${section.label}`}
      {...section} theme={theme} navigation={navigation} collapsible={sections.length > 1} marginTop={1} />)}
  </box>;
}
function OperationRow({ operation, id, theme, width, navigation }: {
  operation: Operation; id: string; theme: ThemeId; width: number; navigation: ExecutionNavigation;
}) {
  const [open, toggle] = useExpansion(id, navigation.expanded), p = palettes[theme];
  return <box flexDirection="column" flexShrink={0}>
    <Action id={id} navigation={navigation} color={p.accent} action={toggle}>
      {disclosureIcon(open)}{fitExecution(open ? operationLabel(operation) : visibleText(operationTitle(operation)), Math.max(1, width - 6))}
    </Action>
    <text height={1} fg={failedExecution(operation.status) ? p.error : p.muted}>
      {fitExecution(operationReceipt(operation), Math.max(1, width - 4))}
    </text>
    {operation.error && !open && <text fg={p.error} wrapMode="word">{visibleText(operation.error)}</text>}
    {open && <box flexDirection="column" flexShrink={0} paddingLeft={2}>
      <OperationBody operation={operation} id={id} theme={theme} navigation={navigation} />
    </box>}
  </box>;
}
export function ExecutionCard({ card, theme, width, navigation }: {
  card: Card; theme: ThemeId; width: number; navigation: ExecutionNavigation;
}) {
  const record = card.execution!;
  const id = `execution-${card.id}`;
  const [open, toggle] = useExpansion(id, navigation.expanded), t = useI18n(), p = palettes[theme];
  const view = executionPresentation(record);
  const operations = [...record.operations.values()];
  const result = executionResult(record, record.result ?? "");
  const title = open ? view.expandedTitle ?? view.title : view.title;
  return <box id={`entry-${card.id}`} flexDirection="column" flexShrink={0} marginTop={1}>
    {view.expandable ? <Action id={id} navigation={navigation} color={p.accent} action={toggle}>
      {disclosureIcon(open)}{fitExecution(visibleText(title), Math.max(1, width - 4))}
    </Action> : <text fg={p.accent} wrapMode="char">{"  " + view.title}</text>}
    <text height={1} fg={failedExecution(view.status) || view.status === "partial" ? p.error : p.muted}>
      {fitExecution(view.receipt, Math.max(1, width))}
    </text>
    {view.category === "execution" && record.evidenceId && <text fg={p.muted}>{t("Evidence {id}", { id: record.evidenceId })}</text>}
    {view.error && <text fg={p.error} wrapMode="word">{visibleText(view.error)}</text>}
    {open && view.expandable && <ExpandedContent id={`${id}-details`} theme={theme}>
      {view.category !== "execution" ? <>
        {view.sections.map((section, index) => <box key={index} flexDirection="column" flexShrink={0}>
          <text fg={p.muted}>{visibleText(section.label)}</text>
          <text id={`${id}-business-${index}-content`} wrapMode="char" fg={p.text}>{visibleText(section.text)}</text>
        </box>)}
        {record.resultRef && <Section id={`${id}-result`} label="Result" text="" detail={record.resultRef}
          theme={theme} navigation={navigation} />}
      </> : <>
        {operations.length === 1 ? <OperationBody operation={operations[0]!} id={`${id}-op`} theme={theme}
          navigation={navigation} /> : operations.map((operation) => <OperationRow key={operation.operation_id}
          id={`${id}-${operation.operation_id}`} operation={operation} theme={theme} width={Math.max(1, width - 2)}
          navigation={navigation} />)}
        {result && <Section id={`${id}-result`} label="Result" text={result} detail={record.resultRef}
          theme={theme} navigation={navigation} collapsible={operations.length > 0} />}
        {!operations.length && !record.result && <text fg={p.muted}>{t(record.received ? "No output" : "Waiting for execution details")}</text>}
      </>}
    </ExpandedContent>}
  </box>;
}
