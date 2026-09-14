use crate::{
    app::{App, TranscriptItem},
    protocol::SubagentInfo,
    workbench::ViewId,
};

#[derive(Default)]
pub struct Subagents {
    pub agents: Vec<AgentTranscript>,
    pub selection: Option<String>,
    pub viewing: Option<String>,
    main_scroll: Option<(u16, bool)>,
}

pub struct AgentTranscript {
    pub info: SubagentInfo,
    pub transcript: Vec<TranscriptItem>,
    scroll: Option<(u16, bool)>,
}

pub struct AgentRow {
    pub agent_id: Option<String>,
    pub selectable: bool,
    pub label: String,
}

impl Subagents {
    pub fn agent(&self, agent_id: &str) -> Option<&AgentTranscript> {
        self.agents.iter().find(|a| a.info.agent_id == agent_id)
    }

    pub fn agent_mut(&mut self, agent_id: &str) -> Option<&mut AgentTranscript> {
        self.agents.iter_mut().find(|a| a.info.agent_id == agent_id)
    }

    pub fn upsert(&mut self, info: SubagentInfo) {
        if let Some(agent) = self.agent_mut(&info.agent_id) {
            agent.info = info;
        } else {
            self.agents.push(AgentTranscript {
                info,
                transcript: Vec::new(),
                scroll: None,
            });
        }
    }

    pub fn rows(&self) -> Vec<AgentRow> {
        let mut rows = vec![AgentRow {
            agent_id: None,
            selectable: true,
            label: "main".to_owned(),
        }];
        for agent in &self.agents {
            // Members render beneath their leader, even when other agents arrive in between.
            if self.agent(&agent.info.parent_id).is_some() {
                continue;
            }
            if agent.info.agent_type == "group-leader" {
                rows.push(AgentRow {
                    agent_id: None,
                    selectable: false,
                    label: format!("{} [{}]", agent.info.name, agent.info.group_id),
                });
                self.append_tree(&mut rows, agent, "  └─ ", "     ");
            } else {
                self.append_tree(&mut rows, agent, "", "");
            }
        }
        rows
    }

    fn append_tree(
        &self,
        rows: &mut Vec<AgentRow>,
        agent: &AgentTranscript,
        prefix: &str,
        indent: &str,
    ) {
        let role = if agent.info.agent_type == "group-leader" {
            "leader"
        } else {
            &agent.info.agent_type
        };
        rows.push(AgentRow {
            agent_id: Some(agent.info.agent_id.clone()),
            selectable: true,
            label: format!(
                "{prefix}{} ({role}) · {}",
                agent.info.name, agent.info.status
            ),
        });
        let children: Vec<_> = self
            .agents
            .iter()
            .filter(|a| a.info.parent_id == agent.info.agent_id)
            .collect();
        for (index, child) in children.iter().enumerate() {
            let last = index + 1 == children.len();
            self.append_tree(
                rows,
                child,
                &format!("{indent}{}", if last { "└─ " } else { "├─ " }),
                &format!("{indent}{}", if last { "   " } else { "│  " }),
            );
        }
    }
}

impl App {
    pub fn visible_transcript(&self) -> &[TranscriptItem] {
        self.subagents
            .viewing
            .as_deref()
            .and_then(|id| self.subagents.agent(id))
            .map_or(&self.transcript, |agent| &agent.transcript)
    }

    pub fn move_subagent_selection(&mut self, down: bool) {
        let rows = self.subagents.rows();
        let choices: Vec<_> = rows.iter().filter(|row| row.selectable).collect();
        let current = choices
            .iter()
            .position(|row| row.agent_id == self.subagents.selection)
            .unwrap_or(0);
        let next = if down {
            (current + 1).min(choices.len() - 1)
        } else {
            current.saturating_sub(1)
        };
        self.subagents.selection = choices[next].agent_id.clone();
        let row = rows
            .iter()
            .position(|row| row.selectable && row.agent_id == self.subagents.selection)
            .unwrap_or(0);
        let geometry = self.geometry(self.terminal_size);
        if let Some(region) = geometry.view(ViewId::Subagents) {
            crate::workbench::scroll_to_row(
                self.layout.view_mut(ViewId::Subagents),
                row,
                usize::from(region.content.height),
            );
        }
    }

    /// Show the selected agent's transcript in the main panel, parking the
    /// scroll position of whichever transcript was on screen.
    pub fn open_selected_subagent(&mut self) {
        if self.subagents.viewing == self.subagents.selection {
            return;
        }
        let scroll = (self.layout.output.scroll, self.layout.output.follow);
        match self.subagents.viewing.take() {
            Some(id) => {
                if let Some(agent) = self.subagents.agent_mut(&id) {
                    agent.scroll = Some(scroll);
                }
            }
            None => self.subagents.main_scroll = Some(scroll),
        }
        self.subagents.viewing = self.subagents.selection.clone();
        let restored = match self.subagents.viewing.as_deref() {
            Some(id) => self.subagents.agent(id).and_then(|agent| agent.scroll),
            None => self.subagents.main_scroll,
        }
        .unwrap_or((0, true));
        (self.layout.output.scroll, self.layout.output.follow) = restored;
    }
}
