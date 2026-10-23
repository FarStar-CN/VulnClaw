import { useEffect, useState } from "react";
import { MouseButton } from "@opentui/core";
import type { Card } from "../session/controller";
import { executionPresentation, executionResult, failedExecution, fitExecution, operationLabel,
  operationReceipt, operationSections, operationTitle, type Operation } from "../session/executions";
import { palettes, type ThemeId } from "../theme/palette";
import { disclosureIcon, visibleText } from "./content";
import { useI18n } from "../i18n";

export interface ExecutionNavigation {
  actions: Map<string, () => void>;
  selected: string;
  select(id: string): void;
  expanded: Map<string, boolean>;
}
function useExpansion(id: string, navigation: ExecutionNavigation): [boolean, () => void] {
  const [open, setOpen] = useState(navigation.expanded.get(id) ?? false);
  return [open, () => setOpen(value => {
    navigation.expanded.set(id, !value);
    return !value;
  })];
}
function Action({ id, navigation, action, children, color }: {
  id: string; navigation: ExecutionNavigation; action(): void; children: React.ReactNode; color: string;
}) {
  useEffect(() => { navigation.actions.set(id, action); return () => { navigation.actions.delete(id); }; }, [id, navigation.actions, action]);
  return <text id={id} fg={color} height={1} alignSelf="flex-start" maxWidth="100%" onMouseDown={(event) => {
    if (event.button === MouseButton.LEFT) { navigation.select(id); action(); }
  }}>{navigation.selected === id ? "› " : "  "}{children}</text>;
}
function Section({ id, label, text, theme, navigation, collapsible = true }: {
  id: string; label: string; text: string; theme: ThemeId; navigation: ExecutionNavigation; collapsible?: boolean;
}) {
  const [open, toggle] = useExpansion(id, navigation);
  const t = useI18n(), p = palettes[theme];
  return <box flexDirection="column" flexShrink={0} marginTop={1}>
    {collapsible ? <Action id={id} navigation={navigation} color={p.muted} action={toggle}>
      {disclosureIcon(open)}{t(label)}
    </Action> : <text fg={p.muted}>{t(label)}</text>}
    {(!collapsible || open) && <text id={`${id}-content`} wrapMode="char" fg={p.text}>{visibleText(text)}</text>}
  </box>;
}
function OperationBody({ operation, id, theme, navigation }: {
  operation: Operation; id: string; theme: ThemeId; navigation: ExecutionNavigation;
}) {
  const p = palettes[theme];
  const original = operationTitle(operation);
  const sections = operationSections(operation);
  return <box flexDirection="column" flexShrink={0} paddingLeft={2}>
    <text id={`${id}-command`} wrapMode="char" fg={p.text}>{visibleText(original)}</text>
    {operation.error && <text fg={p.error} wrapMode="word">{visibleText(operation.error)}</text>}
    {sections.map((section) => <Section key={section.label} id={`${id}-${section.label}`}
      {...section} theme={theme} navigation={navigation} collapsible={sections.length > 1} />)}
  </box>;
}
function OperationRow({ operation, id, theme, width, navigation }: {
  operation: Operation; id: string; theme: ThemeId; width: number; navigation: ExecutionNavigation;
}) {
  const [open, toggle] = useExpansion(id, navigation), p = palettes[theme];
  return <box flexDirection="column" flexShrink={0} marginTop={1} paddingLeft={2}>
    <Action id={id} navigation={navigation} color={p.accent} action={toggle}>
      {disclosureIcon(open)}{fitExecution(open ? operationLabel(operation) : visibleText(operationTitle(operation)), Math.max(1, width - 6))}
    </Action>
    <text height={1} fg={failedExecution(operation.status) ? p.error : p.muted}>
      {fitExecution(operationReceipt(operation), Math.max(1, width - 4))}
    </text>
    {operation.error && !open && <text fg={p.error} wrapMode="word">{visibleText(operation.error)}</text>}
    {open && <OperationBody operation={operation} id={id} theme={theme} navigation={navigation} />}
  </box>;
}
export function ExecutionCard({ card, theme, width, navigation }: {
  card: Card; theme: ThemeId; width: number; navigation: ExecutionNavigation;
}) {
  const record = card.execution!;
  const id = `execution-${card.id}`;
  const [open, toggle] = useExpansion(id, navigation), t = useI18n(), p = palettes[theme];
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
    {open && view.expandable && (view.category !== "execution" ? <box flexDirection="column" flexShrink={0} paddingLeft={2}>
      {view.sections.map((section, index) => <box key={index} flexDirection="column" flexShrink={0} marginTop={1}>
        <text fg={p.muted}>{visibleText(section.label)}</text>
        <text id={`${id}-business-${index}-content`} wrapMode="char" fg={p.text}>{visibleText(section.text)}</text>
      </box>)}
    </box> : <>
      {operations.length === 1 ? <OperationBody operation={operations[0]!} id={`${id}-op`} theme={theme}
        navigation={navigation} /> : operations.map((operation) => <OperationRow key={operation.operation_id}
        id={`${id}-${operation.operation_id}`} operation={operation} theme={theme} width={width}
        navigation={navigation} />)}
      {result && <Section id={`${id}-result`} label="Result" text={result}
        theme={theme} navigation={navigation} collapsible={operations.length > 0} />}
      {!operations.length && !record.result && <text fg={p.muted}>{t(record.received ? "No output" : "Waiting for execution details")}</text>}
    </>)}
  </box>;
}
